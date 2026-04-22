from __future__ import annotations

import json
import importlib.util
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
    merged_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "GenericAgentLauncher/0.1",
    }
    merged_headers.update(dict(headers or {}))
    req = Request(url, headers=merged_headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def _extract_model_id(item):
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(
            item.get("id")
            or item.get("name")
            or item.get("model")
            or item.get("model_id")
            or ""
        ).strip()
    return ""


def _extract_model_ids(payload):
    out = []
    seen = set()

    def add(model_id):
        text = str(model_id or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    def walk(node, depth=0):
        if depth > 3:
            return
        if isinstance(node, list):
            for item in node:
                add(_extract_model_id(item))
            for item in node:
                if isinstance(item, (list, dict)):
                    walk(item, depth + 1)
            return
        if isinstance(node, dict):
            for key in ("data", "models", "items", "results", "result", "output"):
                child = node.get(key)
                if isinstance(child, (list, dict)):
                    walk(child, depth + 1)

    walk(payload)
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


def _oai_models_candidates(apibase):
    raw = (apibase or "").strip()
    if not raw:
        return []
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    root = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    path = _strip_known_api_suffix(parsed.path or "").rstrip("/")
    out = []
    candidates = []
    if path:
        candidates.append(_join_url(root + path, "/models"))
        if path != "/v1":
            candidates.append(_join_url(root + path, "/v1/models"))
    candidates.extend((
        _join_url(root, "/v1/models"),
        _join_url(root, "/models"),
    ))
    for candidate in candidates:
        if candidate not in out:
            out.append(candidate)
    return out


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


def _header_variants(kind, apikey):
    key = str(apikey or "").strip()
    if not key:
        if kind == "native_claude":
            return [{"anthropic-version": "2023-06-01"}, {}]
        return [{}]

    variants = []

    def add(headers):
        payload = dict(headers or {})
        if payload not in variants:
            variants.append(payload)

    if kind == "native_claude":
        add({"x-api-key": key, "anthropic-version": "2023-06-01"})
        add({"Authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"})
        add({"api-key": key, "anthropic-version": "2023-06-01"})
        add({"x-api-key": key})
        add({"Authorization": f"Bearer {key}"})
        add({"api-key": key})
        return variants

    add({"Authorization": f"Bearer {key}"})
    add({"x-api-key": key})
    add({"api-key": key})
    return variants


def _fetch_remote_models(format_key, apibase, apikey):
    base = (apibase or "").strip()
    fmt = SIMPLE_FORMAT_RULES.get(format_key) or SIMPLE_FORMAT_RULES["oai_chat"]
    kind = fmt.get("kind")
    if not base:
        raise ValueError("请先填写 URL，再拉取模型。")

    if kind == "native_oai":
        last_error = None
        for url in _oai_models_candidates(base):
            for headers in _header_variants("native_oai", apikey):
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
        raise ValueError("模型接口返回为空，可能该渠道不支持 /models。")

    last_error = None
    for url in _anthropic_models_candidates(base):
        for headers in _header_variants("native_claude", apikey):
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
