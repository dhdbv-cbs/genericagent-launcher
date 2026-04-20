from __future__ import annotations

import json
import os
import re
import shutil
import time

from .channels import COMM_CHANNEL_INDEX
from .constants import TOKEN_ESTIMATE_DIVISOR, TOKEN_USAGE_VERSION


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
