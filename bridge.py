"""GenericAgent 桥接进程 - 由启动器用系统 Python 启动
通过 stdin/stdout 收发 JSON 行与启动器通信。
"""
import ast
import json
import os
import re
import sys
import threading
import time
import traceback


def _strip_incompatible_pyinstaller_runtime_from_sys_path():
    """Drop the PyInstaller runtime dir from sys.path for external Python runs.

    When the frozen launcher starts a system Python interpreter to execute this
    bridge script, the script can live under PyInstaller's extraction dir
    (`_MEI...`). CPython prepends that script directory to `sys.path`, which can
    cause the system interpreter to import incompatible `.pyd` files from the
    frozen runtime. This bridge only needs imports from stdlib and `agent_dir`,
    so it is safe to remove the runtime dir before later imports happen.
    """

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        markers = (
            os.path.isfile(os.path.join(script_dir, "python3.dll"))
            and os.path.isfile(os.path.join(script_dir, "base_library.zip"))
        )
        if not markers:
            for dll_name in ("python312.dll", "python311.dll", "python310.dll", "python39.dll", "python38.dll"):
                if os.path.isfile(os.path.join(script_dir, dll_name)):
                    markers = True
                    break
        if not markers:
            return

        def _norm(path):
            try:
                return os.path.normcase(os.path.abspath(str(path)))
            except Exception:
                return ""

        bad = _norm(script_dir)
        if not bad:
            return
        sys.path[:] = [p for p in list(sys.path) if _norm(p) != bad]
    except Exception:
        # Startup must not fail because of this sanitization.
        return


_strip_incompatible_pyinstaller_runtime_from_sys_path()

_RESTORE_BLOCK_RE = re.compile(
    r"^=== (Prompt|Response) ===.*?\n(.*?)(?=^=== (?:Prompt|Response) ===|\Z)",
    re.DOTALL | re.MULTILINE,
)
_HISTORY_RE = re.compile(r"<history>\s*(.*?)\s*</history>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL)
_TURN_RE = re.compile(r"Current turn:\s*(\d+)")
_USAGE_LOCAL = threading.local()


def _llm_backend(llmclient):
    if llmclient is None:
        return None
    backend = getattr(llmclient, "backend", None)
    if backend is not None:
        return backend
    if isinstance(llmclient, dict):
        backend = llmclient.get("backend")
        return backend if backend is not None else None
    return None


def _copy_backend_history(llmclient):
    backend = _llm_backend(llmclient)
    if backend is None:
        return []
    try:
        return list(getattr(backend, "history", None) or [])
    except Exception:
        return []


def _assign_backend_history(llmclient, history):
    backend = _llm_backend(llmclient)
    if backend is None:
        return False
    try:
        backend.history = list(history or [])
        return True
    except Exception:
        return False


def _reset_backend_usage(llmclient):
    backend = _llm_backend(llmclient)
    if backend is None:
        return None
    try:
        backend._ga_launcher_task_usage = None
        backend._ga_launcher_current_usage = None
    except Exception:
        pass
    return backend


def _reset_llm_last_tools(llmclient):
    if llmclient is None:
        return
    try:
        setattr(llmclient, "last_tools", "")
    except Exception:
        pass


def _sanitize_agent_llmclients(agent):
    usable = [item for item in list(getattr(agent, "llmclients", []) or []) if _llm_backend(item) is not None]
    bad_count = max(len(list(getattr(agent, "llmclients", []) or [])) - len(usable), 0)
    if not usable:
        if bad_count:
            return False, (
                "检测到 LLM 配置条目，但没有成功初始化出可用 backend。"
                "这通常表示 mykey 里存在失效的 mixin/渠道配置，或底层依赖未准备好。"
            )
        return False, "未配置 LLM：请在 GenericAgent/mykey.py 中填入 API key。"
    agent.llmclients = usable
    agent.llm_no = 0
    agent.llmclient = usable[0]
    _reset_llm_last_tools(agent.llmclient)
    return True, (f"已忽略 {bad_count} 个无效 LLM 配置条目。" if bad_count else "")

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


def _int_token(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def _normalize_provider_usage(raw):
    if not isinstance(raw, dict):
        return {}
    cached = _int_token((raw.get("input_tokens_details") or {}).get("cached_tokens", 0))
    if not cached:
        cached = _int_token((raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    input_tokens = _int_token(raw.get("input_tokens", raw.get("prompt_tokens", 0)))
    output_tokens = _int_token(raw.get("output_tokens", raw.get("completion_tokens", 0)))
    total_tokens = _int_token(raw.get("total_tokens", raw.get("total", input_tokens + output_tokens)))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or (input_tokens + output_tokens),
        "cached_tokens": cached,
        "cache_creation_input_tokens": _int_token(raw.get("cache_creation_input_tokens", 0)),
        "cache_read_input_tokens": _int_token(raw.get("cache_read_input_tokens", 0)),
        "usage_source": "provider",
    }


def _merge_call_usage(base, update):
    base = dict(base or {})
    update = dict(update or {})
    if not update:
        return base
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        val = update.get(key)
        if val is None:
            continue
        val = _int_token(val)
        if val or key not in base:
            base[key] = val
    if "usage_source" in update:
        base["usage_source"] = update["usage_source"]
    inp = _int_token(base.get("input_tokens", 0))
    out = _int_token(base.get("output_tokens", 0))
    if not _int_token(base.get("total_tokens", 0)):
        base["total_tokens"] = inp + out
    return base


def _accumulate_task_usage(base, call_usage):
    if not call_usage:
        return dict(base or {})
    result = dict(base or {})
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        result[key] = _int_token(result.get(key, 0)) + _int_token(call_usage.get(key, 0))
    result["usage_source"] = "provider"
    result["api_calls"] = _int_token(result.get("api_calls", 0)) + 1
    return result


def _store_current_usage(update):
    backend = getattr(_USAGE_LOCAL, "backend", None)
    if backend is None:
        return
    current = getattr(backend, "_ga_launcher_current_usage", None)
    backend._ga_launcher_current_usage = _merge_call_usage(current, update)


def _patch_llm_usage_capture():
    import llmcore

    if getattr(llmcore, "_ga_launcher_usage_patched", False):
        return llmcore

    def _parse_claude_sse_patched(resp_lines):
        content_blocks = []
        current_block = None
        tool_json_buf = ""
        stop_reason = None
        got_message_stop = False
        warn = None
        for line in resp_lines:
            if not line:
                continue
            line = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line.startswith("data:"):
                continue
            data_str = line[5:].lstrip()
            if data_str == "[DONE]":
                break
            try:
                evt = json.loads(data_str)
            except Exception as e:
                print(f"[SSE] JSON parse error: {e}, line: {data_str[:200]}")
                continue
            evt_type = evt.get("type", "")
            if evt_type == "message_start":
                usage = evt.get("message", {}).get("usage", {})
                _store_current_usage(_normalize_provider_usage(usage))
                ci = usage.get("cache_creation_input_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                inp = usage.get("input_tokens", 0)
                print(f"[Cache] input={inp} creation={ci} read={cr}")
            elif evt_type == "content_block_start":
                block = evt.get("content_block", {})
                if block.get("type") == "text":
                    current_block = {"type": "text", "text": ""}
                elif block.get("type") == "thinking":
                    current_block = {"type": "thinking", "thinking": ""}
                elif block.get("type") == "tool_use":
                    current_block = {"type": "tool_use", "id": block.get("id", ""), "name": block.get("name", ""), "input": {}}
                    tool_json_buf = ""
            elif evt_type == "content_block_delta":
                delta = evt.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if current_block and current_block.get("type") == "text":
                        current_block["text"] += text
                    if text:
                        yield text
                elif delta.get("type") == "thinking_delta":
                    if current_block and current_block.get("type") == "thinking":
                        current_block["thinking"] += delta.get("thinking", "")
                elif delta.get("type") == "input_json_delta":
                    tool_json_buf += delta.get("partial_json", "")
            elif evt_type == "content_block_stop":
                if current_block:
                    if current_block["type"] == "tool_use":
                        try:
                            current_block["input"] = json.loads(tool_json_buf) if tool_json_buf else {}
                        except Exception:
                            current_block["input"] = {"_raw": tool_json_buf}
                    content_blocks.append(current_block)
                    current_block = None
            elif evt_type == "message_delta":
                delta = evt.get("delta", {})
                stop_reason = delta.get("stop_reason", stop_reason)
                out_usage = evt.get("usage", {})
                _store_current_usage(_normalize_provider_usage(out_usage))
                out_tokens = out_usage.get("output_tokens", 0)
                if out_tokens:
                    print(f"[Output] tokens={out_tokens} stop_reason={stop_reason}")
            elif evt_type == "message_stop":
                got_message_stop = True
            elif evt_type == "error":
                err = evt.get("error", {})
                emsg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                warn = f"\n\n[SSE Error: {emsg}]"
                break
        if not warn:
            if not got_message_stop and not stop_reason:
                warn = "\n\n[!!! 流异常中断，未收到完整响应 !!!]"
            elif stop_reason == "max_tokens":
                warn = "\n\n[!!! Response truncated: max_tokens !!!]"
        if warn:
            print(f"[WARN] {warn.strip()}")
            content_blocks.append({"type": "text", "text": warn})
            yield warn
        return content_blocks

    def _parse_openai_sse_patched(resp_lines, api_mode="chat_completions"):
        content_text = ""
        if api_mode == "responses":
            seen_delta = False
            fc_buf = {}
            current_fc_idx = None
            for line in resp_lines:
                if not line:
                    continue
                line = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].lstrip()
                if data_str == "[DONE]":
                    break
                try:
                    evt = json.loads(data_str)
                except Exception:
                    continue
                etype = evt.get("type", "")
                if etype == "response.output_text.delta":
                    seen_delta = True
                    delta = evt.get("delta", "")
                    if delta:
                        content_text += delta
                        yield delta
                elif etype == "response.output_item.added":
                    item = evt.get("item", {})
                    if item.get("type") == "function_call":
                        idx = evt.get("output_index", 0)
                        fc_buf[idx] = {"id": item.get("call_id", item.get("id", "")), "name": item.get("name", ""), "args": ""}
                        current_fc_idx = idx
                elif etype == "response.function_call_arguments.delta":
                    idx = evt.get("output_index", current_fc_idx or 0)
                    if idx in fc_buf:
                        fc_buf[idx]["args"] += evt.get("delta", "")
                elif etype == "response.function_call_arguments.done":
                    idx = evt.get("output_index", current_fc_idx or 0)
                    if idx in fc_buf:
                        fc_buf[idx]["args"] = evt.get("arguments", fc_buf[idx]["args"])
                elif etype == "error":
                    err = evt.get("error", {})
                    emsg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    if emsg:
                        content_text += f"Error: {emsg}"
                        yield f"Error: {emsg}"
                    break
                elif etype == "response.completed":
                    usage = evt.get("response", {}).get("usage", {})
                    _store_current_usage(_normalize_provider_usage(usage))
                    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
                    inp = usage.get("input_tokens", 0)
                    if inp:
                        print(f"[Cache] input={inp} cached={cached}")
                    break
            blocks = []
            if content_text:
                blocks.append({"type": "text", "text": content_text})
            for idx in sorted(fc_buf):
                fc = fc_buf[idx]
                try:
                    inp = json.loads(fc["args"]) if fc["args"] else {}
                except Exception:
                    inp = {"_raw": fc["args"]}
                blocks.append({"type": "tool_use", "id": fc["id"], "name": fc["name"], "input": inp})
            return blocks

        tc_buf = {}
        for line in resp_lines:
            if not line:
                continue
            line = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
            if not line.startswith("data:"):
                continue
            data_str = line[5:].lstrip()
            if data_str == "[DONE]":
                break
            try:
                evt = json.loads(data_str)
            except Exception:
                continue
            ch = (evt.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            if delta.get("content"):
                text = delta["content"]
                content_text += text
                yield text
            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                if idx not in tc_buf:
                    tc_buf[idx] = {"id": tc.get("id", ""), "name": "", "args": ""}
                if tc.get("function", {}).get("name"):
                    tc_buf[idx]["name"] = tc["function"]["name"]
                if tc.get("function", {}).get("arguments"):
                    tc_buf[idx]["args"] += tc["function"]["arguments"]
            usage = evt.get("usage")
            if usage:
                _store_current_usage(_normalize_provider_usage(usage))
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                print(f"[Cache] input={usage.get('prompt_tokens',0)} cached={cached}")
        blocks = []
        if content_text:
            blocks.append({"type": "text", "text": content_text})
        for idx in sorted(tc_buf):
            tc = tc_buf[idx]
            try:
                inp = json.loads(tc["args"]) if tc["args"] else {}
            except Exception:
                inp = {"_raw": tc["args"]}
            blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": inp})
        return blocks

    def _wrap_raw_ask(cls):
        original = cls.raw_ask

        def wrapped(self, messages):
            prev = getattr(_USAGE_LOCAL, "backend", None)
            _USAGE_LOCAL.backend = self
            self._ga_launcher_current_usage = None
            gen = original(self, messages)
            try:
                while True:
                    yield next(gen)
            except StopIteration as e:
                call_usage = getattr(self, "_ga_launcher_current_usage", None)
                if call_usage:
                    self._ga_launcher_task_usage = _accumulate_task_usage(
                        getattr(self, "_ga_launcher_task_usage", None),
                        call_usage,
                    )
                return e.value or []
            finally:
                _USAGE_LOCAL.backend = prev

        cls.raw_ask = wrapped

    llmcore._parse_claude_sse = _parse_claude_sse_patched
    llmcore._parse_openai_sse = _parse_openai_sse_patched
    for cls_name in ("ClaudeSession", "LLMSession", "NativeClaudeSession", "NativeOAISession"):
        cls = getattr(llmcore, cls_name, None)
        if cls is not None:
            _wrap_raw_ask(cls)
    llmcore._ga_launcher_usage_patched = True
    return llmcore


def _patch_code_run_stdin():
    """bridge 主线程阻塞读 stdin pipe，ga.code_run 的 Popen 默认继承 stdin 会让孙子
    python 进程在 Windows 上启动卡 60 秒才被强杀。显式用 DEVNULL 作 stdin 规避。"""
    import ga
    orig = ga.subprocess.Popen
    if getattr(orig, '_ga_launcher_stdin_patched', False):
        return
    def popen(*args, **kwargs):
        if 'stdin' not in kwargs:
            kwargs['stdin'] = ga.subprocess.DEVNULL
        return orig(*args, **kwargs)
    popen._ga_launcher_stdin_patched = True
    ga.subprocess.Popen = popen


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


def _response_text_only(response_body):
    try:
        blocks = ast.literal_eval((response_body or "").strip())
    except Exception:
        return ""
    if not isinstance(blocks, list):
        return ""
    texts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return "\n\n".join(texts).strip()


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

    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    os.environ.pop('PYTHONLEGACYWINDOWSSTDIO', None)

    try:
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    try:
        send({"event": "log", "msg": "导入 agentmain…"})
        _patch_llm_usage_capture()
        import agentmain
        _patch_code_run_stdin()
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
        ok, setup_msg = _sanitize_agent_llmclients(agent)
        if not ok:
            send({"event": "error", "msg": setup_msg})
            return
        agent.inc_out = False
        if setup_msg:
            send({"event": "log", "msg": setup_msg})
        threading.Thread(target=agent.run, daemon=True).start()
        send({"event": "ready", "llms": _ui_llms(agent)})
    except Exception as e:
        send({"event": "error", "msg": str(e), "trace": traceback.format_exc()[-1500:]})
        return

    def relay(dq, backend=None, session_id=None):
        try:
            while True:
                item = dq.get(timeout=3600)
                if "next" in item:
                    send({"event": "next", "text": item["next"]})
                if "done" in item:
                    usage = None
                    try:
                        raw_usage = getattr(backend, "_ga_launcher_task_usage", None)
                        if isinstance(raw_usage, dict) and raw_usage:
                            usage = dict(raw_usage)
                    except Exception:
                        usage = None
                    payload = {"event": "done", "text": item["done"]}
                    if usage:
                        payload["usage"] = usage
                    send(payload)
                    try:
                        deadline = time.time() + 1.5
                        while getattr(agent, "is_running", False) and time.time() < deadline:
                            time.sleep(0.05)
                    except Exception:
                        pass
                    backend_hist = _copy_backend_history(getattr(agent, "llmclient", None))
                    send(
                        {
                            "event": "turn_snapshot",
                            "session_id": session_id,
                            "backend_history": backend_hist,
                            "agent_history": list(agent.history or []),
                            "llm_idx": int(getattr(agent, "llm_no", 0) or 0),
                            "process_pid": os.getpid(),
                            "snapshot_ts": time.time(),
                        }
                    )
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
                backend = _reset_backend_usage(getattr(agent, "llmclient", None))
                dq = agent.put_task(cmd.get("text", ""), source="user")
                threading.Thread(target=relay, args=(dq, backend, cmd.get("session_id")), daemon=True).start()
            elif c == "abort":
                agent.abort()
                send({"event": "aborted"})
            elif c == "switch_llm":
                agent.next_llm(int(cmd.get("idx", 0)))
                if _llm_backend(getattr(agent, "llmclient", None)) is None:
                    ok, setup_msg = _sanitize_agent_llmclients(agent)
                    if not ok:
                        send({"event": "error", "msg": setup_msg})
                        continue
                send({"event": "llm_switched", "llms": _ui_llms(agent)})
            elif c == "new_session":
                try: agent.abort()
                except Exception: pass
                agent.history = []
                _assign_backend_history(getattr(agent, "llmclient", None), [])
                agent.handler = None
                send({"event": "new_session_ok"})
            elif c == "get_state":
                backend_hist = _copy_backend_history(getattr(agent, "llmclient", None))
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
                    requested_llm_idx = cmd.get("llm_idx")
                    if requested_llm_idx is not None:
                        try:
                            agent.next_llm(int(requested_llm_idx))
                        except Exception:
                            pass
                    if _llm_backend(getattr(agent, "llmclient", None)) is None:
                        ok, setup_msg = _sanitize_agent_llmclients(agent)
                        if not ok:
                            raise RuntimeError(setup_msg)
                    agent.history = list(cmd.get("agent_history") or [])
                    _assign_backend_history(getattr(agent, "llmclient", None), cmd.get("backend_history") or [])
                    agent.handler = None
                    send({"event": "state_loaded", "llms": _ui_llms(agent), "llm_idx": int(getattr(agent, "llm_no", 0) or 0)})
                except Exception as e:
                    send({"event": "error", "msg": f"set_state: {e}"})
            elif c == "quit":
                send({"event": "bye"}); return
            elif c == "reinject_tools":
                try:
                    _reset_llm_last_tools(getattr(agent, "llmclient", None))
                    hist_path = os.path.join(agent_dir, 'assets',
                                              'tool_usable_history.json')
                    with open(hist_path, 'r', encoding='utf-8') as f:
                        tool_hist = json.load(f)
                    backend = _llm_backend(getattr(agent, "llmclient", None))
                    if backend is None:
                        raise RuntimeError("当前 LLM backend 不可用，无法注入工具示范。")
                    backend.history.extend(tool_hist)
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
