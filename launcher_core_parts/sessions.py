from __future__ import annotations

import json
import os
import re
import shutil
import time

from .channels import COMM_CHANNEL_INDEX
from .constants import TOKEN_ESTIMATE_DIVISOR, TOKEN_USAGE_VERSION


_SESSION_INDEX_CACHE = {}


def sessions_dir(agent_dir):
    d = os.path.join(agent_dir, "temp", "launcher_sessions")
    os.makedirs(d, exist_ok=True)
    return d


def sessions_meta_dir(agent_dir):
    d = os.path.join(agent_dir, "temp", "launcher_sessions_meta")
    os.makedirs(d, exist_ok=True)
    return d


def sessions_index_path(agent_dir):
    return os.path.join(agent_dir, "temp", "launcher_sessions_index.json")


def _session_meta_path(agent_dir, sid):
    text = str(sid or "").strip()
    if not text:
        return ""
    return os.path.join(sessions_meta_dir(agent_dir), f"{text}.json")


def _session_meta_path_for_file(fp):
    path = str(fp or "").strip()
    if not path:
        return ""
    parent = os.path.basename(os.path.dirname(path))
    if parent != "launcher_sessions":
        return ""
    base = os.path.splitext(os.path.basename(path))[0]
    if not base:
        return ""
    meta_dir = os.path.join(os.path.dirname(os.path.dirname(path)), "launcher_sessions_meta")
    return os.path.join(meta_dir, f"{base}.json")


def _agent_dir_from_session_file(fp):
    path = str(fp or "").strip()
    if not path:
        return ""
    parent = os.path.dirname(path)
    if os.path.basename(parent) != "launcher_sessions":
        return ""
    temp_dir = os.path.dirname(parent)
    if os.path.basename(temp_dir) != "temp":
        return ""
    return os.path.dirname(temp_dir)


def _safe_float(value, default=0.0):
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    try:
        if value in (None, ""):
            return int(default or 0)
        return int(value or 0)
    except Exception:
        return int(default or 0)


def _safe_cost(value, default=0.0):
    try:
        if value is None:
            return round(float(default or 0), 8)
        if isinstance(value, str) and not value.strip():
            return round(float(default or 0), 8)
        return round(float(value), 8)
    except Exception:
        return round(float(default or 0), 8)


def _safe_bool(value):
    return bool(value)


def usage_pricing_target_key(device_scope="local", device_id="local"):
    scope = str(device_scope or "local").strip().lower()
    if scope not in ("local", "remote"):
        scope = "local"
    did = str(device_id or "").strip() if scope == "remote" else "local"
    if scope == "remote" and not did:
        scope = "local"
        did = "local"
    return f"{scope}:{did or 'local'}"


def normalize_usage_currency(value):
    text = str(value or "USD").strip().upper()
    if not text:
        text = "USD"
    return re.sub(r"[^A-Z0-9_\-]", "", text)[:12] or "USD"


def _normalize_price_per_1m(value):
    try:
        number = float(value or 0)
    except Exception:
        number = 0.0
    if number < 0:
        number = 0.0
    return round(number, 8)


def normalize_usage_pricing_config(cfg):
    root_cfg = cfg if isinstance(cfg, dict) else {}
    pricing = root_cfg.get("usage_pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    currency = normalize_usage_currency(pricing.get("currency") or root_cfg.get("usage_currency") or "USD")
    targets = pricing.get("targets")
    if not isinstance(targets, dict):
        targets = {}
    normalized_targets = {}
    for raw_key, raw_rules in list(targets.items()):
        rules = raw_rules if isinstance(raw_rules, dict) else {}
        key = str(raw_key or "").strip()
        if ":" not in key:
            key = usage_pricing_target_key("local", "local")
        normalized_rules = {}
        for raw_var, raw_rule in list(rules.items()):
            api_var = str(raw_var or "").strip()
            if not api_var:
                continue
            rule = raw_rule if isinstance(raw_rule, dict) else {}
            normalized_rules[api_var] = {
                "api_card_var": api_var,
                "api_card_label": str(rule.get("api_card_label") or rule.get("label") or api_var).strip() or api_var,
                "input_per_1m": _normalize_price_per_1m(rule.get("input_per_1m")),
                "output_per_1m": _normalize_price_per_1m(rule.get("output_per_1m")),
                "cache_read_per_1m": _normalize_price_per_1m(rule.get("cache_read_per_1m")),
                "cache_creation_per_1m": _normalize_price_per_1m(rule.get("cache_creation_per_1m")),
                "updated_at": _safe_float(rule.get("updated_at"), 0.0),
            }
        normalized_targets[key] = normalized_rules
    pricing = {"version": 1, "currency": currency, "targets": normalized_targets}
    if isinstance(cfg, dict):
        cfg["usage_pricing"] = pricing
    return pricing


def usage_price_rule(cfg, device_scope="local", device_id="local", api_card_var=""):
    api_var = str(api_card_var or "").strip()
    if not api_var:
        return None
    pricing = normalize_usage_pricing_config(cfg)
    target_key = usage_pricing_target_key(device_scope, device_id)
    rule = (pricing.get("targets") or {}).get(target_key, {}).get(api_var)
    return dict(rule) if isinstance(rule, dict) else None


def set_usage_price_rule(cfg, device_scope="local", device_id="local", api_card_var="", rule=None):
    if not isinstance(cfg, dict):
        return None
    api_var = str(api_card_var or "").strip()
    if not api_var:
        return None
    pricing = normalize_usage_pricing_config(cfg)
    target_key = usage_pricing_target_key(device_scope, device_id)
    targets = pricing.setdefault("targets", {})
    bucket = targets.setdefault(target_key, {})
    raw = rule if isinstance(rule, dict) else {}
    normalized = {
        "api_card_var": api_var,
        "api_card_label": str(raw.get("api_card_label") or raw.get("label") or api_var).strip() or api_var,
        "input_per_1m": _normalize_price_per_1m(raw.get("input_per_1m")),
        "output_per_1m": _normalize_price_per_1m(raw.get("output_per_1m")),
        "cache_read_per_1m": _normalize_price_per_1m(raw.get("cache_read_per_1m")),
        "cache_creation_per_1m": _normalize_price_per_1m(raw.get("cache_creation_per_1m")),
        "updated_at": _safe_float(raw.get("updated_at"), time.time()),
    }
    if not any(normalized.get(k, 0) > 0 for k in ("input_per_1m", "output_per_1m", "cache_read_per_1m", "cache_creation_per_1m")):
        bucket.pop(api_var, None)
        return None
    bucket[api_var] = normalized
    cfg["usage_pricing"] = pricing
    return normalized


def usage_price_snapshot(rule, currency="USD"):
    data = rule if isinstance(rule, dict) else {}
    api_var = str(data.get("api_card_var") or "").strip()
    if not api_var:
        return None
    snapshot = {
        "version": 1,
        "api_card_var": api_var,
        "api_card_label": str(data.get("api_card_label") or api_var).strip() or api_var,
        "currency": normalize_usage_currency(currency),
        "unit": "per_1m_tokens",
        "input_per_1m": _normalize_price_per_1m(data.get("input_per_1m")),
        "output_per_1m": _normalize_price_per_1m(data.get("output_per_1m")),
        "cache_read_per_1m": _normalize_price_per_1m(data.get("cache_read_per_1m")),
        "cache_creation_per_1m": _normalize_price_per_1m(data.get("cache_creation_per_1m")),
        "captured_at": time.time(),
    }
    if not any(snapshot.get(k, 0) > 0 for k in ("input_per_1m", "output_per_1m", "cache_read_per_1m", "cache_creation_per_1m")):
        return None
    return snapshot


def apply_usage_price_snapshot(event, snapshot):
    ev = event if isinstance(event, dict) else {}
    snap = snapshot if isinstance(snapshot, dict) else None
    if not snap:
        ev["billing_mode"] = "unpriced"
        return ev
    input_tokens = _safe_int(ev.get("input_tokens"))
    output_tokens = _safe_int(ev.get("output_tokens"))
    cache_read = _safe_int(ev.get("cache_read_input_tokens"))
    cache_creation = _safe_int(ev.get("cache_creation_input_tokens"))
    billable_input = max(0, input_tokens - cache_read - cache_creation)
    cost_input = _safe_cost(billable_input * _normalize_price_per_1m(snap.get("input_per_1m")) / 1_000_000)
    cost_output = _safe_cost(output_tokens * _normalize_price_per_1m(snap.get("output_per_1m")) / 1_000_000)
    cost_cache_read = _safe_cost(cache_read * _normalize_price_per_1m(snap.get("cache_read_per_1m")) / 1_000_000)
    cost_cache_creation = _safe_cost(cache_creation * _normalize_price_per_1m(snap.get("cache_creation_per_1m")) / 1_000_000)
    ev["price_snapshot"] = dict(snap)
    ev["currency"] = normalize_usage_currency(snap.get("currency"))
    ev["billable_input_tokens"] = billable_input
    ev["cost_input"] = cost_input
    ev["cost_output"] = cost_output
    ev["cost_cache_read"] = cost_cache_read
    ev["cost_cache_creation"] = cost_cache_creation
    ev["cost_total"] = _safe_cost(cost_input + cost_output + cost_cache_read + cost_cache_creation)
    ev["billing_mode"] = "priced"
    return ev


def usage_event_is_priced(event):
    ev = event if isinstance(event, dict) else {}
    return isinstance(ev.get("price_snapshot"), dict) or any(
        key in ev for key in ("cost_input", "cost_output", "cost_cache_read", "cost_cache_creation", "cost_total")
    )


def _session_meta_from_payload(payload, *, sid="", path=""):
    data = payload if isinstance(payload, dict) else {}
    meta_id = str(data.get("id") or sid or "").strip()
    channel_id = _normalize_usage_channel_id(data.get("channel_id"), "launcher")
    channel_label = str(data.get("channel_label") or _usage_channel_label(channel_id)).strip() or _usage_channel_label(channel_id)
    device_scope = str(data.get("device_scope") or "local").strip().lower()
    if device_scope not in ("local", "remote"):
        device_scope = "local"
    device_id = str(data.get("device_id") or "").strip() if device_scope == "remote" else "local"
    if device_scope == "remote" and not device_id:
        device_scope = "local"
        device_id = "local"
    device_name = str(data.get("device_name") or ("本机" if device_scope == "local" else "远程设备")).strip()
    if not device_name:
        device_name = "本机" if device_scope == "local" else "远程设备"
    return {
        "id": meta_id,
        "title": str(data.get("title") or "(未命名)").strip() or "(未命名)",
        "updated_at": _safe_float(data.get("updated_at"), 0.0),
        "pinned": _safe_bool(data.get("pinned", False)),
        "channel_id": channel_id,
        "channel_label": channel_label,
        "device_scope": device_scope,
        "device_id": device_id,
        "device_name": device_name,
        "session_kind": str(data.get("session_kind") or "").strip().lower(),
        "path": str(path or "").strip(),
    }


def _load_session_meta_file(path):
    fp = str(path or "").strip()
    if not fp or not os.path.isfile(fp):
        return None
    try:
        with open(fp, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return _session_meta_from_payload(payload, sid=payload.get("id"), path=payload.get("path"))


def _write_session_meta_file(path, payload):
    fp = str(path or "").strip()
    if not fp:
        return
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(payload if isinstance(payload, dict) else {}, f, ensure_ascii=False, indent=2)


def _session_index_cache_key(agent_dir):
    root = str(agent_dir or "").strip()
    if not root:
        return ""
    try:
        return os.path.normcase(os.path.abspath(root))
    except Exception:
        return os.path.normcase(root)


def _copy_session_index_rows(index):
    out = {}
    for sid, row in (index or {}).items():
        key = str(sid or "").strip()
        if not key or not isinstance(row, dict):
            continue
        out[key] = dict(row)
    return out


def _load_sessions_index(agent_dir):
    fp = sessions_index_path(agent_dir)
    cache_key = _session_index_cache_key(agent_dir)
    try:
        mtime = float(os.path.getmtime(fp)) if os.path.isfile(fp) else 0.0
    except Exception:
        mtime = 0.0
    cached = _SESSION_INDEX_CACHE.get(cache_key) if cache_key else None
    if isinstance(cached, dict) and float(cached.get("mtime", 0.0) or 0.0) == mtime:
        return _copy_session_index_rows(cached.get("index"))
    if not os.path.isfile(fp):
        if cache_key:
            _SESSION_INDEX_CACHE[cache_key] = {"mtime": 0.0, "index": {}}
        return {}
    try:
        with open(fp, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out = {}
    for sid, row in payload.items():
        key = str(sid or "").strip()
        if not key:
            continue
        if not isinstance(row, dict):
            continue
        out[key] = _session_meta_from_payload(row, sid=key, path=row.get("path"))
    if cache_key:
        _SESSION_INDEX_CACHE[cache_key] = {"mtime": mtime, "index": _copy_session_index_rows(out)}
    return out


def _save_sessions_index(agent_dir, index):
    fp = sessions_index_path(agent_dir)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    payload = {}
    for sid, row in (index or {}).items():
        key = str(sid or "").strip()
        if not key or not isinstance(row, dict):
            continue
        payload[key] = _session_meta_from_payload(row, sid=key, path=row.get("path"))
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    cache_key = _session_index_cache_key(agent_dir)
    if cache_key:
        try:
            mtime = float(os.path.getmtime(fp))
        except Exception:
            mtime = 0.0
        _SESSION_INDEX_CACHE[cache_key] = {"mtime": mtime, "index": _copy_session_index_rows(payload)}


def _update_session_index_row(agent_dir, sid, row):
    root = str(agent_dir or "").strip()
    key = str(sid or "").strip()
    if not root or not key:
        return
    index = _load_sessions_index(root)
    index[key] = _session_meta_from_payload(row, sid=key, path=row.get("path"))
    _save_sessions_index(root, index)


def _remove_session_index_row(agent_dir, sid):
    root = str(agent_dir or "").strip()
    key = str(sid or "").strip()
    if not root or not key:
        return
    index = _load_sessions_index(root)
    if key in index:
        index.pop(key, None)
        _save_sessions_index(root, index)


def _sync_session_meta_for_file(fp, payload):
    meta_fp = _session_meta_path_for_file(fp)
    if not meta_fp:
        return
    meta = _session_meta_from_payload(payload, sid=os.path.splitext(os.path.basename(fp))[0], path=fp)
    agent_dir = _agent_dir_from_session_file(fp)
    if agent_dir:
        current_index = _load_sessions_index(agent_dir)
        current_meta = current_index.get(str(meta.get("id") or "").strip())
        if isinstance(current_meta, dict) and _session_meta_from_payload(current_meta, sid=meta.get("id"), path=fp) == meta:
            return
    _write_session_meta_file(meta_fp, meta)
    if agent_dir:
        _update_session_index_row(agent_dir, meta.get("id"), meta)


def _json_unescape_string(text):
    raw = str(text or "")
    if not raw:
        return ""
    try:
        return json.loads(f"\"{raw}\"")
    except Exception:
        return raw


def _quick_read_session_meta(fp, sid):
    path = str(fp or "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read(32 * 1024)
    except Exception:
        return None
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    title_match = re.search(r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    updated_match = re.search(r'"updated_at"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
    channel_match = re.search(r'"channel_id"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    scope_match = re.search(r'"device_scope"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    device_id_match = re.search(r'"device_id"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    device_name_match = re.search(r'"device_name"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    session_kind_match = re.search(r'"session_kind"\s*:\s*"((?:\\.|[^"\\])*)"', text)
    pinned_match = re.search(r'"pinned"\s*:\s*(true|false)', text, flags=re.IGNORECASE)
    title = _json_unescape_string(title_match.group(1)) if title_match else "(未命名)"
    channel_raw = _json_unescape_string(channel_match.group(1)) if channel_match else ""
    channel_id = _normalize_usage_channel_id(channel_raw, "launcher")
    device_scope = _json_unescape_string(scope_match.group(1)).strip().lower() if scope_match else "local"
    if device_scope not in ("local", "remote"):
        device_scope = "local"
    device_id = _json_unescape_string(device_id_match.group(1)).strip() if device_id_match else ""
    if device_scope == "remote":
        if not device_id:
            device_scope = "local"
            device_id = "local"
    else:
        device_id = "local"
    device_name = _json_unescape_string(device_name_match.group(1)).strip() if device_name_match else ""
    if not device_name:
        device_name = "本机" if device_scope == "local" else "远程设备"
    session_kind = (
        _json_unescape_string(session_kind_match.group(1)).strip().lower()
        if session_kind_match
        else ""
    )
    updated_at = _safe_float(updated_match.group(1) if updated_match else 0, 0.0)
    if updated_at <= 0:
        try:
            updated_at = float(os.path.getmtime(path))
        except Exception:
            updated_at = 0.0
    pinned = bool(pinned_match and str(pinned_match.group(1)).strip().lower() == "true")
    return {
        "id": str(sid or "").strip(),
        "title": str(title or "(未命名)").strip() or "(未命名)",
        "updated_at": float(updated_at or 0),
        "pinned": bool(pinned),
        "channel_id": channel_id,
        "channel_label": _usage_channel_label(channel_id),
        "device_scope": device_scope,
        "device_id": device_id,
        "device_name": device_name,
        "session_kind": session_kind,
        "path": path,
    }


def _session_meta_needs_quick_refresh(row):
    if not isinstance(row, dict):
        return True
    channel_id = _normalize_usage_channel_id(row.get("channel_id"), "launcher")
    session_kind = str(row.get("session_kind") or "").strip().lower()
    if session_kind == "channel_process" and channel_id == "launcher":
        return True
    if channel_id != "launcher":
        return False
    title = str(row.get("title") or "")
    if ("进程" in title) or ("channel process" in title.lower()):
        return True
    return False


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
    index = _load_sessions_index(agent_dir)
    changed_index = False
    out = []
    present_ids = set()
    for fn in os.listdir(d):
        if not fn.endswith(".json") or fn.startswith("."):
            continue
        fp = os.path.join(d, fn)
        sid = fn[:-5]
        if sid:
            present_ids.add(sid)
        indexed = index.get(sid)
        payload = None
        if isinstance(indexed, dict):
            row = dict(indexed)
            row["id"] = str(row.get("id") or sid)
            row["path"] = fp
            if _session_meta_needs_quick_refresh(row):
                quick = _quick_read_session_meta(fp, sid)
                if isinstance(quick, dict):
                    quick["path"] = fp
                    quick_channel = _normalize_usage_channel_id(quick.get("channel_id"), "launcher")
                    row_channel = _normalize_usage_channel_id(row.get("channel_id"), "launcher")
                    quick_kind = str(quick.get("session_kind") or "").strip().lower()
                    row_kind = str(row.get("session_kind") or "").strip().lower()
                    if (quick_channel != row_channel) or (quick_kind != row_kind):
                        row = quick
                        index[sid] = row
                        changed_index = True
            out.append(row)
            continue
        meta_fp = _session_meta_path_for_file(fp)
        cached = _load_session_meta_file(meta_fp)
        if isinstance(cached, dict):
            row = dict(cached)
            row["id"] = str(row.get("id") or sid)
            row["path"] = fp
            out.append(row)
            index[sid] = row
            changed_index = True
            continue
        quick = _quick_read_session_meta(fp, sid)
        if isinstance(quick, dict):
            out.append(quick)
            index[sid] = quick
            changed_index = True
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            row = _session_meta_from_payload(payload, sid=sid, path=fp)
            out.append(row)
            index[sid] = row
            changed_index = True
            continue
    stale_ids = [sid for sid in list(index.keys()) if sid not in present_ids]
    if stale_ids:
        for sid in stale_ids:
            index.pop(sid, None)
        changed_index = True
    if changed_index:
        try:
            _save_sessions_index(agent_dir, index)
        except Exception:
            pass
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
    try:
        _sync_session_meta_for_file(fp, session)
    except Exception:
        pass


def delete_session(agent_dir, sid):
    fp = os.path.join(sessions_dir(agent_dir), f"{sid}.json")
    delete_session_file(fp)
    meta_fp = _session_meta_path(agent_dir, sid)
    if meta_fp and os.path.isfile(meta_fp):
        try:
            os.remove(meta_fp)
        except Exception:
            pass
    try:
        _remove_session_index_row(agent_dir, sid)
    except Exception:
        pass


def delete_session_file(fp):
    if os.path.isfile(fp):
        try:
            os.remove(fp)
        except Exception:
            pass
    meta_fp = _session_meta_path_for_file(fp)
    if meta_fp and os.path.isfile(meta_fp):
        try:
            os.remove(meta_fp)
        except Exception:
            pass
    agent_dir = _agent_dir_from_session_file(fp)
    if agent_dir:
        try:
            sid = os.path.splitext(os.path.basename(str(fp or "").strip()))[0]
            if sid:
                _remove_session_index_row(agent_dir, sid)
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


def usage_input_side_tokens(item):
    row = item if isinstance(item, dict) else {}
    if "input_side_tokens" in row:
        return _safe_int(row.get("input_side_tokens"))
    return (
        _safe_int(row.get("input_tokens"))
        + _safe_int(row.get("cache_creation_input_tokens"))
        + _safe_int(row.get("cache_read_input_tokens"))
    )


def usage_total_consumed_tokens(item):
    row = item if isinstance(item, dict) else {}
    if "usage_total_tokens" in row:
        return _safe_int(row.get("usage_total_tokens"))
    return usage_input_side_tokens(row) + _safe_int(row.get("output_tokens"))


def usage_cache_hit_rate(item):
    row = item if isinstance(item, dict) else {}
    try:
        if "cache_hit_rate" in row:
            return max(0.0, float(row.get("cache_hit_rate") or 0.0))
    except Exception:
        pass
    input_side = usage_input_side_tokens(row)
    if input_side <= 0:
        return 0.0
    return round((_safe_int(row.get("cache_read_input_tokens")) / float(input_side)) * 100.0, 4)


def summarize_usage_rows(rows):
    summary = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_side_tokens": 0,
        "usage_total_tokens": 0,
        "cached_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "api_calls": 0,
        "event_count": 0,
    }
    for row in list(rows or []):
        if not isinstance(row, dict):
            continue
        summary["input_tokens"] += _safe_int(row.get("input_tokens"))
        summary["output_tokens"] += _safe_int(row.get("output_tokens"))
        summary["total_tokens"] += _safe_int(row.get("total_tokens"))
        summary["input_side_tokens"] += usage_input_side_tokens(row)
        summary["usage_total_tokens"] += usage_total_consumed_tokens(row)
        summary["cached_tokens"] += _safe_int(row.get("cached_tokens"))
        summary["cache_creation_input_tokens"] += _safe_int(row.get("cache_creation_input_tokens"))
        summary["cache_read_input_tokens"] += _safe_int(row.get("cache_read_input_tokens"))
        summary["api_calls"] += _safe_int(row.get("api_calls"))
        summary["event_count"] += 1
    summary["cache_hit_rate"] = usage_cache_hit_rate(summary)
    return summary


def summarize_session_usage(session):
    data = session if isinstance(session, dict) else {}
    usage = data.get("token_usage") if isinstance(data.get("token_usage"), dict) else {}
    events = list(usage.get("events") or [])
    if not events:
        events = _fallback_token_events_from_bubbles(
            data.get("bubbles") or [],
            base_ts=data.get("updated_at") or data.get("created_at") or time.time(),
            channel_id=_normalize_usage_channel_id(data.get("channel_id"), "launcher"),
            model_name=str(usage.get("last_model") or "").strip(),
        )
    summary = summarize_usage_rows(events)
    summary["turns"] = sum(1 for ev in events if _safe_int((ev or {}).get("input_tokens")) > 0)
    summary["mode"] = _usage_mode_from_sources(
        {
            str((ev or {}).get("usage_source") or "estimate").strip().lower() or "estimate"
            for ev in events
            if isinstance(ev, dict)
        }
    )
    summary["last_model"] = str(usage.get("last_model") or "").strip()
    summary["channel_id"] = _normalize_usage_channel_id(data.get("channel_id") or usage.get("channel_id"), "launcher")
    summary["channel_label"] = _usage_channel_label(summary["channel_id"])
    summary["session_id"] = str(data.get("id") or "").strip()
    summary["title"] = str(data.get("title") or "").strip()
    summary["last_active"] = float(data.get("updated_at") or data.get("created_at") or 0)
    return summary


def _normalize_token_usage_inplace(session):
    if not isinstance(session, dict):
        return session
    default_channel = _normalize_usage_channel_id(session.get("channel_id"), "launcher")
    usage = session.get("token_usage")
    if not isinstance(usage, dict):
        usage = {}
    usage_cleared = bool(usage.get("launcher_usage_cleared"))
    events = usage.get("events")
    if not isinstance(events, list):
        events = []

    normalized_events = []
    default_scope = str(session.get("device_scope") or "local").strip().lower()
    if default_scope not in ("local", "remote"):
        default_scope = "local"
    default_device_id = str(session.get("device_id") or "").strip() if default_scope == "remote" else "local"
    if default_scope == "remote" and not default_device_id:
        default_scope = "local"
        default_device_id = "local"
    usage_currency = normalize_usage_currency(usage.get("currency") or "USD")
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            ts = float(ev.get("ts", session.get("updated_at", time.time())) or time.time())
        except Exception:
            ts = time.time()
        inp = _safe_int(ev.get("input_tokens"))
        out = _safe_int(ev.get("output_tokens"))
        row_scope = str(ev.get("device_scope") or ev.get("target_scope") or default_scope).strip().lower()
        if row_scope not in ("local", "remote"):
            row_scope = default_scope
        row_device_id = str(ev.get("device_id") or ev.get("target_device_id") or default_device_id).strip() if row_scope == "remote" else "local"
        if row_scope == "remote" and not row_device_id:
            row_scope = "local"
            row_device_id = "local"
        row = {
            "ts": ts,
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": _safe_int(ev.get("total_tokens"), inp + out) or (inp + out),
            "channel_id": _normalize_usage_channel_id(ev.get("channel_id"), default_channel),
            "model": str(ev.get("model") or "").strip(),
            "usage_source": str(ev.get("usage_source") or "estimate").strip().lower() or "estimate",
            "cached_tokens": _safe_int(ev.get("cached_tokens")),
            "cache_creation_input_tokens": _safe_int(ev.get("cache_creation_input_tokens")),
            "cache_read_input_tokens": _safe_int(ev.get("cache_read_input_tokens")),
            "api_calls": _safe_int(ev.get("api_calls")),
            "device_scope": row_scope,
            "device_id": row_device_id,
            "target_key": usage_pricing_target_key(row_scope, row_device_id),
            "llm_idx": _safe_int(ev.get("llm_idx")) if "llm_idx" in ev else None,
            "api_card_var": str(ev.get("api_card_var") or "").strip(),
            "api_card_label": str(ev.get("api_card_label") or ev.get("api_card_var") or "").strip(),
            "billing_mode": str(ev.get("billing_mode") or "").strip().lower(),
        }
        if row["llm_idx"] is None:
            row.pop("llm_idx", None)
        snapshot = ev.get("price_snapshot") if isinstance(ev.get("price_snapshot"), dict) else None
        if snapshot:
            snap = dict(snapshot)
            snap["currency"] = normalize_usage_currency(snap.get("currency") or ev.get("currency") or usage_currency)
            row["price_snapshot"] = snap
            row["currency"] = snap["currency"]
        elif ev.get("currency"):
            row["currency"] = normalize_usage_currency(ev.get("currency"))
        cost_input = _safe_cost(ev.get("cost_input"))
        cost_output = _safe_cost(ev.get("cost_output"))
        cost_cache_read = _safe_cost(ev.get("cost_cache_read"))
        cost_cache_creation = _safe_cost(ev.get("cost_cache_creation"))
        has_cost_fields = any(key in ev for key in ("cost_input", "cost_output", "cost_cache_read", "cost_cache_creation", "cost_total"))
        if has_cost_fields:
            row["cost_input"] = cost_input
            row["cost_output"] = cost_output
            row["cost_cache_read"] = cost_cache_read
            row["cost_cache_creation"] = cost_cache_creation
            row["cost_total"] = _safe_cost(ev.get("cost_total"), cost_input + cost_output + cost_cache_read + cost_cache_creation)
        elif snapshot:
            apply_usage_price_snapshot(row, row.get("price_snapshot"))
        if usage_event_is_priced(row):
            row["billing_mode"] = row.get("billing_mode") or "priced"
            row["currency"] = normalize_usage_currency(row.get("currency") or usage_currency)
        else:
            row["billing_mode"] = row.get("billing_mode") or "legacy_unpriced"
        row["input_side_tokens"] = usage_input_side_tokens(row)
        row["usage_total_tokens"] = usage_total_consumed_tokens(row)
        row["cache_hit_rate"] = usage_cache_hit_rate(row)
        normalized_events.append(row)

    if (not normalized_events) and (not usage_cleared) and str(session.get("session_kind") or "").strip().lower() != "channel_process":
        normalized_events = _fallback_token_events_from_bubbles(
            session.get("bubbles") or [],
            base_ts=session.get("created_at") or session.get("updated_at") or time.time(),
            channel_id=default_channel,
            model_name=str(usage.get("last_model") or "").strip(),
        )

    input_tokens = sum(int(ev.get("input_tokens", 0) or 0) for ev in normalized_events)
    output_tokens = sum(int(ev.get("output_tokens", 0) or 0) for ev in normalized_events)
    input_side_tokens = sum(usage_input_side_tokens(ev) for ev in normalized_events)
    usage_total_tokens = sum(usage_total_consumed_tokens(ev) for ev in normalized_events)
    cache_creation_input_tokens = sum(int(ev.get("cache_creation_input_tokens", 0) or 0) for ev in normalized_events)
    cache_read_input_tokens = sum(int(ev.get("cache_read_input_tokens", 0) or 0) for ev in normalized_events)
    cost_input = _safe_cost(sum(float(ev.get("cost_input", 0) or 0) for ev in normalized_events))
    cost_output = _safe_cost(sum(float(ev.get("cost_output", 0) or 0) for ev in normalized_events))
    cost_cache_read = _safe_cost(sum(float(ev.get("cost_cache_read", 0) or 0) for ev in normalized_events))
    cost_cache_creation = _safe_cost(sum(float(ev.get("cost_cache_creation", 0) or 0) for ev in normalized_events))
    cost_total = _safe_cost(sum(float(ev.get("cost_total", 0) or 0) for ev in normalized_events))
    priced_event_count = sum(1 for ev in normalized_events if usage_event_is_priced(ev))
    estimated_priced_event_count = sum(
        1 for ev in normalized_events
        if usage_event_is_priced(ev) and str(ev.get("usage_source") or "").strip().lower() != "provider"
    )
    legacy_unpriced_event_count = max(0, len(normalized_events) - priced_event_count)
    currency_totals = {}
    for ev in normalized_events:
        if not usage_event_is_priced(ev):
            continue
        ev_cost = _safe_cost(ev.get("cost_total"))
        if ev_cost == 0:
            continue
        ev_currency = normalize_usage_currency(ev.get("currency") or (ev.get("price_snapshot") or {}).get("currency") or usage_currency)
        currency_totals[ev_currency] = _safe_cost(currency_totals.get(ev_currency, 0) + ev_cost)
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
        "input_side_tokens": input_side_tokens,
        "usage_total_tokens": usage_total_tokens,
        "turns": sum(1 for ev in normalized_events if int(ev.get("input_tokens", 0) or 0) > 0),
        "events": normalized_events,
        "channel_id": default_channel,
        "channel_label": _usage_channel_label(default_channel),
        "last_model": str(usage.get("last_model") or "").strip(),
        "api_calls": sum(int(ev.get("api_calls", 0) or 0) for ev in normalized_events),
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "cache_hit_rate": usage_cache_hit_rate(
            {
                "input_side_tokens": input_side_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
            }
        ),
        "currency": usage_currency,
        "cost_input": cost_input,
        "cost_output": cost_output,
        "cost_cache_read": cost_cache_read,
        "cost_cache_creation": cost_cache_creation,
        "cost_total": cost_total,
        "currency_totals": currency_totals,
        "mixed_currency": len(currency_totals) > 1,
        "priced_event_count": priced_event_count,
        "estimated_priced_event_count": estimated_priced_event_count,
        "legacy_unpriced_event_count": legacy_unpriced_event_count,
    }
    if usage_cleared and not normalized_events:
        usage["launcher_usage_cleared"] = True
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
