"""GenericAgent 桥接进程 - 由启动器用系统 Python 启动
通过 stdin/stdout 收发 JSON 行与启动器通信。
"""
import ast
import json
import os
import re
import sys
import threading
import traceback

_RESTORE_BLOCK_RE = re.compile(
    r"^=== (Prompt|Response) ===.*?\n(.*?)(?=^=== (?:Prompt|Response) ===|\Z)",
    re.DOTALL | re.MULTILINE,
)
_HISTORY_RE = re.compile(r"<history>\s*(.*?)\s*</history>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
_TURN_RE = re.compile(r"Current turn:\s*(\d+)")

def send(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _ui_llms(agent):
    items = []
    for i, raw_name, current in agent.list_llms():
        name = raw_name.split("/", 1)[1] if "/" in raw_name else raw_name
        items.append({"idx": i, "name": name, "current": current})
    return items


def _native_prompt_obj(prompt_body):
    try:
        prompt = json.loads(prompt_body)
    except Exception:
        return None
    if not isinstance(prompt, dict) or prompt.get("role") != "user":
        return None
    if not isinstance(prompt.get("content"), list):
        return None
    return prompt


def _native_prompt_text(prompt):
    texts = []
    for block in prompt.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text:
                texts.append(text)
    return "\n".join(texts).strip()


def _native_history_lines(prompt_text):
    match = _HISTORY_RE.search(prompt_text or "")
    if not match:
        return []
    restored = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("[USER]: ") or line.startswith("[Agent] "):
            restored.append(line)
    return restored


def _native_first_user_line(prompt_text, file_hint):
    text = (prompt_text or "").strip()
    if not text or "<history>" in text or text.startswith("### [WORKING MEMORY]"):
        return ""
    if file_hint and text.startswith(file_hint):
        text = text[len(file_hint):].lstrip()
    if "### 用户当前消息" in text:
        text = text.split("### 用户当前消息", 1)[-1].strip()
    return text


def _native_response_blocks(response_body):
    try:
        blocks = ast.literal_eval((response_body or "").strip())
    except Exception:
        return []
    return blocks if isinstance(blocks, list) else []


def _extract_turn_no(prompt_text, fallback_turn):
    match = _TURN_RE.search(prompt_text or "")
    if not match:
        return fallback_turn
    try:
        return int(match.group(1))
    except Exception:
        return fallback_turn


def _extract_tool_results(prompt_body):
    prompt = _native_prompt_obj(prompt_body)
    if prompt is None:
        return []
    results = []
    for block in prompt.get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            content = block.get("content", "")
            if isinstance(content, str) and content.strip():
                results.append(content.strip())
    return results


def _render_tool_use(block):
    name = str(block.get("name", "tool")).strip() or "tool"
    args = block.get("input", {})
    try:
        pretty = json.dumps(args, ensure_ascii=False, indent=2)
    except Exception:
        pretty = str(args)
    return f"🛠️ Tool: `{name}`  📥 args:\n````text\n{pretty}\n````\n"


def _render_native_turn(prompt_body, response_body, next_prompt_body, fallback_turn):
    prompt = _native_prompt_obj(prompt_body)
    if prompt is None:
        return ""
    prompt_text = _native_prompt_text(prompt)
    turn_no = _extract_turn_no(prompt_text, fallback_turn)
    pieces = [f"**LLM Running (Turn {turn_no}) ...**\n\n"]
    has_content = False

    for block in _native_response_blocks(response_body):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                pieces.append(text.strip() + "\n\n")
                has_content = True
        elif btype == "tool_use":
            pieces.append(_render_tool_use(block))
            has_content = True

    for tool_result in _extract_tool_results(next_prompt_body):
        pieces.append(f"`````\n{tool_result}\n`````\n")
        has_content = True

    return "".join(pieces).strip() if has_content else ""


def _build_history_from_bubbles(bubbles):
    history = []
    for bubble in bubbles:
        role = bubble.get("role")
        text = (bubble.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            history.append(f"[USER]: {text}")
            continue
        match = _SUMMARY_RE.findall(text)
        if match:
            summary = match[-1].strip()
        else:
            summary = text
            summary = re.sub(r"(?P<fence>`{3,})[\s\S]*?(?P=fence)", "", summary)
            summary = re.sub(r"<thinking>[\s\S]*?</thinking>", "", summary, flags=re.IGNORECASE)
            summary = re.sub(r"\n{2,}", "\n", summary).strip().splitlines()[0] if summary.strip() else "..."
        history.append(f"[Agent] {summary[:500]}")
    return history


def _restore_legacy_chat(content, cc):
    users = re.findall(r"=== USER ===\n(.+?)(?==== |$)", content or "", re.DOTALL)
    resps = re.findall(r"=== Response ===.*?\n(.+?)(?==== Prompt|$)", content or "", re.DOTALL)
    if users and resps:
        bubbles = []
        for user_text, resp_text in zip(users, resps):
            user_text = (user_text or "").strip()
            resp_text = (resp_text or "").strip()
            if user_text:
                bubbles.append({"role": "user", "text": user_text})
            if resp_text:
                bubbles.append({"role": "assistant", "text": resp_text})
        agent_history = cc._restore_text_pairs(content) or _build_history_from_bubbles(bubbles)
        return bubbles, agent_history

    pairs = []
    pending_prompt = None
    for label, body in _RESTORE_BLOCK_RE.findall(content or ""):
        if label == "Prompt":
            pending_prompt = body
        elif pending_prompt is not None:
            pairs.append((pending_prompt, body))
            pending_prompt = None
    if not pairs:
        return [], []

    first_prompt = _native_prompt_obj(pairs[0][0])
    first_prompt_text = _native_prompt_text(first_prompt) if first_prompt else ""
    bubbles = []

    for line in _native_history_lines(first_prompt_text):
        if line.startswith("[USER]: "):
            bubbles.append({"role": "user", "text": line[8:]})
        elif line.startswith("[Agent] "):
            bubbles.append({"role": "assistant", "text": line[8:]})

    current_user = _native_first_user_line(first_prompt_text, getattr(cc, "FILE_HINT", ""))
    if current_user:
        bubbles.append({"role": "user", "text": current_user})

    turn_texts = []
    for idx, (prompt_body, response_body) in enumerate(pairs):
        next_prompt_body = pairs[idx + 1][0] if idx + 1 < len(pairs) else None
        turn_text = _render_native_turn(prompt_body, response_body, next_prompt_body, idx + 1)
        if turn_text:
            turn_texts.append(turn_text)
    if turn_texts:
        bubbles.append({"role": "assistant", "text": "\n\n".join(turn_texts)})

    agent_history = cc._restore_native_history(content) or _build_history_from_bubbles(bubbles)
    return bubbles, agent_history


def _mykey_hint_path(agent_dir):
    py_path = os.path.join(agent_dir, "mykey.py")
    if os.path.isfile(py_path):
        return py_path
    return os.path.join(agent_dir, "mykey.json")

def main():
    if len(sys.argv) < 2:
        send({"event": "error", "msg": "缺少 agent_dir 参数"}); return
    agent_dir = sys.argv[1]
    if not os.path.isdir(agent_dir):
        send({"event": "error", "msg": f"目录不存在: {agent_dir}"}); return

    sys.path.insert(0, agent_dir)
    try: os.chdir(agent_dir)
    except Exception: pass

    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    try:
        send({"event": "log", "msg": "导入 agentmain…"})
        import agentmain
        send({"event": "log", "msg": "实例化 Agent…"})
        try:
            agent = agentmain.GeneraticAgent()
        except IndexError:
            cfg_path = _mykey_hint_path(agent_dir)
            send({
                "event": "error",
                "msg": ("未配置 LLM，或当前 mykey 配置无效。\n\n"
                        f"请先在 {cfg_path} 中填写至少一个可用渠道，"
                        "或回到启动器的「设置 → API」添加 API 卡片。")
            })
            return
        if not agent.llmclients:
            send({"event": "error", "msg": "未配置 LLM：请在 GenericAgent/mykey.py 中填入 API key。"})
            return
        agent.next_llm(0)
        agent.inc_out = False
        threading.Thread(target=agent.run, daemon=True).start()
        send({"event": "ready", "llms": _ui_llms(agent)})
    except Exception as e:
        send({"event": "error", "msg": str(e), "trace": traceback.format_exc()[-1500:]})
        return

    def relay(dq):
        try:
            while True:
                item = dq.get(timeout=3600)
                if "next" in item:
                    send({"event": "next", "text": item["next"]})
                if "done" in item:
                    send({"event": "done", "text": item["done"]})
                    break
        except Exception as e:
            send({"event": "done", "text": f"[错误] {e}"})

    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try:
            cmd = json.loads(line)
        except Exception as e:
            send({"event": "error", "msg": f"bad json: {e}"}); continue
        c = cmd.get("cmd")
        try:
            if c == "send":
                dq = agent.put_task(cmd.get("text", ""), source="user")
                threading.Thread(target=relay, args=(dq,), daemon=True).start()
            elif c == "abort":
                agent.abort()
                send({"event": "aborted"})
            elif c == "switch_llm":
                agent.next_llm(int(cmd.get("idx", 0)))
                send({"event": "llm_switched", "llms": _ui_llms(agent)})
            elif c == "new_session":
                try: agent.abort()
                except Exception: pass
                agent.history = []
                if agent.llmclient and hasattr(agent.llmclient, "backend"):
                    agent.llmclient.backend.history = []
                agent.handler = None
                send({"event": "new_session_ok"})
            elif c == "get_state":
                backend_hist = []
                try:
                    if agent.llmclient and hasattr(agent.llmclient, "backend"):
                        backend_hist = list(agent.llmclient.backend.history or [])
                except Exception:
                    pass
                send({"event": "state",
                      "backend_history": backend_hist,
                      "agent_history": list(agent.history or []),
                      "llm_idx": agent.llm_no,
                      "session_id": cmd.get("session_id"),
                      "request_id": cmd.get("request_id")})
            elif c == "set_state":
                try: agent.abort()
                except Exception: pass
                try:
                    agent.history = list(cmd.get("agent_history") or [])
                    if agent.llmclient and hasattr(agent.llmclient, "backend"):
                        agent.llmclient.backend.history = list(cmd.get("backend_history") or [])
                    agent.handler = None
                    send({"event": "state_loaded"})
                except Exception as e:
                    send({"event": "error", "msg": f"set_state: {e}"})
            elif c == "list_legacy":
                # 扫描 GenericAgent 原生历史 (temp/model_responses/*.txt)
                items = []
                try:
                    import glob
                    from frontends import chatapp_common as cc
                    for pattern in cc.RESTORE_GLOBS:
                        for fp in glob.glob(pattern):
                            try:
                                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                                    content = f.read()
                                bubbles, agent_history = _restore_legacy_chat(content, cc)
                                if not bubbles and not agent_history:
                                    continue
                                title = ""
                                for bubble in bubbles:
                                    if bubble.get("role") == "user":
                                        title = (bubble.get("text") or "").strip()
                                        if title:
                                            break
                                items.append({
                                    "file": fp,
                                    "title": (title or os.path.basename(fp))[:60],
                                    "mtime": os.path.getmtime(fp),
                                    "pairs": sum(1 for b in bubbles if b.get("role") == "user") or sum(1 for l in agent_history if l.startswith("[USER]: ")),
                                })
                            except Exception:
                                continue
                except Exception as e:
                    send({"event": "legacy_list", "items": [], "error": str(e)}); continue
                items.sort(key=lambda x: x["mtime"], reverse=True)
                send({"event": "legacy_list", "items": items})
            elif c == "restore_legacy":
                # 根据 file 解析内容，返回可展示的气泡 + agent_history，由启动器自己灌回状态
                fp = cmd.get("file", "")
                try:
                    from frontends import chatapp_common as cc
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    bubbles, agent_history = _restore_legacy_chat(content, cc)
                    send({"event": "legacy_restored",
                          "file": fp,
                          "bubbles": bubbles,
                          "agent_history": agent_history})
                except Exception as e:
                    send({"event": "legacy_restored",
                          "file": fp,
                          "bubbles": [],
                          "agent_history": [],
                          "error": str(e)})
            elif c == "quit":
                send({"event": "bye"}); return
            elif c == "reinject_tools":
                try:
                    try: agent.llmclient.last_tools = ''
                    except Exception: pass
                    hist_path = os.path.join(agent_dir, 'assets',
                                              'tool_usable_history.json')
                    with open(hist_path, 'r', encoding='utf-8') as f:
                        tool_hist = json.load(f)
                    agent.llmclient.backend.history.extend(tool_hist)
                    send({"event": "tools_reinjected",
                          "count": len(tool_hist)})
                except Exception as e:
                    send({"event": "error",
                          "msg": f"reinject_tools: {e}"})
            elif c == "launch_pet":
                try:
                    import subprocess as _sp, platform as _pf
                    kwargs = {'creationflags': 0x08} if _pf.system() == 'Windows' else {}
                    frontends = os.path.join(agent_dir, 'frontends')
                    pet = os.path.join(frontends, 'desktop_pet_v2.pyw')
                    if not os.path.isfile(pet):
                        pet = os.path.join(frontends, 'desktop_pet.pyw')
                    if not os.path.isfile(pet):
                        send({"event": "error",
                              "msg": "未找到 desktop_pet.pyw"}); continue
                    _sp.Popen([sys.executable, pet], **kwargs)
                    # 注册 pet 钩子（完全仿照 stapp.py）
                    try:
                        from urllib.request import urlopen
                        from urllib.parse import quote
                        def _pet_req(q):
                            def _do():
                                try: urlopen(f'http://127.0.0.1:41983/?{q}', timeout=2)
                                except Exception: pass
                            threading.Thread(target=_do, daemon=True).start()
                        agent._pet_req = _pet_req
                        if not hasattr(agent, '_turn_end_hooks'):
                            agent._turn_end_hooks = {}
                        def _pet_hook(ctx):
                            parts = [f"Turn {ctx.get('turn','?')}"]
                            if ctx.get('summary'): parts.append(ctx['summary'])
                            if ctx.get('exit_reason'): parts.append('任务已完成')
                            _pet_req(f'msg={quote(chr(10).join(parts))}')
                            if ctx.get('exit_reason'): _pet_req('state=idle')
                        agent._turn_end_hooks['pet'] = _pet_hook
                    except Exception: pass
                    send({"event": "pet_launched"})
                except Exception as e:
                    send({"event": "error",
                          "msg": f"launch_pet: {e}"})
        except Exception as e:
            send({"event": "error", "msg": f"cmd {c}: {e}",
                  "trace": traceback.format_exc()[-800:]})

if __name__ == "__main__":
    main()
