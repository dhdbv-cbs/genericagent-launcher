from __future__ import annotations

import ast
import hashlib
import os
import re

try:
    import tomllib
except Exception:  # pragma: no cover - Python 3.10 fallback path
    tomllib = None

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
        "evidence": "Quick Start / Usage -> uv pip install -e \".[ui]\"；desktop 入口 python launch.pyw；Terminal UI 推荐入口 python frontends/tui_v3.py",
    },
    {
        "source": "docs/installation.md",
        "evidence": "详细安装说明：Python 3.11/3.12；可运行 frontends/GenericAgent.exe；可选 python assets/configure_mykey.py",
    },
    {
        "source": "docs/installation_zh.md",
        "evidence": "中文安装说明与 README 保持一致：launch.pyw / tui_v3.py / assets/configure_mykey.py",
    },
    {
        "source": "pyproject.toml",
        "evidence": "核心依赖含 aiohttp；ui extra 含 streamlit / pywebview / textual / prompt_toolkit / rich / pillow",
    },
    {
        "source": "frontends/dcapp.py",
        "evidence": "Discord bot frontend; pip install discord.py",
    },
    {
        "source": "frontends/desktop_bridge.py",
        "evidence": "Web2 / Tauri 桌面桥依赖 aiohttp",
    },
    {
        "source": "frontends/qtapp.py",
        "evidence": "依赖: pip install PySide6；可选: pip install markdown",
    },
    {
        "source": "frontends/tui_v3.py",
        "evidence": "最新 Terminal UI；README 推荐入口；依赖 rich / prompt_toolkit，图片粘贴需要 pillow",
    },
    {
        "source": "frontends/tuiapp_v2.py",
        "evidence": "Textual Terminal UI v2；作为 tui_v3 不存在时的兼容回退",
    },
    {
        "source": "frontends/slash_cmds.py",
        "evidence": "TUI v2/v3 共用 slash 命令包：/update、/scheduler、/goal、/hive、/morphling、/conductor 等",
    },
    {
        "source": "frontends/conductor.py",
        "evidence": "Conductor 子 Agent 编排网页控制台；依赖 fastapi / uvicorn / pydantic",
    },
    {
        "source": "assets/configure_mykey.py",
        "evidence": "交互式 mykey 配置向导入口；新增 CommonStack 统一网关模板",
    },
    {
        "source": "ga_cli/cli.py",
        "evidence": "上游全局 ga 命令分发入口；当前仍提供 tui/tui2，未提供 tui3 子命令",
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
    {
        "id": "conductor_frontend",
        "label": "Conductor 编排台可选",
        "description": "如果用户要使用上游 frontends/conductor.py，这组依赖需要可用",
        "required": False,
        "items": [
            {
                "package": "fastapi",
                "import": "fastapi",
                "optional": True,
                "note": "Conductor 本地 HTTP / WebSocket 控制台",
            },
            {
                "package": "uvicorn[standard]",
                "import": "uvicorn",
                "optional": True,
                "note": "Conductor 本地 Web 服务入口",
            },
            {
                "package": "pydantic",
                "import": "pydantic",
                "optional": True,
                "note": "Conductor 请求体模型",
            },
        ],
    },
    {
        "id": "desktop_bridge_frontend",
        "label": "桌面桥前端可选",
        "description": "如果用户要使用上游 desktop_bridge.py / Tauri 桌面壳，这组依赖需要可用",
        "required": False,
        "items": [
            {
                "package": "aiohttp>=3.9",
                "import": "aiohttp",
                "optional": True,
                "note": "desktop_bridge.py HTTP + WebSocket bridge",
            },
        ],
    },
]

_UPSTREAM_PYPROJECT_NAME = "pyproject.toml"
_PYPROJECT_SOURCE_EVIDENCE = "优先解析 [project].dependencies 和 [project.optional-dependencies]"
_SYNC_FALLBACK_DEPENDENCIES = [
    "beautifulsoup4>=4.12",
    "bottle>=0.12",
    "simple-websocket-server>=0.4.4",
]
_REMOTE_FALLBACK_EXTRA_DEPENDENCIES = [
    "streamlit>=1.37",
    "markdown>=3.6",
    "textual>=0.70",
    "prompt_toolkit>=3.0,<4",
    "rich>=13.0",
    "pillow>=9.0",
    "qrcode>=8.0",
    "pycryptodome>=3.20",
]
_PYPROJECT_FRONTEND_GROUP_SELECTIONS = {
    "launch_web_ui": ("ui", {"streamlit", "pywebview"}),
    "streamlit_frontends": ("ui", {"streamlit"}),
}
_PYPROJECT_REMOTE_OPTIONAL_TARGETS = {
    "ui": {"streamlit", "textual", "prompt_toolkit", "rich", "pillow"},
    "all-frontends": {"qrcode", "pycryptodome"},
}
_PYPROJECT_IMPORT_NAME_MAP = {
    "pywebview": "webview",
    "pillow": "PIL",
}


def _package_base_name(spec: str) -> str:
    text = str(spec or "").strip()
    if not text:
        return ""
    return re.split(r"[<>=!~\[\]]", text, maxsplit=1)[0].strip().lower()


def _hash_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def _hash_specs(specs) -> str:
    payload = "\n".join(str(item or "").strip() for item in (specs or []) if str(item or "").strip())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest() if payload else ""


def _clone_dependency_items(items):
    return [dict(item or {}) for item in (items or [])]


def _clone_frontend_groups(groups):
    out = []
    for group in groups or []:
        row = dict(group or {})
        row["items"] = _clone_dependency_items(row.get("items") or [])
        out.append(row)
    return out


def _dedupe_specs(specs):
    out = []
    seen = set()
    for raw in specs or []:
        spec = str(raw or "").strip()
        if not spec:
            continue
        base = _package_base_name(spec)
        key = base or spec.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def _merge_dependency_items(primary_items, fallback_items):
    out = []
    seen = set()
    for item in list(primary_items or []) + list(fallback_items or []):
        row = dict(item or {})
        package = str(row.get("package") or "").strip()
        if not package:
            continue
        base = _package_base_name(package)
        if base in seen:
            continue
        seen.add(base)
        out.append(row)
    return out


def _build_dependency_item(spec: str, *, optional: bool, note: str = ""):
    package = str(spec or "").strip()
    base = _package_base_name(package)
    return {
        "package": package,
        "import": _PYPROJECT_IMPORT_NAME_MAP.get(base, base.replace("-", "_") if base else ""),
        "optional": bool(optional),
        "note": str(note or "").strip(),
    }


def _strip_toml_comment(line: str) -> str:
    text = str(line or "")
    quote = ""
    escaped = False
    out = []
    for ch in text:
        if quote:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _array_bracket_delta(text: str) -> int:
    quote = ""
    escaped = False
    delta = 0
    for ch in str(text or ""):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in ('"', "'"):
            quote = ch
            continue
        if ch == "[":
            delta += 1
        elif ch == "]":
            delta -= 1
    return delta


def _extract_section_text(pyproject_text: str, section_name: str) -> str:
    lines = str(pyproject_text or "").splitlines()
    inside = False
    captured = []
    target = str(section_name or "").strip()
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip()
            if inside:
                break
            inside = current == target
            continue
        if inside:
            captured.append(raw)
    return "\n".join(captured)


def _extract_string_array_assignments(section_text: str):
    lines = str(section_text or "").splitlines()
    out = {}
    i = 0
    while i < len(lines):
        raw = _strip_toml_comment(lines[i]).strip()
        if not raw:
            i += 1
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+)$", raw)
        if not match:
            i += 1
            continue
        key = match.group(1).strip()
        value_text = match.group(2).strip()
        delta = _array_bracket_delta(value_text)
        while delta > 0 and (i + 1) < len(lines):
            i += 1
            extra = _strip_toml_comment(lines[i]).strip()
            if extra:
                value_text += "\n" + extra
                delta += _array_bracket_delta(extra)
        try:
            parsed = ast.literal_eval(value_text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            out[key] = [str(item or "").strip() for item in parsed if str(item or "").strip()]
        i += 1
    return out


def _parse_pyproject_text_fallback(pyproject_text: str):
    project_text = _extract_section_text(pyproject_text, "project")
    project_rows = _extract_string_array_assignments(project_text)
    optional_rows = _extract_string_array_assignments(_extract_section_text(pyproject_text, "project.optional-dependencies"))
    requires_python = ""
    for raw in str(project_text or "").splitlines():
        clean = _strip_toml_comment(raw).strip()
        if not clean:
            continue
        match = re.match(r"^requires-python\s*=\s*(.+)$", clean)
        if not match:
            continue
        try:
            parsed = ast.literal_eval(match.group(1).strip())
        except Exception:
            parsed = ""
        requires_python = str(parsed or "").strip()
        if requires_python:
            break
    return {
        "project": {
            "dependencies": list(project_rows.get("dependencies") or []),
            "requires-python": requires_python,
            "optional-dependencies": {key: list(value or []) for key, value in optional_rows.items()},
        }
    }


def _load_pyproject_doc(pyproject_path: str):
    errors = []
    if tomllib is not None:
        try:
            with open(pyproject_path, "rb") as f:
                return tomllib.load(f), ""
        except Exception as e:
            errors.append(str(e))
    try:
        with open(pyproject_path, "r", encoding="utf-8") as f:
            return _parse_pyproject_text_fallback(f.read()), ""
    except Exception as e:
        errors.append(str(e))
    return {}, "；".join(err for err in errors if err)


def _requirements_path(agent_dir: str) -> str:
    root = str(agent_dir or "").strip()
    if not root:
        return ""
    path = os.path.join(root, "requirements.txt")
    return path if os.path.isfile(path) else ""


def _pyproject_path(agent_dir: str) -> str:
    root = str(agent_dir or "").strip()
    if not root:
        return ""
    return os.path.join(root, _UPSTREAM_PYPROJECT_NAME)


def _pyproject_dependency_map(pyproject_doc):
    project = dict((pyproject_doc or {}).get("project") or {})
    dependencies = [str(item or "").strip() for item in (project.get("dependencies") or []) if str(item or "").strip()]
    optional_map = {}
    requires_python = str(project.get("requires-python") or "").strip()
    for key, value in dict(project.get("optional-dependencies") or {}).items():
        if not isinstance(value, (list, tuple)):
            continue
        optional_map[str(key or "").strip()] = [str(item or "").strip() for item in value if str(item or "").strip()]
    return dependencies, optional_map, requires_python


def _resolve_frontend_groups(optional_map):
    groups = _clone_frontend_groups(UPSTREAM_FRONTEND_DEPENDENCY_GROUPS)
    py_items_by_extra = {
        key: [_build_dependency_item(spec, optional=True, note=f"来自 {_UPSTREAM_PYPROJECT_NAME} [project.optional-dependencies].{key}") for spec in specs]
        for key, specs in dict(optional_map or {}).items()
    }
    for group in groups:
        group_id = str(group.get("id") or "").strip()
        selection = _PYPROJECT_FRONTEND_GROUP_SELECTIONS.get(group_id)
        if not selection:
            continue
        extra_name, allowed_bases = selection
        preferred = []
        for item in py_items_by_extra.get(extra_name, []):
            if _package_base_name(item.get("package")) in allowed_bases:
                preferred.append(item)
        if preferred:
            group["items"] = _merge_dependency_items(preferred, group.get("items") or [])
    return groups


def _fallback_sync_specs():
    return _dedupe_specs([*(dep.get("package") for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES), *_SYNC_FALLBACK_DEPENDENCIES])


def _resolve_remote_fallback_specs(sync_specs, optional_map):
    extras = []
    for extra_name, allowed_bases in _PYPROJECT_REMOTE_OPTIONAL_TARGETS.items():
        for spec in dict(optional_map or {}).get(extra_name, []) or []:
            if _package_base_name(spec) in allowed_bases:
                extras.append(spec)
    return _dedupe_specs([*list(sync_specs or []), *extras, *_REMOTE_FALLBACK_EXTRA_DEPENDENCIES])


def resolve_upstream_dependency_manifest(agent_dir=""):
    req_path = _requirements_path(agent_dir)
    pyproject_path = _pyproject_path(agent_dir)
    pyproject_found = bool(pyproject_path and os.path.isfile(pyproject_path))
    groups = _clone_frontend_groups(UPSTREAM_FRONTEND_DEPENDENCY_GROUPS)
    sources = [dict(item or {}) for item in UPSTREAM_DEPENDENCY_SOURCES]
    fallback_specs = _fallback_sync_specs()
    manifest = {
        "pyproject_path": pyproject_path,
        "pyproject_found": pyproject_found,
        "pyproject_used": False,
        "pyproject_error": "",
        "requires_python": "",
        "requirements_path": req_path,
        "sync_mode": "fallback",
        "sync_path": "",
        "sync_hash": _hash_specs(fallback_specs),
        "sync_label": "启动器维护依赖表",
        "sync_specs": fallback_specs,
        "frontend_groups": groups,
        "dependency_sources": sources,
        "project_core_dependencies": [],
        "remote_fallback_specs": _dedupe_specs([*fallback_specs, *_REMOTE_FALLBACK_EXTRA_DEPENDENCIES]),
    }
    if pyproject_found:
        pyproject_doc, pyproject_error = _load_pyproject_doc(pyproject_path)
        dependencies, optional_map, requires_python = _pyproject_dependency_map(pyproject_doc)
        manifest["requires_python"] = requires_python
        if dependencies:
            sync_specs = _dedupe_specs([*dependencies, *(dep.get("package") for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES)])
            manifest["pyproject_used"] = True
            manifest["sync_mode"] = "pyproject"
            manifest["sync_path"] = pyproject_path
            manifest["sync_hash"] = _hash_file(pyproject_path) or _hash_specs(sync_specs)
            manifest["sync_label"] = f"{_UPSTREAM_PYPROJECT_NAME} [project].dependencies"
            manifest["sync_specs"] = sync_specs
            manifest["frontend_groups"] = _resolve_frontend_groups(optional_map)
            manifest["project_core_dependencies"] = [
                _build_dependency_item(spec, optional=False, note=f"来自 {_UPSTREAM_PYPROJECT_NAME} [project].dependencies")
                for spec in dependencies
            ]
            manifest["remote_fallback_specs"] = _resolve_remote_fallback_specs(sync_specs, optional_map)
            manifest["dependency_sources"] = [
                {
                    "source": _UPSTREAM_PYPROJECT_NAME,
                    "evidence": f"{_PYPROJECT_SOURCE_EVIDENCE}；启动依赖、本地报告分组、远端 fallback requirements 优先复用这份声明。",
                },
                *sources,
            ]
            return manifest
        manifest["pyproject_error"] = pyproject_error or "缺少可用的 [project].dependencies"
        manifest["dependency_sources"] = [
            {
                "source": _UPSTREAM_PYPROJECT_NAME,
                "evidence": f"解析不可用：{manifest['pyproject_error']}；已回退到 {'requirements.txt' if req_path else '启动器维护依赖表'}。",
            },
            *sources,
        ]
    else:
        manifest["dependency_sources"] = [
            {
                "source": _UPSTREAM_PYPROJECT_NAME,
                "evidence": f"文件不存在；已回退到 {'requirements.txt' if req_path else '启动器维护依赖表'}。",
            },
            *sources,
        ]
    if req_path:
        manifest["sync_mode"] = "requirements"
        manifest["sync_path"] = req_path
        manifest["sync_hash"] = _hash_file(req_path)
        manifest["sync_label"] = "requirements.txt"
        manifest["sync_specs"] = []
    return manifest


def resolve_upstream_frontend_dependency_groups(agent_dir=""):
    return _clone_frontend_groups(resolve_upstream_dependency_manifest(agent_dir).get("frontend_groups") or [])


def resolve_upstream_dependency_sources(agent_dir=""):
    return [dict(item or {}) for item in resolve_upstream_dependency_manifest(agent_dir).get("dependency_sources") or []]


def resolve_upstream_runtime_dependency_specs(agent_dir=""):
    return list(resolve_upstream_dependency_manifest(agent_dir).get("sync_specs") or [])


def resolve_remote_fallback_requirement_specs(agent_dir=""):
    return list(resolve_upstream_dependency_manifest(agent_dir).get("remote_fallback_specs") or [])
