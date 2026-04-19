"""Shared non-UI core for the GenericAgent launcher."""

from __future__ import annotations

import ast as _ast
import importlib.util as _il_util
import json
import os
import shutil
import re
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import qrcode
import requests

REPO_URL = "https://github.com/lsdefine/GenericAgent"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "launcher_config.json")
WX_BOT_API = "https://ilinkai.weixin.qq.com"
WX_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".wxbot", "token.json")
TOKEN_ESTIMATE_DIVISOR = 2.5
TOKEN_USAGE_VERSION = 1


FONT_TITLE = ("Microsoft YaHei UI", 22, "bold")
FONT_SUB = ("Microsoft YaHei UI", 13)
FONT_BODY = ("Microsoft YaHei UI", 12)
FONT_BTN = ("Microsoft YaHei UI", 13, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 10)
FONT_MONO = ("Consolas", 10)

COLOR_ACCENT = "#4f8cff"
COLOR_ACCENT_HOVER = "#3a75e0"
COLOR_APP_BG = ("#f4f7fb", "#1c1e22")
COLOR_PANEL = ("#ffffff", "#23262c")
COLOR_SURFACE = ("#ffffff", "#1c1e22")
COLOR_SIDEBAR_BG = ("#eef2f7", "#181a1e")
COLOR_CARD = ("#ffffff", "#2a2d33")
COLOR_CARD_HOVER = ("#e8edf6", "#34383f")
COLOR_FIELD_BG = ("#ffffff", "#14161a")
COLOR_FIELD_ALT = ("#f3f6fb", "#262a31")
COLOR_ACTIVE = ("#dbe7ff", "#2d3544")
COLOR_ACTIVE_HOVER = ("#cfdcf7", "#34405a")
COLOR_TEXT = ("#1f2937", "#e8ecf2")
COLOR_TEXT_SOFT = ("#3f4957", "#cfd4dc")
COLOR_MUTED = ("#6b7280", "#8a8f99")
COLOR_DIVIDER = ("#d7deea", "#3a3f47")
COLOR_DANGER_TEXT = ("#b94a4a", "#ea7070")
COLOR_DANGER_BG = ("#dc6666", "#c24848")
COLOR_DANGER_BG_HOVER = ("#c85757", "#a13a3a")
COLOR_CODE_BG = ("#f4f7fb", "#14161a")
COLOR_CODE_TEXT = ("#253041", "#dde1e7")


def _bridge_script_path():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "bridge.py")


def _python_creationflags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] save failed: {e}")


def is_valid_agent_dir(path):
    return bool(
        path
        and os.path.isdir(path)
        and os.path.isfile(os.path.join(path, "launch.pyw"))
        and os.path.isfile(os.path.join(path, "agentmain.py"))
    )


def _system_python_commands():
    candidates = []
    cfg_py = None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg_py = json.load(f).get("python_exe")
    except Exception:
        pass
    if cfg_py:
        candidates.append([cfg_py])
    if os.name == "nt":
        candidates += [["py", "-3"], ["python"], ["python3"]]
    else:
        candidates += [["python3"], ["python"]]
    return candidates


def _probe_python_command(cmd):
    try:
        r = subprocess.run(
            cmd + ["-c", "import sys;print(sys.executable);print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=_python_creationflags(),
        )
        if r.returncode != 0:
            return None
        lines = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
        if not lines:
            return None
        path = lines[0]
        ver = lines[1] if len(lines) > 1 else ""
        if path and os.path.isfile(path):
            return {"cmd": list(cmd), "path": path, "version": ver}
    except Exception:
        pass
    return None


def _system_python_candidates():
    items = []
    seen = set()
    for cmd in _system_python_commands():
        info = _probe_python_command(cmd)
        if not info:
            continue
        key = os.path.normcase(os.path.normpath(info["path"]))
        if key in seen:
            continue
        seen.add(key)
        items.append(info)
    return items


def _find_system_python():
    items = _system_python_candidates()
    return items[0]["path"] if items else None


def _probe_python_agent_compat(py, agent_dir):
    code = (
        "import os, sys\n"
        "import requests\n"
        "agent_dir = sys.argv[1]\n"
        "os.chdir(agent_dir)\n"
        "sys.path.insert(0, agent_dir)\n"
        "import agentmain\n"
        "print('OK')\n"
    )
    try:
        r = subprocess.run(
            [py, "-c", code, agent_dir],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=agent_dir,
            encoding="utf-8",
            errors="replace",
            creationflags=_python_creationflags(),
        )
    except Exception as e:
        return False, str(e)
    if r.returncode == 0 and "OK" in (r.stdout or ""):
        return True, ""
    detail = (r.stderr or r.stdout or "").strip()
    if detail:
        lines = [line.strip() for line in detail.splitlines() if line.strip()]
        detail = lines[-1] if lines else detail
    else:
        detail = f"退出码 {r.returncode}"
    return False, detail


def _format_python_candidate_label(info):
    if not info:
        return ""
    ver = (info.get("version") or "").strip()
    path = info.get("path") or ""
    return f"{path} (Python {ver})" if ver else path


def _find_compatible_system_python(agent_dir):
    candidates = _system_python_candidates()
    if not candidates:
        return None, "未找到系统 Python。请先安装 Python 并加入 PATH，或在 launcher_config.json 中设置 python_exe。"
    failures = []
    for info in candidates:
        ok, detail = _probe_python_agent_compat(info["path"], agent_dir)
        if ok:
            return info["path"], None
        failures.append((info, detail))
    lines = ["已找到系统 Python，但都无法载入 GenericAgent 内核。"]
    for info, detail in failures[:3]:
        lines.append(f"- {_format_python_candidate_label(info)}: {detail}")
    lines.append("可在 launcher_config.json 中手动指定 python_exe。")
    lines.append("当前不会强制限制版本，但如果高版本解释器兼容性不稳，通常改用 Python 3.11 / 3.12 更稳。")
    return None, "\n".join(lines)


def _ensure_mykey_file(agent_dir):
    py_path = os.path.join(agent_dir, "mykey.py")
    json_path = os.path.join(agent_dir, "mykey.json")
    if os.path.isfile(py_path) or os.path.isfile(json_path):
        return {"ok": True, "created": False, "path": py_path if os.path.isfile(py_path) else json_path}
    try:
        with open(py_path, "w", encoding="utf-8") as dst:
            dst.write(
                "# mykey.py\n"
                "# 已由 GenericAgent 启动器自动创建。\n"
                "# 请在启动器的「设置 -> API」中填写渠道配置。\n"
            )
        return {"ok": True, "created": True, "path": py_path}
    except Exception as e:
        return {"ok": False, "created": False, "path": py_path, "error": str(e)}


def _strip_known_api_suffix(path):
    raw = (path or "").strip().rstrip("/")
    for suffix in (
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/responses",
        "/responses",
        "/v1/messages",
        "/messages",
        "/claude/office",
    ):
        if raw.endswith(suffix):
            return raw[: -len(suffix)] or "/"
    return raw


def _join_url(base, suffix):
    base = (base or "").rstrip("/")
    suffix = "/" + suffix.lstrip("/")
    return f"{base}{suffix}"


def _http_json(url, headers=None, timeout=12):
    req = Request(url, headers=headers or {}, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def _extract_model_ids(payload):
    items = []
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            items = payload.get("data") or []
        elif isinstance(payload.get("models"), list):
            items = payload.get("models") or []
        elif isinstance(payload.get("items"), list):
            items = payload.get("items") or []
    elif isinstance(payload, list):
        items = payload
    out = []
    seen = set()
    for item in items:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        else:
            model_id = ""
        if model_id and model_id not in seen:
            seen.add(model_id)
            out.append(model_id)
    return out


def _oai_models_base(apibase):
    raw = (apibase or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    root = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    path = _strip_known_api_suffix(parsed.path or "")
    path = path.rstrip("/")
    if not path:
        path = "/v1"
    if not path.startswith("/"):
        path = "/" + path
    return root + path


def _anthropic_models_candidates(apibase):
    raw = (apibase or "").strip()
    if not raw:
        return []
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    root = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    path = _strip_known_api_suffix(parsed.path or "").rstrip("/")
    out = []
    for candidate in (
        _join_url(root + path, "/v1/models"),
        _join_url(root + path, "/models"),
        _join_url(root, "/v1/models"),
        _join_url(root, "/models"),
    ):
        if candidate not in out:
            out.append(candidate)
    return out


def _fetch_remote_models(format_key, apibase, apikey):
    key = (apikey or "").strip()
    base = (apibase or "").strip()
    fmt = SIMPLE_FORMAT_RULES.get(format_key) or SIMPLE_FORMAT_RULES["oai_chat"]
    kind = fmt.get("kind")
    if not base:
        raise ValueError("请先填写 URL，再拉取模型。")

    if kind == "native_oai":
        headers = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = _http_json(_join_url(_oai_models_base(base), "/models"), headers=headers)
        models = _extract_model_ids(payload)
        if models:
            return models
        raise ValueError("模型接口返回为空，可能该渠道不支持 /models。")

    headers = {}
    if key.startswith("sk-ant-"):
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    last_error = None
    for url in _anthropic_models_candidates(base):
        try:
            payload = _http_json(url, headers=headers)
            models = _extract_model_ids(payload)
            if models:
                return models
        except Exception as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    raise ValueError("未拿到模型列表，可能该 Claude 渠道没有开放模型接口。")


def sessions_dir(agent_dir):
    d = os.path.join(agent_dir, "temp", "launcher_sessions")
    os.makedirs(d, exist_ok=True)
    return d


def archived_sessions_dir(agent_dir, channel_id=None):
    root = os.path.join(agent_dir, "temp", "launcher_sessions_archive")
    if channel_id is None:
        os.makedirs(root, exist_ok=True)
        return root
    cid = _normalize_usage_channel_id(channel_id, "launcher")
    d = os.path.join(root, cid)
    os.makedirs(d, exist_ok=True)
    return d


def _canon_path(path):
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(raw)))
    except Exception:
        return os.path.normcase(os.path.normpath(raw))


def list_sessions(agent_dir):
    d = sessions_dir(agent_dir)
    out = []
    for fn in os.listdir(d):
        if not fn.endswith(".json") or fn.startswith("."):
            continue
        fp = os.path.join(d, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            out.append(
                {
                    "id": data.get("id") or fn[:-5],
                    "title": data.get("title") or "(未命名)",
                    "updated_at": data.get("updated_at", 0),
                    "pinned": bool(data.get("pinned", False)),
                    "path": fp,
                }
            )
        except Exception:
            continue
    out.sort(key=lambda x: (x["pinned"], x["updated_at"]), reverse=True)
    return out


def load_session(agent_dir, sid):
    fp = os.path.join(sessions_dir(agent_dir), f"{sid}.json")
    return load_session_file(fp)


def load_session_file(fp):
    if not os.path.isfile(fp):
        return None
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return None
    _normalize_session_paths_inplace(data)
    _normalize_token_usage_inplace(data)
    _normalize_snapshot_inplace(data)
    return data


def save_session(agent_dir, session, *, touch=True):
    fp = os.path.join(sessions_dir(agent_dir), f"{session['id']}.json")
    save_session_file(fp, session, touch=touch)


def save_session_file(fp, session, *, touch=True):
    _normalize_session_paths_inplace(session)
    _normalize_token_usage_inplace(session)
    _normalize_snapshot_inplace(session)
    if touch:
        session["updated_at"] = time.time()
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def delete_session(agent_dir, sid):
    fp = os.path.join(sessions_dir(agent_dir), f"{sid}.json")
    delete_session_file(fp)


def delete_session_file(fp):
    if os.path.isfile(fp):
        try:
            os.remove(fp)
        except Exception:
            pass


def archive_session(agent_dir, sid, session=None, *, reason="auto_limit", archived_at=None):
    delete_session(agent_dir, sid)
    return ""


def list_archived_sessions(agent_dir, channel_id=None):
    return []


def unarchive_session(agent_dir, sid, channel_id=None):
    return None


def purge_archived_sessions(agent_dir):
    root = os.path.join(agent_dir, "temp", "launcher_sessions_archive")
    if not os.path.isdir(root):
        return 0
    removed = 0
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if not fn.endswith(".json") or fn.startswith("."):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                os.remove(fp)
                removed += 1
            except Exception:
                continue
    try:
        shutil.rmtree(root, ignore_errors=True)
    except Exception:
        pass
    return removed


def _normalize_session_text(text):
    text = (text or "").replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def _session_user_signature(bubbles):
    users = []
    for bubble in bubbles or []:
        if bubble.get("role") != "user":
            continue
        text = _normalize_session_text(bubble.get("text", ""))
        if text:
            users.append(text)
    return tuple(users)


def _signature_is_tail(full_sig, tail_sig):
    full_sig = tuple(full_sig or ())
    tail_sig = tuple(tail_sig or ())
    if not full_sig or not tail_sig:
        return False
    if len(tail_sig) > len(full_sig):
        return False
    return full_sig[-len(tail_sig) :] == tail_sig


def _is_under_dir(path, root):
    try:
        path_norm = os.path.normcase(os.path.abspath(path))
        root_norm = os.path.normcase(os.path.abspath(root))
    except Exception:
        return False
    return path_norm == root_norm or path_norm.startswith(root_norm + os.sep)


_FILE_TAG_RE = re.compile(r"\[FILE:([^\]]+)\]")


def _same_session_source(a, b):
    aa = _canon_path(a)
    bb = _canon_path(b)
    return bool(aa and bb and aa == bb)


def _estimate_tokens(text):
    try:
        return int(len(str(text or "")) / TOKEN_ESTIMATE_DIVISOR)
    except Exception:
        return 0


def _usage_channel_label(channel_id):
    cid = str(channel_id or "").strip().lower()
    if cid == "launcher":
        return "启动器"
    if cid in ("official", "official_import"):
        return "启动器"
    if cid == "unknown":
        return "未知"
    spec = COMM_CHANNEL_INDEX.get(cid)
    if spec:
        return spec.get("label", cid)
    return cid or "未知"


def _normalize_usage_channel_id(channel_id, fallback="launcher"):
    cid = str(channel_id or "").strip().lower()
    fallback = str(fallback or "launcher").strip().lower() or "launcher"
    if cid in ("", "official", "official_import", "unknown"):
        return fallback
    return cid


def _usage_mode_from_sources(sources):
    normalized = {str(item or "estimate").strip().lower() or "estimate" for item in (sources or [])}
    if not normalized:
        return "estimate_chars_div_2_5"
    if normalized == {"provider"}:
        return "provider_usage"
    if "provider" in normalized:
        return "mixed_provider_and_estimate"
    return "estimate_chars_div_2_5"


def _usage_mode_label(mode):
    mode = str(mode or "estimate_chars_div_2_5").strip().lower()
    if mode == "provider_usage":
        return "真实"
    if mode == "mixed_provider_and_estimate":
        return "混合"
    return "估算"


def _fallback_token_events_from_bubbles(bubbles, base_ts=0, channel_id="unknown", model_name=""):
    events = []
    ts = float(base_ts or time.time())
    for bubble in bubbles or []:
        role = bubble.get("role")
        tokens = _estimate_tokens(bubble.get("text", ""))
        if role == "user":
            events.append(
                {
                    "ts": ts + len(events),
                    "input_tokens": tokens,
                    "output_tokens": 0,
                    "total_tokens": tokens,
                    "channel_id": channel_id,
                    "model": model_name,
                }
            )
            continue
        if role == "assistant":
            if events:
                last = events[-1]
                if int(last.get("output_tokens", 0) or 0) == 0:
                    last["output_tokens"] = tokens
                    last["total_tokens"] = int(last.get("input_tokens", 0) or 0) + tokens
                    continue
            events.append(
                {
                    "ts": ts + len(events),
                    "input_tokens": 0,
                    "output_tokens": tokens,
                    "total_tokens": tokens,
                    "channel_id": channel_id,
                    "model": model_name,
                }
            )
    return events


def _normalize_token_usage_inplace(session):
    if not isinstance(session, dict):
        return session
    default_channel = _normalize_usage_channel_id(session.get("channel_id"), "launcher")
    usage = session.get("token_usage")
    if not isinstance(usage, dict):
        usage = {}
    events = usage.get("events")
    if not isinstance(events, list):
        events = []

    normalized_events = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            ts = float(ev.get("ts", session.get("updated_at", time.time())) or time.time())
        except Exception:
            ts = time.time()
        inp = int(ev.get("input_tokens", 0) or 0)
        out = int(ev.get("output_tokens", 0) or 0)
        normalized_events.append(
            {
                "ts": ts,
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": int(ev.get("total_tokens", inp + out) or (inp + out)),
                "channel_id": _normalize_usage_channel_id(ev.get("channel_id"), default_channel),
                "model": str(ev.get("model") or "").strip(),
                "usage_source": str(ev.get("usage_source") or "estimate").strip().lower() or "estimate",
                "cached_tokens": int(ev.get("cached_tokens", 0) or 0),
                "cache_creation_input_tokens": int(ev.get("cache_creation_input_tokens", 0) or 0),
                "cache_read_input_tokens": int(ev.get("cache_read_input_tokens", 0) or 0),
                "api_calls": int(ev.get("api_calls", 0) or 0),
            }
        )

    if not normalized_events and str(session.get("session_kind") or "").strip().lower() != "channel_process":
        normalized_events = _fallback_token_events_from_bubbles(
            session.get("bubbles") or [],
            base_ts=session.get("created_at") or session.get("updated_at") or time.time(),
            channel_id=default_channel,
            model_name=str(usage.get("last_model") or "").strip(),
        )

    input_tokens = sum(int(ev.get("input_tokens", 0) or 0) for ev in normalized_events)
    output_tokens = sum(int(ev.get("output_tokens", 0) or 0) for ev in normalized_events)
    sources = {str(ev.get("usage_source") or "estimate").strip().lower() or "estimate" for ev in normalized_events}
    if sources == {"provider"}:
        mode = "provider_usage"
    elif "provider" in sources:
        mode = "mixed_provider_and_estimate"
    else:
        mode = "estimate_chars_div_2_5"

    usage = {
        "version": TOKEN_USAGE_VERSION,
        "mode": mode,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "turns": sum(1 for ev in normalized_events if int(ev.get("input_tokens", 0) or 0) > 0),
        "events": normalized_events,
        "channel_id": default_channel,
        "channel_label": _usage_channel_label(default_channel),
        "last_model": str(usage.get("last_model") or "").strip(),
        "api_calls": sum(int(ev.get("api_calls", 0) or 0) for ev in normalized_events),
    }
    session["channel_id"] = default_channel
    session["channel_label"] = _usage_channel_label(default_channel)
    session["token_usage"] = usage
    return session


def _normalize_session_paths_inplace(session):
    if not isinstance(session, dict):
        return session
    for key in (
        "imported_from",
        "official_log_path",
        "restored_from_official",
        "official_log_mtime",
        "legacy_restore_version",
    ):
        session.pop(key, None)
    return session


def _normalize_snapshot_inplace(session):
    if not isinstance(session, dict):
        return session
    snapshot = session.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    llm_idx = snapshot.get("llm_idx", session.get("llm_idx", 0))
    try:
        llm_idx = int(llm_idx or 0)
    except Exception:
        llm_idx = 0
    try:
        process_pid = int(snapshot.get("process_pid", session.get("process_pid", 0)) or 0)
    except Exception:
        process_pid = 0
    snapshot = {
        "version": int(snapshot.get("version", 1) or 1),
        "kind": str(snapshot.get("kind") or "turn_complete").strip() or "turn_complete",
        "captured_at": float(snapshot.get("captured_at", session.get("updated_at", time.time())) or time.time()),
        "turns": int(snapshot.get("turns", ((session.get("token_usage") or {}).get("turns", 0) or 0)) or 0),
        "llm_idx": llm_idx,
        "process_pid": process_pid,
        "has_backend_history": bool(session.get("backend_history")),
        "has_agent_history": bool(session.get("agent_history")),
    }
    session["llm_idx"] = llm_idx
    session["snapshot"] = snapshot
    return session


def _classify_config_kind(var_name):
    n = (var_name or "").lower()
    if "mixin" in n:
        return "mixin"
    if "native" in n and "claude" in n:
        return "native_claude"
    if "native" in n and "oai" in n:
        return "native_oai"
    if "claude" in n:
        return "claude"
    if "oai" in n:
        return "oai"
    return "unknown"


KIND_LABEL = {
    "native_claude": "原生 Claude",
    "native_oai": "原生 OpenAI",
    "mixin": "Mixin 故障转移",
    "claude": "Claude (文本协议)",
    "oai": "OpenAI (文本协议)",
    "unknown": "未知",
}


EXTRA_KEYS = {
    "proxy",
    "tg_bot_token",
    "tg_allowed_users",
    "qq_app_id",
    "qq_app_secret",
    "qq_allowed_users",
    "fs_app_id",
    "fs_app_secret",
    "fs_allowed_users",
    "wecom_bot_id",
    "wecom_secret",
    "wecom_allowed_users",
    "wecom_welcome_message",
    "dingtalk_client_id",
    "dingtalk_client_secret",
    "dingtalk_allowed_users",
}

COMM_CHANNEL_SPECS = [
    {
        "id": "wechat",
        "label": "微信",
        "subtitle": "个人微信扫码登录",
        "script": "wechatapp.py",
        "log_name": "wechatapp.log",
        "pip": "pycryptodome qrcode requests",
        "fields": [],
        "required": [],
        "notes": "无需在 mykey.py 填 Key。首次启动会弹二维码完成绑定。",
        "conflicts_with": ["qq"],
    },
    {
        "id": "telegram",
        "label": "Telegram / 纸飞机",
        "subtitle": "Bot Token + 白名单用户",
        "script": "tgapp.py",
        "log_name": "tgapp.log",
        "pip": "python-telegram-bot",
        "fields": [
            {"key": "tg_bot_token", "label": "Bot Token", "kind": "password", "placeholder": "例如 123456:AA..."},
            {"key": "tg_allowed_users", "label": "允许用户", "kind": "list_int", "placeholder": "逗号分隔用户 ID"},
        ],
        "required": ["tg_bot_token", "tg_allowed_users"],
        "notes": "Telegram 前端要求填写允许访问的用户 ID，留空会直接退出。",
        "conflicts_with": [],
    },
    {
        "id": "qq",
        "label": "QQ",
        "subtitle": "QQ 开放平台机器人",
        "script": "qqapp.py",
        "log_name": "qqapp.log",
        "pip": "qq-botpy",
        "fields": [
            {"key": "qq_app_id", "label": "App ID", "kind": "text", "placeholder": "QQ 机器人 AppID"},
            {"key": "qq_app_secret", "label": "App Secret", "kind": "password", "placeholder": "QQ 机器人密钥"},
            {"key": "qq_allowed_users", "label": "允许用户", "kind": "list_str", "placeholder": "openid，逗号分隔；可填 *"},
        ],
        "required": ["qq_app_id", "qq_app_secret"],
        "notes": "QQ 和微信沿用上游同一个单实例锁，不能同时启动。",
        "conflicts_with": ["wechat"],
    },
    {
        "id": "feishu",
        "label": "飞书",
        "subtitle": "Lark 长连接 Bot",
        "script": "fsapp.py",
        "log_name": "fsapp.log",
        "pip": "lark-oapi",
        "fields": [
            {"key": "fs_app_id", "label": "App ID", "kind": "text", "placeholder": "cli_xxx"},
            {"key": "fs_app_secret", "label": "App Secret", "kind": "password", "placeholder": "飞书应用密钥"},
            {"key": "fs_allowed_users", "label": "允许用户", "kind": "list_str", "placeholder": "open_id，逗号分隔；可填 *"},
        ],
        "required": ["fs_app_id", "fs_app_secret"],
        "notes": "详细权限和入站配置仍建议对照上游的飞书接入文档。",
        "conflicts_with": [],
    },
    {
        "id": "wecom",
        "label": "企业微信",
        "subtitle": "WeCom 机器人",
        "script": "wecomapp.py",
        "log_name": "wecomapp.log",
        "pip": "wecom_aibot_sdk",
        "fields": [
            {"key": "wecom_bot_id", "label": "Bot ID", "kind": "text", "placeholder": "企业微信 bot_id"},
            {"key": "wecom_secret", "label": "Secret", "kind": "password", "placeholder": "企业微信 secret"},
            {"key": "wecom_allowed_users", "label": "允许用户", "kind": "list_str", "placeholder": "user_id，逗号分隔；可填 *"},
            {"key": "wecom_welcome_message", "label": "欢迎语", "kind": "text", "placeholder": "可留空"},
        ],
        "required": ["wecom_bot_id", "wecom_secret"],
        "notes": "欢迎语为空时不会主动发送进入会话提示。",
        "conflicts_with": [],
    },
    {
        "id": "dingtalk",
        "label": "钉钉",
        "subtitle": "DingTalk Stream Bot",
        "script": "dingtalkapp.py",
        "log_name": "dingtalkapp.log",
        "pip": "dingtalk-stream",
        "fields": [
            {"key": "dingtalk_client_id", "label": "Client ID", "kind": "text", "placeholder": "应用 AppKey"},
            {"key": "dingtalk_client_secret", "label": "Client Secret", "kind": "password", "placeholder": "应用 AppSecret"},
            {"key": "dingtalk_allowed_users", "label": "允许用户", "kind": "list_str", "placeholder": "staff_id，逗号分隔；可填 *"},
        ],
        "required": ["dingtalk_client_id", "dingtalk_client_secret"],
        "notes": "钉钉走 stream 长连接，不需要公网 webhook。",
        "conflicts_with": [],
    },
]

COMM_CHANNEL_INDEX = {spec["id"]: spec for spec in COMM_CHANNEL_SPECS}


def _looks_like_config_name(name):
    n = (name or "").lower()
    return any(x in n for x in ("api", "config", "cookie"))


def _is_config_var(name, value):
    if name.startswith("_"):
        return False
    if not isinstance(value, dict):
        return False
    return _looks_like_config_name(name) or ("apikey" in value) or ("llm_nos" in value) or ("apibase" in value) or ("model" in value)


def _is_passthrough_var(name, value):
    if name.startswith("_"):
        return False
    return ("cookie" in (name or "").lower()) and not isinstance(value, dict)


def parse_mykey_py(path):
    out = {"configs": [], "extras": {}, "passthrough": [], "error": None}
    if not os.path.isfile(path):
        return out
    try:
        src = open(path, "r", encoding="utf-8").read()
    except Exception as e:
        out["error"] = f"读取失败: {e}"
        return out

    order = []
    try:
        tree = _ast.parse(src)
        for node in tree.body:
            if isinstance(node, _ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], _ast.Name):
                order.append(node.targets[0].id)
    except Exception as e:
        out["error"] = f"语法解析失败: {e}"

    values = {}
    try:
        spec = _il_util.spec_from_loader("mykey_runtime", loader=None)
        mod = _il_util.module_from_spec(spec)
        exec(compile(src, path, "exec"), mod.__dict__)
        for k, v in mod.__dict__.items():
            if k.startswith("__"):
                continue
            values[k] = v
    except Exception as e:
        out["error"] = f"执行失败: {e}"
        return out

    seen = set()
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        if name in values and _is_config_var(name, values[name]):
            out["configs"].append({"var": name, "kind": _classify_config_kind(name), "data": dict(values[name])})
        elif name in values and _is_passthrough_var(name, values[name]):
            out["passthrough"].append({"name": name, "value": values[name]})
        elif name in values and name in EXTRA_KEYS:
            out["extras"][name] = values[name]
    for name, v in values.items():
        if name in seen:
            continue
        if _is_config_var(name, v):
            out["configs"].append({"var": name, "kind": _classify_config_kind(name), "data": dict(v)})
        elif _is_passthrough_var(name, v):
            out["passthrough"].append({"name": name, "value": v})
        elif name in EXTRA_KEYS:
            out["extras"][name] = v
    return out


_FIELD_ORDER = [
    "name",
    "apikey",
    "apibase",
    "model",
    "api_mode",
    "fake_cc_system_prompt",
    "thinking_type",
    "thinking_budget_tokens",
    "reasoning_effort",
    "temperature",
    "max_tokens",
    "stream",
    "max_retries",
    "connect_timeout",
    "read_timeout",
    "context_win",
    "proxy",
    "llm_nos",
    "base_delay",
    "spring_back",
]


def _ordered_items(d):
    idx = {k: i for i, k in enumerate(_FIELD_ORDER)}
    return sorted(d.items(), key=lambda kv: (idx.get(kv[0], 999), kv[0]))


def _fmt_dict(d):
    if not d:
        return "{}"
    lines = ["{"]
    for k, v in _ordered_items(d):
        lines.append(f"    {k!r}: {v!r},")
    lines.append("}")
    return "\n".join(lines)


def serialize_mykey_py(configs, extras, passthrough=None):
    header = (
        "# ══════════════════════════════════════════════════════════════════════════════\n"
        "#  mykey.py — 由 GenericAgent 启动器「设置 → API」面板生成。\n"
        "#\n"
        "#  推荐在启动器里编辑；直接改本文件也行，但下次从面板保存时手写的注释会被覆盖，\n"
        "#  GenericAgent 能识别的配置变量，以及 cookie / proxy / 聊天平台 token 等原文项会保留。\n"
        "#\n"
        "#  Session 类型由变量名决定：\n"
        "#    含 'native' + 'claude' → NativeClaudeSession (原生 Anthropic 协议)\n"
        "#    含 'native' + 'oai'    → NativeOAISession   (OpenAI + 原生 tool 字段)\n"
        "#    含 'mixin'            → MixinSession       (按 llm_nos 顺序故障转移)\n"
        "#    含 'claude' (非 native) → ClaudeSession     (deprecated)\n"
        "#    含 'oai' (非 native)   → LLMSession         (deprecated)\n"
        "#\n"
        "#  apibase 自动拼接：host:port → /v1/chat/completions；host/v1 → /chat/completions；\n"
        "#  完整 URL 原样使用。model 后缀 '[1m]' 触发 1m 上下文 beta。\n"
        "# ══════════════════════════════════════════════════════════════════════════════\n"
    )

    groups = [
        ("mixin", "# ── Mixin 故障转移 ───────────────────────────────────────────────"),
        ("native_claude", "# ── NativeClaudeSession 渠道 ──────────────────────────────────────"),
        ("native_oai", "# ── NativeOAISession 渠道 ─────────────────────────────────────────"),
        ("claude", "# ── ClaudeSession 渠道 (deprecated) ───────────────────────────────"),
        ("oai", "# ── LLMSession 渠道 (deprecated) ──────────────────────────────────"),
        ("unknown", "# ── 其它 ─────────────────────────────────────────────────────────"),
    ]
    by_kind = {}
    for c in configs:
        by_kind.setdefault(c.get("kind", "unknown"), []).append(c)

    parts = [header]
    for kind, title in groups:
        items = by_kind.get(kind, [])
        if not items:
            continue
        parts.append("\n" + title + "\n")
        for c in items:
            parts.append(f"{c['var']} = {_fmt_dict(c.get('data') or {})}\n")

    passthrough = list(passthrough or [])
    if passthrough:
        parts.append("\n# ── 其它保留项（表单不直接编辑）───────────────────────────────────────\n")
        for item in passthrough:
            name = item.get("name")
            if not name:
                continue
            parts.append(f"{name} = {item.get('value')!r}\n")

    if extras:
        parts.append("\n# ── 全局代理 / 聊天平台集成 ─────────────────────────────────────────\n")
        extra_order = [
            "proxy",
            "tg_bot_token",
            "tg_allowed_users",
            "qq_app_id",
            "qq_app_secret",
            "qq_allowed_users",
            "fs_app_id",
            "fs_app_secret",
            "fs_allowed_users",
            "wecom_bot_id",
            "wecom_secret",
            "wecom_allowed_users",
            "wecom_welcome_message",
            "dingtalk_client_id",
            "dingtalk_client_secret",
            "dingtalk_allowed_users",
        ]
        for name in extra_order:
            if name in extras:
                parts.append(f"{name} = {extras[name]!r}\n")

    return "".join(parts)


def auto_config_var(kind, existing_vars):
    base = {
        "native_claude": "native_claude_config",
        "native_oai": "native_oai_config",
        "mixin": "mixin_config",
        "claude": "claude_config",
        "oai": "oai_config",
    }.get(kind, "config")
    if base not in existing_vars:
        return base
    i = 2
    while f"{base}{i}" in existing_vars:
        i += 1
    return f"{base}{i}"


CHANNEL_TEMPLATES = [
    ("anthropic", "Anthropic 官方", "native_claude", {"apibase": "https://api.anthropic.com", "model": "claude-opus-4-6[1m]"}),
    ("cc-switch", "CC Switch / 反代中转", "native_claude", {"apibase": "", "model": "claude-opus-4-6", "fake_cc_system_prompt": True}),
    ("crs-claude", "CRS 反代 Claude Max", "native_claude", {"apibase": "", "model": "claude-opus-4-6[1m]", "fake_cc_system_prompt": True, "max_tokens": 32768, "read_timeout": 180}),
    ("crs-gemini", "CRS Gemini Ultra", "native_claude", {"apibase": "", "model": "claude-opus-4-6-thinking", "stream": False, "max_tokens": 32768, "read_timeout": 180}),
    ("glm", "智谱 GLM-5.1", "native_claude", {"apibase": "https://open.bigmodel.cn/api/anthropic", "model": "glm-5.1"}),
    ("minimax-anth", "MiniMax (Anthropic 路径)", "native_claude", {"apibase": "https://api.minimaxi.com/anthropic", "model": "MiniMax-M2.7"}),
    ("oai-generic", "通用 OAI 原生", "native_oai", {"apibase": "", "model": "gpt-5.4"}),
    ("openai", "OpenAI 官方", "native_oai", {"apibase": "https://api.openai.com/v1", "model": "gpt-5.4"}),
    ("openrouter", "OpenRouter", "native_oai", {"apibase": "https://openrouter.ai/api/v1", "model": "anthropic/claude-opus-4-6"}),
    ("minimax-oai", "MiniMax (OAI 路径)", "native_oai", {"apibase": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.7", "context_win": 50000}),
    ("kimi", "Kimi / Moonshot", "native_oai", {"apibase": "https://api.moonshot.cn/v1", "model": "kimi-k2-turbo-preview"}),
    ("mixin", "Mixin 故障转移", "mixin", {"llm_nos": [], "max_retries": 10, "base_delay": 0.5}),
    ("custom-claude", "自定义 Native Claude", "native_claude", {}),
    ("custom-oai", "自定义 Native OAI", "native_oai", {}),
]

TEMPLATE_INDEX = {key: {"label": label, "kind": kind, "defaults": dict(defaults)} for key, label, kind, defaults in CHANNEL_TEMPLATES}

SIMPLE_FORMAT_LABEL = {
    "claude_native": "Claude 原生",
    "oai_chat": "Chat Completions",
    "oai_responses": "Responses",
}

SIMPLE_FORMAT_RULES = {
    "claude_native": {
        "kind": "native_claude",
        "api_mode": None,
        "default_template": "cc-switch",
        "templates": ["cc-switch", "anthropic", "crs-claude", "crs-gemini", "glm", "minimax-anth", "custom-claude"],
        "hint": "走 Claude / Anthropic 原生协议。",
    },
    "oai_chat": {
        "kind": "native_oai",
        "api_mode": "chat_completions",
        "default_template": "openai",
        "templates": ["openai", "oai-generic", "openrouter", "minimax-oai", "kimi", "custom-oai"],
        "hint": "走 OpenAI Chat Completions 协议。",
    },
    "oai_responses": {
        "kind": "native_oai",
        "api_mode": "responses",
        "default_template": "openai",
        "templates": ["openai", "oai-generic", "openrouter", "minimax-oai", "kimi", "custom-oai"],
        "hint": "走 OpenAI Responses 协议。",
    },
}

TEMPLATE_MANAGED_KEYS = {field for meta in TEMPLATE_INDEX.values() for field in meta.get("defaults", {}) if field != "apibase"}


_TURN_RE = re.compile(r"(\**LLM Running \(Turn \d+\) \.\.\.\*\**)")
_CLEAN_BLOCK_RE = re.compile(r"(?P<fence>`{3,})[\s\S]*?(?P=fence)|<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary>\s*((?:(?!<summary>).)*?)\s*</summary>", re.DOTALL)

_RE_THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_RE_SUMMARY = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL | re.IGNORECASE)
_RE_TOOLUSE = re.compile(r"<tool_use>(.*?)</tool_use>", re.DOTALL | re.IGNORECASE)
_RE_FILE_CONTENT = re.compile(r"<file_content>(.*?)</file_content>", re.DOTALL | re.IGNORECASE)


def fold_turns(text):
    parts = _TURN_RE.split(text or "")
    if len(parts) < 4:
        return [{"type": "text", "content": text or ""}]
    segments = []
    if parts[0].strip():
        segments.append({"type": "text", "content": parts[0]})
    turns = []
    for i in range(1, len(parts), 2):
        marker = parts[i]
        content = parts[i + 1] if i + 1 < len(parts) else ""
        turns.append((marker, content))
    for idx, (marker, content) in enumerate(turns):
        if idx < len(turns) - 1:
            cleaned = _CLEAN_BLOCK_RE.sub("", content)
            match = _SUMMARY_RE.search(cleaned)
            if match:
                title = match.group(1).strip().split("\n")[0]
                if len(title) > 50:
                    title = title[:50] + "..."
            else:
                title = marker.strip("*")
            segments.append({"type": "fold", "title": title, "content": content})
        else:
            segments.append({"type": "text", "content": marker + content})
    return segments


def _normalize_markup(text):
    if not text:
        return ""
    text = _RE_THINKING.sub("", text)
    text = _RE_SUMMARY.sub(lambda m: f"\n> {m.group(1).strip()}\n", text)
    text = _RE_TOOLUSE.sub(lambda m: f"\n```tool_use\n{m.group(1).strip()}\n```\n", text)
    text = _RE_FILE_CONTENT.sub(lambda m: f"\n```file_content\n{m.group(1).strip()}\n```\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _assistant_visible_markup(text):
    raw = text or ""
    summaries = [m.strip() for m in _RE_SUMMARY.findall(raw) if m.strip()]
    visible = _RE_SUMMARY.sub("", raw)
    visible = _normalize_markup(visible)
    visible = _FILE_TAG_RE.sub(r"\1", visible).strip()
    if visible:
        return visible
    return "\n\n".join(summaries).strip()


def _strip_turn_marker(text):
    return _TURN_RE.sub("", text or "", count=1).strip()


def _turn_marker_title(text):
    m = _TURN_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip("*").strip()
