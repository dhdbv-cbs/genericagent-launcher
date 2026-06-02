from __future__ import annotations

import ast as _ast
import importlib.util as _il_util
import json
import os
import re

from . import conductor_runtime as _conductor_runtime

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
    "langfuse_config",
    "tg_bot_token",
    "tg_allowed_users",
    "discord_bot_token",
    "discord_allowed_users",
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


COMM_CHANNEL_SPECS: list[dict[str, object]] = [
    {
        "id": "wechat",
        "label": "微信",
        "subtitle": "个人微信扫码登录",
        "script": "wechatapp.py",
        "log_name": "wechatapp.log",
        "pip": "pycryptodome qrcode requests charset-normalizer Pillow",
        "fields": [],
        "required": [],
        "notes": "无需在 mykey.py 填 Key。首次启动会弹二维码完成绑定。",
        "conflicts_with": [],
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
        "id": "discord",
        "label": "Discord",
        "subtitle": "Discord Bot",
        "script": "dcapp.py",
        "log_name": "dcapp.log",
        "pip": "discord.py",
        "fields": [
            {"key": "discord_bot_token", "label": "Bot Token", "kind": "password", "placeholder": "Discord bot token"},
            {"key": "discord_allowed_users", "label": "允许用户", "kind": "list_str", "placeholder": "user_id，逗号分隔；可填 *"},
        ],
        "required": ["discord_bot_token"],
        "notes": "需要在 Discord Developer Portal 开启 Message Content Intent。",
        "conflicts_with": [],
    },
    {
        "id": "tui",
        "label": "终端 TUI",
        "subtitle": "TUI v3 / Textual v2 终端会话入口",
        "script": "tui_v3.py",
        "script_candidates": ["tui_v3.py", "tuiapp_v2.py", "tuiapp.py"],
        "log_name": "tuiapp.log",
        "pip": "prompt_toolkit rich Pillow textual",
        "launch_mode": "terminal",
        "fields": [],
        "required": [],
        "notes": "优先打开上游最新的 tui_v3.py；旧版仓库会自动回退到 tuiapp_v2.py / tuiapp.py。",
        "conflicts_with": [],
    },
    {
        "id": "conductor",
        "label": "Conductor 总管台",
        "subtitle": "本机子 Agent 编排网页控制台",
        "script": "conductor.py",
        "log_name": "conductor.log",
        "pip": "fastapi uvicorn[standard] pydantic",
        "launch_mode": "web",
        "local_only": True,
        "web_url": "http://127.0.0.1:8900/",
        "fields": [],
        "required": [],
        "notes": "启动后会托管本地网页控制台，并由上游脚本自动尝试打开浏览器；当前仅支持启动器本机使用，不支持远端托管。",
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
        "notes": "建议按上游最新文档配置开放平台权限与回调。",
        "conflicts_with": [],
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
        "pip": "dingtalk-stream>=0.20",
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

COMM_CHANNEL_INDEX: dict[str, dict[str, object]] = {
    str(spec.get("id") or ""): spec for spec in COMM_CHANNEL_SPECS
}


def _channel_spec_row(channel_or_spec):
    if isinstance(channel_or_spec, dict):
        return dict(channel_or_spec)
    cid = str(channel_or_spec or "").strip()
    if not cid:
        return {}
    row = COMM_CHANNEL_INDEX.get(cid)
    if row:
        return dict(row)
    for spec in COMM_CHANNEL_SPECS:
        if str((spec or {}).get("id") or "").strip() == cid:
            return dict(spec)
    return {}


def channel_script_candidates(channel_or_spec):
    spec = _channel_spec_row(channel_or_spec)
    seen = set()
    out = []
    for raw in [spec.get("script"), *(spec.get("script_candidates") or [])]:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def resolve_channel_script(channel_or_spec, agent_dir="", *, existing_only=False):
    candidates = channel_script_candidates(channel_or_spec)
    if not candidates:
        return ""
    root = str(agent_dir or "").strip()
    if root:
        frontends_dir = os.path.join(root, "frontends")
        for name in candidates:
            if os.path.isfile(os.path.join(frontends_dir, name)):
                return name
    return "" if existing_only else candidates[0]


def channel_script_rel(channel_or_spec, agent_dir="", *, existing_only=False):
    name = resolve_channel_script(channel_or_spec, agent_dir=agent_dir, existing_only=existing_only)
    return (("frontends/" + name).replace("\\", "/")) if name else ""


def channel_script_rel_candidates(channel_or_spec):
    return [(("frontends/" + name).replace("\\", "/")) for name in channel_script_candidates(channel_or_spec)]


def channel_script_path(agent_dir, channel_or_spec, *, existing_only=False):
    root = str(agent_dir or "").strip()
    spec = COMM_CHANNEL_INDEX.get(channel_or_spec, {}) if isinstance(channel_or_spec, str) else dict(channel_or_spec or {})
    if str(spec.get("id") or "").strip().lower() == "conductor":
        return str((_conductor_runtime.ensure_launcher_conductor_runtime() or {}).get("script") or "").strip()
    if not root:
        return ""
    name = resolve_channel_script(channel_or_spec, agent_dir=root, existing_only=existing_only)
    return os.path.join(root, "frontends", name) if name else ""


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
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
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
        if name in values and name in EXTRA_KEYS:
            out["extras"][name] = values[name]
        elif name in values and _is_config_var(name, values[name]):
            out["configs"].append({"var": name, "kind": _classify_config_kind(name), "data": dict(values[name])})
        elif name in values and _is_passthrough_var(name, values[name]):
            out["passthrough"].append({"name": name, "value": values[name]})
    for name, v in values.items():
        if name in seen:
            continue
        if name in EXTRA_KEYS:
            out["extras"][name] = v
        elif _is_config_var(name, v):
            out["configs"].append({"var": name, "kind": _classify_config_kind(name), "data": dict(v)})
        elif _is_passthrough_var(name, v):
            out["passthrough"].append({"name": name, "value": v})
    return out


def parse_mykey_json(path):
    out = {"configs": [], "extras": {}, "passthrough": [], "error": None}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            values = json.load(f)
    except Exception as e:
        out["error"] = f"JSON 解析失败: {e}"
        return out
    if not isinstance(values, dict):
        out["error"] = "JSON 根节点必须是对象。"
        return out
    for name, value in values.items():
        if name in EXTRA_KEYS:
            out["extras"][name] = value
        elif _is_config_var(name, value):
            out["configs"].append({"var": name, "kind": _classify_config_kind(name), "data": dict(value)})
        elif _is_passthrough_var(name, value):
            out["passthrough"].append({"name": name, "value": value})
    return out


def parse_mykey_source(path):
    suffix = os.path.splitext(str(path or "").strip())[1].lower()
    if suffix == ".json":
        return parse_mykey_json(path)
    return parse_mykey_py(path)


def resolve_mykey_source_path(agent_dir):
    root = str(agent_dir or "").strip()
    py_path = os.path.join(root, "mykey.py")
    json_path = os.path.join(root, "mykey.json")
    if os.path.isfile(py_path):
        return py_path
    if os.path.isfile(json_path):
        return json_path
    return py_path


_FIELD_ORDER = [
    "name",
    "apikey",
    "apibase",
    "model",
    "api_mode",
    "fake_cc_system_prompt",
    "user_agent",
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

    group_titles = {
        "mixin": "# ── Mixin 故障转移 ───────────────────────────────────────────────",
        "native_claude": "# ── NativeClaudeSession 渠道 ──────────────────────────────────────",
        "native_oai": "# ── NativeOAISession 渠道 ─────────────────────────────────────────",
        "claude": "# ── ClaudeSession 渠道 (deprecated) ───────────────────────────────",
        "oai": "# ── LLMSession 渠道 (deprecated) ──────────────────────────────────",
        "unknown": "# ── 其它 ─────────────────────────────────────────────────────────",
    }

    parts = [header]
    last_kind = None
    for c in configs:
        kind = str(c.get("kind") or "unknown").strip() or "unknown"
        if kind not in group_titles:
            kind = "unknown"
        if kind != last_kind:
            parts.append("\n" + group_titles[kind] + "\n")
            last_kind = kind
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
            "langfuse_config",
            "tg_bot_token",
            "tg_allowed_users",
            "discord_bot_token",
            "discord_allowed_users",
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


def validate_api_config_references(configs):
    rows = [dict(item) for item in (configs or []) if isinstance(item, dict)]
    session_names = []
    session_names_set = set()
    for row in rows:
        kind = str(row.get("kind") or "").strip()
        if kind == "mixin":
            continue
        data = dict(row.get("data") or {})
        name = str(data.get("name") or "").strip()
        if not name or name in session_names_set:
            continue
        session_names.append(name)
        session_names_set.add(name)

    errors = []
    for row in rows:
        kind = str(row.get("kind") or "").strip()
        if kind != "mixin":
            continue
        var_name = str(row.get("var") or "mixin_config").strip() or "mixin_config"
        data = dict(row.get("data") or {})
        llm_nos = data.get("llm_nos")
        if not isinstance(llm_nos, (list, tuple)) or not llm_nos:
            errors.append(f"{var_name}.llm_nos 不能为空。")
            continue
        for idx, target in enumerate(llm_nos):
            if isinstance(target, int):
                if 0 <= int(target) < len(session_names):
                    continue
                errors.append(
                    f"{var_name}.llm_nos[{idx}]={target} 超出可用会话范围；当前可引用 {len(session_names)} 个非 mixin API 会话。"
                )
                continue
            target_name = str(target or "").strip()
            if not target_name:
                errors.append(f"{var_name}.llm_nos[{idx}] 不能为空字符串。")
                continue
            if target_name not in session_names_set:
                available = "、".join(session_names) if session_names else "（无）"
                errors.append(
                    f"{var_name}.llm_nos[{idx}]={target_name!r} 找不到同名 API 会话；当前可引用 name 为：{available}。"
                )
    return errors


def validate_runnable_api_configs(configs):
    rows = [dict(item) for item in (configs or []) if isinstance(item, dict)]
    errors = [str(err or "").strip() for err in validate_api_config_references(rows) if str(err or "").strip()]
    runnable_count = 0
    incomplete = []
    for row in rows:
        kind = str(row.get("kind") or "").strip().lower()
        if kind == "mixin":
            continue
        data = dict(row.get("data") or {})
        ident = str(data.get("name") or row.get("var") or "未命名配置").strip() or "未命名配置"
        missing = []
        for key in ("apikey", "apibase"):
            if not str(data.get(key) or "").strip():
                missing.append(key)
        if missing:
            incomplete.append(f"{ident} 缺少 {' / '.join(missing)}")
            continue
        runnable_count += 1
    if runnable_count > 0:
        return errors
    if incomplete:
        errors.append("当前没有可直接运行的非 mixin API 会话：" + "；".join(incomplete))
    else:
        errors.append("当前没有可直接运行的非 mixin API 会话。请先填写至少一条模型配置。")
    return errors


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


def sync_config_var_kind(kind, current_var, existing_vars):
    current = str(current_var or "").strip()
    existing = {str(name or "").strip() for name in (existing_vars or ()) if str(name or "").strip()}
    if current:
        existing.discard(current)
    if current and _classify_config_kind(current) == kind and current not in existing:
        return current
    base = {
        "native_claude": "native_claude_config",
        "native_oai": "native_oai_config",
        "mixin": "mixin_config",
        "claude": "claude_config",
        "oai": "oai_config",
    }.get(kind, "config")
    match = re.search(r"(\d+)$", current)
    if match:
        candidate = f"{base}{match.group(1)}"
        if candidate not in existing:
            return candidate
    return auto_config_var(kind, existing)


CHANNEL_TEMPLATES: list[tuple[str, str, str, dict[str, object]]] = [
    ("anthropic", "Anthropic 官方", "native_claude", {"apibase": "https://api.anthropic.com", "model": "claude-opus-4-7[1m]"}),
    ("cc-switch", "CC Switch / 反代中转", "native_claude", {"apibase": "", "model": "claude-opus-4-7", "fake_cc_system_prompt": True}),
    ("crs-claude", "CRS 反代 Claude Max", "native_claude", {"apibase": "", "model": "claude-opus-4-7[1m]", "fake_cc_system_prompt": True, "max_tokens": 32768, "read_timeout": 180}),
    ("crs-gemini", "CRS Gemini Ultra", "native_claude", {"apibase": "", "model": "claude-opus-4-7-thinking", "stream": False, "max_tokens": 32768, "read_timeout": 180}),
    ("glm", "智谱 GLM-5.1", "native_claude", {"apibase": "https://open.bigmodel.cn/api/anthropic", "model": "glm-5.1"}),
    ("minimax-anth", "MiniMax (Anthropic 路径)", "native_claude", {"apibase": "https://api.minimaxi.com/anthropic", "model": "MiniMax-M2.7"}),
    ("oai-generic", "通用 OAI 原生", "native_oai", {"apibase": "", "model": "gpt-5.4"}),
    ("openai", "OpenAI 官方", "native_oai", {"apibase": "https://api.openai.com/v1", "model": "gpt-5.4"}),
    ("openrouter", "OpenRouter", "native_oai", {"apibase": "https://openrouter.ai/api/v1", "model": "anthropic/claude-opus-4-7"}),
    (
        "commonstack",
        "CommonStack 统一网关",
        "native_oai",
        {
            "apibase": "https://api.commonstack.ai/v1",
            "model": "anthropic/claude-opus-4-7",
            "api_mode": "chat_completions",
            "max_retries": 3,
            "connect_timeout": 10,
            "read_timeout": 120,
        },
    ),
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
    "mixin": "Mixin 故障转移",
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
        "templates": ["openai", "oai-generic", "openrouter", "commonstack", "minimax-oai", "kimi", "custom-oai"],
        "hint": "走 OpenAI Chat Completions 协议。",
    },
    "oai_responses": {
        "kind": "native_oai",
        "api_mode": "responses",
        "default_template": "openai",
        "templates": ["openai", "oai-generic", "openrouter", "minimax-oai", "kimi", "custom-oai"],
        "hint": "走 OpenAI Responses 协议。",
    },
    "mixin": {
        "kind": "mixin",
        "api_mode": None,
        "default_template": "mixin",
        "templates": ["mixin"],
        "hint": "按 llm_nos 顺序引用已有非 mixin API 卡片，启动时按顺序故障转移。",
    },
}

TEMPLATE_MANAGED_KEYS = {field for meta in TEMPLATE_INDEX.values() for field in meta.get("defaults", {}) if field != "apibase"}
