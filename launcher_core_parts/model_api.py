from __future__ import annotations

import json
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .channels import SIMPLE_FORMAT_RULES


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
