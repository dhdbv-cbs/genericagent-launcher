"""GenericAgent 桥接进程 - 由启动器用系统 Python 启动
通过 stdin/stdout 收发 JSON 行与启动器通信。
"""
import ast
import base64
import inspect
import importlib.util
import json
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import traceback
import types


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


_REASONING_EFFORT_VALUES = {"none", "minimal", "low", "medium", "high", "xhigh"}


def _normalize_reasoning_effort(value):
    text = str(value or "").strip().lower()
    if not text:
        return None
    return text if text in _REASONING_EFFORT_VALUES else None


def _current_reasoning_effort(agent):
    backend = _llm_backend(getattr(agent, "llmclient", None))
    if backend is None:
        return None
    return _normalize_reasoning_effort(getattr(backend, "reasoning_effort", None))


def _reasoning_effort_defaults(agent):
    out = {}
    for idx, llmclient in enumerate(list(getattr(agent, "llmclients", []) or [])):
        backend = _llm_backend(llmclient)
        out[idx] = _normalize_reasoning_effort(getattr(backend, "reasoning_effort", None) if backend is not None else None)
    return out


def _apply_reasoning_effort(agent, value, *, defaults=None):
    backend = _llm_backend(getattr(agent, "llmclient", None))
    if backend is None:
        return None
    normalized = _normalize_reasoning_effort(value)
    if normalized is None and isinstance(defaults, dict):
        try:
            normalized = _normalize_reasoning_effort(defaults.get(int(getattr(agent, "llm_no", 0) or 0)))
        except Exception:
            normalized = None
    setattr(backend, "reasoning_effort", normalized)
    return _current_reasoning_effort(agent)


def _copy_backend_history(llmclient):
    backend = _llm_backend(llmclient)
    if backend is None:
        return []
    try:
        return list(getattr(backend, "history", None) or [])
    except Exception:
        return []


def _history_char_count(history):
    total = 0
    for item in list(history or []):
        try:
            total += len(json.dumps(item, ensure_ascii=False))
        except Exception:
            continue
    return total


def _backend_context_metrics(llmclient, history=None):
    backend = _llm_backend(llmclient)
    if backend is None:
        return {"context_window_chars": 0, "current_input_chars": 0}
    try:
        context_window_chars = max(0, int(getattr(backend, "context_win", 0) or 0)) * 3
    except Exception:
        context_window_chars = 0
    history_rows = history if isinstance(history, list) else _copy_backend_history(llmclient)
    return {
        "context_window_chars": context_window_chars,
        "current_input_chars": max(0, int(_history_char_count(history_rows) or 0)),
    }


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


def _ui_llm_display_name(raw_name):
    text = str(raw_name or "").strip()
    if not text:
        return ""
    if "/" not in text:
        return text
    return text.split("/", 1)[1].strip() or text


def _ui_llms(agent):
    items = []
    for i, raw_name, current in agent.list_llms():
        items.append({"idx": i, "name": _ui_llm_display_name(raw_name), "current": current})
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

    def _record_usage_patched(usage, api_mode):
        _store_current_usage(_normalize_provider_usage(usage))
        original = getattr(_record_usage_patched, "_ga_launcher_original", None)
        if callable(original):
            return original(usage, api_mode)
        if not usage:
            return
        if api_mode == "responses":
            cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            inp = usage.get("input_tokens", 0)
            print(f"[Cache] input={inp} cached={cached}")
        elif api_mode == "chat_completions":
            cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            inp = usage.get("prompt_tokens", 0)
            print(f"[Cache] input={inp} cached={cached}")
        elif api_mode == "messages":
            ci = usage.get("cache_creation_input_tokens", 0)
            cr = usage.get("cache_read_input_tokens", 0)
            inp = usage.get("input_tokens", 0)
            print(f"[Cache] input={inp} creation={ci} read={cr}")

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
                    current_block = {"type": "thinking", "thinking": "", "signature": ""}
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
                elif delta.get("type") == "signature_delta":
                    if current_block and current_block.get("type") == "thinking":
                        current_block["signature"] = current_block.get("signature", "") + delta.get("signature", "")
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

    _record_usage_patched._ga_launcher_original = getattr(llmcore, "_record_usage", None)
    llmcore._record_usage = _record_usage_patched
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


def _collect_existing_image_paths(raw_images):
    images = []
    if not isinstance(raw_images, list):
        return images
    for item in raw_images:
        path = str(item or "").strip()
        if path and os.path.isfile(path):
            images.append(path)
    return images


def _image_mime_type(path):
    mime, _encoding = mimetypes.guess_type(str(path or ""))
    if isinstance(mime, str) and mime.startswith("image/"):
        return mime
    ext = os.path.splitext(str(path or ""))[1].lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext, "image/png")


def _image_data_url(path):
    with open(path, "rb") as f:
        raw = f.read()
    return f"data:{_image_mime_type(path)};base64,{base64.b64encode(raw).decode('ascii')}"


def _build_prompt_with_images(prompt_text, image_paths):
    prompt = str(prompt_text or "")
    images = _collect_existing_image_paths(image_paths)
    if not images:
        return prompt

    count = len(images)
    intro = f"用户发送了 {count} 张图片，请结合这些图片回答。"
    attachment_chunks = ["[用户上传图片附件]"]

    for idx, path in enumerate(images, start=1):
        name = os.path.basename(path) or f"image_{idx}"
        try:
            with open(path, "rb") as f:
                raw = f.read()
            encoded = base64.b64encode(raw).decode("ascii")
            attachment_chunks.append(
                f"- [图片附件 {idx}] {name} ({len(raw)} bytes)\n"
                f"  磁盘路径: {path}\n"
                f"  data:{_image_mime_type(path)};base64,{encoded}"
            )
        except Exception as exc:
            attachment_chunks.append(
                f"- [图片附件 {idx}] {name}\n"
                f"  磁盘路径: {path}\n"
                f"  [读取失败] {exc}"
            )

    attachment_text = "\n".join(attachment_chunks)
    if prompt.strip():
        return f"{prompt.rstrip()}\n\n{intro}\n{attachment_text}"
    return f"{intro}\n{attachment_text}"


def _build_turn_history_text(prompt_text, image_paths):
    prompt = str(prompt_text or "").strip()
    count = len(_collect_existing_image_paths(image_paths))
    if prompt:
        return prompt
    if count > 0:
        return f"[用户发送了 {count} 张图片]"
    return ""


def _clone_content_blocks(content):
    if isinstance(content, list):
        return [dict(item) if isinstance(item, dict) else item for item in content]
    return content


def _build_scrubbed_user_content(prompt_text, image_paths):
    clean_text = _build_turn_history_text(prompt_text, image_paths)
    if not clean_text:
        return []
    return [{"type": "text", "text": clean_text}]


def _build_multimodal_user_content(prompt_text, image_paths):
    prompt = str(prompt_text or "").strip()
    images = _collect_existing_image_paths(image_paths)
    if not images:
        return None
    parts = []
    if prompt:
        parts.append({"type": "text", "text": prompt})
    else:
        parts.append({"type": "text", "text": "请结合图片内容回答。"})
    for path in images:
        try:
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
        except Exception:
            continue
    return parts or None


def _supports_native_multimodal(llmclient):
    return type(llmclient).__name__ == "NativeToolClient"


def _content_has_image_payload(content):
    if isinstance(content, str):
        return "data:image/" in content or "[用户上传图片附件]" in content
    if not isinstance(content, list):
        return False
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).strip().lower()
        if item_type in {"image", "image_url", "input_image"}:
            return True
        if item_type == "text" and "data:image/" in str(item.get("text") or ""):
            return True
    return False


def _scrub_last_user_history(llmclient, replacement_content, *, start_len=0):
    backend = _llm_backend(llmclient)
    history = getattr(backend, "history", None) if backend is not None else None
    if not isinstance(history, list):
        return False
    replacement = _clone_content_blocks(replacement_content)
    if not replacement:
        return False
    begin = max(0, int(start_len or 0))
    for idx in range(begin, len(history)):
        item = history[idx]
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip().lower() != "user":
            continue
        if not _content_has_image_payload(item.get("content")):
            continue
        history[idx] = {**item, "content": replacement}
        return True
    return False


def _patch_agent_launcher_multimodal(agent, agentmain_mod):
    if getattr(agent, "_ga_launcher_multimodal_patched", False):
        return agent
    task_queue = getattr(agent, "task_queue", None)
    if any(not callable(getattr(task_queue, name, None)) for name in ("put", "get", "task_done")):
        return agent

    def put_task_patched(
        self,
        query,
        source="user",
        images=None,
        *,
        initial_user_content=None,
        history_text=None,
        scrub_user_content=None,
    ):
        display_queue = queue.Queue()
        self.task_queue.put(
            {
                "query": query,
                "source": source,
                "images": images or [],
                "output": display_queue,
                "initial_user_content": _clone_content_blocks(initial_user_content),
                "history_text": history_text,
                "scrub_user_content": _clone_content_blocks(scrub_user_content),
            }
        )
        return display_queue

    def run_patched(self):
        while True:
            task = self.task_queue.get()
            raw_query = task["query"]
            source = task["source"]
            images = task.get("images") or []
            display_queue = task["output"]
            initial_user_content = _clone_content_blocks(task.get("initial_user_content"))
            history_text = str(task.get("history_text") or raw_query or "")
            scrub_user_content = _clone_content_blocks(task.get("scrub_user_content"))
            raw_query = self._handle_slash_cmd(raw_query, display_queue)
            if raw_query is None:
                self.task_queue.task_done()
                continue
            self.is_running = True
            rquery = agentmain_mod.smart_format(history_text.replace("\n", " "), max_str_len=200)
            self.history.append(f"[USER]: {rquery}")

            sys_prompt = agentmain_mod.get_system_prompt() + getattr(self.llmclient.backend, "extra_sys_prompt", "")
            script_dir = os.path.dirname(os.path.abspath(agentmain_mod.__file__))
            handler = agentmain_mod.GenericAgentHandler(self, self.history, os.path.join(script_dir, "temp"))
            if self.handler and "key_info" in self.handler.working:
                ki = re.sub(r"\n\[SYSTEM\] 此为.*?工作记忆[。\n]*", "", self.handler.working["key_info"])
                handler.working["key_info"] = ki
                handler.working["passed_sessions"] = ps = self.handler.working.get("passed_sessions", 0) + 1
                if ps > 0:
                    handler.working["key_info"] += f"\n[SYSTEM] 此为 {ps} 个对话前设置的key_info，若已在新任务，先更新或清除工作记忆。\n"
            self.handler = handler
            user_input = raw_query
            rich_content = initial_user_content
            if source == "feishu" and len(self.history) > 1:
                user_input = handler._get_anchor_prompt() + f"\n\n### 用户当前消息\n{raw_query}"
                rich_content = None
            history_start_len = len(_copy_backend_history(getattr(self, "llmclient", None)))
            try:
                self.llmclient.log_path = self.log_path
            except Exception:
                pass
            loop_kwargs = {
                "max_turns": 70,
                "verbose": self.verbose,
                "initial_user_content": rich_content,
            }
            try:
                loop_params = inspect.signature(agentmain_mod.agent_runner_loop).parameters
            except Exception:
                loop_params = {}
            if "yield_info" in loop_params:
                loop_kwargs["max_turns"] = 80
                loop_kwargs["yield_info"] = True
            gen = agentmain_mod.agent_runner_loop(
                self.llmclient,
                sys_prompt,
                user_input,
                handler,
                agentmain_mod.TOOLS_SCHEMA,
                **loop_kwargs,
            )
            try:
                full_resp = ""
                last_pos = 0
                curr_turn = 0
                turn_resps = []
                for chunk in gen:
                    if agentmain_mod.consume_file(self.task_dir, "_stop"):
                        self.abort()
                    if self.stop_sig:
                        break
                    if isinstance(chunk, dict) and ("turn" in chunk):
                        try:
                            curr_turn = int(chunk.get("turn", 0) or 0)
                        except Exception:
                            curr_turn = int(curr_turn or 0)
                        turn_resps.append("")
                        continue
                    if not turn_resps:
                        turn_resps.append("")
                        if curr_turn <= 0:
                            curr_turn = len(turn_resps)
                    full_resp += chunk
                    turn_resps[-1] += chunk
                    if len(full_resp) - last_pos > 30 or "LLM Running" in chunk:
                        display_queue.put(
                            {
                                "next": full_resp[last_pos:] if self.inc_out else full_resp,
                                "source": source,
                                "turn": curr_turn,
                                "outputs": turn_resps[-2:],
                            }
                        )
                        last_pos = len(full_resp)
                if self.inc_out and last_pos < len(full_resp):
                    display_queue.put(
                        {
                            "next": full_resp[last_pos:],
                            "source": source,
                            "turn": curr_turn,
                            "outputs": turn_resps[-2:],
                        }
                    )
                if "</summary>" in full_resp:
                    full_resp = full_resp.replace("</summary>", "</summary>\n\n")
                if "</file_content>" in full_resp:
                    full_resp = re.sub(r"<file_content>\s*(.*?)\s*</file_content>", r"\n````\n<file_content>\n\1\n</file_content>\n````", full_resp, flags=re.DOTALL)
                _scrub_last_user_history(
                    getattr(self, "llmclient", None),
                    scrub_user_content,
                    start_len=history_start_len,
                )
                display_queue.put(
                    {
                        "done": full_resp,
                        "source": source,
                        "turn": curr_turn,
                        "outputs": turn_resps.copy(),
                    }
                )
                self.history = handler.history_info
            except Exception as e:
                _scrub_last_user_history(
                    getattr(self, "llmclient", None),
                    scrub_user_content,
                    start_len=history_start_len,
                )
                print(f"Backend Error: {agentmain_mod.format_error(e)}")
                display_queue.put(
                    {
                        "done": full_resp + f"\n```\n{agentmain_mod.format_error(e)}\n```",
                        "source": source,
                        "turn": curr_turn,
                        "outputs": turn_resps.copy(),
                    }
                )
            finally:
                if self.stop_sig:
                    print("User aborted the task.")
                _scrub_last_user_history(
                    getattr(self, "llmclient", None),
                    scrub_user_content,
                    start_len=history_start_len,
                )
                self.is_running = self.stop_sig = False
                self.task_queue.task_done()

    agent.put_task = types.MethodType(put_task_patched, agent)
    agent.run = types.MethodType(run_patched, agent)
    agent._ga_launcher_multimodal_patched = True
    return agent


def _load_python_module_from_path(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _agent_log_path(agent):
    for raw in (
        getattr(agent, "log_path", None),
        getattr(getattr(agent, "llmclient", None), "log_path", None),
    ):
        path = str(raw or "").strip()
        if path:
            return path
    return ""


def _format_continue_list_with_names(continue_mod, session_names_mod, *, exclude_pid=None, limit=20):
    sessions = list(getattr(continue_mod, "list_sessions")(exclude_pid=exclude_pid))
    if not sessions:
        return "❌ 没有可恢复的历史会话"
    rel_time = getattr(continue_mod, "_rel_time", None)
    escape_md = getattr(continue_mod, "_escape_md", None)
    if not callable(rel_time):
        rel_time = lambda _mtime: ""
    if not callable(escape_md):
        escape_md = lambda text: str(text or "")
    lines = ["**可恢复会话**（输入 `/continue N` 或 `/continue 名字` 恢复）：", ""]
    for idx, (path, mtime, first, rounds) in enumerate(sessions[: max(1, int(limit or 20))], 1):
        name = ""
        if session_names_mod is not None:
            try:
                name = str(getattr(session_names_mod, "name_for")(path) or "").strip()
            except Exception:
                name = ""
        preview = escape_md((str(first or "（无法预览）").replace("\n", " "))[:60])
        row = f"{idx}. `{rel_time(mtime)}` · **{int(rounds or 0)} 轮**"
        if name:
            row += f" · **{escape_md(name)}**"
        row += f" · {preview}"
        lines.append(row)
    return "\n".join(lines)


def _dispatch_continue_with_names(agent, query, display_queue, continue_mod, session_names_mod):
    if continue_mod is None:
        return query
    s = str(query or "").strip()
    if not s.startswith("/continue"):
        return query
    exclude_pid = os.getpid()
    if s == "/continue":
        display_queue.put(
            {
                "done": _format_continue_list_with_names(
                    continue_mod,
                    session_names_mod,
                    exclude_pid=exclude_pid,
                ),
                "source": "system",
            }
        )
        return None
    match = re.match(r"/continue\s+(\d+)\s*$", s)
    target_path = ""
    current_log = _agent_log_path(agent)
    if match:
        sessions = list(getattr(continue_mod, "list_sessions")(exclude_pid=exclude_pid))
        idx = int(match.group(1)) - 1
        if not (0 <= idx < len(sessions)):
            display_queue.put({"done": f"❌ 索引越界（有效范围 1-{len(sessions)}）", "source": "system"})
            return None
        target_path = str((sessions[idx] or [None])[0] or "").strip()
    else:
        token = s[len("/continue") :].strip()
        if not token:
            display_queue.put({"done": "用法: /continue、/continue N 或 /continue 名字", "source": "system"})
            return None
        if session_names_mod is None:
            return query
        own_key = os.path.basename(current_log) if current_log else None
        try:
            target_path = str(getattr(session_names_mod, "path_for")(token, exclude_basename=own_key) or "").strip()
        except Exception:
            target_path = ""
        current_name = ""
        if current_log:
            try:
                current_name = str(getattr(session_names_mod, "name_for")(current_log) or "").strip()
            except Exception:
                current_name = ""
        if (not target_path) and current_name and current_name.lower() == token.lower():
            display_queue.put({"done": f"✅ 当前已在 {token!r} 会话中", "source": "system"})
            return None
        if not target_path:
            display_queue.put({"done": f"❌ 未找到名为 {token!r} 的可恢复会话", "source": "system"})
            return None
    getattr(continue_mod, "reset_conversation")(agent, message=None)
    msg, _is_full = getattr(continue_mod, "restore")(agent, target_path)
    if session_names_mod is not None and current_log and target_path and (not str(msg or "").startswith("❌")):
        try:
            getattr(session_names_mod, "migrate")(target_path, current_log)
        except Exception:
            pass
    display_queue.put({"done": str(msg or "✅ 已恢复"), "source": "system"})
    return None


def _dispatch_export_command(agent, query, display_queue, export_mod):
    if export_mod is None:
        return query
    s = str(query or "").strip()
    if not s.startswith("/export"):
        return query
    args = s.split()
    if len(args) <= 1:
        display_queue.put(
            {
                "done": "\n".join(
                    [
                        "用法:",
                        "- /export clip",
                        "- /export all",
                        "- /export <文件名>",
                        "- /export file <文件名>",
                    ]
                ),
                "source": "system",
            }
        )
        return None
    sub = str(args[1] or "").strip().lower()
    kind = "file"
    filename = ""
    if sub in ("clip", "copy"):
        kind = "clip"
    elif sub == "all":
        kind = "all"
    elif sub == "file":
        kind = "file"
        filename = " ".join(args[2:]).strip()
    else:
        filename = s[len("/export") :].strip()
    try:
        if kind == "all":
            log_path = _agent_log_path(agent)
            if log_path and os.path.isfile(log_path):
                done_text = f"📂 完整日志:\n{log_path}"
            else:
                done_text = "❌ 尚无日志文件"
        else:
            text = getattr(export_mod, "last_assistant_text")(agent)
            if not text:
                done_text = "❌ 还没有可导出的回复"
            elif kind == "clip":
                done_text = f"📋 最后一轮回复:\n\n{getattr(export_mod, 'wrap_for_clipboard')(text)}"
            else:
                if not filename:
                    filename = "export-" + time.strftime("%Y%m%d-%H%M%S") + ".md"
                path = getattr(export_mod, "export_to_temp")(text, filename)
                done_text = f"✅ 已导出: {path}"
    except Exception as e:
        done_text = f"❌ 导出失败: {type(e).__name__}: {e}"
    display_queue.put({"done": done_text, "source": "system"})
    return None


def _dispatch_session_rename(agent, query, display_queue, session_names_mod):
    if session_names_mod is None:
        return query
    s = str(query or "").strip()
    if not s.startswith("/rename"):
        return query
    name = s[len("/rename") :].strip()
    if not name:
        display_queue.put({"done": "用法: /rename <name>", "source": "system"})
        return None
    log_path = _agent_log_path(agent)
    if not log_path:
        display_queue.put({"done": "❌ 当前会话还没有可命名的日志文件", "source": "system"})
        return None
    own_key = os.path.basename(log_path)
    try:
        if getattr(session_names_mod, "has_name")(name, exclude_basename=own_key):
            display_queue.put({"done": "❌ 名称已被另一会话注册，请换一个", "source": "system"})
            return None
    except Exception:
        pass
    try:
        current_name = str(getattr(session_names_mod, "name_for")(log_path) or "").strip()
    except Exception:
        current_name = ""
    if current_name and current_name.lower() == name.lower():
        display_queue.put({"done": f"⚠️ 已经叫 {name!r}", "source": "system"})
        return None
    try:
        getattr(session_names_mod, "set_name")(log_path, name)
    except Exception as e:
        display_queue.put({"done": f"❌ 名称持久化失败: {type(e).__name__}: {e}", "source": "system"})
        return None
    display_queue.put({"done": f"✅ 已重命名为 {name!r}", "source": "system"})
    return None


def _install_launcher_frontend_bridge_commands(agent_cls, loaded_modules):
    orig = getattr(agent_cls, "_handle_slash_cmd", None)
    if not callable(orig) or getattr(agent_cls, "_ga_launcher_frontend_bridge_cmds_patched", False):
        return
    continue_mod = loaded_modules.get("continue_cmd.py")
    export_mod = loaded_modules.get("export_cmd.py")
    session_names_mod = loaded_modules.get("session_names.py")

    def patched(self, raw_query, display_queue):
        s = str(raw_query or "").strip()
        if s.startswith("/rename"):
            result = _dispatch_session_rename(self, raw_query, display_queue, session_names_mod)
            if result is None:
                return None
        if s.startswith("/export"):
            result = _dispatch_export_command(self, raw_query, display_queue, export_mod)
            if result is None:
                return None
        if s.startswith("/continue"):
            result = _dispatch_continue_with_names(self, raw_query, display_queue, continue_mod, session_names_mod)
            if result is None:
                return None
        return orig(self, raw_query, display_queue)

    patched._ga_launcher_frontend_bridge_cmds_patched = True
    agent_cls._handle_slash_cmd = patched
    agent_cls._ga_launcher_frontend_bridge_cmds_patched = True


def _install_optional_upstream_frontend_slash_patches(agent_dir, agent_cls):
    frontends_dir = os.path.join(str(agent_dir or "").strip(), "frontends")
    if not os.path.isdir(frontends_dir):
        return
    if frontends_dir not in sys.path:
        sys.path.insert(0, frontends_dir)
    patch_specs = (
        ("continue_cmd.py", "_ga_launcher_continue_cmd", True),
        ("btw_cmd.py", "_ga_launcher_btw_cmd", True),
        ("review_cmd.py", "_ga_launcher_review_cmd", True),
        ("export_cmd.py", "_ga_launcher_export_cmd", False),
        ("session_names.py", "_ga_launcher_session_names", False),
    )
    loaded_modules = {}
    for filename, module_name, install_patch in patch_specs:
        path = os.path.join(frontends_dir, filename)
        if not os.path.isfile(path):
            continue
        try:
            mod = _load_python_module_from_path(module_name, path)
            loaded_modules[filename] = mod
            installer = getattr(mod, "install", None)
            if install_patch and callable(installer):
                installer(agent_cls)
        except Exception:
            continue
    _install_launcher_frontend_bridge_commands(agent_cls, loaded_modules)

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
        _install_optional_upstream_frontend_slash_patches(agent_dir, agentmain.GeneraticAgent)
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
        _patch_agent_launcher_multimodal(agent, agentmain)
        ok, setup_msg = _sanitize_agent_llmclients(agent)
        if not ok:
            send({"event": "error", "msg": setup_msg})
            return
        agent.inc_out = False
        reasoning_defaults = _reasoning_effort_defaults(agent)
        if setup_msg:
            send({"event": "log", "msg": setup_msg})
        threading.Thread(target=agent.run, daemon=True).start()
        send({"event": "ready", "llms": _ui_llms(agent), "reasoning_effort": _current_reasoning_effort(agent)})
    except Exception as e:
        send({"event": "error", "msg": str(e), "trace": traceback.format_exc()[-1500:]})
        return

    def relay(dq, backend=None, session_id=None):
        try:
            while True:
                item = dq.get(timeout=3600)
                if "next" in item:
                    payload = {"event": "next", "text": item["next"]}
                    if "turn" in item:
                        payload["turn"] = item.get("turn")
                    if "outputs" in item:
                        payload["outputs"] = item.get("outputs")
                    send(payload)
                if "done" in item:
                    usage = None
                    try:
                        raw_usage = getattr(backend, "_ga_launcher_task_usage", None)
                        if isinstance(raw_usage, dict) and raw_usage:
                            usage = dict(raw_usage)
                    except Exception:
                        usage = None
                    payload = {"event": "done", "text": item["done"]}
                    if "turn" in item:
                        payload["turn"] = item.get("turn")
                    if "outputs" in item:
                        payload["outputs"] = item.get("outputs")
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
                    context_metrics = _backend_context_metrics(getattr(agent, "llmclient", None), history=backend_hist)
                    send(
                        {
                            "event": "turn_snapshot",
                            "session_id": session_id,
                            "backend_history": backend_hist,
                            "agent_history": list(agent.history or []),
                            "llm_idx": int(getattr(agent, "llm_no", 0) or 0),
                            "reasoning_effort": _current_reasoning_effort(agent),
                            "process_pid": os.getpid(),
                            "snapshot_ts": time.time(),
                            "context_window_chars": context_metrics.get("context_window_chars", 0),
                            "current_input_chars": context_metrics.get("current_input_chars", 0),
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
                images = _collect_existing_image_paths(cmd.get("images") or [])
                prompt_text = str(cmd.get("text", "") or "")
                scrub_user_content = _build_scrubbed_user_content(prompt_text, images)
                history_text = _build_turn_history_text(prompt_text, images)
                rich_content = None
                task_text = prompt_text
                if images and _supports_native_multimodal(getattr(agent, "llmclient", None)):
                    rich_content = _build_multimodal_user_content(prompt_text, images)
                    task_text = history_text
                elif images:
                    task_text = _build_prompt_with_images(prompt_text, images)
                dq = agent.put_task(
                    task_text,
                    source="user",
                    images=images,
                    initial_user_content=rich_content,
                    history_text=history_text,
                    scrub_user_content=scrub_user_content,
                )
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
                applied = _apply_reasoning_effort(agent, None, defaults=reasoning_defaults)
                send({"event": "llm_switched", "llms": _ui_llms(agent), "reasoning_effort": applied})
            elif c == "switch_reasoning_effort":
                applied = _apply_reasoning_effort(agent, cmd.get("reasoning_effort"), defaults=reasoning_defaults)
                send({"event": "reasoning_effort_switched", "reasoning_effort": applied})
            elif c == "new_session":
                try: agent.abort()
                except Exception: pass
                agent.history = []
                _assign_backend_history(getattr(agent, "llmclient", None), [])
                agent.handler = None
                send({"event": "new_session_ok"})
            elif c == "get_state":
                backend_hist = _copy_backend_history(getattr(agent, "llmclient", None))
                context_metrics = _backend_context_metrics(getattr(agent, "llmclient", None), history=backend_hist)
                send({"event": "state",
                      "backend_history": backend_hist,
                      "agent_history": list(agent.history or []),
                      "llm_idx": agent.llm_no,
                      "reasoning_effort": _current_reasoning_effort(agent),
                      "context_window_chars": context_metrics.get("context_window_chars", 0),
                      "current_input_chars": context_metrics.get("current_input_chars", 0),
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
                    if "reasoning_effort" in cmd:
                        _apply_reasoning_effort(agent, cmd.get("reasoning_effort"), defaults=reasoning_defaults)
                    if _llm_backend(getattr(agent, "llmclient", None)) is None:
                        ok, setup_msg = _sanitize_agent_llmclients(agent)
                        if not ok:
                            raise RuntimeError(setup_msg)
                    _apply_reasoning_effort(
                        agent,
                        cmd.get("reasoning_effort") if "reasoning_effort" in cmd else None,
                        defaults=reasoning_defaults,
                    )
                    agent.history = list(cmd.get("agent_history") or [])
                    _assign_backend_history(getattr(agent, "llmclient", None), cmd.get("backend_history") or [])
                    agent.handler = None
                    send({
                        "event": "state_loaded",
                        "llms": _ui_llms(agent),
                        "llm_idx": int(getattr(agent, "llm_no", 0) or 0),
                        "reasoning_effort": _current_reasoning_effort(agent),
                    })
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
