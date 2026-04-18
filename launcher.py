"""GenericAgent 启动器 - 现代化中文前端"""
import os, sys, json, subprocess, threading, queue, uuid, time, re
from datetime import datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import tkinter as tk
from tkinter import filedialog, messagebox
import requests, qrcode

try:
    import customtkinter as ctk
except ImportError:
    import tkinter as tk
    from tkinter import messagebox as mb
    r = tk.Tk(); r.withdraw()
    mb.showerror("缺少依赖", "请先安装 customtkinter：\n\npip install customtkinter")
    sys.exit(1)

from PIL import Image

REPO_URL = "https://github.com/lsdefine/GenericAgent"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_PATH = os.path.join(APP_DIR, "launcher_config.json")
WX_BOT_API = "https://ilinkai.weixin.qq.com"
WX_TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".wxbot", "token.json")


def _bridge_script_path():
    """定位 bridge.py（源码运行 / PyInstaller onefile 解压目录）"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "bridge.py")


def _find_system_python():
    """查找系统 Python（GenericAgent 的依赖在那里）。失败返回 None。"""
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
    for cmd in candidates:
        try:
            r = subprocess.run(
                cmd + ["-c", "import sys;print(sys.executable)"],
                capture_output=True, text=True, timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if r.returncode == 0:
                p = r.stdout.strip().splitlines()[-1].strip()
                if p and os.path.isfile(p):
                    return p
        except Exception:
            continue
    return None


def _probe_download_requirements():
    """探测下载/启动 GenericAgent 所需的关键环境。"""
    out = {
        "git_ok": False,
        "git_text": "未检测到 Git",
        "python_ok": False,
        "python_text": "未检测到系统 Python",
        "requests_ok": False,
        "requests_text": "无法检查 requests",
    }
    try:
        r = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode == 0:
            out["git_ok"] = True
            out["git_text"] = (r.stdout or r.stderr or "Git 已安装").strip().splitlines()[0]
    except Exception:
        pass

    py = _find_system_python()
    if not py:
        out["requests_text"] = "需先安装 Python 才能检查 requests"
        return out

    out["python_ok"] = True
    try:
        r = subprocess.run(
            [py, "-c", "import sys;print(sys.version.split()[0])"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        ver = (r.stdout or "").strip().splitlines()[-1].strip() if r.returncode == 0 else ""
    except Exception:
        ver = ""
    if ver:
        base = f"Python {ver}"
        if ver.startswith("3.14"):
            out["python_text"] = f"{base}（不推荐，建议改用 3.11 / 3.12）"
        elif ver.startswith("3.11") or ver.startswith("3.12"):
            out["python_text"] = f"{base}（推荐）"
        else:
            out["python_text"] = f"{base}（可用，推荐 3.11 / 3.12）"
    else:
        out["python_text"] = f"已找到 Python：{py}"

    try:
        r = subprocess.run(
            [py, "-c", "import requests;print(requests.__version__)"],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if r.returncode == 0:
            ver = (r.stdout or "").strip().splitlines()[-1].strip()
            out["requests_ok"] = True
            out["requests_text"] = f"requests {ver}" if ver else "requests 已安装"
        else:
            out["requests_text"] = "未安装 requests（首次启动前建议先 pip install requests）"
    except Exception:
        out["requests_text"] = "无法检查 requests（首次启动前建议先 pip install requests）"
    return out


def _ensure_mykey_file(agent_dir):
    """确保 GenericAgent 目录内存在 mykey.py / mykey.json。"""
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
            return raw[:-len(suffix)] or "/"
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
            model_id = str(
                item.get("id") or item.get("name") or item.get("model") or ""
            ).strip()
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

# ---------- 主题配置 ----------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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
COLOR_POPUP_BORDER = ("#d7deea", "#3a3f47")
COLOR_POPUP_BG = ("#ffffff", "#26292f")
COLOR_ACTIVE = ("#dbe7ff", "#2d3544")
COLOR_ACTIVE_HOVER = ("#cfdcf7", "#34405a")
COLOR_CHIP_BG = ("#dfe7f4", "#0d0f12")
COLOR_TEXT = ("#1f2937", "#e8ecf2")
COLOR_TEXT_SOFT = ("#3f4957", "#cfd4dc")
COLOR_SUCCESS = "#2fb170"
COLOR_MUTED = ("#6b7280", "#8a8f99")
COLOR_DIVIDER = ("#d7deea", "#3a3f47")
COLOR_DANGER_TEXT = ("#b94a4a", "#ea7070")
COLOR_DANGER_HOVER = ("#fde8e8", "#3a2222")
COLOR_DANGER_BG = ("#dc6666", "#c24848")
COLOR_DANGER_BG_HOVER = ("#c85757", "#a13a3a")
COLOR_DANGER_BG_DISABLED = ("#f3d2d2", "#5a3a3a")
COLOR_WARNING_TEXT = ("#b66a1e", "#f0b37a")
COLOR_OK_TEXT = ("#22835b", "#8fd3a7")
COLOR_ON_ACCENT = "#ffffff"
COLOR_INFO_BG = ("#eaf2ff", "#203044")
COLOR_INFO_TEXT = ("#255a97", "#d7e7ff")
COLOR_ERROR_BG = ("#fdecec", "#3a2222")
COLOR_ERROR_TEXT = ("#b94a4a", "#f0c0c0")
COLOR_CODE_BG = ("#f4f7fb", "#14161a")
COLOR_CODE_TEXT = ("#253041", "#dde1e7")


# ---------- 配置 ----------
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
    if not path or not os.path.isdir(path):
        return False
    return os.path.isfile(os.path.join(path, "launch.pyw")) and \
           os.path.isfile(os.path.join(path, "agentmain.py"))


# ---------- 会话存储 ----------
def sessions_dir(agent_dir):
    d = os.path.join(agent_dir, "temp", "launcher_sessions")
    os.makedirs(d, exist_ok=True)
    return d


def list_sessions(agent_dir):
    """返回按 置顶/更新时间 排序的会话元信息列表。"""
    d = sessions_dir(agent_dir)
    out = []
    for fn in os.listdir(d):
        if not fn.endswith(".json"): continue
        fp = os.path.join(d, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            out.append({
                "id": data.get("id") or fn[:-5],
                "title": data.get("title") or "(未命名)",
                "updated_at": data.get("updated_at", 0),
                "pinned": bool(data.get("pinned", False)),
                "path": fp,
            })
        except Exception:
            continue
    out.sort(key=lambda x: (x["pinned"], x["updated_at"]), reverse=True)
    return out


def load_session(agent_dir, sid):
    fp = os.path.join(sessions_dir(agent_dir), f"{sid}.json")
    if not os.path.isfile(fp): return None
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def save_session(agent_dir, session):
    session["updated_at"] = time.time()
    fp = os.path.join(sessions_dir(agent_dir), f"{session['id']}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def delete_session(agent_dir, sid):
    fp = os.path.join(sessions_dir(agent_dir), f"{sid}.json")
    if os.path.isfile(fp):
        try: os.remove(fp)
        except Exception: pass


def _blacklist_path(agent_dir):
    return os.path.join(sessions_dir(agent_dir), ".import_blacklist.json")


def load_import_blacklist(agent_dir):
    fp = _blacklist_path(agent_dir)
    if not os.path.isfile(fp): return set()
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return set(json.load(f) or [])
    except Exception:
        return set()


def save_import_blacklist(agent_dir, items):
    fp = _blacklist_path(agent_dir)
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(sorted(items), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[blacklist] {e}")


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
    return full_sig[-len(tail_sig):] == tail_sig


# ---------- mykey.py 解析 / 生成 ----------
import ast as _ast
import importlib.util as _il_util


def _classify_config_kind(var_name):
    n = (var_name or "").lower()
    if "mixin" in n: return "mixin"
    if "native" in n and "claude" in n: return "native_claude"
    if "native" in n and "oai" in n:    return "native_oai"
    if "claude" in n: return "claude"
    if "oai" in n:    return "oai"
    return "unknown"


KIND_LABEL = {
    "native_claude": "原生 Claude",
    "native_oai":    "原生 OpenAI",
    "mixin":         "Mixin 故障转移",
    "claude":        "Claude (文本协议)",
    "oai":           "OpenAI (文本协议)",
    "unknown":       "未知",
}


EXTRA_KEYS = {
    "proxy",
    "tg_bot_token", "tg_allowed_users",
    "qq_app_id", "qq_app_secret", "qq_allowed_users",
    "fs_app_id", "fs_app_secret", "fs_allowed_users",
    "wecom_bot_id", "wecom_secret", "wecom_allowed_users", "wecom_welcome_message",
    "dingtalk_client_id", "dingtalk_client_secret", "dingtalk_allowed_users",
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
    if name.startswith("_"): return False
    if not isinstance(value, dict): return False
    return (_looks_like_config_name(name) or
            ("apikey" in value) or ("llm_nos" in value) or
            ("apibase" in value) or ("model" in value))


def _is_passthrough_var(name, value):
    if name.startswith("_"): return False
    return ("cookie" in (name or "").lower()) and not isinstance(value, dict)


def parse_mykey_py(path):
    """解析 mykey.py。返回 {'configs': [...], 'extras': {...}, 'passthrough': [...], 'error': str|None}."""
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
            if isinstance(node, _ast.Assign) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], _ast.Name):
                order.append(node.targets[0].id)
    except Exception as e:
        out["error"] = f"语法解析失败: {e}"

    values = {}
    try:
        spec = _il_util.spec_from_loader("mykey_runtime", loader=None)
        mod = _il_util.module_from_spec(spec)
        exec(compile(src, path, "exec"), mod.__dict__)
        for k, v in mod.__dict__.items():
            if k.startswith("__"): continue
            values[k] = v
    except Exception as e:
        out["error"] = f"执行失败: {e}"
        return out

    seen = set()
    for name in order:
        if name in seen: continue
        seen.add(name)
        if name in values and _is_config_var(name, values[name]):
            out["configs"].append({
                "var": name,
                "kind": _classify_config_kind(name),
                "data": dict(values[name]),
            })
        elif name in values and _is_passthrough_var(name, values[name]):
            out["passthrough"].append({"name": name, "value": values[name]})
        elif name in values and name in EXTRA_KEYS:
            out["extras"][name] = values[name]
    for name, v in values.items():
        if name in seen: continue
        if _is_config_var(name, v):
            out["configs"].append({
                "var": name,
                "kind": _classify_config_kind(name),
                "data": dict(v),
            })
        elif _is_passthrough_var(name, v):
            out["passthrough"].append({"name": name, "value": v})
        elif name in EXTRA_KEYS:
            out["extras"][name] = v
    return out


_FIELD_ORDER = [
    "name", "apikey", "apibase", "model",
    "api_mode", "fake_cc_system_prompt",
    "thinking_type", "thinking_budget_tokens", "reasoning_effort",
    "temperature", "max_tokens",
    "stream",
    "max_retries", "connect_timeout", "read_timeout",
    "context_win", "proxy",
    "llm_nos", "base_delay", "spring_back",
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
    """按固定模板重新生成 mykey.py 源码。"""
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
        ("mixin",         "# ── Mixin 故障转移 ───────────────────────────────────────────────"),
        ("native_claude", "# ── NativeClaudeSession 渠道 ──────────────────────────────────────"),
        ("native_oai",    "# ── NativeOAISession 渠道 ─────────────────────────────────────────"),
        ("claude",        "# ── ClaudeSession 渠道 (deprecated) ───────────────────────────────"),
        ("oai",           "# ── LLMSession 渠道 (deprecated) ──────────────────────────────────"),
        ("unknown",       "# ── 其它 ─────────────────────────────────────────────────────────"),
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
            "tg_bot_token", "tg_allowed_users",
            "qq_app_id", "qq_app_secret", "qq_allowed_users",
            "fs_app_id", "fs_app_secret", "fs_allowed_users",
            "wecom_bot_id", "wecom_secret", "wecom_allowed_users", "wecom_welcome_message",
            "dingtalk_client_id", "dingtalk_client_secret", "dingtalk_allowed_users",
        ]
        for name in extra_order:
            if name in extras:
                parts.append(f"{name} = {extras[name]!r}\n")

    return "".join(parts)


def auto_config_var(kind, existing_vars):
    base = {
        "native_claude": "native_claude_config",
        "native_oai":    "native_oai_config",
        "mixin":         "mixin_config",
        "claude":        "claude_config",
        "oai":           "oai_config",
    }.get(kind, "config")
    if base not in existing_vars:
        return base
    i = 2
    while f"{base}{i}" in existing_vars:
        i += 1
    return f"{base}{i}"


CHANNEL_TEMPLATES = [
    ("anthropic",     "Anthropic 官方",           "native_claude",
        {"apibase": "https://api.anthropic.com", "model": "claude-opus-4-6[1m]"}),
    ("cc-switch",     "CC Switch / 反代中转",      "native_claude",
        {"apibase": "", "model": "claude-opus-4-6", "fake_cc_system_prompt": True}),
    ("crs-claude",    "CRS 反代 Claude Max",       "native_claude",
        {"apibase": "", "model": "claude-opus-4-6[1m]", "fake_cc_system_prompt": True,
         "max_tokens": 32768, "read_timeout": 180}),
    ("crs-gemini",    "CRS Gemini Ultra",          "native_claude",
        {"apibase": "", "model": "claude-opus-4-6-thinking", "stream": False,
         "max_tokens": 32768, "read_timeout": 180}),
    ("glm",           "智谱 GLM-5.1",              "native_claude",
        {"apibase": "https://open.bigmodel.cn/api/anthropic", "model": "glm-5.1"}),
    ("minimax-anth",  "MiniMax (Anthropic 路径)",  "native_claude",
        {"apibase": "https://api.minimaxi.com/anthropic", "model": "MiniMax-M2.7"}),
    ("oai-generic",   "通用 OAI 原生",             "native_oai",
        {"apibase": "", "model": "gpt-5.4"}),
    ("openai",        "OpenAI 官方",              "native_oai",
        {"apibase": "https://api.openai.com/v1", "model": "gpt-5.4"}),
    ("openrouter",    "OpenRouter",                "native_oai",
        {"apibase": "https://openrouter.ai/api/v1", "model": "anthropic/claude-opus-4-6"}),
    ("minimax-oai",   "MiniMax (OAI 路径)",        "native_oai",
        {"apibase": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.7",
         "context_win": 50000}),
    ("kimi",          "Kimi / Moonshot",           "native_oai",
        {"apibase": "https://api.moonshot.cn/v1", "model": "kimi-k2-turbo-preview"}),
    ("mixin",         "Mixin 故障转移",             "mixin",
        {"llm_nos": [], "max_retries": 10, "base_delay": 0.5}),
    ("custom-claude", "自定义 Native Claude",       "native_claude", {}),
    ("custom-oai",    "自定义 Native OAI",          "native_oai",    {}),
]


TEMPLATE_INDEX = {
    key: {"label": label, "kind": kind, "defaults": dict(defaults)}
    for key, label, kind, defaults in CHANNEL_TEMPLATES
}

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

TEMPLATE_MANAGED_KEYS = {
    field
    for meta in TEMPLATE_INDEX.values()
    for field in meta.get("defaults", {})
    if field != "apibase"
}


FIELD_HELP = {
    "name":    "这个渠道在界面里显示的名字；Mixin 的 llm_nos 也是靠它引用。建议填短而不重复的名字，例如 cc-relay-1、gpt-native。",
    "apikey":  "服务商给你的密钥。sk-ant- 开头通常按 Anthropic 官方方式发送；其它常见前缀（sk-、cr_、amp_* 等）一般按 Bearer 发送。",
    "apibase": "接口地址。最省事是直接填到域名/端口，或填到 /v1；GA 会自动补到正确路径。只有你已经拿到完整接口 URL 时才需要填完整。",
    "model":   "模型 ID，必须与服务商支持的名字一致。Claude 型号后面加 [1m]，表示请求 1m 上下文 beta。",
    "api_mode": "OpenAI / OAI 渠道用。大多数情况保持 chat_completions；只有服务商明确支持 Responses API 时再切到 responses。",
    "fake_cc_system_prompt": "只给 Claude Code 透传或镜像渠道打开。CC Switch、CRS、AnyRouter 这类通常要开；Anthropic 官方直连一般不要开。",
    "thinking_type": "Claude 的 thinking 模式。新手通常选 adaptive；想强制指定思考预算时选 enabled 并填写下面的 thinking_budget_tokens；完全不要 thinking 才选 disabled。",
    "thinking_budget_tokens": "只有 thinking_type=enabled 时才生效。可以粗略理解为 4096=低、10240=中、32768=高 思考预算。",
    "reasoning_effort": "推理强度等级。OpenAI o 系列 / Responses API 可直接使用；Claude Native 也会映射。拿不准时留空，或先用 medium。",
    "temperature": "生成随机度。越低越稳，越高越发散；多数渠道保持默认即可。如果你想更稳定、更少跑偏，可以适当调低。",
    "max_tokens":  "单次回复最多生成多少 token。调大能减少回答被截断，但通常也会更慢。",
    "stream":      "是否边生成边显示。开着时回复会实时流出；如果某个中转渠道流式经常报错或被 CDN 截断，可以先关掉试试。",
    "max_retries": "遇到 429、408、5xx 这类临时错误时，自动再试几次。中转渠道不稳定时可以适当调高。",
    "connect_timeout": "建立连接的超时时间，单位秒。",
    "read_timeout":    "等待服务端持续返回内容的超时时间，单位秒。大上下文、1m 上下文或慢渠道建议调到 180 甚至更高。",
    "context_win":     "历史裁剪阈值，不是模型真实的硬上下文上限。值越大，通常保留的历史越多。",
    "proxy":           "只让这个渠道单独走代理，例如 http://127.0.0.1:2082。留空表示这个渠道不单独走代理。",
    "llm_nos":         "Mixin 专用。按顺序填写要轮换的渠道 name，例如 cc-relay-1, cc-relay-2, gpt-native；前一个失败才会尝试下一个。",
    "base_delay":      "Mixin 重试的起始等待时间，单位秒；后续会按指数退避逐步变长。",
    "spring_back":     "切到备用渠道后，隔多久再尝试切回列表里的第一个渠道，单位秒。",
}


FIELD_KIND = {
    "name": "text", "apikey": "password", "apibase": "text", "model": "text",
    "api_mode": "enum:chat_completions,responses",
    "fake_cc_system_prompt": "bool",
    "thinking_type": "enum:,adaptive,enabled,disabled",
    "thinking_budget_tokens": "int",
    "reasoning_effort": "enum:,none,minimal,low,medium,high,xhigh",
    "temperature": "float", "max_tokens": "int",
    "stream": "bool",
    "max_retries": "int", "connect_timeout": "int", "read_timeout": "int",
    "context_win": "int", "proxy": "text",
    "llm_nos": "list", "base_delay": "float", "spring_back": "int",
}


KIND_FIELD_MAP = {
    "native_claude": {
        "basic":    ["name", "apikey", "apibase", "model"],
        "advanced": ["fake_cc_system_prompt", "thinking_type", "thinking_budget_tokens",
                     "reasoning_effort", "max_tokens", "temperature",
                     "max_retries", "connect_timeout", "read_timeout",
                     "stream", "context_win", "proxy"],
    },
    "native_oai": {
        "basic":    ["name", "apikey", "apibase", "model"],
        "advanced": ["api_mode", "reasoning_effort", "max_tokens", "temperature",
                     "max_retries", "connect_timeout", "read_timeout",
                     "context_win", "proxy"],
    },
    "mixin": {
        "basic":    ["llm_nos", "max_retries", "base_delay"],
        "advanced": ["spring_back"],
    },
    "claude": {
        "basic":    ["name", "apikey", "apibase", "model"],
        "advanced": ["context_win", "max_retries", "proxy"],
    },
    "oai": {
        "basic":    ["name", "apikey", "apibase", "model", "api_mode"],
        "advanced": ["max_retries", "read_timeout", "temperature",
                     "max_tokens", "context_win", "proxy"],
    },
    "unknown": {"basic": ["name"], "advanced": []},
}


# ---------- 回复内容折叠（与 stapp.py 保持一致） ----------
_TURN_RE = re.compile(r'(\**LLM Running \(Turn \d+\) \.\.\.\*\**)')
_CLEAN_BLOCK_RE = re.compile(
    r'(?P<fence>`{3,})[\s\S]*?(?P=fence)|<thinking>.*?</thinking>',
    re.DOTALL | re.IGNORECASE
)
_SUMMARY_RE = re.compile(r'<summary>\s*((?:(?!<summary>).)*?)\s*</summary>', re.DOTALL)


def fold_turns(text):
    """按 Turn 分段：非末段折叠，标题取 <summary>，否则取 marker 原文。"""
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
            _c = _CLEAN_BLOCK_RE.sub("", content)
            m = _SUMMARY_RE.search(_c)
            if m:
                title = m.group(1).strip().split("\n")[0]
                if len(title) > 50:
                    title = title[:50] + "..."
            else:
                title = marker.strip("*")
            segments.append({"type": "fold", "title": title, "content": content})
        else:
            segments.append({"type": "text", "content": marker + content})
    return segments


def legacy_session_needs_refresh(data):
    """旧版导入会话只保存了摘要气泡，需要重新按 richer parser 导入一次。"""
    if not data or not data.get("imported_from"):
        return False
    if int(data.get("legacy_restore_version", 0) or 0) >= 2:
        return False
    for bubble in data.get("bubbles", []):
        if bubble.get("role") != "assistant":
            continue
        text = bubble.get("text", "") or ""
        if "LLM Running (Turn " in text:
            return False
    return True


class FoldSection(ctk.CTkFrame):
    """可展开/收起的段落，用于历史 Turn。内部用富文本渲染。"""
    def __init__(self, master, title, content, wraplength,
                 text_color=COLOR_TEXT_SOFT, **kw):
        super().__init__(master, fg_color=COLOR_FIELD_ALT, corner_radius=8, **kw)
        self.expanded = False
        self.content_text = content
        self._wrap = wraplength
        self._title = title
        self._text_color = text_color
        self.head = ctk.CTkButton(
            self, text=f"▸  {title}",
            font=FONT_SMALL, anchor="w",
            fg_color=COLOR_CARD_HOVER, hover_color=COLOR_ACTIVE_HOVER,
            corner_radius=6, height=28,
            text_color=COLOR_TEXT_SOFT,
            command=self.toggle,
        )
        self.head.pack(fill="x", padx=2, pady=2)
        self._body_frame = None
        self._body_widgets = []

    def toggle(self):
        if self.expanded:
            if self._body_frame:
                self._body_frame.destroy()
                self._body_frame = None
                self._body_widgets = []
            self.head.configure(text=f"▸  {self._title}")
        else:
            self._body_frame = ctk.CTkFrame(self, fg_color="transparent")
            self._body_frame.pack(fill="x", padx=10, pady=(0, 8), anchor="w")
            self._body_widgets = render_rich(
                self._body_frame, self.content_text, self._wrap,
                text_color=self._text_color)
            self.head.configure(text=f"▾  {self._title}")
        self.expanded = not self.expanded

    def set_wrap(self, w):
        self._wrap = w
        for widget in self._body_widgets:
            if isinstance(widget, ctk.CTkLabel):
                try: widget.configure(wraplength=w)
                except Exception: pass


# ---------- 富文本分块渲染 ----------
_RE_THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
_RE_SUMMARY = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL | re.IGNORECASE)
_RE_TOOLUSE = re.compile(r"<tool_use>(.*?)</tool_use>", re.DOTALL | re.IGNORECASE)
_RE_FILE_CONTENT = re.compile(r"<file_content>(.*?)</file_content>", re.DOTALL | re.IGNORECASE)
_RE_CODE_BLOCK = re.compile(
    r"(?P<fence>`{3,})(?P<lang>[\w+\-]*)\n(?P<code>[\s\S]*?)\n(?P=fence)"
)


def _normalize_markup(text):
    """统一处理 GenericAgent 回复中的特殊标签，便于后续分块。"""
    if not text:
        return ""
    # 1) 去掉思考块
    text = _RE_THINKING.sub("", text)
    # 2) <summary>xxx</summary> → ▸ 引用行（用 markdown 风格的 > 前缀）
    text = _RE_SUMMARY.sub(lambda m: f"\n> {m.group(1).strip()}\n", text)
    # 3) <tool_use>/<file_content> → 包装成代码块
    text = _RE_TOOLUSE.sub(
        lambda m: f"\n```tool_use\n{m.group(1).strip()}\n```\n", text)
    text = _RE_FILE_CONTENT.sub(
        lambda m: f"\n```file_content\n{m.group(1).strip()}\n```\n", text)
    # 3×换行压缩
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_rich(parent, text, wraplength,
                text_color=COLOR_TEXT,
                quote_color=COLOR_MUTED,
                code_fg=COLOR_CODE_TEXT,
                code_bg=COLOR_CODE_BG):
    """把 text 按代码块/引用行/普通文本切块渲染。返回 widget 列表便于 resize。"""
    widgets = []
    t = _normalize_markup(text)
    if not t:
        return widgets

    def render_text_chunk(chunk):
        lines = chunk.strip("\n").split("\n") if chunk.strip() else []
        buf = []

        def flush():
            if not buf:
                return
            para = "\n".join(buf).strip()
            if para:
                lbl = ctk.CTkLabel(parent, text=para, font=FONT_BODY,
                                   wraplength=wraplength, justify="left",
                                   anchor="w", text_color=text_color)
                lbl.pack(fill="x", anchor="w", pady=(2, 4))
                widgets.append(lbl)
            buf.clear()

        for ln in lines:
            if ln.lstrip().startswith(">"):
                flush()
                q = ln.lstrip()[1:].strip()
                if q:
                    qf = ctk.CTkFrame(parent, fg_color=COLOR_CARD_HOVER,
                                      corner_radius=6)
                    qf.pack(fill="x", anchor="w", pady=2)
                    qlbl = ctk.CTkLabel(
                        qf, text=f"❝ {q}",
                        font=("Microsoft YaHei UI", 11, "italic"),
                        wraplength=max(180, wraplength - 24),
                        justify="left", anchor="w",
                        text_color=quote_color)
                    qlbl.pack(fill="x", anchor="w", padx=10, pady=6)
                    widgets.append(qlbl)
            else:
                buf.append(ln)
        flush()

    pos = 0
    for match in _RE_CODE_BLOCK.finditer(t):
        if match.start() > pos:
            render_text_chunk(t[pos:match.start()])

        lang = (match.group("lang") or "").strip()
        code = match.group("code") or ""
        cb = ctk.CTkFrame(parent, fg_color=code_bg, corner_radius=6)
        cb.pack(fill="x", anchor="w", pady=4)
        if lang:
            ctk.CTkLabel(cb, text=lang, font=FONT_SMALL,
                         text_color=COLOR_MUTED, anchor="w").pack(
                fill="x", padx=10, pady=(6, 0))
        lines = code.count("\n") + 1
        height = max(40, min(320, 16 * lines + 18))
        tb = ctk.CTkTextbox(cb, font=("Consolas", 10),
                             fg_color=code_bg, text_color=code_fg,
                             wrap="none",
                             height=height, border_width=0,
                             activate_scrollbars=False)
        tb.insert("1.0", code)
        tb.configure(state="disabled")
        tb.pack(fill="x", padx=10, pady=(2, 8))
        pos = match.end()

    if pos < len(t):
        render_text_chunk(t[pos:])
    return widgets


# ---------- 可点击卡片 ----------
class OptionCard(ctk.CTkFrame):
    def __init__(self, master, icon, title, desc, command, **kw):
        super().__init__(master, fg_color=COLOR_CARD, corner_radius=14,
                         height=92, cursor="hand2", **kw)
        self.command = command
        self.grid_propagate(False)
        self.columnconfigure(1, weight=1)

        icon_lbl = ctk.CTkLabel(self, text=icon, font=("Segoe UI Emoji", 30))
        icon_lbl.grid(row=0, column=0, rowspan=2, padx=(22, 16), pady=16)

        title_lbl = ctk.CTkLabel(self, text=title, font=FONT_BTN, anchor="w")
        title_lbl.grid(row=0, column=1, sticky="sw", pady=(18, 0))

        desc_lbl = ctk.CTkLabel(self, text=desc, font=FONT_SMALL,
                                text_color=COLOR_MUTED, anchor="w")
        desc_lbl.grid(row=1, column=1, sticky="nw", pady=(2, 18))

        arrow = ctk.CTkLabel(self, text="›", font=("Microsoft YaHei UI", 28, "bold"),
                             text_color=COLOR_MUTED)
        arrow.grid(row=0, column=2, rowspan=2, padx=(0, 22))

        for w in (self, icon_lbl, title_lbl, desc_lbl, arrow):
            w.bind("<Button-1>", lambda e: self.command())
            w.bind("<Enter>", lambda e: self.configure(fg_color=COLOR_CARD_HOVER))
            w.bind("<Leave>", lambda e: self.configure(fg_color=COLOR_CARD))


# ---------- 主窗口 ----------
class Launcher(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("GenericAgent 启动器")
        self.geometry("720x540")
        self._center()
        self.cfg = load_config()
        ctk.set_appearance_mode(self._normalize_appearance_mode(
            self.cfg.get("appearance_mode", "dark")))
        self.configure(fg_color=COLOR_APP_BG)
        self.minsize(640, 480)
        self.agent_dir = ctk.StringVar(value=self.cfg.get("agent_dir", ""))
        self.install_parent = ctk.StringVar(value=self.cfg.get("install_parent", ""))
        self._setup_mode_no_kernel = False
        self._api_setup_reason = ""
        self._state_request_seq = 0
        self._abort_requested = False
        self._current_stream_text = ""
        self._current_settings_panel = None
        self._channel_procs = {}

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=36, pady=28)

        self.show_welcome()

    def _center(self):
        self.update_idletasks()
        w, h = 720, 540
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def _normalize_appearance_mode(self, mode):
        return "light" if str(mode).strip().lower() == "light" else "dark"

    def _tk_theme_color(self, color):
        if not isinstance(color, tuple):
            return color
        mode = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "dark"))
        return color[0] if mode == "light" else color[1]

    def _refresh_theme_button(self):
        btn = getattr(self, "theme_btn", None)
        if btn is None or not btn.winfo_exists():
            return
        mode = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "dark"))
        btn.configure(text=("☀" if mode == "dark" else "🌙"))

    def _set_launcher_appearance_mode(self, mode, persist=True):
        mode = self._normalize_appearance_mode(mode)
        try:
            ctk.set_appearance_mode(mode)
        except Exception:
            pass
        self.cfg["appearance_mode"] = mode
        try:
            self.configure(fg_color=COLOR_APP_BG)
        except Exception:
            pass
        if persist:
            save_config(self.cfg)
        self._refresh_theme_button()
        self._refresh_theme_sensitive_widgets()

    def _toggle_appearance_mode(self):
        cur = self._normalize_appearance_mode(self.cfg.get("appearance_mode", "dark"))
        self._set_launcher_appearance_mode("light" if cur == "dark" else "dark")

    def _refresh_theme_sensitive_widgets(self):
        widgets = [
            ("log", {"fg_color": COLOR_FIELD_BG, "text_color": COLOR_CODE_TEXT}),
            ("input_box", {"fg_color": COLOR_CARD, "text_color": COLOR_TEXT}),
            ("llm_menu", {
                "fg_color": COLOR_FIELD_BG,
                "button_color": COLOR_ACCENT,
                "button_hover_color": COLOR_ACCENT_HOVER,
                "dropdown_fg_color": COLOR_POPUP_BG,
                "dropdown_hover_color": COLOR_CARD_HOVER,
                "dropdown_text_color": COLOR_TEXT,
                "text_color": COLOR_TEXT,
            }),
        ]
        for attr, kwargs in widgets:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            try:
                if widget.winfo_exists():
                    widget.configure(**kwargs)
            except Exception:
                pass

    def _header(self, title, subtitle=None, back=None, parent=None):
        host = parent or self.container
        top = ctk.CTkFrame(host, fg_color="transparent")
        top.pack(fill="x", pady=(0, 18))
        if back:
            ctk.CTkButton(top, text="‹ 返回", width=72, height=30,
                          font=FONT_SMALL, fg_color="transparent",
                          hover_color=COLOR_CARD, text_color=COLOR_MUTED,
                          command=back).pack(side="left")
        ctk.CTkLabel(host, text=title, font=FONT_TITLE,
                     anchor="w").pack(fill="x")
        if subtitle:
            ctk.CTkLabel(host, text=subtitle, font=FONT_SUB,
                         text_color=COLOR_MUTED, anchor="w").pack(fill="x", pady=(4, 0))

    # ---------- 欢迎页 ----------
    def show_welcome(self):
        self._setup_mode_no_kernel = False
        self.clear()

        brand = ctk.CTkFrame(self.container, fg_color="transparent")
        brand.pack(fill="x", pady=(6, 22))
        ctk.CTkLabel(brand, text="⚙", font=("Segoe UI Emoji", 40),
                     text_color=COLOR_ACCENT).pack(side="left", padx=(0, 14))
        title_box = ctk.CTkFrame(brand, fg_color="transparent")
        title_box.pack(side="left", fill="y")
        ctk.CTkLabel(title_box, text="GenericAgent 启动器",
                     font=FONT_TITLE, anchor="w").pack(anchor="w")
        ctk.CTkLabel(title_box, text="通用智能体 · 一键启动",
                     font=FONT_SUB, text_color=COLOR_MUTED,
                     anchor="w").pack(anchor="w", pady=(2, 0))

        # 上次使用
        if self.agent_dir.get() and is_valid_agent_dir(self.agent_dir.get()):
            recent = ctk.CTkFrame(self.container, fg_color=COLOR_INFO_BG,
                                  corner_radius=12, height=74)
            recent.pack(fill="x", pady=(0, 14))
            recent.pack_propagate(False)
            info = ctk.CTkFrame(recent, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=18, pady=12)
            ctk.CTkLabel(info, text="📁 上次使用的目录", font=FONT_SMALL,
                         text_color=COLOR_INFO_TEXT, anchor="w").pack(anchor="w")
            path_txt = self.agent_dir.get()
            if len(path_txt) > 60:
                path_txt = "…" + path_txt[-59:]
            ctk.CTkLabel(info, text=path_txt, font=FONT_BODY,
                         anchor="w").pack(anchor="w", pady=(2, 0))
            ctk.CTkButton(recent, text="直接启动", width=100, height=34,
                          font=FONT_BTN, corner_radius=8,
                          fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                          command=self.launch_kernel).pack(side="right", padx=16)

        ctk.CTkLabel(self.container, text="请选择你的情况",
                     font=FONT_SUB, text_color=COLOR_MUTED,
                     anchor="w").pack(fill="x", pady=(4, 10))

        OptionCard(self.container, icon="✅",
                   title="我已经下载了 GenericAgent",
                   desc="选择本地目录，立即载入内核",
                   command=self.show_locate).pack(fill="x", pady=6)

        OptionCard(self.container, icon="⬇",
                   title="我还没有，帮我下载",
                   desc=f"从 GitHub 自动克隆到你指定的位置",
                   command=self.show_download).pack(fill="x", pady=6)

        ctk.CTkLabel(self.container, text=f"源：{REPO_URL}",
                     font=FONT_SMALL, text_color=COLOR_MUTED).pack(side="bottom", pady=(10, 0))

    # ---------- 定位已存在目录 ----------
    def show_locate(self):
        self.clear()
        self._header("选择 GenericAgent 目录",
                     "目录中需包含 launch.pyw 与 agentmain.py",
                     back=self.show_welcome)

        card = ctk.CTkFrame(self.container, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(card, text="目录路径", font=FONT_SMALL,
                     text_color=COLOR_MUTED, anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 18))
        entry = ctk.CTkEntry(row, textvariable=self.agent_dir, height=38,
                             font=FONT_BODY, corner_radius=8)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="浏览…", width=90, height=38, font=FONT_BODY,
                      corner_radius=8, command=self.pick_agent_dir).pack(side="right")

        hint = ctk.CTkFrame(self.container, fg_color="transparent")
        hint.pack(fill="x", pady=16)
        ctk.CTkLabel(hint, text="💡 提示：选择 GenericAgent 项目的根目录",
                     font=FONT_SMALL, text_color=COLOR_MUTED).pack(anchor="w")

        btns = ctk.CTkFrame(self.container, fg_color="transparent")
        btns.pack(side="bottom", fill="x", pady=(10, 0))
        ctk.CTkButton(btns, text="载入内核 →", height=44, font=FONT_BTN,
                      corner_radius=10, fg_color=COLOR_ACCENT,
                      hover_color=COLOR_ACCENT_HOVER,
                      command=self.confirm_and_launch).pack(fill="x")

    def pick_agent_dir(self):
        path = filedialog.askdirectory(title="选择 GenericAgent 目录")
        if path:
            self.agent_dir.set(path)

    def confirm_and_launch(self):
        path = self.agent_dir.get().strip()
        if not is_valid_agent_dir(path):
            messagebox.showerror("目录无效",
                "该目录中未找到 launch.pyw / agentmain.py\n\n请确认选择的是 GenericAgent 的根目录。")
            return
        self.cfg["agent_dir"] = path
        save_config(self.cfg)
        self.launch_kernel()

    # ---------- 下载页 ----------
    def show_download(self):
        self.clear()
        page = ctk.CTkFrame(self.container, fg_color="transparent")
        self._header("下载 GenericAgent",
                     f"来源：{REPO_URL}",
                     back=self.show_welcome,
                     parent=page)

        body = ctk.CTkScrollableFrame(page, fg_color="transparent",
                                      corner_radius=0)
        body.pack(fill="both", expand=True, pady=(10, 0))

        install_card = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=12)
        install_card.pack(fill="x")
        ctk.CTkLabel(install_card, text="安装位置",
                     font=("Microsoft YaHei UI", 14, "bold"),
                     anchor="w").pack(fill="x", padx=18, pady=(16, 2))
        ctk.CTkLabel(
            install_card,
            text="启动器会在你选择的目录下创建 `GenericAgent` 文件夹；如果该目录已存在，会先让你确认是否直接使用。",
            font=FONT_SMALL, text_color=COLOR_MUTED,
            anchor="w", justify="left", wraplength=620
        ).pack(fill="x", padx=18, pady=(0, 10))
        row = ctk.CTkFrame(install_card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 10))
        ctk.CTkEntry(row, textvariable=self.install_parent, height=38,
                     font=FONT_BODY, corner_radius=8).pack(
            side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="浏览…", width=90, height=38, font=FONT_BODY,
                      corner_radius=8, command=self.pick_install_parent).pack(side="right")

        self._download_target_var = ctk.StringVar(value="")
        try:
            old_trace = getattr(self, "_install_parent_trace_id", None)
            if old_trace:
                self.install_parent.trace_remove("write", old_trace)
        except Exception:
            pass
        def _refresh_target_label(*_):
            parent = self.install_parent.get().strip()
            target = os.path.join(parent, "GenericAgent") if parent else "未选择安装位置"
            self._download_target_var.set(f"目标目录：{target}")
        try:
            self._install_parent_trace_id = self.install_parent.trace_add("write", _refresh_target_label)
        except Exception:
            self._install_parent_trace_id = None
        _refresh_target_label()
        ctk.CTkLabel(install_card, textvariable=self._download_target_var,
                     font=FONT_SMALL, text_color=COLOR_INFO_TEXT,
                     anchor="w").pack(fill="x", padx=18, pady=(0, 16))

        deps = _probe_download_requirements()
        deps_card = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=12)
        deps_card.pack(fill="x", pady=(14, 0))
        ctk.CTkLabel(deps_card, text="环境提示",
                     font=("Microsoft YaHei UI", 14, "bold"),
                     anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            deps_card,
            text=("自动下载只依赖 Git；下载完成后，这个启动器会直接拉起 GenericAgent 的 `agentmain`，"
                  "因此还需要系统 Python。推荐 Python 3.11 / 3.12，尽量不要用 3.14。\n"
                  "上游 README 里的 `streamlit` / `pywebview` 主要是给原版 GUI，用这个启动器时不是必需。"),
            font=FONT_SMALL, text_color=COLOR_MUTED,
            anchor="w", justify="left", wraplength=620
        ).pack(fill="x", padx=18, pady=(0, 10))

        def _dep_row(parent, title, ok, detail, bad_color=COLOR_ERROR_TEXT):
            row = ctk.CTkFrame(parent, fg_color=COLOR_FIELD_ALT, corner_radius=8)
            row.pack(fill="x", padx=18, pady=4)
            icon = "✓" if ok else "!"
            color = COLOR_OK_TEXT if ok else bad_color
            ctk.CTkLabel(row, text=icon, width=18, font=("Microsoft YaHei UI", 12, "bold"),
                         text_color=color).pack(side="left", padx=(10, 8), pady=9)
            ctk.CTkLabel(row, text=title, width=92, anchor="w",
                        font=FONT_BODY, text_color=COLOR_TEXT).pack(side="left")
            ctk.CTkLabel(row, text=detail, anchor="w", justify="left",
                         font=FONT_SMALL, text_color=(COLOR_MUTED if ok else bad_color),
                         wraplength=470).pack(side="left", fill="x", expand=True, padx=(0, 10), pady=9)

        _dep_row(deps_card, "Git", deps["git_ok"], deps["git_text"])
        _dep_row(deps_card, "Python", deps["python_ok"], deps["python_text"],
                bad_color=(COLOR_WARNING_TEXT if deps["python_ok"] else COLOR_ERROR_TEXT))
        _dep_row(deps_card, "requests", deps["requests_ok"], deps["requests_text"], bad_color=COLOR_WARNING_TEXT)

        log_card = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=12)
        log_card.pack(fill="x", pady=(14, 0))
        head = ctk.CTkFrame(log_card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkLabel(head, text="下载日志",
                     font=("Microsoft YaHei UI", 14, "bold"),
                     anchor="w").pack(side="left")
        ctk.CTkLabel(head, text="执行 git clone 时会在这里实时输出",
                     font=FONT_SMALL, text_color=COLOR_MUTED,
                     anchor="e").pack(side="right")

        log_box = ctk.CTkFrame(log_card, fg_color=COLOR_FIELD_BG, corner_radius=10)
        log_box.pack(fill="x", padx=16, pady=(0, 16))
        self.log = ctk.CTkTextbox(log_box, height=220,
                                  font=FONT_MONO, fg_color=COLOR_FIELD_BG,
                                  text_color=COLOR_CODE_TEXT, corner_radius=10,
                                  border_width=0, wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.configure(state="disabled")
        self.log_print("等待开始…")

        footer = ctk.CTkFrame(page, fg_color="transparent")
        footer.pack(fill="x", pady=(12, 0))
        self.progress = ctk.CTkProgressBar(footer, height=6,
                                            corner_radius=3, progress_color=COLOR_ACCENT)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress.set(0)

        self.dl_btn = ctk.CTkButton(footer, text="开始下载", height=44, font=FONT_BTN,
                                     corner_radius=10, fg_color=COLOR_ACCENT,
                                     hover_color=COLOR_ACCENT_HOVER,
                                     command=self.start_download)
        self.dl_btn.pack(fill="x")
        page.pack(fill="both", expand=True)

    def pick_install_parent(self):
        path = filedialog.askdirectory(title="选择安装位置")
        if path:
            self.install_parent.set(path)

    def log_print(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_download(self):
        parent = self.install_parent.get().strip()
        if not parent or not os.path.isdir(parent):
            messagebox.showerror("位置无效", "请选择有效的安装位置。")
            return
        target = os.path.join(parent, "GenericAgent")
        if os.path.exists(target):
            if not messagebox.askyesno("目录已存在",
                f"{target}\n\n已存在。是否直接使用它作为 GenericAgent 目录？"):
                return
            if is_valid_agent_dir(target):
                self.agent_dir.set(target)
                self.cfg["agent_dir"] = target
                self.cfg["install_parent"] = parent
                save_config(self.cfg)
                self.launch_kernel()
            else:
                messagebox.showerror("目录无效", "该目录存在但不是有效的 GenericAgent 目录。")
            return

        self.dl_btn.configure(state="disabled", text="下载中…")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.cfg["install_parent"] = parent
        save_config(self.cfg)
        threading.Thread(target=self._run_clone, args=(parent, target), daemon=True).start()

    def _run_clone(self, parent, target):
        def ui(fn):
            self.after(0, fn)

        try:
            try:
                subprocess.run(["git", "--version"], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception:
                ui(lambda: (self.progress.stop(), self.progress.set(0),
                            self.dl_btn.configure(state="normal", text="开始下载"),
                            messagebox.showerror("缺少 git",
                                "未检测到 git。\n\n请先安装 Git for Windows：\nhttps://git-scm.com/download/win")))
                return

            ui(lambda: self.log_print(f"$ git clone {REPO_URL}"))
            ui(lambda: self.log_print(f"  → {target}\n"))
            proc = subprocess.Popen(
                ["git", "clone", "--progress", REPO_URL, target],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for line in proc.stdout:
                ui(lambda l=line.rstrip(): self.log_print(l))
            proc.wait()

            if proc.returncode != 0 or not is_valid_agent_dir(target):
                ui(lambda: (self.progress.stop(), self.progress.set(0),
                            self.dl_btn.configure(state="normal", text="重试下载"),
                            messagebox.showerror("下载失败", "git clone 失败，请检查网络后重试。")))
                return

            self.agent_dir.set(target)
            self.cfg["agent_dir"] = target
            save_config(self.cfg)
            ui(lambda: (self.progress.stop(), self.progress.configure(mode="determinate"),
                        self.progress.set(1), self.log_print("\n✓ 下载完成，准备载入内核…")))
            self.after(800, self.launch_kernel)
        except Exception as e:
            ui(lambda: (self.progress.stop(),
                        self.dl_btn.configure(state="normal", text="开始下载"),
                        messagebox.showerror("错误", str(e))))

    # ---------- 载入内核 ----------
    def launch_kernel(self):
        path = self.agent_dir.get().strip()
        if not is_valid_agent_dir(path):
            messagebox.showerror("目录无效", "GenericAgent 目录无效。")
            self.show_welcome()
            return
        self._stop_managed_channels_not_matching(path)
        ensured = _ensure_mykey_file(path)
        if not ensured.get("ok"):
            messagebox.showerror(
                "初始化失败",
                "无法准备 mykey.py。\n\n"
                f"目标：{ensured.get('path', '')}\n"
                f"错误：{ensured.get('error', '未知错误')}"
            )
            return
        if ensured.get("created"):
            messagebox.showinfo(
                "已初始化配置文件",
                "已自动创建 mykey.py。\n\n"
                "接下来如果提示未配置 LLM，请到「设置 → API」填写你的渠道信息。"
            )

        self.clear()
        wrap = ctk.CTkFrame(self.container, fg_color="transparent")
        wrap.pack(expand=True)
        ctk.CTkLabel(wrap, text="⏳", font=("Segoe UI Emoji", 56)).pack(pady=(60, 14))
        ctk.CTkLabel(wrap, text="正在载入 GenericAgent 内核…",
                     font=FONT_TITLE).pack()
        self.load_status = ctk.CTkLabel(wrap, text="导入模块…",
                                         font=FONT_SUB, text_color=COLOR_MUTED)
        self.load_status.pack(pady=(10, 20))
        bar = ctk.CTkProgressBar(wrap, width=360, height=6,
                                  progress_color=COLOR_ACCENT)
        bar.pack()
        bar.configure(mode="indeterminate")
        bar.start()
        threading.Thread(target=self._bootstrap_kernel,
                          args=(path,), daemon=True).start()

    def _bootstrap_kernel(self, path):
        def ui(fn): self.after(0, fn)
        try:
            ui(lambda: self.load_status.configure(text="查找系统 Python…"))
            py = _find_system_python()
            if not py:
                raise RuntimeError("未找到系统 Python。请先安装 Python 3.10+ 并加入 PATH，或在启动器所在目录的 launcher_config.json 中设置 python_exe 绝对路径。")

            bridge = _bridge_script_path()
            if not os.path.isfile(bridge):
                raise RuntimeError(f"bridge.py 不存在：{bridge}")

            ui(lambda: self.load_status.configure(text=f"启动内核进程…（{os.path.basename(py)}）"))
            self._stderr_buf = []
            proc = subprocess.Popen(
                [py, "-u", bridge, path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=path, text=True, encoding="utf-8", errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.bridge_proc = proc
            self.event_queue = queue.Queue()
            self.llms = []
            self._relay_dq_assistant = True

            def read_stderr():
                try:
                    for line in proc.stderr:
                        self._stderr_buf.append(line.rstrip())
                        if len(self._stderr_buf) > 200:
                            self._stderr_buf = self._stderr_buf[-200:]
                except Exception:
                    pass
            threading.Thread(target=read_stderr, daemon=True).start()

            ready = False
            while True:
                line = proc.stdout.readline()
                if not line:
                    stderr_tail = "\n".join(self._stderr_buf[-30:])
                    raise RuntimeError(f"内核进程意外退出。stderr 尾部：\n{stderr_tail or '(空)'}")
                line = line.strip()
                if not line: continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                et = ev.get("event")
                if et == "log":
                    msg = ev.get("msg", "")
                    ui(lambda m=msg: self.load_status.configure(text=m))
                elif et == "ready":
                    self.llms = ev.get("llms", [])
                    ready = True
                    break
                elif et == "error":
                    msg = ev.get("msg", "")
                    trace = ev.get("trace", "")
                    stderr_tail = "\n".join(self._stderr_buf[-20:])
                    if "未配置 LLM" in msg or "mykey 配置无效" in msg:
                        ui(lambda m=msg: self._enter_api_setup_mode(m))
                        return
                    raise RuntimeError(f"{msg}\n\n{trace}\n\n{stderr_tail}")

            if ready:
                self._setup_mode_no_kernel = False
                self._api_setup_reason = ""
                threading.Thread(target=self._event_reader,
                                  args=(proc,), daemon=True).start()
                ui(lambda: (self.show_chat(), self.after(300, self._start_autostart_channels)))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            ui(lambda: messagebox.showerror("载入失败",
                f"无法载入 GenericAgent 内核：\n\n{e}\n\n详细：\n{tb[-1000:]}"))
            ui(lambda: self.show_welcome())

    def _event_reader(self, proc):
        """持续读取内核输出的 JSON 事件行。"""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line: continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                self.after(0, lambda e=ev: self._handle_event(e))
        except Exception:
            pass

    def _handle_event(self, ev):
        et = ev.get("event")
        if et == "next":
            self._stream_update(ev.get("text", ""))
        elif et == "done":
            final_text = ev.get("text", "")
            if self._abort_requested:
                final_text = self._format_interrupted_text(final_text)
            self._stream_done(final_text)
            self._busy = False
            self._abort_requested = False
            self._last_activity = time.time()
            self._finish_generation_ui()
            if self.current_session is not None:
                session_id = self.current_session.get("id")
                self.current_session["bubbles"].append(
                    {"role": "assistant", "text": final_text})
                self._persist_current_session()
                self._request_backend_state(session_id)
        elif et == "aborted":
            self._abort_requested = True
            try:
                self.send_btn.configure(state="disabled", text="中断中")
                self.stop_btn.configure(state="disabled", text="已发送")
            except Exception:
                pass
        elif et == "tools_reinjected":
            n = ev.get("count", 0)
            messagebox.showinfo("工具注入",
                f"✅ 已重新注入 {n} 条工具示范到当前会话历史。")
        elif et == "pet_launched":
            messagebox.showinfo("桌面宠物", "🐱 桌面宠物已启动。")
        elif et == "state":
            session_id = ev.get("session_id") or ((self.current_session or {}).get("id"))
            self._apply_state_to_session(
                session_id,
                ev.get("backend_history") or [],
                ev.get("agent_history") or [],
            )
        elif et == "state_loaded":
            pass
        elif et == "legacy_list":
            self._legacy_items = ev.get("items", [])
            self._auto_import_legacy()
        elif et == "legacy_restored":
            self._on_legacy_restored(ev)
        elif et == "llm_switched":
            self.llms = ev.get("llms", self.llms)
            self._refresh_llm_selector()
        elif et == "error":
            msg = ev.get("msg", "")
            self._update_last(f"[错误] {msg}")
            self._busy = False
            self._abort_requested = False
            self._finish_generation_ui()

    def _send_cmd(self, obj):
        try:
            self.bridge_proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.bridge_proc.stdin.flush()
        except Exception as e:
            messagebox.showerror("通信失败", f"内核管道错误：{e}")

    def _request_backend_state(self, session_id=None):
        if not session_id:
            session_id = (self.current_session or {}).get("id")
        if not session_id:
            return
        self._state_request_seq += 1
        self._send_cmd({
            "cmd": "get_state",
            "session_id": session_id,
            "request_id": self._state_request_seq,
        })

    def _apply_state_to_session(self, session_id, backend_history, agent_history):
        if not session_id:
            return
        target = None
        if self.current_session and self.current_session.get("id") == session_id:
            target = self.current_session
        else:
            try:
                target = load_session(self.agent_dir.get(), session_id)
            except Exception:
                target = None
        if not target:
            return
        target["backend_history"] = list(backend_history or [])
        target["agent_history"] = list(agent_history or [])
        if self.current_session and self.current_session.get("id") == session_id:
            self.current_session = target
        self._persist_session_data(target)

    def _finish_generation_ui(self):
        try:
            self.send_btn.configure(state="normal", text="发送")
            self.stop_btn.configure(state="disabled", text="中断")
        except Exception:
            pass

    def _format_interrupted_text(self, final_text=None):
        text = (final_text or "").strip()
        if not text:
            text = (getattr(self, "_current_stream_text", None) or
                    getattr(self, "_pending_stream_text", None) or "").strip()
        if text.endswith("▌"):
            text = text[:-1].rstrip()
        if "已按用户请求中断" in text:
            return text
        if not text:
            return "[系统] 已按用户请求中断本轮生成。"
        return text + "\n\n[系统] 已按用户请求中断本轮生成。"

    # ---------- 聊天视图 ----------
    def show_chat(self, offline=False):
        self.clear()
        try:
            self.state("zoomed")
        except Exception:
            try: self.attributes("-zoomed", True)
            except Exception: pass
        self.minsize(900, 600)
        self.container.pack_forget()
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=0, pady=0)

        # 会话状态（仅首次进入聊天时初始化；重启/切换视图保留）
        if not hasattr(self, "current_session"):
            self.current_session = None
        self._bubble_labels = []
        self._bubble_rows = []
        self._fold_sections = []
        self._current_assistant_content = None
        self._current_stream_text = ""
        if not hasattr(self, "_legacy_items"): self._legacy_items = []
        if not hasattr(self, "_pending_import_queue"): self._pending_import_queue = []
        if not hasattr(self, "_importing"): self._importing = False
        if not hasattr(self, "sidebar_collapsed"): self.sidebar_collapsed = False
        if not hasattr(self, "_batch_mode"): self._batch_mode = False
        if not hasattr(self, "_batch_selected"): self._batch_selected = set()

        # 根：sidebar | main
        self.root_grid = ctk.CTkFrame(self.container, fg_color="transparent")
        self.root_grid.pack(fill="both", expand=True)
        self.root_grid.rowconfigure(0, weight=1)
        sidebar_width = 48 if self.sidebar_collapsed else 280
        self.root_grid.columnconfigure(0, weight=0, minsize=sidebar_width)
        self.root_grid.columnconfigure(1, weight=1)

        self.sidebar_host = ctk.CTkFrame(self.root_grid, fg_color="transparent",
                                          corner_radius=0, width=sidebar_width)
        self.sidebar_host.grid(row=0, column=0, sticky="ns")
        self.sidebar_host.grid_propagate(False)

        self._build_sidebar()
        self.main_area = ctk.CTkFrame(self.root_grid, fg_color="transparent")
        self.main_area.grid(row=0, column=1, sticky="nsew")

        self._build_chat_main(self.main_area)

        if not offline:
            self._send_cmd({"cmd": "list_legacy"})
        self.bind("<Configure>", self._on_resize)
        self._last_activity = time.time()
        if self.cfg.get("autonomous_enabled", False) and not getattr(
                self, "_idle_thread_started", False):
            self._idle_thread_started = True
            threading.Thread(target=self._idle_monitor, daemon=True).start()

        # 若已有当前会话，回放气泡
        if self.current_session:
            for b in self.current_session.get("bubbles", []):
                self._add_bubble(b.get("role", "assistant"),
                                  b.get("text", ""), final=True)

    def _enter_api_setup_mode(self, reason=None):
        self._setup_mode_no_kernel = True
        self._api_setup_reason = reason or ""
        self.llms = []
        try:
            if getattr(self, "bridge_proc", None) and self.bridge_proc.poll() is None:
                try:
                    self.bridge_proc.terminate()
                except Exception:
                    pass
        except Exception:
            pass
        self.bridge_proc = None
        self.show_chat(offline=True)
        self._open_settings_view()

    # ---------- 侧边栏（仿 AionUi 双状态） ----------
    def _build_sidebar(self):
        width = 48 if self.sidebar_collapsed else 280
        if hasattr(self, "sidebar_host") and self.sidebar_host.winfo_exists():
            try:
                self.sidebar_host.grid()
            except Exception:
                pass
            try:
                self.sidebar_host.configure(width=width)
            except Exception:
                pass
            for w in self.sidebar_host.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
        try:
            self.root_grid.grid_columnconfigure(0, minsize=width, weight=0)
        except Exception:
            pass
        self.sidebar = ctk.CTkFrame(self.sidebar_host, fg_color=COLOR_SIDEBAR_BG,
                                      corner_radius=0, width=width)
        self.sidebar.pack(fill="both", expand=True)
        self.sidebar.pack_propagate(False)

        # 顶栏：折叠按钮
        top = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=44)
        top.pack(fill="x", pady=(8, 0))
        top.pack_propagate(False)
        toggle_text = "⇥" if self.sidebar_collapsed else "⇤"
        ctk.CTkButton(top, text=toggle_text, width=32, height=32,
                       font=("Segoe UI Emoji", 14),
                       corner_radius=6,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_TEXT_SOFT,
                       command=self._toggle_sidebar).pack(side="left", padx=12)

        # 品牌区
        if self.sidebar_collapsed:
            logo = ctk.CTkLabel(self.sidebar, text="⚙",
                                  font=("Segoe UI Emoji", 18),
                                  fg_color=COLOR_CHIP_BG, corner_radius=8,
                                  width=36, height=36, text_color=COLOR_ACCENT)
            logo.pack(padx=10, pady=(10, 18))
        else:
            brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
            brand.pack(fill="x", padx=14, pady=(10, 18))
            ctk.CTkLabel(brand, text="⚙", font=("Segoe UI Emoji", 22),
                         fg_color=COLOR_CHIP_BG, corner_radius=8,
                         width=42, height=42,
                         text_color=COLOR_ACCENT).pack(side="left", padx=(0, 10))
            ctk.CTkLabel(brand, text="GenericAgent",
                         font=("Microsoft YaHei UI", 16, "bold")).pack(
                side="left", anchor="w")

        # 新会话
        if self.sidebar_collapsed:
            ctk.CTkButton(self.sidebar, text="+", width=36, height=36,
                          font=("Microsoft YaHei UI", 16, "bold"),
                          corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_CARD,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._new_session).pack(padx=10, pady=4)
            ctk.CTkButton(self.sidebar, text="🔍", width=36, height=36,
                          font=("Segoe UI Emoji", 13),
                          corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_CARD,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._open_search).pack(padx=10, pady=4)
        else:
            new_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
            new_row.pack(fill="x", padx=14, pady=(0, 6))
            ctk.CTkButton(new_row, text="＋  新会话", anchor="w",
                          height=36, font=FONT_BTN, corner_radius=8,
                          fg_color=COLOR_FIELD_ALT, hover_color=COLOR_CARD_HOVER,
                          command=self._new_session).pack(
                side="left", fill="x", expand=True)
            search_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
            search_row.pack(fill="x", padx=14, pady=(0, 12))
            ctk.CTkButton(search_row, text="🔍  搜索", anchor="w",
                          height=32, font=FONT_BODY, corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_FIELD_ALT,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._open_search).pack(
                side="left", fill="x", expand=True)
            # 分组标题
            ctk.CTkLabel(self.sidebar, text="更早", font=FONT_SMALL,
                         text_color=COLOR_MUTED, anchor="w").pack(
                fill="x", padx=18, pady=(4, 2))

        # 会话列表（折叠时只显示前若干个图标，展开时完整列表）
        self.sess_list = ctk.CTkScrollableFrame(self.sidebar,
                                                 fg_color="transparent",
                                                 corner_radius=0)
        self.sess_list.pack(fill="both", expand=True, padx=(6 if self.sidebar_collapsed else 8),
                             pady=(0, 4))

        # 底部 设置 按钮
        bottom = ctk.CTkFrame(self.sidebar, fg_color="transparent", height=52)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        self._sidebar_bottom = bottom
        self.batch_bar = None
        if self.sidebar_collapsed:
            ctk.CTkButton(bottom, text="⚙", width=36, height=36,
                          font=("Segoe UI Emoji", 16),
                          corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_CARD,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._open_settings_view).pack(padx=10, pady=8)
        else:
            ctk.CTkButton(bottom, text="⚙   设置", anchor="w",
                          height=36, font=FONT_BODY, corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_FIELD_ALT,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._open_settings_view).pack(
                fill="x", padx=14, pady=8)

        self._refresh_sessions()
        self._render_batch_bar()
        try:
            self.sidebar.configure(width=width)
            self.root_grid.update_idletasks()
            self.sidebar_host.update_idletasks()
        except Exception:
            pass

    def _toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        self._build_sidebar()

    def _open_search(self):
        """打开「检索会话内容」模态：全文搜索，结果卡片可跳转到对应消息。"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("检索会话内容")
        dlg.configure(fg_color=COLOR_SURFACE)
        dlg.transient(self)
        try: dlg.grab_set()
        except Exception: pass
        # 居中
        dlg.update_idletasks()
        dw, dh = 740, 580
        try:
            sx = self.winfo_rootx(); sy = self.winfo_rooty()
            sw = self.winfo_width() or self.winfo_screenwidth()
            sh = self.winfo_height() or self.winfo_screenheight()
            x = sx + max(0, (sw - dw) // 2)
            y = sy + max(0, (sh - dh) // 3)
        except Exception:
            x = y = 80
        dlg.geometry(f"{dw}x{dh}+{x}+{y}")
        dlg.minsize(520, 360)

        # ── 头部 ──
        head = ctk.CTkFrame(dlg, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(20, 6))
        title_box = ctk.CTkFrame(head, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_box, text="检索会话内容",
                      font=("Microsoft YaHei UI", 18, "bold"),
                      text_color=COLOR_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(title_box,
                      text="输入关键词搜索历史消息，支持按会话分组并跳转到对应消息。",
                      font=FONT_SMALL, text_color=COLOR_MUTED,
                      anchor="w").pack(anchor="w", pady=(4, 0))
        ctk.CTkButton(head, text="×", width=34, height=34,
                       font=("Segoe UI Emoji", 16),
                       corner_radius=8, fg_color="transparent",
                       hover_color=COLOR_CARD, text_color=COLOR_TEXT_SOFT,
                       command=dlg.destroy).pack(side="right")

        # ── 搜索框 ──
        bar = ctk.CTkFrame(dlg, fg_color=COLOR_FIELD_ALT, corner_radius=12, height=50)
        bar.pack(fill="x", padx=28, pady=(10, 12))
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="🔍", font=("Segoe UI Emoji", 14),
                      text_color=COLOR_MUTED, width=32).pack(side="left", padx=(12, 0))
        q_var = ctk.StringVar(value="")
        entry = ctk.CTkEntry(bar, textvariable=q_var, height=36,
                              font=FONT_BODY, fg_color=COLOR_FIELD_ALT,
                              border_width=0, text_color=COLOR_TEXT,
                              placeholder_text="输入关键词开始检索")
        entry.pack(side="left", fill="x", expand=True, padx=(4, 4))
        def _clear_q():
            q_var.set(""); entry.focus_set()
        clear_btn = ctk.CTkButton(bar, text="×", width=30, height=30,
                                   font=("Segoe UI Emoji", 12),
                                   corner_radius=6, fg_color="transparent",
                                   hover_color=COLOR_CARD,
                                   text_color=COLOR_MUTED,
                                   command=_clear_q)
        clear_btn.pack(side="right", padx=(0, 10))

        # ── 结果区 ──
        results = ctk.CTkScrollableFrame(dlg, fg_color=COLOR_FIELD_BG,
                                           corner_radius=12)
        results.pack(fill="both", expand=True, padx=28, pady=(0, 20))

        # 状态提示（空/无结果/加载）
        status_box = {"label": None}
        def _show_status(text):
            for w in results.winfo_children():
                try: w.destroy()
                except Exception: pass
            lbl = ctk.CTkLabel(results, text=text, font=FONT_SUB,
                                text_color=COLOR_MUTED)
            lbl.pack(pady=60)
            status_box["label"] = lbl

        _show_status("输入关键词开始检索")

        debounce = {"job": None}

        def _snippet(text, kw, width=80):
            if not text:
                return ""
            t = text.replace("\n", " ").replace("\r", " ")
            t = re.sub(r"\s+", " ", t).strip()
            lk = kw.lower()
            idx = t.lower().find(lk)
            if idx < 0:
                return t[:width] + ("…" if len(t) > width else "")
            pre = 24
            start = max(0, idx - pre)
            end = min(len(t), idx + len(kw) + (width - pre))
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(t) else ""
            return prefix + t[start:end] + suffix

        def _make_result_card(parent, meta, bubble_idx, role, snippet, kw):
            card = ctk.CTkFrame(parent, fg_color=COLOR_CARD,
                                 corner_radius=10, cursor="hand2")
            card.pack(fill="x", padx=6, pady=4)

            head_row = ctk.CTkFrame(card, fg_color="transparent")
            head_row.pack(fill="x", padx=14, pady=(10, 2))
            role_icon = "🙂" if role == "user" else "🤖"
            title_text = (meta.get("title") or "(未命名)")
            title_text = title_text[:38] + ("…" if len(title_text) > 38 else "")
            ctk.CTkLabel(head_row,
                          text=f"{role_icon}  {title_text} · 第 {bubble_idx + 1} 条消息",
                          font=("Microsoft YaHei UI", 12, "bold"),
                          text_color=COLOR_TEXT,
                          anchor="w").pack(side="left")
            try:
                ts = datetime.fromtimestamp(meta.get("updated_at", 0)).strftime("%m-%d %H:%M")
            except Exception:
                ts = ""
            ctk.CTkLabel(head_row, text=ts, font=FONT_SMALL,
                          text_color=COLOR_MUTED).pack(side="right")

            # 片段：用 tk.Text 做关键词高亮
            body_row = ctk.CTkFrame(card, fg_color="transparent")
            body_row.pack(fill="x", padx=14, pady=(2, 10))
            card_bg = COLOR_CARD
            card_hv = COLOR_CARD_HOVER
            txt = tk.Text(body_row, height=2, wrap="word",
                           font=("Microsoft YaHei UI", 11),
                           bg=self._tk_theme_color(card_bg), fg=self._tk_theme_color(COLOR_TEXT_SOFT),
                           bd=0, relief="flat", padx=0, pady=0,
                           highlightthickness=0, cursor="hand2")
            txt.tag_configure("hl", foreground=COLOR_ACCENT,
                               font=("Microsoft YaHei UI", 11, "bold"))
            lk = kw.lower()
            cur = 0
            low = snippet.lower()
            while True:
                i = low.find(lk, cur)
                if i < 0:
                    txt.insert("end", snippet[cur:])
                    break
                txt.insert("end", snippet[cur:i])
                txt.insert("end", snippet[i:i + len(kw)], "hl")
                cur = i + len(kw)
            txt.configure(state="disabled")
            txt.pack(fill="x")

            def enter(e):
                card.configure(fg_color=card_hv)
                try: txt.configure(bg=self._tk_theme_color(card_hv))
                except Exception: pass
            def leave(e):
                card.configure(fg_color=card_bg)
                try: txt.configure(bg=self._tk_theme_color(card_bg))
                except Exception: pass

            def activate(e):
                try: dlg.destroy()
                except Exception: pass
                try:
                    self._load_session(meta)
                except Exception as exc:
                    messagebox.showerror("打开失败", str(exc))
                    return
                # 滚动到对应气泡
                self.after(180, lambda: self._jump_to_bubble(bubble_idx))

            for w in (card, head_row, body_row, *head_row.winfo_children(),
                       *body_row.winfo_children()):
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", activate)
            try:
                txt.bind("<Button-1>", activate)
                txt.bind("<Enter>", enter)
                txt.bind("<Leave>", leave)
            except Exception: pass

        def _run_search():
            kw = (q_var.get() or "").strip()
            if not kw:
                _show_status("输入关键词开始检索")
                return
            for w in results.winfo_children():
                try: w.destroy()
                except Exception: pass
            kl = kw.lower()
            try:
                metas = list_sessions(self.agent_dir.get())
            except Exception:
                metas = []
            hits = 0
            MAX_HITS = 80
            for meta in metas:
                if hits >= MAX_HITS: break
                try:
                    data = load_session(self.agent_dir.get(), meta["id"])
                except Exception:
                    data = None
                if not data: continue
                # 仅按真实消息命中出结果；同一会话内多条命中应全部展示。
                for i, b in enumerate(data.get("bubbles", []) or []):
                    if hits >= MAX_HITS: break
                    txt = b.get("text", "") or ""
                    if kl in txt.lower():
                        _make_result_card(results, meta, i,
                                           b.get("role", "assistant"),
                                           _snippet(txt, kw), kw)
                        hits += 1
            if hits == 0:
                _show_status(f"未找到包含“{kw}”的消息")

        def _on_key(_e=None):
            if debounce["job"] is not None:
                try: dlg.after_cancel(debounce["job"])
                except Exception: pass
            debounce["job"] = dlg.after(220, _run_search)

        entry.bind("<KeyRelease>", _on_key)
        entry.bind("<Return>", lambda e: _run_search())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        entry.focus_set()

    # ---------- 主聊天区（独立构建，便于在视图间切换） ----------
    def _build_chat_main(self, main):
        # 顶栏：右上角=功能（齿轮）
        top = ctk.CTkFrame(main, fg_color=COLOR_PANEL, corner_radius=0, height=56)
        top.pack(fill="x")
        top.pack_propagate(False)
        self.chat_top_bar = top
        self._functions_bar = None

        ctk.CTkLabel(top, text="GenericAgent",
                     font=("Microsoft YaHei UI", 14, "bold"),
                     text_color=COLOR_TEXT_SOFT).pack(side="left", padx=18)

        self.gear_btn = ctk.CTkButton(top, text="⚙", width=44, height=36,
                                       font=("Segoe UI Emoji", 18),
                                       corner_radius=8,
                                       fg_color="transparent",
                                       hover_color=COLOR_CARD,
                                       text_color=COLOR_TEXT_SOFT,
                                       command=self._open_functions_menu)
        self.gear_btn.pack(side="right", padx=10)
        self.theme_btn = ctk.CTkButton(top, text="", width=44, height=36,
                                        font=("Segoe UI Emoji", 16),
                                        corner_radius=8,
                                        fg_color="transparent",
                                        hover_color=COLOR_CARD,
                                        text_color=COLOR_TEXT_SOFT,
                                        command=self._toggle_appearance_mode)
        self.theme_btn.pack(side="right", padx=(0, 2))
        self._refresh_theme_button()

        # LLM 列表
        self.llm_var = ctk.StringVar(value="(无LLM)")

        # 消息区
        self.msg_area = ctk.CTkScrollableFrame(main, fg_color=COLOR_SURFACE,
                                                corner_radius=0)
        self.msg_area.pack(fill="both", expand=True)
        self.msg_area.columnconfigure(0, weight=1)
        self._msg_row = 0
        self._bubble_labels = []
        self._bubble_rows = []
        self._fold_sections = []
        self._current_assistant_label = None
        self._current_assistant_content = None
        self._live_label = None
        self._stream_frozen = 0
        self._pending_stream_text = None
        self._stream_render_scheduled = False
        self._last_wrap = None

        # 输入区
        bottom = ctk.CTkFrame(main, fg_color=COLOR_PANEL, corner_radius=0)
        bottom.pack(fill="x")
        inner = ctk.CTkFrame(bottom, fg_color=COLOR_CARD, corner_radius=14)
        inner.pack(fill="x", padx=16, pady=14)

        self.input_box = ctk.CTkTextbox(inner, height=78, font=FONT_BODY,
                                         fg_color=COLOR_CARD, border_width=0,
                                         wrap="word")
        self.input_box.pack(side="top", fill="both", expand=True,
                             padx=(12, 12), pady=(10, 4))
        self.input_box.bind("<Control-Return>", lambda e: (self._send(), "break"))

        tool_row = ctk.CTkFrame(inner, fg_color="transparent")
        tool_row.pack(side="top", fill="x", padx=10, pady=(2, 8))
        self.llm_tool_row = tool_row
        self.llm_menu = None
        self.llm_empty_label = None
        self._refresh_llm_selector()

        self.stop_btn = ctk.CTkButton(tool_row, text="中断", width=72, height=30,
                                       font=FONT_SMALL, corner_radius=8,
                                       fg_color="transparent",
                                       hover_color=COLOR_DANGER_HOVER,
                                       text_color=COLOR_DANGER_TEXT,
                                       command=self._abort)
        self.stop_btn.pack(side="right", padx=(6, 0))
        self.stop_btn.configure(state="disabled")

        self.send_btn = ctk.CTkButton(tool_row, text="发送",
                                       width=96, height=30, font=FONT_BTN,
                                       corner_radius=8, fg_color=COLOR_ACCENT,
                                       hover_color=COLOR_ACCENT_HOVER,
                                       command=self._send)
        self.send_btn.pack(side="right")

    # ---------- 设置视图（替换主区） ----------
    SETTINGS_CATEGORIES = [
        ("api",   "🔑  API"),
        ("channels", "💬  通讯渠道"),
        ("schedule", "⏰  定时任务"),
        ("usage", "📊  使用计数"),
        ("about", "ℹ  关于"),
    ]

    def _open_settings_view(self):
        self._close_functions_panel()
        if hasattr(self, "sidebar_host") and self.sidebar_host.winfo_exists():
            try:
                self.sidebar_host.grid_remove()
            except Exception:
                pass
        try:
            self.root_grid.grid_columnconfigure(0, minsize=0, weight=0)
        except Exception:
            pass
        # 清掉聊天主区
        for w in self.main_area.winfo_children():
            w.destroy()
        self._current_assistant_content = None

        # 顶栏
        top = ctk.CTkFrame(self.main_area, fg_color=COLOR_PANEL,
                            corner_radius=0, height=56)
        top.pack(fill="x")
        top.pack_propagate(False)
        back_text = "←  返回首页" if getattr(self, "_setup_mode_no_kernel", False) else "←  返回聊天"
        back_cmd = self.show_welcome if getattr(self, "_setup_mode_no_kernel", False) else self._back_to_chat
        ctk.CTkButton(top, text=back_text, height=34, width=120,
                       font=FONT_BTN, corner_radius=8,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_TEXT_SOFT,
                       command=back_cmd).pack(side="left", padx=14)
        ctk.CTkLabel(top, text="设置", font=("Microsoft YaHei UI", 15, "bold"),
                     text_color=COLOR_TEXT).pack(side="left", padx=8)

        # 双栏：左 nav | 右 content
        body = ctk.CTkFrame(self.main_area, fg_color=COLOR_SURFACE,
                             corner_radius=0)
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        nav = ctk.CTkFrame(body, fg_color=COLOR_SIDEBAR_BG, corner_radius=0,
                            width=220)
        nav.grid(row=0, column=0, sticky="nsw")
        nav.grid_propagate(False)
        ctk.CTkLabel(nav, text="分类", font=FONT_SMALL,
                     text_color=COLOR_MUTED, anchor="w").pack(
            fill="x", padx=18, pady=(16, 4))

        self.settings_content = ctk.CTkFrame(body, fg_color="transparent")
        self.settings_content.grid(row=0, column=1, sticky="nsew",
                                    padx=24, pady=20)

        self._settings_nav_buttons = {}
        for key, label in self.SETTINGS_CATEGORIES:
            btn = ctk.CTkButton(nav, text=label, anchor="w",
                                 height=36, font=FONT_BODY, corner_radius=8,
                                 fg_color="transparent",
                                 hover_color=COLOR_FIELD_ALT,
                                 text_color=COLOR_TEXT_SOFT,
                                 command=lambda k=key: self._show_settings_panel(k))
            btn.pack(fill="x", padx=10, pady=2)
            self._settings_nav_buttons[key] = btn

        self._show_settings_panel("api")

    def _show_settings_panel(self, key):
        self._current_settings_panel = key
        # 高亮当前 nav
        for k, b in self._settings_nav_buttons.items():
            if k == key:
                b.configure(fg_color=COLOR_ACTIVE, text_color=COLOR_TEXT)
            else:
                b.configure(fg_color="transparent", text_color=COLOR_TEXT_SOFT)
        for w in self.settings_content.winfo_children():
            w.destroy()
        if key == "api":
            self._build_api_panel(self.settings_content)
        elif key == "channels":
            self._build_channels_panel(self.settings_content)
        elif key == "schedule":
            self._build_schedule_panel(self.settings_content)
        elif key == "usage":
            self._build_usage_panel(self.settings_content)
        elif key == "about":
            self._build_about_panel(self.settings_content)
        else:
            ctk.CTkLabel(self.settings_content,
                         text="(此分类待实现)",
                         font=FONT_SUB, text_color=COLOR_MUTED).pack(pady=40)

    def _build_api_panel(self, parent):
        agent_dir = self.agent_dir.get().strip()
        py_path = os.path.join(agent_dir, "mykey.py")
        tpl_path = os.path.join(agent_dir, "mykey_template.py")
        if not os.path.isfile(py_path):
            if os.path.isfile(tpl_path):
                try:
                    open(py_path, "w", encoding="utf-8").write(
                        open(tpl_path, "r", encoding="utf-8").read())
                except Exception: pass
            else:
                open(py_path, "w", encoding="utf-8").write("# mykey.py\n")

        parsed = parse_mykey_py(py_path)
        self._api_hidden_configs = [
            {"var": c["var"], "kind": c["kind"], "data": dict(c["data"])}
            for c in parsed["configs"]
            if c["kind"] not in ("native_claude", "native_oai")
        ]
        self._api_state = [
            self._api_make_simple_state(c)
            for c in parsed["configs"]
            if c["kind"] in ("native_claude", "native_oai")
        ]
        if not self._api_state:
            self._api_add_channel("oai_chat")
        self._api_extras = dict(parsed["extras"])
        self._api_passthrough = list(parsed.get("passthrough") or [])
        self._api_py_path = py_path

        # ── 顶部 ──
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        title_box = ctk.CTkFrame(head, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(title_box, text="API 配置",
                    font=("Microsoft YaHei UI", 15, "bold"),
                    text_color=COLOR_TEXT).pack(anchor="w")
        ctk.CTkLabel(title_box, text=py_path,
                     font=FONT_SMALL, text_color=COLOR_MUTED).pack(anchor="w")
        ctk.CTkButton(head, text="⌨  直接编辑文件", height=28, width=128,
                       font=FONT_SMALL, corner_radius=6,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_MUTED,
                       command=self._build_api_panel_raw_switch).pack(side="right")

        if parsed["error"]:
            err = ctk.CTkFrame(parent, fg_color=COLOR_ERROR_BG, corner_radius=8)
            err.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(err,
                         text=f"⚠ 解析 mykey.py 失败：{parsed['error']}\n"
                              "保存会覆盖整个文件，建议先点右上「直接编辑文件」排查。",
                         font=FONT_SMALL, text_color=COLOR_ERROR_TEXT,
                         justify="left", wraplength=640).pack(
                fill="x", padx=12, pady=8)

        setup_reason = (getattr(self, "_api_setup_reason", "") or "").strip()
        if setup_reason:
            notice = ctk.CTkFrame(parent, fg_color=COLOR_INFO_BG, corner_radius=8)
            notice.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                notice,
                text=("当前还没有可用渠道，已自动为你创建一张默认 API 卡片。\n"
                      f"{setup_reason}"),
                font=FONT_SMALL, text_color=COLOR_INFO_TEXT,
                justify="left", anchor="w", wraplength=760
            ).pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(
            parent,
            text=("每张卡只保留五项：API 格式、链接模板、URL、Key、模型。\n"
                  "格式决定协议层（Claude / Chat / Responses），模板决定这个项目要套用的默认参数。"),
            font=FONT_SMALL, text_color=COLOR_MUTED,
            anchor="w", justify="left", wraplength=760
        ).pack(fill="x", pady=(0, 8))
        if self._api_hidden_configs:
            ctk.CTkLabel(
                parent,
                text=(f"检测到 {len(self._api_hidden_configs)} 条旧式或高级配置"
                      "（例如 Mixin / 旧协议）。简化卡片不会直接编辑它们，但保存时会原样保留。"),
                font=FONT_SMALL, text_color=COLOR_MUTED,
                anchor="w", justify="left", wraplength=760
            ).pack(fill="x", pady=(0, 8))
        if self._api_passthrough:
            ctk.CTkLabel(
                parent,
                text=(f"检测到 {len(self._api_passthrough)} 条表单不直接编辑的旧式原文项"
                      "（例如 cookie）。它们不会显示成卡片，但保存时会原样保留。"),
                font=FONT_SMALL, text_color=COLOR_MUTED,
                anchor="w", justify="left", wraplength=760
            ).pack(fill="x", pady=(0, 8))

        # ── 操作栏 ──
        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(bar, text="+  添加 API 卡片", height=32, width=140,
                      font=FONT_BTN, corner_radius=8,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      text_color=COLOR_ON_ACCENT,
                      command=self._api_add_channel_menu).pack(side="left")

        # ── 卡片列表 ──
        self._api_list = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        self._api_list.pack(fill="both", expand=True, pady=(0, 8))
        self._api_render_cards()

        # ── 底部按钮 ──
        btns = ctk.CTkFrame(parent, fg_color="transparent")
        btns.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(btns, text="仅保存", width=110, height=34,
                       fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                       text_color=COLOR_TEXT,
                       command=lambda: self._api_save(restart=False)).pack(
            side="right", padx=(6, 0))
        ctk.CTkButton(btns, text="保存并重启内核", width=160, height=34,
                       fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                       text_color=COLOR_ON_ACCENT,
                       command=lambda: self._api_save(restart=True)).pack(
            side="right")

    # ---------- API 面板：卡片渲染 ----------
    def _api_render_cards(self):
        for w in self._api_list.winfo_children():
            w.destroy()
        if not self._api_state:
            ctk.CTkLabel(self._api_list,
                         text="（还没有 API 卡片，点上方「+ 添加 API 卡片」开始）",
                         font=FONT_SMALL, text_color=COLOR_MUTED).pack(pady=30)
            return
        for idx, s in enumerate(self._api_state):
            self._api_build_card(s, idx)

    def _api_format_options(self):
        return [SIMPLE_FORMAT_LABEL[k] for k in ("claude_native", "oai_chat", "oai_responses")]

    def _api_format_from_label(self, label):
        for format_key, txt in SIMPLE_FORMAT_LABEL.items():
            if txt == label:
                return format_key
        return "oai_chat"

    def _api_format_meta(self, format_key):
        return SIMPLE_FORMAT_RULES.get(format_key) or SIMPLE_FORMAT_RULES["oai_chat"]

    def _api_template_choices(self, format_key):
        keys = self._api_format_meta(format_key).get("templates", [])
        return [(k, TEMPLATE_INDEX[k]["label"]) for k in keys if k in TEMPLATE_INDEX]

    def _api_infer_template_key(self, kind, data):
        choices = [
            (tpl_key, meta["label"])
            for tpl_key, meta in TEMPLATE_INDEX.items()
            if meta.get("kind") == kind
        ]
        best_key = None
        best_score = -1
        for tpl_key, _ in choices:
            defaults = dict(TEMPLATE_INDEX[tpl_key]["defaults"])
            if not defaults:
                continue
            matched = True
            score = 0
            for dk, dv in defaults.items():
                if dk == "apibase" and dv == "":
                    continue
                if data.get(dk) != dv:
                    matched = False
                    break
                score += 1
            if matched:
                if score > best_score:
                    best_key = tpl_key
                    best_score = score
        if best_key:
            return best_key
        return "custom-claude" if kind == "native_claude" else "custom-oai"

    def _api_infer_format_key(self, kind, data):
        if kind == "native_claude":
            return "claude_native"
        if kind == "native_oai":
            return "oai_responses" if data.get("api_mode") == "responses" else "oai_chat"
        return "oai_chat"

    def _api_prune_managed_extra(self, raw_extra, *, drop_template=False, drop_format=False):
        extra = dict(raw_extra or {})
        if drop_template:
            for key in TEMPLATE_MANAGED_KEYS:
                extra.pop(key, None)
        if drop_format:
            extra.pop("api_mode", None)
        return extra

    def _api_make_simple_state(self, cfg):
        data = dict(cfg.get("data") or {})
        kind = cfg.get("kind") or "native_oai"
        format_key = self._api_infer_format_key(kind, data)
        tpl_key = self._api_infer_template_key(kind, data)
        valid_tpl_keys = {k for k, _ in self._api_template_choices(format_key)}
        if tpl_key not in valid_tpl_keys:
            tpl_key = "custom-claude" if kind == "native_claude" else "custom-oai"
        raw_extra = dict(data)
        for k in ("name", "apikey", "apibase", "model"):
            raw_extra.pop(k, None)
        for k in TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}):
            raw_extra.pop(k, None)
        raw_extra.pop("api_mode", None)
        return {
            "var": cfg["var"],
            "format": format_key,
            "tpl_key": tpl_key,
            "apibase": data.get("apibase", ""),
            "apikey": data.get("apikey", ""),
            "model": data.get("model", TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "")),
            "raw_extra": raw_extra,
            "model_choices": [],
            "model_status": "",
            "model_fetching": False,
            "widgets": {},
            "vars": {},
            "card": None,
        }

    def _api_base_name(self, state, idx):
        raw = (state.get("apibase") or "").strip()
        host = ""
        if raw:
            try:
                parsed = urlparse(raw if "://" in raw else f"https://{raw}")
                host = (parsed.netloc or parsed.path.split("/", 1)[0]).strip()
            except Exception:
                host = ""
        if host:
            host = host.split("@")[-1].split(":", 1)[0].strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if host:
                return host
        tpl_key = state.get("tpl_key")
        label = TEMPLATE_INDEX.get(tpl_key, {}).get("label", "api")
        return f"{label}-{idx + 1}"

    def _api_build_save_configs(self):
        configs = []
        used_names = set()
        for idx, s in enumerate(self._api_state):
            fmt = self._api_format_meta(s.get("format"))
            tpl = TEMPLATE_INDEX.get(s.get("tpl_key"), {})
            kind = fmt.get("kind") or tpl.get("kind") or "native_oai"
            data = dict(tpl.get("defaults") or {})
            data.update(dict(s.get("raw_extra") or {}))
            api_mode = fmt.get("api_mode")
            if api_mode:
                data["api_mode"] = api_mode
            else:
                data.pop("api_mode", None)
            apibase = (s.get("apibase") or "").strip()
            apikey = (s.get("apikey") or "").strip()
            if apibase:
                data["apibase"] = apibase
            elif not data.get("apibase"):
                data.pop("apibase", None)
            if apikey:
                data["apikey"] = apikey
            else:
                data.pop("apikey", None)
            model = (s.get("model") or "").strip()
            if model:
                data["model"] = model
            else:
                data.pop("model", None)
            base_name = self._api_base_name(s, idx) or f"api-{idx + 1}"
            name = base_name
            serial = 2
            while name in used_names:
                name = f"{base_name}-{serial}"
                serial += 1
            used_names.add(name)
            data["name"] = name
            configs.append({"var": s["var"], "kind": kind, "data": data})
        return configs + list(getattr(self, "_api_hidden_configs", []))

    def _api_default_model_for_state(self, state):
        tpl_key = state.get("tpl_key")
        return str(TEMPLATE_INDEX.get(tpl_key, {}).get("defaults", {}).get("model", "") or "").strip()

    def _api_apply_template_model(self, state, previous_default=""):
        new_default = self._api_default_model_for_state(state)
        current = (state.get("model") or "").strip()
        if (not current) or (previous_default and current == previous_default):
            state["model"] = new_default

    def _api_fetch_models(self, state):
        base = (state.get("apibase") or "").strip()
        if not base:
            state["model_status"] = "请先填写 URL，再拉取模型。"
            self._api_render_cards()
            return
        if state.get("model_fetching"):
            return
        state["model_fetching"] = True
        state["model_status"] = "正在拉取模型列表…"
        self._api_render_cards()

        def worker():
            try:
                models = _fetch_remote_models(
                    state.get("format"),
                    state.get("apibase"),
                    state.get("apikey"),
                )
                def done_ok():
                    state["model_fetching"] = False
                    state["model_choices"] = models
                    if models and not (state.get("model") or "").strip():
                        state["model"] = models[0]
                    state["model_status"] = f"已拉取 {len(models)} 个模型，可直接选择或继续手输。"
                    if hasattr(self, "_api_list") and self._api_list.winfo_exists():
                        self._api_render_cards()
                self.after(0, done_ok)
            except HTTPError as e:
                try:
                    body = e.read().decode("utf-8", errors="replace").strip()
                except Exception:
                    body = ""
                msg = f"拉取失败：HTTP {e.code}"
                if body:
                    msg += f" · {body[:180]}"
                def done_err():
                    state["model_fetching"] = False
                    state["model_status"] = msg
                    if hasattr(self, "_api_list") and self._api_list.winfo_exists():
                        self._api_render_cards()
                self.after(0, done_err)
            except URLError as e:
                def done_url_err():
                    state["model_fetching"] = False
                    state["model_status"] = f"拉取失败：{e.reason}"
                    if hasattr(self, "_api_list") and self._api_list.winfo_exists():
                        self._api_render_cards()
                self.after(0, done_url_err)
            except Exception as e:
                def done_generic_err():
                    state["model_fetching"] = False
                    state["model_status"] = f"拉取失败：{e}"
                    if hasattr(self, "_api_list") and self._api_list.winfo_exists():
                        self._api_render_cards()
                self.after(0, done_generic_err)

        threading.Thread(target=worker, daemon=True).start()

    def _api_build_card(self, state, idx):
        card = ctk.CTkFrame(self._api_list, fg_color=COLOR_CARD,
                             corner_radius=12)
        card.pack(fill="x", pady=6)
        state["card"] = card

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(10, 4))
        format_lbl = SIMPLE_FORMAT_LABEL.get(state.get("format"), state.get("format", ""))
        tpl_lbl = TEMPLATE_INDEX.get(state.get("tpl_key"), {}).get("label", "未选择模板")
        ctk.CTkLabel(head, text=f"● API 卡片 {idx + 1}",
                    font=("Microsoft YaHei UI", 13, "bold"),
                    text_color=COLOR_TEXT).pack(side="left")
        ctk.CTkLabel(head, text=f"  {format_lbl}  ·  {tpl_lbl}",
                     font=FONT_SMALL, text_color=COLOR_MUTED).pack(side="left")
        ctk.CTkButton(head, text="删除", width=60, height=26,
                      font=FONT_SMALL, corner_radius=6,
                      fg_color="transparent", hover_color=COLOR_DANGER_HOVER,
                      text_color=COLOR_DANGER_TEXT,
                      command=lambda i=idx: self._api_delete(i)).pack(side="right")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(0, 10))
        row1 = ctk.CTkFrame(body, fg_color="transparent")
        row1.pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(row1, text="API 格式", font=FONT_SMALL, width=90,
                    anchor="w", text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
        format_var = tk.StringVar(value=SIMPLE_FORMAT_LABEL.get(state.get("format"), "Chat Completions"))
        format_menu = ctk.CTkOptionMenu(
            row1, variable=format_var, values=self._api_format_options(),
            width=180, height=30, font=FONT_BODY,
            fg_color=COLOR_FIELD_BG, button_color=COLOR_ACTIVE,
            button_hover_color=COLOR_ACTIVE_HOVER, dropdown_fg_color=COLOR_POPUP_BG,
            dropdown_hover_color=COLOR_CARD_HOVER, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT)
        format_menu.pack(side="left")

        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row2, text="链接模板", font=FONT_SMALL, width=90,
                    anchor="w", text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
        tpl_choices = self._api_template_choices(state.get("format"))
        tpl_label_map = {k: lbl for k, lbl in tpl_choices}
        tpl_rev_map = {lbl: k for k, lbl in tpl_choices}
        current_tpl_label = tpl_label_map.get(state.get("tpl_key"), tpl_choices[0][1] if tpl_choices else "未选择")
        tpl_var = tk.StringVar(value=current_tpl_label)
        tpl_menu = ctk.CTkOptionMenu(
            row2, variable=tpl_var, values=[lbl for _, lbl in tpl_choices],
            width=260, height=30, font=FONT_BODY,
            fg_color=COLOR_FIELD_BG, button_color=COLOR_ACTIVE,
            button_hover_color=COLOR_ACTIVE_HOVER, dropdown_fg_color=COLOR_POPUP_BG,
            dropdown_hover_color=COLOR_CARD_HOVER, dropdown_text_color=COLOR_TEXT,
            text_color=COLOR_TEXT)
        tpl_menu.pack(side="left")

        row3 = ctk.CTkFrame(body, fg_color="transparent")
        row3.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row3, text="URL", font=FONT_SMALL, width=90,
                    anchor="w", text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
        apibase_var = tk.StringVar(value=state.get("apibase", ""))
        apibase_ent = ctk.CTkEntry(row3, textvariable=apibase_var, height=30,
                                   font=FONT_BODY, corner_radius=6)
        apibase_ent.pack(side="left", fill="x", expand=True)

        row4 = ctk.CTkFrame(body, fg_color="transparent")
        row4.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(row4, text="模型", font=FONT_SMALL, width=90,
                    anchor="w", text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
        model_value = (state.get("model") or self._api_default_model_for_state(state) or "").strip()
        if model_value and model_value not in state.get("model_choices", []):
            state["model_choices"] = [model_value] + [m for m in state.get("model_choices", []) if m != model_value]
        model_var = tk.StringVar(value=model_value)
        model_combo = ctk.CTkComboBox(
            row4, variable=model_var,
            values=state.get("model_choices") or [model_value or ""],
            height=30, font=FONT_BODY, corner_radius=6,
            fg_color=COLOR_FIELD_BG, border_color=COLOR_DIVIDER,
            button_color=COLOR_ACTIVE, button_hover_color=COLOR_ACTIVE_HOVER,
            dropdown_fg_color=COLOR_POPUP_BG, dropdown_hover_color=COLOR_CARD_HOVER,
            dropdown_text_color=COLOR_TEXT, text_color=COLOR_TEXT)
        model_combo.pack(side="left", fill="x", expand=True)
        fetch_text = "拉取中…" if state.get("model_fetching") else "拉取模型"
        fetch_btn = ctk.CTkButton(
            row4, text=fetch_text, width=92, height=30,
            font=FONT_SMALL, corner_radius=6,
            fg_color=COLOR_CARD_HOVER, hover_color=COLOR_ACTIVE_HOVER,
            text_color=COLOR_TEXT,
            state=("disabled" if state.get("model_fetching") else "normal"),
            command=lambda s=state: self._api_fetch_models(s))
        fetch_btn.pack(side="left", padx=(6, 0))

        model_status = ctk.CTkLabel(body, text=(state.get("model_status") or ""),
                                    font=FONT_SMALL, text_color=COLOR_MUTED,
                                    anchor="w", justify="left", wraplength=640)
        model_status.pack(fill="x", pady=(0, 4))

        row5 = ctk.CTkFrame(body, fg_color="transparent")
        row5.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(row5, text="API Key", font=FONT_SMALL, width=90,
                    anchor="w", text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
        apikey_var = tk.StringVar(value=state.get("apikey", ""))
        apikey_ent = ctk.CTkEntry(row5, textvariable=apikey_var, height=30,
                                  font=FONT_BODY, corner_radius=6, show="*")
        apikey_ent.pack(side="left", fill="x", expand=True)
        shown = {"v": False}
        def toggle_show():
            shown["v"] = not shown["v"]
            apikey_ent.configure(show="" if shown["v"] else "*")
            eye_btn.configure(text=("🙈" if shown["v"] else "👁"))
        eye_btn = ctk.CTkButton(row5, text="👁", width=34, height=30,
                                font=FONT_SMALL, fg_color=COLOR_CARD_HOVER,
                                hover_color=COLOR_ACTIVE_HOVER, text_color=COLOR_TEXT,
                                command=toggle_show)
        eye_btn.pack(side="left", padx=(4, 0))

        summary = ctk.CTkLabel(body, text="", font=FONT_SMALL,
                               text_color=COLOR_MUTED, anchor="w",
                               justify="left", wraplength=640)
        summary.pack(fill="x", pady=(8, 0))

        def sync_summary():
            fmt = self._api_format_meta(state.get("format"))
            tpl_key = state.get("tpl_key")
            defaults = dict(TEMPLATE_INDEX.get(tpl_key, {}).get("defaults") or {})
            model = (state.get("model") or defaults.get("model") or "请手动填写模型名").strip()
            notes = []
            if defaults.get("fake_cc_system_prompt"):
                notes.append("自动带 Claude Code 兼容参数")
            if fmt.get("api_mode"):
                notes.append(f"api_mode={fmt['api_mode']}")
            if defaults.get("read_timeout"):
                notes.append(f"read_timeout={defaults['read_timeout']}")
            hint = "，".join(notes) if notes else "自动写入模板里的默认参数"
            proto_hint = fmt.get("hint", "")
            summary.configure(text=f"{proto_hint} 模板会自动带出模型与兼容参数：{model}；{hint}。")

        def on_format_change(choice):
            format_key = self._api_format_from_label(choice)
            previous_default = self._api_default_model_for_state(state)
            state["format"] = format_key
            state["model_status"] = ""
            state["raw_extra"] = self._api_prune_managed_extra(
                state.get("raw_extra"),
                drop_template=True,
                drop_format=True,
            )
            new_choices = self._api_template_choices(format_key)
            new_label_map = {k: lbl for k, lbl in new_choices}
            nonlocal tpl_label_map, tpl_rev_map
            tpl_label_map = new_label_map
            tpl_rev_map = {lbl: k for k, lbl in new_choices}
            current_key = state.get("tpl_key")
            if current_key not in tpl_label_map:
                current_key = self._api_format_meta(format_key).get(
                    "default_template",
                    new_choices[0][0] if new_choices else "",
                )
            state["tpl_key"] = current_key
            self._api_apply_template_model(state, previous_default)
            tpl_menu.configure(values=[lbl for _, lbl in new_choices])
            tpl_var.set(tpl_label_map.get(current_key, "未选择"))
            sync_summary()
            self._api_render_cards()

        def on_tpl_change(choice):
            previous_default = self._api_default_model_for_state(state)
            state["tpl_key"] = tpl_rev_map.get(choice, state.get("tpl_key"))
            state["model_status"] = ""
            state["raw_extra"] = self._api_prune_managed_extra(
                state.get("raw_extra"),
                drop_template=True,
                drop_format=False,
            )
            self._api_apply_template_model(state, previous_default)
            sync_summary()
            self._api_render_cards()

        def on_base_change(*_):
            state["apibase"] = apibase_var.get()
            state["model_status"] = ""

        def on_key_change(*_):
            state["apikey"] = apikey_var.get()
            state["model_status"] = ""

        def on_model_change(*_):
            state["model"] = model_var.get().strip()
            if not state.get("model_fetching"):
                state["model_status"] = ""

        format_menu.configure(command=on_format_change)
        tpl_menu.configure(command=on_tpl_change)
        apibase_var.trace_add("write", on_base_change)
        apikey_var.trace_add("write", on_key_change)
        model_var.trace_add("write", on_model_change)
        state["vars"] = {
            "format": format_var,
            "template": tpl_var,
            "apibase": apibase_var,
            "apikey": apikey_var,
            "model": model_var,
        }
        sync_summary()

    def _api_field_row(self, parent, state, key):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(6, 2))
        ctk.CTkLabel(row, text=key, font=FONT_SMALL, width=160, anchor="w",
                    text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))

        kind = FIELD_KIND.get(key, "text")
        cur = state["data"].get(key, None)

        if kind == "bool":
            var = tk.BooleanVar(value=bool(cur) if cur is not None else False)
            def on_switch(v=var, k=key, had=cur is not None):
                if not v.get() and not had:
                    state["data"].pop(k, None)
                else:
                    state["data"][k] = v.get()
            sw = ctk.CTkSwitch(row, text="", variable=var, onvalue=True,
                                offvalue=False, width=50, command=on_switch,
                                progress_color=COLOR_ACCENT)
            sw.pack(side="left")
            state["vars"][key] = var

        elif kind.startswith("enum:"):
            opts = kind.split(":", 1)[1].split(",")
            display_opts = [(o if o else "(默认)") for o in opts]
            rev = {d: o for d, o in zip(display_opts, opts)}
            init_real = cur if cur in opts else ""
            init_disp = "(默认)" if init_real == "" else init_real
            var = tk.StringVar(value=init_disp)
            def on_enum(choice, k=key, r=rev):
                real = r.get(choice, "")
                if real == "":
                    state["data"].pop(k, None)
                else:
                    state["data"][k] = real
            om = ctk.CTkOptionMenu(row, variable=var, values=display_opts,
                                    command=on_enum, width=200, height=30,
                                    fg_color=COLOR_FIELD_BG,
                                    button_color=COLOR_ACTIVE,
                                    button_hover_color=COLOR_ACTIVE_HOVER,
                                    dropdown_fg_color=COLOR_POPUP_BG,
                                    dropdown_hover_color=COLOR_CARD_HOVER,
                                    dropdown_text_color=COLOR_TEXT,
                                    text_color=COLOR_TEXT)
            om.pack(side="left")
            state["vars"][key] = var

        elif kind == "list":
            var = tk.StringVar(value=", ".join(str(x) for x in (cur or [])))
            def on_list(*_, v=var, k=key):
                raw = v.get().strip()
                items = [x.strip() for x in raw.split(",") if x.strip()]
                if items:
                    state["data"][k] = items
                else:
                    state["data"].pop(k, None)
            var.trace_add("write", on_list)
            ent = ctk.CTkEntry(row, textvariable=var, height=30,
                                font=FONT_BODY, corner_radius=6,
                                placeholder_text="逗号分隔，如 cc-relay-1, gpt-native")
            ent.pack(side="left", fill="x", expand=True)
            state["vars"][key] = var

        elif kind in ("int", "float"):
            var = tk.StringVar(value=("" if cur is None else str(cur)))
            def on_num(*_, v=var, k=key, kd=kind):
                raw = v.get().strip()
                if raw == "":
                    state["data"].pop(k, None); return
                try:
                    state["data"][k] = int(raw) if kd == "int" else float(raw)
                except ValueError:
                    state["data"][k] = raw  # 保留，保存时再清理
            var.trace_add("write", on_num)
            ent = ctk.CTkEntry(row, textvariable=var, height=30, width=200,
                                font=FONT_BODY, corner_radius=6)
            ent.pack(side="left")
            state["vars"][key] = var

        else:  # text / password
            var = tk.StringVar(value=("" if cur is None else str(cur)))
            show = "*" if kind == "password" else ""
            def on_text(*_, v=var, k=key):
                val = v.get()
                if val == "":
                    state["data"].pop(k, None)
                else:
                    state["data"][k] = val
                if k == "name":
                    self._api_refresh_card_title(state)
            var.trace_add("write", on_text)
            ent = ctk.CTkEntry(row, textvariable=var, height=30,
                                font=FONT_BODY, corner_radius=6, show=show)
            ent.pack(side="left", fill="x", expand=True)
            state["vars"][key] = var
            if kind == "password":
                shown = {"v": False}
                eye_btn = None
                def toggle_show(e=ent, s=shown):
                    s["v"] = not s["v"]
                    e.configure(show="" if s["v"] else "*")
                    eye_btn.configure(text=("🙈" if s["v"] else "👁"))
                eye_btn = ctk.CTkButton(row, text="👁", width=34, height=30,
                                         font=FONT_SMALL,
                                         fg_color=COLOR_CARD_HOVER,
                                         hover_color=COLOR_ACTIVE_HOVER,
                                         text_color=COLOR_TEXT,
                                         command=toggle_show)
                eye_btn.pack(side="left", padx=(4, 0))

        hint = FIELD_HELP.get(key, "")
        if hint:
            hint_row = ctk.CTkFrame(parent, fg_color="transparent")
            hint_row.pack(fill="x")
            ctk.CTkLabel(hint_row, text="", width=160).pack(side="left",
                                                             padx=(0, 8))
            ctk.CTkLabel(hint_row, text=hint, font=FONT_SMALL,
                         text_color=COLOR_MUTED, anchor="w",
                         justify="left", wraplength=520).pack(
                side="left", fill="x", expand=True)

    def _api_refresh_card_title(self, state):
        card = state.get("card")
        if not card or not card.winfo_exists(): return
        try:
            head = card.winfo_children()[0]
            name_lbl = head.winfo_children()[0]
            nm = state["data"].get("name") or state["var"]
            name_lbl.configure(text=f"● {nm}")
        except Exception:
            pass

    def _api_delete(self, idx):
        try:
            if 0 <= idx < len(self._api_state):
                del self._api_state[idx]
                self._api_render_cards()
        except Exception:
            pass

    def _api_add_channel_menu(self):
        items = [
            ("Claude 原生", lambda: self._api_add_channel("claude_native")),
            ("Chat Completions", lambda: self._api_add_channel("oai_chat")),
            ("Responses", lambda: self._api_add_channel("oai_responses")),
        ]
        try:
            self._popup_menu(self.winfo_pointerx(), self.winfo_pointery(),
                             items, min_width=220)
        except Exception:
            self._api_add_channel("oai_chat")

    def _api_add_channel(self, format_key):
        fmt = self._api_format_meta(format_key)
        kind = fmt.get("kind")
        if kind not in ("native_claude", "native_oai"):
            return
        existing = {s["var"] for s in self._api_state}
        existing.update({c["var"] for c in getattr(self, "_api_hidden_configs", [])})
        var = auto_config_var(kind, existing)
        tpl_key = fmt.get("default_template", "openai")
        defaults = dict(TEMPLATE_INDEX.get(tpl_key, {}).get("defaults") or {})
        self._api_state.append({
            "var": var,
            "format": format_key,
            "tpl_key": tpl_key,
            "apibase": defaults.get("apibase", ""),
            "apikey": "",
            "raw_extra": {},
            "widgets": {},
            "vars": {},
            "card": None,
        })
        if hasattr(self, "_api_list") and self._api_list.winfo_exists():
            self._api_render_cards()

    def _api_save(self, restart=False):
        try:
            txt = serialize_mykey_py(
                configs=self._api_build_save_configs(),
                extras=self._api_extras,
                passthrough=self._api_passthrough,
            )
            open(self._api_py_path, "w", encoding="utf-8").write(txt)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        restarted = self._restart_running_channels(show_errors=False)
        if restart:
            self._restart_bridge()
        else:
            extra_msg = ""
            if restarted:
                extra_msg = f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            messagebox.showinfo("已保存",
                "已写入 mykey.py。\n需要重启内核才能生效（可点「保存并重启内核」）。"
                + extra_msg)

    def _build_api_panel_raw_switch(self):
        for w in self.settings_content.winfo_children():
            w.destroy()
        self._build_api_panel_raw(self.settings_content)

    def _build_api_panel_raw(self, parent):
        """保底：当表单覆盖不到时，直接编辑 mykey.py 文本。"""
        agent_dir = self.agent_dir.get().strip()
        py_path = os.path.join(agent_dir, "mykey.py")
        try:
            content = open(py_path, "r", encoding="utf-8").read()
        except Exception as e:
            ctk.CTkLabel(parent, text=f"读取失败：{e}",
                        text_color=COLOR_ERROR_TEXT).pack(pady=20)
            return

        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))
        ctk.CTkButton(head, text="←  返回表单", height=28, width=112,
                       font=FONT_SMALL, corner_radius=6,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_MUTED,
                       command=lambda: self._show_settings_panel("api")
                       ).pack(side="left")
        ctk.CTkLabel(head, text="直接编辑 mykey.py",
                    font=("Microsoft YaHei UI", 15, "bold"),
                    text_color=COLOR_TEXT).pack(
            side="left", padx=8)
        ctk.CTkLabel(head, text=py_path, font=FONT_SMALL,
                     text_color=COLOR_MUTED).pack(side="right")

        tb = ctk.CTkTextbox(parent, font=("Consolas", 11),
                             fg_color=COLOR_FIELD_BG, text_color=COLOR_CODE_TEXT,
                             wrap="none", corner_radius=10)
        tb.pack(fill="both", expand=True)
        tb.insert("1.0", content)

        btns = ctk.CTkFrame(parent, fg_color="transparent")
        btns.pack(fill="x", pady=(10, 0))

        def save_only():
            try:
                open(py_path, "w", encoding="utf-8").write(tb.get("1.0", "end-1c"))
                messagebox.showinfo("已保存",
                    "已写入 mykey.py。\n需要重启内核才能生效。")
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
        def save_and_restart():
            try:
                open(py_path, "w", encoding="utf-8").write(tb.get("1.0", "end-1c"))
            except Exception as e:
                messagebox.showerror("保存失败", str(e)); return
            self._restart_bridge()

        ctk.CTkButton(btns, text="仅保存", width=110, height=34,
                       fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                       command=save_only).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btns, text="保存并重启内核", width=160, height=34,
                       fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                       command=save_and_restart).pack(side="right")

    def _channel_cfg_bucket(self):
        bucket = self.cfg.get("communication_channels")
        if not isinstance(bucket, dict):
            bucket = {}
            self.cfg["communication_channels"] = bucket
        return bucket

    def _channel_runtime_cfg(self, channel_id):
        bucket = self._channel_cfg_bucket()
        item = bucket.get(channel_id)
        if not isinstance(item, dict):
            item = {}
            bucket[channel_id] = item
        return item

    def _channel_is_auto_start(self, channel_id):
        return bool(self._channel_runtime_cfg(channel_id).get("auto_start", False))

    def _channel_set_auto_start(self, channel_id, enabled, persist=True):
        self._channel_runtime_cfg(channel_id)["auto_start"] = bool(enabled)
        if persist:
            save_config(self.cfg)

    def _channel_format_value(self, field, value):
        if field.get("kind") in ("list_str", "list_int"):
            if not isinstance(value, (list, tuple)):
                return ""
            return ", ".join(str(x) for x in value if str(x).strip())
        return "" if value is None else str(value)

    def _channel_parse_value(self, field, raw):
        text = (raw or "").strip()
        kind = field.get("kind", "text")
        if kind == "list_str":
            return [item.strip() for item in text.split(",") if item.strip()]
        if kind == "list_int":
            out = []
            for item in text.split(","):
                item = item.strip()
                if not item:
                    continue
                out.append(int(item) if re.fullmatch(r"-?\d+", item) else item)
            return out
        return text

    def _channel_field_label(self, channel_id, key):
        spec = COMM_CHANNEL_INDEX.get(channel_id, {})
        for field in spec.get("fields", []):
            if field.get("key") == key:
                return field.get("label", key)
        return key

    def _wx_token_path(self):
        return WX_TOKEN_PATH

    def _wx_token_info(self):
        path = self._wx_token_path()
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_wx_token_info(self, payload):
        path = self._wx_token_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._wx_token_info()
        current.update(payload or {})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)

    def _open_wechat_qr_dialog(self, save_pending=True, show_errors=True, restart_after=False):
        if save_pending and getattr(self, "_current_settings_panel", "") == "channels":
            if not self._channels_save(silent=True, apply_running=False):
                return False
        try:
            resp = requests.get(f"{WX_BOT_API}/ilink/bot/get_bot_qrcode",
                                params={"bot_type": 3}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            qr_id = data["qrcode"]
            qr_text = data.get("qrcode_img_content", "")
            if not qr_text:
                raise RuntimeError("接口没有返回二维码内容。")
        except Exception as e:
            if show_errors:
                messagebox.showerror("微信二维码获取失败", str(e))
            return False

        dlg = ctk.CTkToplevel(self)
        dlg.title("微信扫码登录")
        dlg.geometry("420x560")
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(fg_color=COLOR_SURFACE)

        status_var = ctk.StringVar(value="请使用微信扫码，确认后会自动完成绑定。")
        detail_var = ctk.StringVar(value=f"二维码 ID: {qr_id}")
        done = {"v": False}
        stop_event = threading.Event()

        wrap = ctk.CTkFrame(dlg, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(wrap, text="微信扫码登录",
                     font=("Microsoft YaHei UI", 18, "bold"),
                     text_color=COLOR_TEXT).pack(anchor="w")
        ctk.CTkLabel(
            wrap,
            text="这是上游个人微信 Bot 的登录二维码。扫码并在手机上确认后，启动器会写入绑定缓存，然后再启动微信渠道。",
            font=FONT_SMALL, text_color=COLOR_MUTED,
            justify="left", anchor="w", wraplength=360
        ).pack(fill="x", pady=(6, 12))

        qr_box = ctk.CTkFrame(wrap, fg_color=COLOR_CARD, corner_radius=12)
        qr_box.pack(fill="x", pady=(0, 12))
        qr_img = qrcode.make(qr_text).convert("RGB")
        qr_size = 280
        ctk_img = ctk.CTkImage(light_image=qr_img, dark_image=qr_img, size=(qr_size, qr_size))
        img_label = ctk.CTkLabel(qr_box, text="", image=ctk_img)
        img_label.image = ctk_img
        img_label.pack(padx=20, pady=20)

        ctk.CTkLabel(wrap, textvariable=status_var,
                     font=FONT_BODY, text_color=COLOR_TEXT,
                     justify="left", wraplength=360).pack(fill="x")
        ctk.CTkLabel(wrap, textvariable=detail_var,
                     font=FONT_SMALL, text_color=COLOR_MUTED,
                     justify="left", wraplength=360).pack(fill="x", pady=(6, 0))

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.pack(fill="x", side="bottom", pady=(18, 0))

        def close_dialog():
            stop_event.set()
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()

        def restart_login():
            close_dialog()
            self.after(80, lambda: self._open_wechat_qr_dialog(
                save_pending=False,
                show_errors=show_errors,
                restart_after=restart_after
            ))

        ctk.CTkButton(btns, text="关闭", width=96, height=34,
                      fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                      text_color=COLOR_TEXT, command=close_dialog).pack(side="left")
        ctk.CTkButton(btns, text="重新获取", width=110, height=34,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      text_color=COLOR_ON_ACCENT, command=restart_login).pack(side="right")

        def on_confirm(payload):
            if done["v"]:
                return
            done["v"] = True
            stop_event.set()
            bot_token = str(payload.get("bot_token", "") or "").strip()
            bot_id = str(payload.get("ilink_bot_id", "") or "").strip()
            self._save_wx_token_info({
                "bot_token": bot_token,
                "ilink_bot_id": bot_id,
                "login_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()
            if restart_after and self._channel_proc_alive("wechat"):
                self._stop_channel_process("wechat", refresh=False)
            self.after(80, lambda: self._start_channel_process(
                "wechat", save_pending=False, notify=True, show_errors=show_errors
            ))

        def poll_status():
            last_status = ""
            while not stop_event.is_set():
                time.sleep(2)
                if stop_event.is_set():
                    return
                try:
                    resp = requests.get(f"{WX_BOT_API}/ilink/bot/get_qrcode_status",
                                        params={"qrcode": qr_id}, timeout=60)
                    payload = resp.json()
                except requests.exceptions.ReadTimeout:
                    continue
                except Exception as e:
                    self.after(0, lambda msg=str(e): status_var.set(f"轮询失败：{msg}"))
                    continue
                status = str(payload.get("status", "") or "")
                if status != last_status:
                    last_status = status
                    self.after(0, lambda st=status: status_var.set(f"当前状态：{st or '等待扫码'}"))
                if status == "confirmed":
                    self.after(0, lambda p=payload: on_confirm(p))
                    return
                if status == "expired":
                    self.after(0, lambda: (
                        status_var.set("二维码已过期，请点“重新获取”。"),
                        detail_var.set(f"二维码 ID: {qr_id}"),
                    ))
                    return

        dlg.protocol("WM_DELETE_WINDOW", close_dialog)
        threading.Thread(target=poll_status, daemon=True).start()
        return True

    def _load_channels_source(self):
        agent_dir = self.agent_dir.get().strip()
        py_path = os.path.join(agent_dir, "mykey.py")
        tpl_path = os.path.join(agent_dir, "mykey_template.py")
        if os.path.isdir(agent_dir) and not os.path.isfile(py_path):
            if os.path.isfile(tpl_path):
                try:
                    open(py_path, "w", encoding="utf-8").write(
                        open(tpl_path, "r", encoding="utf-8").read())
                except Exception:
                    pass
            else:
                try:
                    open(py_path, "w", encoding="utf-8").write("# mykey.py\n")
                except Exception:
                    pass
        parsed = parse_mykey_py(py_path)
        return agent_dir, py_path, parsed

    def _channel_values_from_state(self, channel_id, fallback=None):
        fallback = dict(fallback or {})
        state = getattr(self, "_comm_channel_states", {}).get(channel_id)
        if not state:
            return fallback
        out = dict(fallback)
        for field in COMM_CHANNEL_INDEX.get(channel_id, {}).get("fields", []):
            key = field.get("key")
            var = state.get("vars", {}).get(key)
            if var is None:
                continue
            out[key] = self._channel_parse_value(field, var.get())
        return out

    def _channel_missing_required(self, channel_id, values):
        spec = COMM_CHANNEL_INDEX.get(channel_id, {})
        missing = []
        for key in spec.get("required", []):
            value = values.get(key)
            if isinstance(value, (list, tuple, set)):
                if len(value) == 0:
                    missing.append(self._channel_field_label(channel_id, key))
            elif not str(value or "").strip():
                missing.append(self._channel_field_label(channel_id, key))
        return missing

    def _channel_log_path(self, channel_id, agent_dir=None):
        agent_dir = agent_dir or self.agent_dir.get().strip()
        base = os.path.join(agent_dir, "temp", "launcher_channels")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, f"{channel_id}.log")

    def _channel_tail_log(self, channel_id, limit=900):
        info = self._channel_procs.get(channel_id) or {}
        log_path = info.get("log_path")
        if not log_path or not os.path.isfile(log_path):
            return ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return data[-limit:].strip()
        except Exception:
            return ""

    def _channel_proc_alive(self, channel_id):
        info = self._channel_procs.get(channel_id)
        proc = (info or {}).get("proc")
        return bool(proc and proc.poll() is None)

    def _channel_conflict_message(self, channel_id):
        spec = COMM_CHANNEL_INDEX.get(channel_id, {})
        for other_id in spec.get("conflicts_with", []):
            if self._channel_proc_alive(other_id):
                other = COMM_CHANNEL_INDEX.get(other_id, {}).get("label", other_id)
                return f"{spec.get('label', channel_id)} 与 {other} 在上游共用单实例锁，不能同时启动。"
        return ""

    def _channel_status(self, channel_id, values):
        if self._channel_proc_alive(channel_id):
            return "运行中", COLOR_OK_TEXT
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            return "冲突", COLOR_WARNING_TEXT
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            return "待配置", COLOR_WARNING_TEXT
        info = self._channel_procs.get(channel_id) or {}
        proc = info.get("proc")
        if proc and proc.poll() is not None:
            return f"已退出 ({proc.returncode})", COLOR_WARNING_TEXT
        if self._channel_is_auto_start(channel_id):
            return "待自动启动", COLOR_INFO_TEXT
        return "未启动", COLOR_MUTED

    def _close_channel_log_handle(self, channel_id):
        info = self._channel_procs.get(channel_id) or {}
        handle = info.get("log_handle")
        if handle:
            try:
                handle.close()
            except Exception:
                pass
            info["log_handle"] = None

    def _stop_channel_process(self, channel_id, refresh=True):
        info = self._channel_procs.get(channel_id)
        if not info:
            return False
        proc = info.get("proc")
        stopped = False
        try:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            stopped = True
        finally:
            self._close_channel_log_handle(channel_id)
            self._channel_procs.pop(channel_id, None)
            if refresh:
                self._refresh_channels_panel_if_open()
        return stopped

    def _stop_all_managed_channels(self, refresh=True):
        count = 0
        for channel_id in list(self._channel_procs.keys()):
            if self._stop_channel_process(channel_id, refresh=False):
                count += 1
        if refresh:
            self._refresh_channels_panel_if_open()
        return count

    def _stop_managed_channels_not_matching(self, agent_dir):
        for channel_id, info in list(self._channel_procs.items()):
            if os.path.normcase(str(info.get("agent_dir") or "")) != os.path.normcase(str(agent_dir or "")):
                self._stop_channel_process(channel_id, refresh=False)

    def _restart_running_channels(self, show_errors=False):
        running = [channel_id for channel_id in self._channel_procs if self._channel_proc_alive(channel_id)]
        restarted = 0
        for channel_id in running:
            self._stop_channel_process(channel_id, refresh=False)
            if self._start_channel_process(channel_id, save_pending=False, notify=False, show_errors=show_errors):
                restarted += 1
        self._refresh_channels_panel_if_open()
        return restarted

    def _after_channel_launch_check(self, channel_id, show_errors=True):
        info = self._channel_procs.get(channel_id)
        if not info:
            return
        proc = info.get("proc")
        if not proc or proc.poll() is None:
            self._refresh_channels_panel_if_open()
            return
        self._close_channel_log_handle(channel_id)
        spec = COMM_CHANNEL_INDEX.get(channel_id, {})
        tail = self._channel_tail_log(channel_id)
        self._channel_procs.pop(channel_id, None)
        self._refresh_channels_panel_if_open()
        if show_errors:
            detail = f"\n\n日志尾部：\n{tail}" if tail else ""
            messagebox.showerror("渠道启动失败",
                                 f"{spec.get('label', channel_id)} 已退出，返回码 {proc.returncode}.{detail}")

    def _start_channel_process(self, channel_id, save_pending=True, notify=True, show_errors=True):
        spec = COMM_CHANNEL_INDEX.get(channel_id)
        if not spec:
            return False
        if save_pending and getattr(self, "_current_settings_panel", "") == "channels":
            if not self._channels_save(silent=True, apply_running=False):
                return False
        if channel_id == "wechat":
            token_info = self._wx_token_info()
            if not str(token_info.get("bot_token", "") or "").strip():
                return self._open_wechat_qr_dialog(
                    save_pending=False,
                    show_errors=show_errors,
                    restart_after=False,
                )
        if self._channel_proc_alive(channel_id):
            self._refresh_channels_panel_if_open()
            return True
        agent_dir = self.agent_dir.get().strip()
        if not is_valid_agent_dir(agent_dir):
            if show_errors:
                messagebox.showerror("目录无效", "请先载入有效的 GenericAgent 目录。")
            return False
        conflict = self._channel_conflict_message(channel_id)
        if conflict:
            if show_errors:
                messagebox.showerror("无法启动", conflict)
            return False
        py = _find_system_python()
        if not py:
            if show_errors:
                messagebox.showerror("缺少 Python", "未找到系统 Python，无法启动通讯渠道。")
            return False
        _, _, parsed = self._load_channels_source()
        values = {}
        if isinstance(parsed, dict):
            values.update(dict(parsed.get("extras") or {}))
        missing = self._channel_missing_required(channel_id, values)
        if missing:
            if show_errors:
                messagebox.showerror("配置不完整",
                                     f"{spec.get('label', channel_id)} 还缺少这些字段：\n- " + "\n- ".join(missing))
            return False
        script_path = os.path.join(agent_dir, "frontends", spec.get("script", ""))
        if not os.path.isfile(script_path):
            if show_errors:
                messagebox.showerror("脚本不存在", f"未找到渠道脚本：\n{script_path}")
            return False
        log_path = self._channel_log_path(channel_id, agent_dir=agent_dir)
        try:
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            log_handle.write(f"\n==== {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} start {channel_id} ====\n")
            proc = subprocess.Popen(
                [py, "-u", script_path],
                cwd=agent_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as e:
            try:
                log_handle.close()
            except Exception:
                pass
            if show_errors:
                messagebox.showerror("启动失败", str(e))
            return False
        self._channel_procs[channel_id] = {
            "proc": proc,
            "log_handle": log_handle,
            "log_path": log_path,
            "agent_dir": agent_dir,
        }
        self.after(1200, lambda cid=channel_id, se=show_errors: self._after_channel_launch_check(cid, show_errors=se))
        self._refresh_channels_panel_if_open()
        if notify:
            try:
                self._update_last(f"[渠道] 已启动 {spec.get('label', channel_id)}")
            except Exception:
                pass
        return True

    def _start_autostart_channels(self):
        if not is_valid_agent_dir(self.agent_dir.get().strip()):
            return
        for spec in COMM_CHANNEL_SPECS:
            if self._channel_is_auto_start(spec["id"]) and not self._channel_proc_alive(spec["id"]):
                self._start_channel_process(spec["id"], save_pending=False, notify=False, show_errors=False)

    def _refresh_channels_panel_if_open(self):
        if getattr(self, "_current_settings_panel", "") != "channels":
            return
        content = getattr(self, "settings_content", None)
        if content is None:
            return
        try:
            if content.winfo_exists():
                self._show_settings_panel("channels")
        except Exception:
            pass

    def _channels_save(self, silent=False, apply_running=True):
        if not getattr(self, "_comm_py_path", None):
            if not silent:
                messagebox.showerror("保存失败", "尚未载入通讯渠道配置。")
            return False
        if getattr(self, "_comm_parse_error", None):
            if silent:
                return False
            go_on = messagebox.askyesno(
                "存在解析错误",
                "当前 mykey.py 解析失败。\n继续保存会按启动器当前识别到的配置重写文件，可能覆盖手写内容。\n\n是否继续？"
            )
            if not go_on:
                return False
        extras = dict(getattr(self, "_comm_extras", {}) or {})
        for spec in COMM_CHANNEL_SPECS:
            state = getattr(self, "_comm_channel_states", {}).get(spec["id"], {})
            for field in spec.get("fields", []):
                key = field.get("key")
                var = state.get("vars", {}).get(key)
                value = self._channel_parse_value(field, var.get() if var is not None else "")
                if isinstance(value, list):
                    if value:
                        extras[key] = value
                    else:
                        extras.pop(key, None)
                else:
                    if str(value or "").strip():
                        extras[key] = value
                    else:
                        extras.pop(key, None)
            auto_var = state.get("auto_var")
            if auto_var is not None:
                self._channel_set_auto_start(spec["id"], bool(auto_var.get()), persist=False)
        try:
            txt = serialize_mykey_py(
                configs=getattr(self, "_comm_configs", []),
                extras=extras,
                passthrough=getattr(self, "_comm_passthrough", []),
            )
            open(self._comm_py_path, "w", encoding="utf-8").write(txt)
            self._comm_extras = extras
            save_config(self.cfg)
        except Exception as e:
            if not silent:
                messagebox.showerror("保存失败", str(e))
            return False
        restarted = self._restart_running_channels(show_errors=False) if apply_running else 0
        if not silent:
            msg = "已写入 mykey.py 和启动器配置。"
            if restarted:
                msg += f"\n已自动重启 {restarted} 个由启动器托管的通讯渠道。"
            else:
                msg += "\n运行中的通讯渠道若未自动托管，需手动重启后才会读取新配置。"
            messagebox.showinfo("已保存", msg)
        return True

    def _build_channels_panel(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, pady=(12, 0))

        ctk.CTkLabel(wrap, text="通讯渠道",
                     font=("Microsoft YaHei UI", 18, "bold"),
                     text_color=COLOR_TEXT,
                     anchor="w").pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(
            wrap,
            text=("这里接的是 GenericAgent 原项目的 Bot 前端：Telegram、QQ、飞书、企业微信、钉钉、微信。\n"
                  "它们会各自启动独立的 GenericAgent 进程，不和当前聊天页共用会话；保存配置时，启动器会自动重启自己托管的渠道进程。"),
            font=FONT_SUB, text_color=COLOR_MUTED, justify="left",
            anchor="w", wraplength=760
        ).pack(fill="x")

        agent_dir = self.agent_dir.get().strip()
        if not is_valid_agent_dir(agent_dir):
            card = ctk.CTkFrame(wrap, fg_color=COLOR_CARD, corner_radius=12)
            card.pack(fill="x", pady=(18, 0))
            ctk.CTkLabel(card, text="还没有载入 GenericAgent",
                         font=("Microsoft YaHei UI", 15, "bold"),
                         text_color=COLOR_TEXT,
                         anchor="w").pack(fill="x", padx=18, pady=(16, 4))
            ctk.CTkLabel(
                card,
                text="请先选择或下载 GenericAgent 目录，然后再配置通讯渠道。",
                font=FONT_SMALL, text_color=COLOR_MUTED,
                justify="left", anchor="w", wraplength=680
            ).pack(fill="x", padx=18, pady=(0, 16))
            return

        _, py_path, parsed = self._load_channels_source()
        self._comm_py_path = py_path
        self._comm_parse_error = parsed.get("error")
        self._comm_configs = list(parsed.get("configs") or [])
        self._comm_passthrough = list(parsed.get("passthrough") or [])
        self._comm_extras = dict(parsed.get("extras") or {})
        self._comm_channel_states = {}

        if self._comm_parse_error:
            err = ctk.CTkFrame(wrap, fg_color=COLOR_ERROR_BG, corner_radius=8)
            err.pack(fill="x", pady=(12, 0))
            ctk.CTkLabel(
                err,
                text=f"⚠ 当前 mykey.py 解析失败：{self._comm_parse_error}\n继续保存会覆盖成启动器当前可识别的格式，建议先在 API 页面检查。",
                font=FONT_SMALL, text_color=COLOR_ERROR_TEXT,
                justify="left", anchor="w", wraplength=760
            ).pack(fill="x", padx=12, pady=8)

        toolbar = ctk.CTkFrame(wrap, fg_color="transparent")
        toolbar.pack(fill="x", pady=(14, 8))
        ctk.CTkButton(toolbar, text="保存通讯配置", height=32, width=132,
                      font=FONT_BTN, corner_radius=8,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      text_color=COLOR_ON_ACCENT,
                      command=self._channels_save).pack(side="left")
        ctk.CTkButton(toolbar, text="刷新状态", height=32, width=96,
                      font=FONT_SMALL, corner_radius=8,
                      fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                      text_color=COLOR_TEXT,
                      command=lambda: self._show_settings_panel("channels")).pack(side="left", padx=(8, 0))
        running_count = sum(1 for spec in COMM_CHANNEL_SPECS if self._channel_proc_alive(spec["id"]))
        stop_all_text = f"停止全部 ({running_count})" if running_count else "停止全部"
        ctk.CTkButton(toolbar, text=stop_all_text, height=32, width=112,
                      font=FONT_SMALL, corner_radius=8,
                      fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                      text_color=COLOR_TEXT,
                      state=("normal" if running_count else "disabled"),
                      command=lambda: self._stop_all_managed_channels(refresh=True)).pack(side="right")

        lst = ctk.CTkScrollableFrame(wrap, fg_color="transparent")
        lst.pack(fill="both", expand=True, pady=(0, 8))
        for spec in COMM_CHANNEL_SPECS:
            self._build_channel_card(lst, spec)

    def _build_channel_card(self, parent, spec):
        base_values = {
            field["key"]: self._comm_extras.get(field["key"])
            for field in spec.get("fields", [])
        }
        values = self._channel_values_from_state(spec["id"], fallback=base_values)
        status_text, status_color = self._channel_status(spec["id"], values)
        card = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", pady=6)

        state = {"vars": {}, "auto_var": tk.BooleanVar(value=self._channel_is_auto_start(spec["id"]))}
        self._comm_channel_states[spec["id"]] = state

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 8))
        title_box = ctk.CTkFrame(head, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_box, text=spec.get("label", spec["id"]),
                     font=("Microsoft YaHei UI", 15, "bold"),
                     text_color=COLOR_TEXT, anchor="w").pack(anchor="w")
        ctk.CTkLabel(title_box, text=spec.get("subtitle", ""),
                     font=FONT_SMALL, text_color=COLOR_MUTED,
                     anchor="w").pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(head, text=status_text, font=FONT_SMALL,
                     text_color=status_color).pack(side="right")

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkSwitch(ctrl, text="随启动器自动启动", variable=state["auto_var"],
                      onvalue=True, offvalue=False, width=120,
                      progress_color=COLOR_ACCENT).pack(side="left")
        if spec["id"] == "wechat":
            wx_token_exists = bool(str(self._wx_token_info().get("bot_token", "") or "").strip())
            ctk.CTkButton(
                ctrl,
                text=("重新绑定" if wx_token_exists else "扫码登录"),
                width=92, height=30, font=FONT_SMALL, corner_radius=8,
                fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                text_color=COLOR_TEXT,
                command=lambda: self._open_wechat_qr_dialog(
                    save_pending=True,
                    show_errors=True,
                    restart_after=True,
                ),
            ).pack(side="right", padx=(0, 6))
        ctk.CTkButton(ctrl, text="启动", width=78, height=30,
                      font=FONT_SMALL, corner_radius=8,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      text_color=COLOR_ON_ACCENT,
                      state=("disabled" if self._channel_proc_alive(spec["id"]) else "normal"),
                      command=lambda cid=spec["id"]: self._start_channel_process(cid)).pack(side="right")
        ctk.CTkButton(ctrl, text="停止", width=78, height=30,
                      font=FONT_SMALL, corner_radius=8,
                      fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                      text_color=COLOR_TEXT,
                      state=("normal" if self._channel_proc_alive(spec["id"]) else "disabled"),
                      command=lambda cid=spec["id"]: self._stop_channel_process(cid, refresh=True)).pack(side="right", padx=(0, 6))

        for field in spec.get("fields", []):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=(0, 6))
            ctk.CTkLabel(row, text=field.get("label", field.get("key")),
                         font=FONT_SMALL, width=92, anchor="w",
                         text_color=COLOR_TEXT_SOFT).pack(side="left", padx=(0, 8))
            var = tk.StringVar(value=self._channel_format_value(field, base_values.get(field["key"])))
            ent = ctk.CTkEntry(row, textvariable=var, height=30,
                               font=FONT_BODY, corner_radius=6,
                               placeholder_text=field.get("placeholder", ""),
                               show="*" if field.get("kind") == "password" else "")
            ent.pack(side="left", fill="x", expand=True)
            state["vars"][field["key"]] = var
            if field.get("kind") == "password":
                shown = {"v": False}
                eye_holder = {"btn": None}
                def toggle_show(entry=ent, flag=shown, holder=eye_holder):
                    flag["v"] = not flag["v"]
                    entry.configure(show="" if flag["v"] else "*")
                    holder["btn"].configure(text=("🙈" if flag["v"] else "👁"))
                eye_holder["btn"] = ctk.CTkButton(
                    row, text="👁", width=34, height=30,
                    font=FONT_SMALL, fg_color=COLOR_CARD_HOVER,
                    hover_color=COLOR_ACTIVE_HOVER, text_color=COLOR_TEXT,
                    command=toggle_show
                )
                eye_holder["btn"].pack(side="left", padx=(4, 0))

        note_lines = [
            f"依赖：pip install {spec.get('pip', '')}",
            f"脚本：frontends/{spec.get('script', '')}",
        ]
        if spec.get("notes"):
            note_lines.append(spec["notes"])
        if spec["id"] == "wechat":
            token_path = self._wx_token_path()
            token_status = "已检测到本机绑定缓存" if os.path.isfile(token_path) else "本机还没有微信绑定缓存"
            note_lines.append(f"绑定状态：{token_status}")
            note_lines.append("点“启动”或“扫码登录”时，启动器会直接弹出二维码窗口。")
        info = self._channel_procs.get(spec["id"]) or {}
        if info.get("log_path"):
            note_lines.append(f"启动器日志：{info['log_path']}")
        ctk.CTkLabel(card, text="\n".join(note_lines),
                     font=FONT_SMALL, text_color=COLOR_MUTED,
                     justify="left", anchor="w", wraplength=700).pack(
            fill="x", padx=16, pady=(2, 14))

    def _build_about_panel(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, pady=(12, 0))

        ctk.CTkLabel(wrap, text="关于启动器",
                     font=("Microsoft YaHei UI", 18, "bold"),
                     anchor="w").pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(
            wrap,
            text=("这是一个面向 GenericAgent 的桌面启动器，目标是把下载、配置、启动和日常聊天入口收拢到一个更直接的界面里。\n"
                  "当前这里先保留简版介绍，后续再补充版本信息、更新说明、使用文档和常见问题。"),
            font=FONT_SUB, text_color=COLOR_MUTED, justify="left",
            anchor="w", wraplength=720
        ).pack(fill="x")

        info = ctk.CTkFrame(wrap, fg_color=COLOR_CARD, corner_radius=12)
        info.pack(fill="x", pady=(18, 0))

        rows = [
            ("项目定位", "GenericAgent 的非官方桌面启动器 / 前端壳"),
            ("当前状态", "可用，但关于页内容仍待丰富"),
            ("上游仓库", REPO_URL),
        ]
        for title, value in rows:
            row = ctk.CTkFrame(info, fg_color="transparent")
            row.pack(fill="x", padx=18, pady=8)
            ctk.CTkLabel(row, text=title, font=FONT_SMALL, width=88,
                         anchor="w", text_color=COLOR_MUTED).pack(side="left")
            ctk.CTkLabel(row, text=value, font=FONT_BODY, anchor="w",
                         justify="left", wraplength=620,
                         text_color=COLOR_TEXT).pack(side="left", fill="x", expand=True)

    def _build_usage_panel(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, pady=(12, 0))

        ctk.CTkLabel(wrap, text="使用计数",
                     font=("Microsoft YaHei UI", 18, "bold"),
                     anchor="w").pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(
            wrap,
            text=("这里后续计划展示 API 调用次数、消息条数、会话数量、最近活跃时间等统计信息。\n"
                  "当前先保留占位，避免上传到 GitHub 时暴露未完成的杂项设置入口。"),
            font=FONT_SUB, text_color=COLOR_MUTED, justify="left",
            anchor="w", wraplength=720
        ).pack(fill="x")

        card = ctk.CTkFrame(wrap, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", pady=(18, 0))
        ctk.CTkLabel(card, text="功能占位",
                     font=("Microsoft YaHei UI", 15, "bold"),
                     anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            card,
            text=("统计面板尚未接入真实数据。\n"
                  "后续可以在这里汇总模型使用、渠道调用、错误次数和会话活跃度。"),
            font=FONT_SMALL, text_color=COLOR_MUTED,
            justify="left", anchor="w", wraplength=680
        ).pack(fill="x", padx=18, pady=(0, 16))

    def _build_schedule_panel(self, parent):
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="both", expand=True, pady=(12, 0))

        ctk.CTkLabel(wrap, text="定时任务",
                     font=("Microsoft YaHei UI", 18, "bold"),
                     anchor="w").pack(fill="x", pady=(4, 6))
        ctk.CTkLabel(
            wrap,
            text=("这里后续计划接入定时任务入口，用来配置周期性执行的自动任务。\n"
                  "当前先保留占位，后续再补任务列表、执行时间、启停开关和运行记录。"),
            font=FONT_SUB, text_color=COLOR_MUTED, justify="left",
            anchor="w", wraplength=720
        ).pack(fill="x")

        card = ctk.CTkFrame(wrap, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(fill="x", pady=(18, 0))
        ctk.CTkLabel(card, text="功能占位",
                     font=("Microsoft YaHei UI", 15, "bold"),
                     anchor="w").pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(
            card,
            text=("暂未接入真实任务调度逻辑。\n"
                  "上传到 GitHub 时先保留这个入口，避免后续再调整设置页结构。"),
            font=FONT_SMALL, text_color=COLOR_MUTED,
            justify="left", anchor="w", wraplength=680
        ).pack(fill="x", padx=18, pady=(0, 16))

    def _back_to_chat(self):
        self._close_functions_panel()
        self._build_sidebar()
        for w in self.main_area.winfo_children():
            w.destroy()
        self._build_chat_main(self.main_area)
        # 回放当前会话气泡
        if self.current_session:
            for b in self.current_session.get("bubbles", []):
                self._add_bubble(b.get("role", "assistant"),
                                  b.get("text", ""), final=True)

    def _on_resize(self, event=None):
        if event is not None and event.widget is not self:
            return
        job = getattr(self, "_resize_job", None)
        if job:
            try: self.after_cancel(job)
            except Exception: pass
        self._resize_job = self.after(40, self._apply_resize_wrap)

    def _apply_resize_wrap(self):
        self._resize_job = None
        try:
            w = self.msg_area.winfo_width()
            if w < 100: return
            wrap = max(240, w - 200)
            if wrap == getattr(self, "_last_wrap", None):
                return
            self._last_wrap = wrap
            for lbl in self._bubble_labels:
                try: lbl.configure(wraplength=wrap)
                except Exception: pass
            for fs in self._fold_sections:
                try: fs.set_wrap(wrap)
                except Exception: pass
        except Exception:
            pass

    # ---------- 侧边栏会话列表渲染 ----------
    def _session_meta_from_data(self, data):
        sid = data.get("id")
        if not sid:
            return None
        return {
            "id": sid,
            "title": data.get("title") or "(未命名)",
            "updated_at": data.get("updated_at", 0),
            "pinned": bool(data.get("pinned", False)),
            "path": os.path.join(sessions_dir(self.agent_dir.get()), f"{sid}.json"),
        }

    def _get_session_cache(self, force=False):
        agent_dir = self.agent_dir.get()
        if force or getattr(self, "_session_cache_dir", None) != agent_dir or \
                not isinstance(getattr(self, "_sessions_cache", None), list):
            self._session_cache_dir = agent_dir
            try:
                self._sessions_cache = list_sessions(agent_dir)
            except Exception:
                self._sessions_cache = []
        return self._sessions_cache

    def _sort_session_cache(self):
        cache = getattr(self, "_sessions_cache", None)
        if isinstance(cache, list):
            cache.sort(key=lambda x: (x.get("pinned", False), x.get("updated_at", 0)),
                       reverse=True)

    def _session_meta_known(self, sid):
        for meta in self._get_session_cache(force=False):
            if meta.get("id") == sid:
                return True
        return False

    def _upsert_session_meta(self, data):
        meta = self._session_meta_from_data(data or {})
        if meta is None:
            return
        cache = self._get_session_cache(force=False)
        for i, old in enumerate(cache):
            if old.get("id") == meta["id"]:
                cache[i] = meta
                self._sort_session_cache()
                return
        cache.append(meta)
        self._sort_session_cache()

    def _remove_session_meta(self, sid):
        cache = self._get_session_cache(force=False)
        self._sessions_cache = [m for m in cache if m.get("id") != sid]

    def _refresh_sessions(self, force=False):
        for w in self.sess_list.winfo_children():
            w.destroy()
        items = list(self._get_session_cache(force=force))
        if not items:
            ctk.CTkLabel(self.sess_list, text="（暂无历史）",
                         font=FONT_SMALL, text_color=COLOR_MUTED).pack(pady=20)
            return
        cur_id = (self.current_session or {}).get("id")
        for m in items:
            self._make_session_card(m, active=(m["id"] == cur_id))

    def _make_session_card(self, meta, active=False):
        batch = getattr(self, "_batch_mode", False)
        bg = COLOR_ACTIVE if active else COLOR_CARD
        hb = COLOR_ACTIVE_HOVER if active else COLOR_CARD_HOVER

        if self.sidebar_collapsed:
            sid = meta.get("id")
            title = (meta.get("title") or "").strip()
            glyph = "📌" if meta.get("pinned") else ((title[:1].upper() if title else "•"))
            if batch:
                glyph = "☑" if sid in self._batch_selected else "☐"

            card = ctk.CTkFrame(self.sess_list, fg_color=bg, corner_radius=10,
                                 width=40, height=40, cursor="hand2")
            card.pack(padx=6, pady=4)
            card.pack_propagate(False)

            lbl = ctk.CTkLabel(card, text=glyph,
                              font=("Segoe UI Emoji", 14),
                              text_color=COLOR_TEXT)
            lbl.place(relx=0.5, rely=0.5, anchor="center")

            def enter(e): card.configure(fg_color=hb)
            def leave(e): card.configure(fg_color=bg)

            def click(e):
                if batch:
                    if sid in self._batch_selected:
                        self._batch_selected.discard(sid)
                    else:
                        self._batch_selected.add(sid)
                    self._render_batch_bar()
                    self._refresh_sessions()
                else:
                    self._load_session(meta)

            pinned = meta.get("pinned", False)

            def popup(e):
                if getattr(self, "_batch_mode", False):
                    items = [
                        ("退出批量模式", lambda: self._batch_exit()),
                    ]
                else:
                    items = [
                        (("取消置顶" if pinned else "置顶"),
                            lambda m=meta: self._toggle_pin(m)),
                        ("重命名",
                            lambda m=meta: self._rename_session(m)),
                        "---",
                        ("删除会话",
                            lambda m=meta: self._delete_session(m), "danger"),
                        "---",
                        ("☑ 批量删除",
                            lambda m=meta: self._batch_enter(preselect=m.get("id"))),
                    ]
                self._popup_menu(e.x_root, e.y_root, items, min_width=180)

            for w in (card, lbl):
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", click)
                w.bind("<Button-3>", popup)
            return

        card = ctk.CTkFrame(self.sess_list, fg_color=bg, corner_radius=10,
                             height=58, cursor="hand2")
        card.pack(fill="x", pady=4, padx=2)
        card.pack_propagate(False)

        check_var = None
        if batch:
            sid = meta.get("id")
            check_var = tk.BooleanVar(value=(sid in self._batch_selected))

            def _on_check():
                if check_var.get():
                    self._batch_selected.add(sid)
                else:
                    self._batch_selected.discard(sid)
                self._render_batch_bar()

            chk = ctk.CTkCheckBox(card, text="", width=22,
                                  variable=check_var,
                                  onvalue=True, offvalue=False,
                                  fg_color=COLOR_ACCENT,
                                  hover_color=COLOR_ACCENT_HOVER,
                                  border_color=COLOR_DIVIDER,
                                  command=_on_check)
            chk.pack(side="left", padx=(10, 0))

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True,
                   padx=((4 if batch else 12), 4), pady=6)
        pin_mark = "📌 " if meta.get("pinned") else ""
        title = meta["title"][:26] + ("…" if len(meta["title"]) > 26 else "")
        ctk.CTkLabel(info, text=pin_mark + title, font=FONT_BODY,
                    text_color=COLOR_TEXT, anchor="w").pack(anchor="w")
        try:
            date_txt = datetime.fromtimestamp(meta["updated_at"]).strftime("%m-%d %H:%M")
        except Exception:
            date_txt = ""
        ctk.CTkLabel(info, text=date_txt, font=FONT_SMALL,
                     text_color=COLOR_MUTED, anchor="w").pack(anchor="w")

        def enter(e): card.configure(fg_color=hb)
        def leave(e): card.configure(fg_color=bg)

        def click(e):
            if batch:
                check_var.set(not check_var.get())
                if check_var.get():
                    self._batch_selected.add(meta.get("id"))
                else:
                    self._batch_selected.discard(meta.get("id"))
                self._render_batch_bar()
            else:
                self._load_session(meta)

        pinned = meta.get("pinned", False)

        def popup(e):
            if getattr(self, "_batch_mode", False):
                items = [
                    ("退出批量模式", lambda: self._batch_exit()),
                ]
            else:
                items = [
                    (("取消置顶" if pinned else "置顶"),
                        lambda m=meta: self._toggle_pin(m)),
                    ("重命名",
                        lambda m=meta: self._rename_session(m)),
                    "---",
                    ("删除会话",
                        lambda m=meta: self._delete_session(m), "danger"),
                    "---",
                    ("☑ 批量删除",
                        lambda m=meta: self._batch_enter(preselect=m.get("id"))),
                ]
            self._popup_menu(e.x_root, e.y_root, items, min_width=180)

        for w in (card, info, *info.winfo_children()):
            w.bind("<Enter>", enter); w.bind("<Leave>", leave)
            w.bind("<Button-1>", click)
            w.bind("<Button-3>", popup)

    def _toggle_pin(self, meta):
        data = load_session(self.agent_dir.get(), meta["id"])
        if not data: return
        data["pinned"] = not data.get("pinned", False)
        # 保留更新时间，不刷成现在
        original_updated = data.get("updated_at", time.time())
        save_session(self.agent_dir.get(), data)
        data["updated_at"] = original_updated
        try:
            with open(os.path.join(sessions_dir(self.agent_dir.get()),
                                    f"{data['id']}.json"),
                       "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception: pass
        if self.current_session and self.current_session.get("id") == data["id"]:
            self.current_session["pinned"] = data["pinned"]
        self._upsert_session_meta(data)
        self._refresh_sessions()

    def _rename_session(self, meta):
        new = self._prompt_text("重命名会话", "请输入新标题：",
                                 initial=meta.get("title", ""))
        if not new: return
        data = load_session(self.agent_dir.get(), meta["id"])
        if not data: return
        data["title"] = new[:60]
        save_session(self.agent_dir.get(), data)
        if self.current_session and self.current_session.get("id") == data["id"]:
            self.current_session["title"] = data["title"]
        self._upsert_session_meta(data)
        self._refresh_sessions()

    def _prompt_text(self, title, prompt, initial=""):
        dlg = ctk.CTkToplevel(self)
        dlg.title(title)
        dlg.geometry("380x160")
        dlg.transient(self)
        dlg.grab_set()
        dlg.configure(fg_color=COLOR_SURFACE)
        ctk.CTkLabel(dlg, text=prompt, font=FONT_BODY).pack(pady=(18, 8))
        var = ctk.StringVar(value=initial)
        ent = ctk.CTkEntry(dlg, textvariable=var, width=320, height=34,
                            font=FONT_BODY, corner_radius=8)
        ent.pack(pady=4); ent.focus_set()
        result = {"v": None}
        def ok(): result["v"] = var.get().strip(); dlg.destroy()
        def cancel(): dlg.destroy()
        row = ctk.CTkFrame(dlg, fg_color="transparent")
        row.pack(pady=12)
        ctk.CTkButton(row, text="取消", width=90, height=32,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_MUTED, command=cancel).pack(side="left", padx=6)
        ctk.CTkButton(row, text="确定", width=90, height=32,
                       fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                       command=ok).pack(side="left", padx=6)
        ent.bind("<Return>", lambda e: ok())
        dlg.wait_window()
        return result["v"]

    # ---------- 暗色圆角弹出菜单（替代原生 tk.Menu） ----------
    def _popup_menu(self, x, y, items, *, min_width=200, anchor="nw",
                    anchor_widget=None, offset=(0, 0)):
        """暗色 CTk 风格弹出菜单。

        items 中每项可为：
            "---"                      -> 分隔线
            (label, command)           -> 普通项
            (label, command, "danger") -> 危险项（红字）
            (label, None, "header")    -> 非点击标题行
        anchor="ne" 让菜单以 (x, y) 为右上角对齐（用于齿轮按钮等右侧触发）。
        anchor_widget 传入触发控件时，会在弹出后再次按控件屏幕坐标重定位，
        避免最大化/缩放场景下菜单漂移。
        """
        prev_close = getattr(self, "_active_popup_close", None)
        if callable(prev_close):
            try:
                prev_close()
            except Exception:
                pass

        popup = ctk.CTkToplevel(self)
        popup.withdraw()
        try: popup.overrideredirect(True)
        except Exception: pass
        try: popup.attributes("-topmost", True)
        except Exception: pass
        popup.configure(fg_color=COLOR_POPUP_BORDER)  # 1px 描边

        body = ctk.CTkFrame(popup, fg_color=COLOR_POPUP_BG, corner_radius=10)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        inner = ctk.CTkFrame(body, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=6, pady=6)

        state = {"closed": False}
        root_bind_ids = {}

        def close(*_):
            if state["closed"]: return
            state["closed"] = True
            if getattr(self, "_active_popup_menu", None) is popup:
                self._active_popup_menu = None
                self._active_popup_close = None
            for seq, bind_id in list(root_bind_ids.items()):
                try:
                    self.unbind(seq, bind_id)
                except Exception:
                    pass
            try: popup.destroy()
            except Exception: pass

        def _root_click(ev):
            if state["closed"]:
                return
            try:
                widget_name = str(ev.widget)
            except Exception:
                widget_name = ""
            if widget_name.startswith(str(popup)):
                return
            close()

        for it in items:
            if it == "---" or it is None:
                ctk.CTkFrame(inner, height=1,
                             fg_color=COLOR_DIVIDER).pack(fill="x", padx=6, pady=4)
                continue
            if not isinstance(it, (tuple, list)) or len(it) < 2:
                continue
            label = it[0]
            cmd = it[1]
            kind = it[2] if len(it) >= 3 else "normal"

            if kind == "header":
                ctk.CTkLabel(inner, text=label, font=FONT_SMALL,
                             text_color=COLOR_MUTED, anchor="w").pack(
                    fill="x", padx=10, pady=(2, 4))
                continue

            if kind == "danger":
                txt, hov = COLOR_DANGER_TEXT, COLOR_DANGER_HOVER
            else:
                txt, hov = COLOR_TEXT, COLOR_CARD_HOVER

            def _wrap(cmd=cmd):
                close()
                if cmd is not None:
                    self.after(10, cmd)

            ctk.CTkButton(inner, text=label, anchor="w",
                          height=30, font=FONT_BODY, corner_radius=6,
                          fg_color="transparent", hover_color=hov,
                          text_color=txt,
                          command=_wrap).pack(fill="x", padx=2, pady=1)

        def _resolve_position():
            popup.update_idletasks()
            w = max(popup.winfo_reqwidth(), min_width)
            h = popup.winfo_reqheight()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()

            px, py = x, y
            if anchor_widget is not None:
                try:
                    anchor_widget.update_idletasks()
                    self.update_idletasks()
                    px = anchor_widget.winfo_rootx()
                    py = anchor_widget.winfo_rooty()
                    if anchor == "ne":
                        px += anchor_widget.winfo_width()
                        py += anchor_widget.winfo_height()
                    elif anchor == "nw":
                        py += anchor_widget.winfo_height()
                except Exception:
                    px, py = x, y

            px += offset[0]
            py += offset[1]
            if anchor == "ne":
                xi = max(4, min(int(px) - w, sw - w - 4))
            else:
                xi = max(4, min(int(px), sw - w - 4))
            yi = max(4, min(int(py), sh - h - 4))
            return w, h, xi, yi

        w, h, xi, yi = _resolve_position()
        popup.geometry(f"{w}x{h}+{xi}+{yi}")
        popup.deiconify()
        popup.lift()
        self._active_popup_menu = popup
        self._active_popup_close = close
        def _install_root_binds():
            if state["closed"]:
                return
            for seq in ("<ButtonPress-1>", "<ButtonPress-3>"):
                try:
                    root_bind_ids[seq] = self.bind(seq, _root_click, add="+")
                except Exception:
                    pass
        self.after_idle(_install_root_binds)

        # 再次校正一次位置。某些窗口状态下首次读取到的按钮屏幕坐标会有轻微漂移。
        if anchor_widget is not None:
            def _reposition():
                if state["closed"]:
                    return
                try:
                    _w, _h, _xi, _yi = _resolve_position()
                    popup.geometry(f"{_w}x{_h}+{_xi}+{_yi}")
                except Exception:
                    pass
            popup.after_idle(_reposition)
            popup.after(16, _reposition)

        popup.bind("<Escape>", close)
        return popup

    def _delete_session(self, meta):
        if not messagebox.askyesno("删除会话", f"确定删除「{meta['title']}」？"):
            return
        # 若来自原生日志导入，把原文件加入黑名单，防止下次启动重新导入
        data = load_session(self.agent_dir.get(), meta["id"])
        imported_from = (data or {}).get("imported_from", "")
        if imported_from:
            bl = load_import_blacklist(self.agent_dir.get())
            bl.add(imported_from)
            save_import_blacklist(self.agent_dir.get(), bl)
        delete_session(self.agent_dir.get(), meta["id"])
        self._remove_session_meta(meta["id"])
        if self.current_session and self.current_session.get("id") == meta["id"]:
            self.current_session = None
            self._send_cmd({"cmd": "new_session"})
            self._reset_chat_area(greeting=None)
        self._refresh_sessions()

    # ---------- 批量删除模式 ----------
    def _batch_enter(self, preselect=None):
        self._batch_mode = True
        if not hasattr(self, "_batch_selected") or self._batch_selected is None:
            self._batch_selected = set()
        else:
            self._batch_selected.clear()
        if preselect:
            self._batch_selected.add(preselect)
        self._refresh_sessions()
        self._render_batch_bar()

    def _batch_exit(self):
        self._batch_mode = False
        if hasattr(self, "_batch_selected"):
            self._batch_selected.clear()
        self._refresh_sessions()
        self._render_batch_bar()

    def _batch_select_all(self):
        items = list(self._get_session_cache(force=False))
        self._batch_selected = {m["id"] for m in items}
        self._refresh_sessions()
        self._render_batch_bar()

    def _batch_delete_selected(self):
        sids = list(self._batch_selected)
        if not sids:
            return
        if not messagebox.askyesno("批量删除",
                f"确定删除 {len(sids)} 个会话？此操作不可撤销。"):
            return
        ad = self.agent_dir.get()
        bl = load_import_blacklist(ad)
        for sid in sids:
            data = load_session(ad, sid)
            imported_from = (data or {}).get("imported_from", "")
            if imported_from:
                bl.add(imported_from)
            delete_session(ad, sid)
            self._remove_session_meta(sid)
            if self.current_session and self.current_session.get("id") == sid:
                self.current_session = None
                self._send_cmd({"cmd": "new_session"})
                self._reset_chat_area(greeting=None)
        try: save_import_blacklist(ad, bl)
        except Exception: pass
        self._batch_exit()

    def _render_batch_bar(self):
        # 清掉旧的
        if getattr(self, "batch_bar", None) is not None:
            try: self.batch_bar.destroy()
            except Exception: pass
            self.batch_bar = None
        if not getattr(self, "_batch_mode", False):
            return
        bottom_anchor = getattr(self, "_sidebar_bottom", None)
        if bottom_anchor is None or not bottom_anchor.winfo_exists():
            return
        n = len(self._batch_selected)
        bar = ctk.CTkFrame(self.sidebar, fg_color=COLOR_PANEL,
                            height=58, corner_radius=0)
        try:
            bar.pack(fill="x", side="bottom", before=bottom_anchor)
        except Exception:
            bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        if self.sidebar_collapsed:
            # 折叠态只显示一个 × 退出按钮
            ctk.CTkButton(bar, text="×", width=36, height=36,
                           font=("Segoe UI Emoji", 14),
                           corner_radius=8, fg_color=COLOR_ACTIVE,
                           hover_color=COLOR_ACTIVE_HOVER, text_color=COLOR_TEXT_SOFT,
                           command=self._batch_exit).pack(padx=10, pady=8)
        else:
            inner = ctk.CTkFrame(bar, fg_color="transparent")
            inner.pack(fill="both", expand=True, padx=10, pady=8)
            ctk.CTkButton(inner, text="取消", width=52, height=32,
                          font=FONT_SMALL, corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_CARD,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._batch_exit).pack(side="left")
            ctk.CTkButton(inner, text="全选", width=52, height=32,
                          font=FONT_SMALL, corner_radius=8,
                          fg_color="transparent", hover_color=COLOR_CARD,
                          text_color=COLOR_TEXT_SOFT,
                          command=self._batch_select_all).pack(side="left", padx=(6, 0))
            del_text = f"删除 ({n})" if n else "删除"
            del_btn = ctk.CTkButton(inner, text=del_text, height=32,
                                    font=FONT_BTN, corner_radius=8,
                                    fg_color=COLOR_DANGER_BG,
                                    hover_color=COLOR_DANGER_BG_HOVER,
                                    text_color="white",
                                    command=self._batch_delete_selected)
            if n == 0:
                del_btn.configure(state="disabled", fg_color=COLOR_DANGER_BG_DISABLED,
                                  text_color=COLOR_MUTED)
            del_btn.pack(side="right", fill="x", expand=True, padx=(8, 0))

        self.batch_bar = bar

    # ---------- 自动导入 GenericAgent 原生历史 ----------
    def _auto_import_legacy(self):
        """收到 legacy_list 后：为尚未导入且未被黑名单屏蔽的文件逐个建档。"""
        self._prune_duplicate_imported_sessions()
        try:
            existing = {}
            for meta in self._get_session_cache(force=True):
                data = load_session(self.agent_dir.get(), meta["id"])
                if data and data.get("imported_from"):
                    existing[data["imported_from"]] = data
        except Exception:
            existing = {}
        blacklist = load_import_blacklist(self.agent_dir.get())
        for m in self._legacy_items:
            fp = m.get("file", "")
            if fp in blacklist:
                continue
            existing_data = existing.get(fp)
            if existing_data and not legacy_session_needs_refresh(existing_data):
                continue
            item = dict(m)
            if existing_data:
                item["existing_session"] = existing_data
            self._pending_import_queue.append(item)
        if self._pending_import_queue and not self._importing:
            self._process_next_import()

    def _find_duplicate_live_session(self, bubbles, legacy_mtime):
        signature = _session_user_signature(bubbles)
        if not signature:
            return None
        for meta in self._get_session_cache(force=True):
            try:
                data = load_session(self.agent_dir.get(), meta["id"])
            except Exception:
                data = None
            if not data or data.get("imported_from"):
                continue
            live_signature = _session_user_signature(data.get("bubbles") or [])
            if live_signature != signature and not _signature_is_tail(live_signature, signature):
                continue
            try:
                updated_at = float(data.get("updated_at", 0) or 0)
            except Exception:
                updated_at = 0
            if legacy_mtime and updated_at and abs(updated_at - legacy_mtime) > 900:
                continue
            return data
        return None

    def _prune_duplicate_imported_sessions(self):
        agent_dir = self.agent_dir.get()
        blacklist = None
        dirty = False
        for meta in list(self._get_session_cache(force=True)):
            try:
                data = load_session(agent_dir, meta["id"])
            except Exception:
                data = None
            if not data or not data.get("imported_from"):
                continue
            if self.current_session and self.current_session.get("id") == data.get("id"):
                continue
            duplicate_live = self._find_duplicate_live_session(
                data.get("bubbles") or [],
                data.get("updated_at", 0),
            )
            if duplicate_live is None:
                continue
            imported_from = data.get("imported_from", "")
            delete_session(agent_dir, data["id"])
            self._remove_session_meta(data["id"])
            dirty = True
            if imported_from:
                if blacklist is None:
                    blacklist = load_import_blacklist(agent_dir)
                blacklist.add(imported_from)
        if blacklist is not None:
            try:
                save_import_blacklist(agent_dir, blacklist)
            except Exception:
                pass
        if dirty:
            self._refresh_sessions()

    def _process_next_import(self):
        if not self._pending_import_queue:
            self._importing = False
            self._refresh_sessions()
            return
        self._importing = True
        m = self._pending_import_queue.pop(0)
        self._pending_legacy_meta = m
        self._send_cmd({"cmd": "restore_legacy", "file": m.get("file", "")})

    def _on_legacy_restored(self, ev):
        """每份解析结果：为其建一个启动器会话（bubbles 为空则跳过）。"""
        meta = getattr(self, "_pending_legacy_meta", None) or {}
        bubbles = ev.get("bubbles") or []
        agent_history = ev.get("agent_history") or []
        existing = meta.get("existing_session") or {}
        if bubbles:
            duplicate_live = self._find_duplicate_live_session(
                bubbles,
                meta.get("mtime", time.time()),
            )
            if duplicate_live is not None:
                fp = meta.get("file", "")
                if fp:
                    try:
                        bl = load_import_blacklist(self.agent_dir.get())
                        bl.add(fp)
                        save_import_blacklist(self.agent_dir.get(), bl)
                    except Exception:
                        pass
                self.after(30, self._process_next_import)
                return
            title = ""
            for b in bubbles:
                if b.get("role") == "user":
                    title = (b.get("text") or "").strip().replace("\n", " ")[:30]
                    break
            session = {
                "id": existing.get("id") or uuid.uuid4().hex[:12],
                "title": existing.get("title") or title or "(从 GenericAgent 导入)",
                "created_at": existing.get("created_at", meta.get("mtime", time.time())),
                "updated_at": meta.get("mtime", time.time()),
                "bubbles": bubbles,
                "backend_history": existing.get("backend_history") or [],
                "agent_history": agent_history,
                "imported_from": meta.get("file", ""),
                "pinned": existing.get("pinned", False),
                "legacy_restore_version": 2,
            }
            try: save_session(self.agent_dir.get(), session)
            except Exception as e: print(f"[auto-import] {e}")
            self._upsert_session_meta(session)
        self.after(30, self._process_next_import)

    def _load_session(self, meta):
        if getattr(self, "_busy", False):
            messagebox.showinfo("忙碌中", "当前任务还在运行，请稍候。")
            return
        data = load_session(self.agent_dir.get(), meta["id"])
        if not data:
            messagebox.showerror("加载失败", "会话文件无法读取。")
            return
        # 先把状态送入内核
        self._send_cmd({
            "cmd": "set_state",
            "backend_history": data.get("backend_history") or [],
            "agent_history": data.get("agent_history") or [],
        })
        self.current_session = data
        # 重绘气泡（不加系统提示）
        self._reset_chat_area(greeting=None)
        for b in data.get("bubbles", []):
            self._add_bubble(b.get("role", "assistant"),
                             b.get("text", ""), final=True)
        self._upsert_session_meta(data)
        self._refresh_sessions()

    def _reset_chat_area(self, greeting=None):
        for w in self.msg_area.winfo_children():
            w.destroy()
        self._msg_row = 0
        self._current_assistant_label = None
        self._current_assistant_content = None
        self._bubble_labels = []
        self._bubble_rows = []
        self._fold_sections = []
        self._live_label = None
        self._stream_frozen = 0
        self._pending_stream_text = None
        self._stream_render_scheduled = False
        self._current_stream_text = ""
        self._busy = False
        self._abort_requested = False
        try:
            self._finish_generation_ui()
        except Exception:
            pass
        if greeting:
            self._add_bubble("assistant", greeting)

    def _ensure_session(self, first_user_text):
        """首次发送消息时为当前对话创建存档。"""
        if self.current_session is not None:
            return
        title = (first_user_text or "新会话").strip().replace("\n", " ")
        title = title[:30] + ("…" if len(first_user_text) > 30 else "")
        self.current_session = {
            "id": uuid.uuid4().hex[:12],
            "title": title or "新会话",
            "created_at": time.time(),
            "updated_at": time.time(),
            "bubbles": [],
            "backend_history": [],
            "agent_history": [],
        }

    def _persist_current_session(self):
        """把当前消息气泡写入存档；内核状态在 done 事件后异步拉取补入。"""
        if not self.current_session:
            return
        self._persist_session_data(self.current_session)

    def _persist_session_data(self, session):
        if not session:
            return
        was_known = self._session_meta_known(session.get("id"))
        try:
            save_session(self.agent_dir.get(), session)
        except Exception as e:
            print(f"[save_session] {e}")
        self._upsert_session_meta(session)
        if not was_known:
            self._refresh_sessions()

    def _on_switch_llm(self, selection):
        try:
            idx = int(selection.split(".", 1)[0]) - 1
            self._send_cmd({"cmd": "switch_llm", "idx": idx})
        except Exception as e:
            messagebox.showerror("切换失败", str(e))

    def _refresh_llm_selector(self):
        tool_row = getattr(self, "llm_tool_row", None)
        if tool_row is None or not tool_row.winfo_exists():
            return
        menu = getattr(self, "llm_menu", None)
        if menu is not None and not menu.winfo_exists():
            menu = None
            self.llm_menu = None
        label = getattr(self, "llm_empty_label", None)
        if label is not None and not label.winfo_exists():
            label = None
            self.llm_empty_label = None

        llms = list(getattr(self, "llms", []) or [])
        llm_names = [f"{l['idx'] + 1}. {l['name']}" for l in llms]
        current_pos = next((i for i, l in enumerate(llms) if l.get("current")), 0)
        current_value = llm_names[current_pos] if llm_names else "(无LLM)"
        try:
            self.llm_var.set(current_value)
        except Exception:
            self.llm_var = ctk.StringVar(value=current_value)

        if llm_names:
            if label is not None:
                try: label.destroy()
                except Exception: pass
                self.llm_empty_label = None
            if menu is None:
                self.llm_menu = ctk.CTkOptionMenu(
                    tool_row, variable=self.llm_var, values=llm_names,
                    width=240, height=30, font=FONT_SMALL,
                    corner_radius=8, fg_color=COLOR_FIELD_BG,
                    button_color=COLOR_ACCENT,
                    button_hover_color=COLOR_ACCENT_HOVER,
                    dropdown_fg_color=COLOR_POPUP_BG,
                    dropdown_hover_color=COLOR_CARD_HOVER,
                    dropdown_text_color=COLOR_TEXT,
                    text_color=COLOR_TEXT,
                    command=self._on_switch_llm)
                self.llm_menu.pack(side="left")
            else:
                self.llm_menu.configure(values=llm_names, command=self._on_switch_llm)
            self.llm_empty_label = None
        else:
            if menu is not None:
                try: menu.destroy()
                except Exception: pass
                self.llm_menu = None
            if label is None:
                self.llm_empty_label = ctk.CTkLabel(
                    tool_row, text="未配置 LLM", font=FONT_SMALL,
                    text_color=COLOR_MUTED)
                self.llm_empty_label.pack(side="left")
            else:
                self.llm_empty_label.configure(text="未配置 LLM")
            self.llm_menu = None

    def _new_session(self):
        if getattr(self, "_busy", False):
            messagebox.showinfo("忙碌中", "当前任务还在运行，请稍候。")
            return
        self._send_cmd({"cmd": "new_session"})
        self.current_session = None
        self._reset_chat_area(greeting=None)
        self._refresh_sessions()

    def _add_bubble(self, role, text, final=False):
        """role: 'user' | 'assistant'。final=True 时对 assistant 内容立即做折叠渲染。"""
        row = ctk.CTkFrame(self.msg_area, fg_color="transparent")
        row.grid(row=self._msg_row, column=0, sticky="ew", padx=16, pady=6)
        row.columnconfigure(0, weight=1)
        self._msg_row += 1
        # 记录气泡行用于搜索跳转（索引对应 session["bubbles"] 中的下标）
        try: self._bubble_rows.append(row)
        except Exception:
            self._bubble_rows = [row]

        try:
            cur_w = max(self.msg_area.winfo_width(), 400)
        except Exception:
            cur_w = 800
        wrap = max(240, cur_w - 200)

        if role == "user":
            bubble = ctk.CTkFrame(row, fg_color=COLOR_ACCENT, corner_radius=14)
            bubble.grid(row=0, column=0, sticky="e", padx=(80, 0))
            lbl = ctk.CTkLabel(bubble, text=text, font=FONT_BODY,
                                wraplength=wrap, justify="left",
                                text_color="white", anchor="w")
            lbl.pack(padx=14, pady=10)
            self._bubble_labels.append(lbl)
            return bubble

        # assistant：不再有外层大气泡，只做一行头像标题 + 平铺内容栏
        wrap_container = ctk.CTkFrame(row, fg_color="transparent")
        wrap_container.grid(row=0, column=0, sticky="ew", padx=(6, 80))
        wrap_container.columnconfigure(0, weight=1)

        head = ctk.CTkLabel(wrap_container, text="🤖 GenericAgent",
                             font=FONT_SMALL, text_color=COLOR_MUTED,
                             anchor="w")
        head.pack(anchor="w", pady=(0, 4))

        content_frame = ctk.CTkFrame(wrap_container, fg_color="transparent")
        content_frame.pack(fill="x", anchor="w")

        if final:
            self._render_assistant_content(content_frame, text, wrap)
            self._current_assistant_label = None
            self._current_assistant_content = None
            self._current_stream_text = ""
        else:
            # 流式：content_frame 保持为空容器；_stream_update 负责填充
            self._current_assistant_label = None
            self._current_assistant_content = content_frame
            self._stream_frozen = 0
            self._live_label = None
            self._pending_stream_text = None
            self._stream_render_scheduled = False
            self._last_stream_render_time = 0
            self._current_stream_text = ""

        self.after(30, self._scroll_bottom)
        return wrap_container

    def _cur_wrap(self):
        try:
            w = self.msg_area.winfo_width()
            if w < 100: w = 760
        except Exception:
            w = 760
        return max(240, w - 200)

    def _render_assistant_content(self, cf, text, wrap=None):
        """一次性渲染全部段（用于历史会话加载、非流式场景）。"""
        if wrap is None: wrap = self._cur_wrap()
        segments = fold_turns(text or "")
        if len(segments) <= 1:
            widgets = render_rich(cf, text or "…", wrap)
            self._bubble_labels.extend(
                [w for w in widgets if isinstance(w, ctk.CTkLabel)])
            return
        for seg in segments:
            if seg["type"] == "fold":
                fs = FoldSection(cf, seg["title"], seg["content"], wrap)
                fs.pack(fill="x", pady=3, anchor="w")
                self._fold_sections.append(fs)
            else:
                c = (seg["content"] or "").strip()
                if not c: continue
                widgets = render_rich(cf, c, wrap)
                self._bubble_labels.extend(
                    [w for w in widgets if isinstance(w, ctk.CTkLabel)])

    def _stream_update(self, cumulative_text):
        """收到 next 事件时调用：节流后做增量渲染。"""
        self._pending_stream_text = cumulative_text
        self._current_stream_text = cumulative_text or ""
        now = time.time()
        last = getattr(self, "_last_stream_render_time", 0)
        if now - last < 0.08:
            if not getattr(self, "_stream_render_scheduled", False):
                self._stream_render_scheduled = True
                self.after(80, self._flush_stream_render)
            return
        self._last_stream_render_time = now
        self._do_stream_render(cumulative_text)

    def _flush_stream_render(self):
        self._stream_render_scheduled = False
        t = getattr(self, "_pending_stream_text", None)
        if t is None: return
        self._pending_stream_text = None
        self._last_stream_render_time = time.time()
        self._do_stream_render(t)

    def _do_stream_render(self, cumulative_text):
        """实际增量渲染：冻结已完成的 Turn，live 段复用单个 Label。"""
        cf = getattr(self, "_current_assistant_content", None)
        if not cf: return
        segments = fold_turns(cumulative_text or "")
        wrap = self._cur_wrap()
        frozen = getattr(self, "_stream_frozen", 0)
        live_label = getattr(self, "_live_label", None)

        # 跨越 Turn 边界：把之前的 live 段正式冻结（不再刷新）
        while frozen < len(segments) - 1:
            # 销毁当前 live label（其内容会由 fold/static 精确再渲染）
            if live_label is not None:
                try: live_label.destroy()
                except Exception: pass
                if live_label in self._bubble_labels:
                    self._bubble_labels.remove(live_label)
                live_label = None
            seg = segments[frozen]
            if seg["type"] == "fold":
                fs = FoldSection(cf, seg["title"], seg["content"], wrap)
                fs.pack(fill="x", pady=3, anchor="w")
                self._fold_sections.append(fs)
            else:
                c = (seg["content"] or "").strip()
                if c:
                    ws = render_rich(cf, c, wrap)
                    self._bubble_labels.extend(
                        [w for w in ws if isinstance(w, ctk.CTkLabel)])
            frozen += 1
        self._stream_frozen = frozen

        # 更新 / 创建 live 段（最后一段）
        live_seg = segments[-1] if segments else {"content": ""}
        live_text = (live_seg.get("content") or "").rstrip() + " ▌"
        if live_label is None:
            live_label = ctk.CTkLabel(cf, text=live_text, font=FONT_BODY,
                                       wraplength=wrap, justify="left",
                                       anchor="w")
            live_label.pack(fill="x", anchor="w", pady=(2, 2))
            self._bubble_labels.append(live_label)
            self._live_label = live_label
        else:
            try:
                live_label.configure(text=live_text, wraplength=wrap)
            except Exception:
                pass
        self.after(30, self._scroll_bottom)

    def _stream_done(self, final_text):
        """done 事件：清掉 live label，把剩余段渲染成正式结构（含富文本）。"""
        cf = getattr(self, "_current_assistant_content", None)
        if not cf:
            return
        self._current_stream_text = final_text or self._current_stream_text
        # 取消任何待 flush
        self._pending_stream_text = None
        self._stream_render_scheduled = False
        # 销毁 live label
        live_label = getattr(self, "_live_label", None)
        if live_label is not None:
            try: live_label.destroy()
            except Exception: pass
            if live_label in self._bubble_labels:
                self._bubble_labels.remove(live_label)
            self._live_label = None

        segments = fold_turns(final_text or "")
        wrap = self._cur_wrap()
        frozen = getattr(self, "_stream_frozen", 0)
        for i in range(frozen, len(segments)):
            seg = segments[i]
            if seg["type"] == "fold":
                fs = FoldSection(cf, seg["title"], seg["content"], wrap)
                fs.pack(fill="x", pady=3, anchor="w")
                self._fold_sections.append(fs)
            else:
                c = (seg["content"] or "").strip()
                if c:
                    ws = render_rich(cf, c, wrap)
                    self._bubble_labels.extend(
                        [w for w in ws if isinstance(w, ctk.CTkLabel)])
        self._stream_frozen = 0
        self._current_assistant_content = None
        self._current_stream_text = ""
        self.after(30, self._scroll_bottom)

    def _finalize_last_bubble(self, text):
        """兼容旧调用名。"""
        self._stream_done(text)

    def _scroll_bottom(self):
        try:
            self.msg_area._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _jump_to_bubble(self, idx, flash=True):
        """将 msg_area 滚动至指定气泡并短暂高亮。idx 为 session bubbles 下标。"""
        rows = getattr(self, "_bubble_rows", None) or []
        if idx < 0 or idx >= len(rows):
            return
        row = rows[idx]
        try: self.update_idletasks()
        except Exception: pass
        try:
            canvas = self.msg_area._parent_canvas
            inner = self.msg_area._parent_frame
            inner_h = max(1, inner.winfo_height())
            target_y = max(0, row.winfo_y() - 20)
            canvas.yview_moveto(min(1.0, target_y / inner_h))
        except Exception:
            try: row.tkraise()
            except Exception: pass
        if flash:
            try:
                prev = row.cget("fg_color")
            except Exception:
                prev = "transparent"
            try: row.configure(fg_color=COLOR_ACTIVE_HOVER)
            except Exception: pass
            def _unflash():
                try: row.configure(fg_color=prev if prev else "transparent")
                except Exception: pass
            self.after(1400, _unflash)

    def _send(self, text=None, auto=False):
        if text is None:
            text = self.input_box.get("1.0", "end").strip()
        if not text:
            return
        if getattr(self, "_busy", False):
            if not auto:
                messagebox.showinfo("忙碌中", "当前任务还在运行，请稍候或点击“中断”。")
            return
        self._ensure_session(text)
        if not auto:
            self.input_box.delete("1.0", "end")
        self._add_bubble("user", text)
        self._add_bubble("assistant", "")
        self.current_session["bubbles"].append({"role": "user", "text": text})
        self._persist_current_session()
        self._busy = True
        self._abort_requested = False
        self._last_activity = time.time()
        self.send_btn.configure(state="disabled", text="生成中")
        self.stop_btn.configure(state="normal", text="中断")
        self._send_cmd({"cmd": "send", "text": text})

    def _update_last(self, text):
        if self._current_assistant_label is not None:
            try:
                self._current_assistant_label.configure(text=text or "…")
            except Exception:
                pass
            self.after(30, self._scroll_bottom)

    def _abort(self):
        if not getattr(self, "_busy", False) or self._abort_requested:
            return
        self._abort_requested = True
        try:
            self.send_btn.configure(state="disabled", text="中断中")
            self.stop_btn.configure(state="disabled", text="已发送")
        except Exception:
            pass
        self._send_cmd({"cmd": "abort"})

    # ---------- 功能菜单（右上角齿轮） ----------
    AUTO_TASK_TEXT = ("[AUTO]🤖 用户已经离开超过30分钟，作为自主智能体，"
                      "请阅读自动化sop，执行自动任务。")

    def _close_functions_panel(self):
        popup_close = getattr(self, "_active_popup_close", None)
        if callable(popup_close):
            try:
                popup_close()
            except Exception:
                pass
        bar = getattr(self, "_functions_bar", None)
        if bar is not None:
            try:
                bar.destroy()
            except Exception:
                pass
        self._functions_bar = None
        menu = getattr(self, "_functions_menu_native", None)
        if menu is not None:
            try:
                menu.unpost()
            except Exception:
                pass
            try:
                menu.destroy()
            except Exception:
                pass
        self._functions_menu_native = None

    def _open_functions_menu(self, event=None):
        auto_on = bool(self.cfg.get("autonomous_enabled", False))
        self._close_functions_panel()
        items = [
            ("🛠  重新注入工具示范", self._reinject_tools),
            ("🐱  启动桌面宠物", self._launch_pet),
            "---",
            ("🤖  立即触发自主任务", self._trigger_autonomous),
            (("⏸  禁止空闲自主行动" if auto_on else "▶  允许空闲自主行动"),
                self._toggle_autonomous),
            "---",
            ("♻  重启内核", self._restart_bridge),
        ]
        self.update_idletasks()
        self.gear_btn.update_idletasks()
        menu_width = 210
        x = self.gear_btn.winfo_rootx() + self.gear_btn.winfo_width() - menu_width
        y = self.gear_btn.winfo_rooty() + self.gear_btn.winfo_height() + 6
        self._popup_menu(x, y, items, min_width=menu_width, anchor="nw")
        return "break"

    # 兼容老调用名（_open_settings_menu 已退役，统一指向功能菜单）
    def _open_settings_menu(self):
        self._open_functions_menu()

    def _reinject_tools(self):
        self._send_cmd({"cmd": "reinject_tools"})

    def _launch_pet(self):
        self._send_cmd({"cmd": "launch_pet"})

    def _trigger_autonomous(self):
        if getattr(self, "_busy", False):
            messagebox.showinfo("忙碌中", "当前任务还在运行，请稍候。")
            return
        if not messagebox.askyesno("立即触发自主任务",
            "将向 Agent 发送一次自主任务指令，确定继续？"):
            return
        self._send(text=self.AUTO_TASK_TEXT, auto=True)

    def _toggle_autonomous(self):
        new_state = not bool(self.cfg.get("autonomous_enabled", False))
        self.cfg["autonomous_enabled"] = new_state
        save_config(self.cfg)
        messagebox.showinfo("自主行动",
            "已开启：空闲超过 30 分钟会自动触发一次自主任务。" if new_state
            else "已关闭：不再自动触发。")
        if new_state and not getattr(self, "_idle_thread_started", False):
            self._idle_thread_started = True
            threading.Thread(target=self._idle_monitor,
                              daemon=True).start()

    def _idle_monitor(self):
        """每 60s 检查一次；闲置 > 30min 且未忙碌时触发一次自主任务。"""
        while True:
            time.sleep(60)
            try:
                if not self.cfg.get("autonomous_enabled", False):
                    continue
                if getattr(self, "_busy", False):
                    continue
                last = getattr(self, "_last_activity", time.time())
                if time.time() - last < 1800:
                    continue
                self._last_activity = time.time()
                self.after(0, lambda: self._send(
                    text=self.AUTO_TASK_TEXT, auto=True))
            except Exception:
                pass

    def _open_api_editor(self):
        """读取 GenericAgent/mykey.py 并在弹窗里编辑。"""
        agent_dir = self.agent_dir.get().strip()
        py_path = os.path.join(agent_dir, "mykey.py")
        tpl_path = os.path.join(agent_dir, "mykey_template.py")
        if not os.path.isfile(py_path):
            # 首次创建 → 从 template 拷贝
            if os.path.isfile(tpl_path):
                try:
                    with open(tpl_path, "r", encoding="utf-8") as f:
                        open(py_path, "w", encoding="utf-8").write(f.read())
                except Exception as e:
                    messagebox.showerror("创建失败", str(e)); return
            else:
                open(py_path, "w", encoding="utf-8").write("# mykey.py\n")
        try:
            content = open(py_path, "r", encoding="utf-8").read()
        except Exception as e:
            messagebox.showerror("读取失败", str(e)); return

        dlg = ctk.CTkToplevel(self)
        dlg.title("API 设置")
        dlg.geometry("820x580")
        dlg.transient(self); dlg.grab_set()
        dlg.configure(fg_color=COLOR_SURFACE)

        head = ctk.CTkFrame(dlg, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(head, text="编辑 mykey.py",
                     font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ctk.CTkLabel(head, text=py_path, font=FONT_SMALL,
                     text_color=COLOR_MUTED).pack(side="right")

        ctk.CTkLabel(
            dlg,
            text=("这里是直接编辑 mykey.py 的原文入口。\n"
                  "上游现在更常见的是 native_claude_config / native_oai_config / mixin_config 这类配置字典；"
                  "保存后需要重启内核才会生效。"),
            font=FONT_SMALL, text_color=COLOR_MUTED,
            anchor="w", justify="left", wraplength=760
        ).pack(fill="x", padx=20, pady=(0, 8))

        tb = ctk.CTkTextbox(dlg, font=("Consolas", 11),
                             fg_color=COLOR_FIELD_BG, text_color=COLOR_CODE_TEXT,
                             wrap="none", corner_radius=10)
        tb.pack(fill="both", expand=True, padx=20, pady=6)
        tb.insert("1.0", content)

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.pack(fill="x", padx=20, pady=(6, 16))

        def cancel(): dlg.destroy()
        def save_only():
            try:
                open(py_path, "w", encoding="utf-8").write(tb.get("1.0", "end-1c"))
                messagebox.showinfo("已保存", "已写入 mykey.py。\n\n需要重启内核才能生效。")
                dlg.destroy()
            except Exception as e:
                messagebox.showerror("保存失败", str(e))
        def save_and_restart():
            try:
                open(py_path, "w", encoding="utf-8").write(tb.get("1.0", "end-1c"))
            except Exception as e:
                messagebox.showerror("保存失败", str(e)); return
            dlg.destroy()
            self._restart_bridge()

        ctk.CTkButton(btns, text="取消", width=90, height=34,
                       fg_color="transparent", hover_color=COLOR_CARD,
                       text_color=COLOR_MUTED, command=cancel).pack(side="left")
        ctk.CTkButton(btns, text="仅保存", width=110, height=34,
                      fg_color=COLOR_CARD, hover_color=COLOR_CARD_HOVER,
                      text_color=COLOR_TEXT,
                      command=save_only).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btns, text="保存并重启内核", width=150, height=34,
                      fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
                      text_color=COLOR_ON_ACCENT,
                      command=save_and_restart).pack(side="right")

    def _restart_bridge(self):
        """关闭当前桥进程 → 重新 bootstrap。会话历史保留在磁盘。"""
        try:
            if getattr(self, "bridge_proc", None) and self.bridge_proc.poll() is None:
                try:
                    self.bridge_proc.stdin.write('{"cmd":"quit"}\n')
                    self.bridge_proc.stdin.flush()
                except Exception:
                    pass
                try: self.bridge_proc.terminate()
                except Exception: pass
        except Exception:
            pass
        self.bridge_proc = None
        path = self.agent_dir.get().strip()
        self.clear()
        self.container.pack_forget()
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=36, pady=28)
        wrap = ctk.CTkFrame(self.container, fg_color="transparent")
        wrap.pack(expand=True)
        ctk.CTkLabel(wrap, text="⏳", font=("Segoe UI Emoji", 56)).pack(pady=(60, 14))
        ctk.CTkLabel(wrap, text="重启内核…", font=FONT_TITLE).pack()
        self.load_status = ctk.CTkLabel(wrap, text="准备…",
                                         font=FONT_SUB, text_color=COLOR_MUTED)
        self.load_status.pack(pady=(10, 20))
        bar = ctk.CTkProgressBar(wrap, width=360, height=6,
                                  progress_color=COLOR_ACCENT)
        bar.pack(); bar.configure(mode="indeterminate"); bar.start()
        threading.Thread(target=self._bootstrap_kernel,
                          args=(path,), daemon=True).start()

    def _shutdown_for_exit(self):
        try:
            self._stop_all_managed_channels(refresh=False)
        except Exception:
            pass
        try:
            if getattr(self, "bridge_proc", None) and self.bridge_proc.poll() is None:
                try:
                    self.bridge_proc.stdin.write('{"cmd":"quit"}\n')
                    self.bridge_proc.stdin.flush()
                except Exception:
                    pass
                try:
                    self.bridge_proc.terminate()
                except Exception:
                    pass
        except Exception:
            pass


if __name__ == "__main__":
    app = Launcher()
    def _on_close():
        try:
            app._shutdown_for_exit()
        finally:
            app.destroy()
    app.protocol("WM_DELETE_WINDOW", _on_close)
    app.mainloop()
