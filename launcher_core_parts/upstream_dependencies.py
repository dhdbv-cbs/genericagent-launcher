from __future__ import annotations

LAUNCHER_BOOTSTRAP_DEPENDENCIES = [
    {
        "package": "requests>=2.31",
        "import": "requests",
        "optional": False,
        "note": "启动器维护：GenericAgent API/桥接基础通信；自动修复时直接升级到最新版",
    },
    {
        "package": "simplejson>=3.19.3",
        "import": "simplejson",
        "optional": False,
        "note": "启动器维护：兼容 requests/simplejson 的 JSONDecodeError；自动修复时直接升级到最新版",
    },
    {
        "package": "charset-normalizer>=3.3",
        "import": "charset_normalizer",
        "optional": False,
        "note": "启动器维护：避免 requests 在缺少字符集依赖时触发 RequestsDependencyWarning",
    },
]


UPSTREAM_DEPENDENCY_SOURCES = [
    {
        "source": "README.md",
        "evidence": "Method 1: Standard Installation -> pip install streamlit pywebview",
    },
    {
        "source": "README.md",
        "evidence": "Alternative App Frontends -> python frontends/qtapp.py / streamlit run frontends/stapp2.py",
    },
    {
        "source": "README.md",
        "evidence": "Bot Interface -> wechat / qq / feishu / wecom / dingtalk pip install examples",
    },
    {
        "source": "GETTING_STARTED.md",
        "evidence": "让 Agent 自己装依赖；若 API 不通可先手动 pip install requests",
    },
    {
        "source": "frontends/qtapp.py",
        "evidence": "依赖: pip install PySide6；可选: pip install markdown",
    },
]


UPSTREAM_FRONTEND_DEPENDENCY_GROUPS = [
    {
        "id": "launcher_bootstrap",
        "label": "主聊天必需",
        "description": "当前启动器主聊天和桥接必须可用的基础包",
        "required": True,
        "items": LAUNCHER_BOOTSTRAP_DEPENDENCIES,
    },
    {
        "id": "launch_web_ui",
        "label": "上游默认 GUI 可选",
        "description": "如果用户要直接运行上游 launch.pyw，这组依赖需要可用",
        "required": False,
        "items": [
            {
                "package": "streamlit",
                "import": "streamlit",
                "optional": True,
                "note": "上游 README 最小依赖",
            },
            {
                "package": "pywebview",
                "import": "webview",
                "optional": True,
                "note": "上游 README 最小依赖",
            },
        ],
    },
    {
        "id": "qt_frontend",
        "label": "Qt 前端可选",
        "description": "如果用户要使用上游 frontends/qtapp.py，这组依赖需要可用",
        "required": False,
        "items": [
            {
                "package": "PySide6",
                "import": "PySide6",
                "optional": True,
                "note": "frontends/qtapp.py 文件头依赖说明",
            },
            {
                "package": "markdown",
                "import": "markdown",
                "optional": True,
                "note": "frontends/qtapp.py 文件头可选依赖",
            },
        ],
    },
    {
        "id": "streamlit_frontends",
        "label": "Streamlit 前端可选",
        "description": "如果用户要使用上游 stapp.py / stapp2.py，这组依赖需要可用",
        "required": False,
        "items": [
            {
                "package": "streamlit",
                "import": "streamlit",
                "optional": True,
                "note": "上游 Streamlit 前端",
            },
        ],
    },
]
