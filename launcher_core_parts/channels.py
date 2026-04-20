from __future__ import annotations

import ast as _ast
import importlib.util as _il_util
import os

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
