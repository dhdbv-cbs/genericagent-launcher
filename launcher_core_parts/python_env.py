from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time

from .runtime import (
    _bridge_script_path,
    _external_subprocess_env,
    _python_creationflags,
    _python_utf8_subprocess_env,
    _resolve_config_path,
    _run_external_subprocess,
    load_config,
)
from .upstream_dependencies import (
    LAUNCHER_BOOTSTRAP_DEPENDENCIES,
    UPSTREAM_DEPENDENCY_SOURCES,
    UPSTREAM_FRONTEND_DEPENDENCY_GROUPS,
)

_AUTO_BOOTSTRAP_PACKAGES = ("requests", "simplejson", "charset-normalizer")
_DEPENDENCY_STATE_FILE = os.path.join("temp", "launcher_dependency_state.json")
_UV_CMD_CACHE = None
_PACKAGE_IMPORT_NAME_MAP = {
    "pycryptodome": "Crypto",
    "python-telegram-bot": "telegram",
    "qq-botpy": "botpy",
    "lark-oapi": "lark_oapi",
    "dingtalk-stream": "dingtalk_stream",
}


def _system_python_commands():
    candidates = []
    cfg_py = None
    try:
        cfg = load_config()
        if isinstance(cfg, dict):
            cfg_py = cfg.get("python_exe")
    except Exception:
        pass
    if cfg_py:
        resolved = _resolve_config_path(cfg_py)
        if resolved:
            candidates.append([resolved])
    if os.name == "nt":
        candidates += [["py", "-3"], ["python"], ["python3"]]
    else:
        candidates += [["python3"], ["python"]]
    return candidates


def _probe_python_command(cmd):
    try:
        r = _run_external_subprocess(
            cmd + ["-c", "import sys;print(sys.executable);print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
            env=_external_subprocess_env(),
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


def _short_subprocess_detail(result, fallback):
    detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
    if not detail:
        return fallback
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    if not lines:
        return fallback
    return "\n".join(lines[-12:])


def _should_bootstrap_python_runtime(detail):
    lowered = str(detail or "").strip().lower()
    if not lowered:
        return False
    if "no module named 'requests'" in lowered or 'no module named "requests"' in lowered:
        return True
    if "jsondecodeerror" in lowered and "simplejson" in lowered:
        return True
    return False


def _emit_dependency_progress(progress, stage, msg, *, status="info"):
    if not callable(progress):
        return
    try:
        progress({"stage": str(stage or "").strip(), "msg": str(msg or "").strip(), "status": str(status or "info").strip()})
    except Exception:
        pass


def _agent_requirements_path(agent_dir):
    path = os.path.join(str(agent_dir or "").strip(), "requirements.txt")
    return path if os.path.isfile(path) else ""


def _dependency_state_path(agent_dir):
    root = str(agent_dir or "").strip()
    if not root:
        return ""
    return os.path.join(root, _DEPENDENCY_STATE_FILE)


def _load_dependency_state(agent_dir):
    path = _dependency_state_path(agent_dir)
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_dependency_state(agent_dir, data):
    path = _dependency_state_path(agent_dir)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data or {}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _dependency_signature(agent_dir, extra_packages=None):
    payload = {
        "bootstrap": list(_AUTO_BOOTSTRAP_PACKAGES),
        "extra_packages": sorted(str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()),
        "requirements_path": "",
        "requirements_hash": "",
    }
    req_path = _agent_requirements_path(agent_dir)
    if req_path:
        payload["requirements_path"] = req_path
        try:
            with open(req_path, "rb") as f:
                payload["requirements_hash"] = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            payload["requirements_hash"] = "read_error"
    return payload


def _dependency_state_matches(agent_dir, py, extra_packages=None):
    state = _load_dependency_state(agent_dir)
    expected = _dependency_signature(agent_dir, extra_packages=extra_packages)
    return (
        os.path.normcase(os.path.normpath(str(state.get("python") or "")))
        == os.path.normcase(os.path.normpath(str(py or "")))
        and state.get("signature") == expected
    )


def _mark_dependency_state(agent_dir, py, extra_packages=None):
    _save_dependency_state(
        agent_dir,
        {
            "python": str(py or "").strip(),
            "signature": _dependency_signature(agent_dir, extra_packages=extra_packages),
        },
    )


def _split_requirement_tokens(raw):
    return [token.strip() for token in str(raw or "").split() if token.strip()]


def _package_base_name(spec):
    text = str(spec or "").strip()
    if not text:
        return ""
    return re.split(r"[<>=!~\[\]]", text, maxsplit=1)[0].strip()


def _package_import_name(spec):
    base = _package_base_name(spec).lower()
    if not base:
        return ""
    mapped = _PACKAGE_IMPORT_NAME_MAP.get(base)
    if mapped:
        return mapped
    return base.replace("-", "_")


def _minimum_version_from_spec(spec):
    text = str(spec or "").strip()
    if not text:
        return ""
    match = re.search(r">=\s*([A-Za-z0-9_.+-]+)", text)
    return match.group(1).strip() if match else ""


def _numeric_version_parts(version_text):
    parts = []
    for piece in re.split(r"[._+-]+", str(version_text or "").strip()):
        chunk = piece.strip()
        if not chunk:
            continue
        match = re.match(r"(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
        if match.end() < len(chunk) and re.search(r"[A-Za-z]", chunk[match.end() :]):
            break
    return tuple(parts)


def _version_meets_minimum(installed_version, minimum_version):
    minimum = str(minimum_version or "").strip()
    if not minimum:
        return True
    installed = str(installed_version or "").strip()
    if not installed:
        return False
    left = _numeric_version_parts(installed)
    right = _numeric_version_parts(minimum)
    if left and right:
        max_len = max(len(left), len(right))
        left = left + (0,) * (max_len - len(left))
        right = right + (0,) * (max_len - len(right))
        return left >= right
    return installed >= minimum


def _probe_python_dependency(py, spec, *, import_name="", strict_version=True):
    name = str(import_name or "").strip() or _package_import_name(spec)
    if not name:
        return False, "无法确定 import 名称", {}
    ok, detail, payload = _probe_python_module(py, name)
    if not ok:
        return False, detail, payload
    minimum = _minimum_version_from_spec(spec)
    installed = str((payload or {}).get("version") or "").strip()
    if minimum and not installed:
        if not strict_version:
            text = (detail or "可导入").strip()
            return True, f"{text} | 版本未知，跳过最低版本校验（建议 >= {minimum}）", payload
        text = (detail or "可导入").strip()
        return False, f"{text} | 无法识别版本，需要 >= {minimum}", payload
    if minimum and not _version_meets_minimum(installed, minimum):
        if not strict_version:
            text = (detail or "可导入").strip()
            return True, f"{text} | 当前版本可能低于建议值 >= {minimum}", payload
        text = (detail or "可导入").strip()
        return False, f"{text} | 版本过低，需要 >= {minimum}", payload
    return True, detail, payload


def _core_runtime_packages_ready(py):
    for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES:
        spec = str(dep.get("package") or "").strip()
        import_name = str(dep.get("import") or "").strip() or _package_import_name(spec)
        ok, _detail, _payload = _probe_python_dependency(py, spec, import_name=import_name, strict_version=True)
        if not ok:
            return False
    return True


def _core_runtime_packages_import_ready(py):
    for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES:
        spec = str(dep.get("package") or "").strip()
        import_name = str(dep.get("import") or "").strip() or _package_import_name(spec)
        ok, _detail, _payload = _probe_python_dependency(py, spec, import_name=import_name, strict_version=False)
        if not ok:
            return False
    return True


def _missing_dependency_specs(py, specs, *, strict_version=False):
    missing = []
    for spec in specs or []:
        item = str(spec or "").strip()
        if not item:
            continue
        ok, _detail, _payload = _probe_python_dependency(py, item, strict_version=bool(strict_version))
        if not ok:
            missing.append(item)
    return missing


def _should_sync_runtime_dependencies(*, state_matches, extra_packages=None, requirements_path="", force_sync=False):
    if force_sync:
        return True
    if not state_matches:
        return True
    if str(requirements_path or "").strip():
        return False
    return bool([str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()])


def _run_python_command(py, args, *, timeout=600):
    env = _external_subprocess_env()
    return _run_external_subprocess(
        [py, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=_python_creationflags(),
        timeout=timeout,
    )


def _run_command(cmd, *, timeout=30, cwd=None):
    return _run_external_subprocess(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_external_subprocess_env(),
        creationflags=_python_creationflags(),
        timeout=timeout,
        cwd=cwd,
    )


def _dependency_installer_mode():
    mode = str(os.environ.get("GA_LAUNCHER_DEP_INSTALLER") or "").strip().lower()
    if not mode:
        try:
            cfg = load_config()
            if isinstance(cfg, dict):
                mode = str(cfg.get("dependency_installer") or "").strip().lower()
        except Exception:
            mode = ""
    if mode in ("uv", "pip"):
        return mode
    return "auto"


def _detect_uv_command():
    global _UV_CMD_CACHE
    if _UV_CMD_CACHE is not None:
        return list(_UV_CMD_CACHE) if _UV_CMD_CACHE else []
    env_uv = str(os.environ.get("GA_LAUNCHER_UV_EXE") or "").strip()
    candidates = []
    if env_uv:
        candidates.append([env_uv])
    candidates.append(["uv"])
    for cmd in candidates:
        try:
            r = _run_command([*cmd, "--version"], timeout=10)
            if r.returncode == 0:
                _UV_CMD_CACHE = list(cmd)
                return list(cmd)
        except Exception:
            continue
    _UV_CMD_CACHE = []
    return []


def _dependency_installer_candidates(py, install_args):
    args = [str(item or "").strip() for item in (install_args or []) if str(item or "").strip()]
    mode = _dependency_installer_mode()
    out = []
    uv_cmd = _detect_uv_command() if mode in ("auto", "uv") else []
    if uv_cmd:
        out.append(
            {
                "installer": "uv",
                "cmd": [*uv_cmd, "pip", "install", "--python", str(py or "").strip(), *args],
            }
        )
    elif mode == "uv":
        out.append({"installer": "uv", "cmd": [], "missing": True})
    if mode in ("auto", "pip"):
        out.append({"installer": "pip", "cmd": [str(py or "").strip(), "-m", "pip", "install", *args]})
    return out, mode


def _run_dependency_install(py, install_args, *, timeout, progress=None, stage="install", label="安装依赖"):
    candidates, mode = _dependency_installer_candidates(py, install_args)
    errors = []
    for item in candidates:
        installer = str(item.get("installer") or "").strip() or "unknown"
        if item.get("missing"):
            errors.append("uv: 未找到可用的 uv 命令（可设置 GA_LAUNCHER_UV_EXE 指向 uv.exe）")
            continue
        cmd = list(item.get("cmd") or [])
        if not cmd:
            continue
        _emit_dependency_progress(progress, stage, f"{label}（{installer}）")
        try:
            result = _run_command(cmd, timeout=timeout)
        except Exception as e:
            _emit_dependency_progress(progress, stage, f"{label}（{installer}）异常：{e}", status="warn")
            errors.append(f"{installer}: {e}")
            continue
        if result.returncode == 0:
            _emit_dependency_progress(progress, stage, f"{label}（{installer}）完成。", status="ok")
            return True, "", installer
        _emit_dependency_progress(progress, stage, f"{label}（{installer}）失败，准备尝试下一个安装器…", status="warn")
        errors.append(f"{installer}: {_short_subprocess_detail(result, f'{label}失败')}")
    if not errors:
        return False, f"{label}失败：没有可用安装器（mode={mode}）", ""
    return False, "；".join(errors), ""


def _make_report_item(name, status, detail="", *, optional=False, fixed=False):
    return {
        "name": str(name or "").strip(),
        "status": str(status or "info").strip(),
        "detail": str(detail or "").strip(),
        "optional": bool(optional),
        "fixed": bool(fixed),
    }


def _probe_command_version(cmd, label):
    try:
        result = _run_command(cmd, timeout=15)
    except Exception as e:
        return _make_report_item(label, "error", str(e))
    if result.returncode == 0:
        detail = (result.stdout or result.stderr or "").strip()
        return _make_report_item(label, "ok", detail or "可用")
    return _make_report_item(label, "error", _short_subprocess_detail(result, "命令执行失败"))


def _probe_python_module(py, module_name):
    code = (
        "import importlib, json, sys\n"
        "name = sys.argv[1]\n"
        "mod = importlib.import_module(name)\n"
        "version = getattr(mod, '__version__', '') or getattr(getattr(mod, 'version', None), '__version__', '')\n"
        "path = getattr(mod, '__file__', '')\n"
        "print(json.dumps({'version': str(version or ''), 'path': str(path or '')}, ensure_ascii=False))\n"
    )
    try:
        result = _run_python_command(py, ["-c", code, module_name], timeout=25)
    except Exception as e:
        return False, str(e), {}
    if result.returncode != 0:
        return False, _short_subprocess_detail(result, "导入失败"), {}
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except Exception:
        payload = {}
    detail_bits = []
    if payload.get("version"):
        detail_bits.append(f"版本 {payload['version']}")
    if payload.get("path"):
        detail_bits.append(str(payload["path"]))
    return True, " | ".join(detail_bits) if detail_bits else "可导入", payload


def _probe_python_import(py, module_name, *, cwd=None, search_paths=None):
    path_args = [str(item or "").strip() for item in (search_paths or []) if str(item or "").strip()]
    code = (
        "import importlib, json, os, sys\n"
        "module_name = sys.argv[1]\n"
        "paths = json.loads(sys.argv[2])\n"
        "for item in reversed(paths):\n"
        "    if item and item not in sys.path:\n"
        "        sys.path.insert(0, item)\n"
        "mod = importlib.import_module(module_name)\n"
        "print(getattr(mod, '__file__', '') or 'OK')\n"
    )
    try:
        result = _run_python_command(py, ["-c", code, module_name, json.dumps(path_args, ensure_ascii=False)], timeout=30)
    except Exception as e:
        return False, str(e)
    if result.returncode == 0:
        return True, ((result.stdout or "").strip() or "可导入")
    return False, _short_subprocess_detail(result, "导入失败")


def _probe_python_compile(py, file_path, *, cwd=None):
    code = (
        "import py_compile, sys\n"
        "py_compile.compile(sys.argv[1], doraise=True)\n"
        "print('OK')\n"
    )
    try:
        result = _run_python_command(py, ["-c", code, file_path], timeout=30)
    except Exception as e:
        return False, str(e)
    if result.returncode == 0:
        return True, "语法检查通过"
    return False, _short_subprocess_detail(result, "语法检查失败")


def _bootstrap_python_runtime(py, *, progress=None):
    mode = _dependency_installer_mode()
    if mode != "uv":
        _emit_dependency_progress(progress, "ensurepip", f"检查 pip：{py}")
        try:
            _run_python_command(py, ["-m", "ensurepip", "--upgrade"], timeout=180)
        except Exception:
            pass
    missing = _missing_dependency_specs(py, list(_AUTO_BOOTSTRAP_PACKAGES), strict_version=False)
    if not missing:
        _emit_dependency_progress(progress, "bootstrap", "基础依赖已可用，跳过安装。", status="ok")
        return True, ""
    _emit_dependency_progress(progress, "bootstrap", "检测到缺失基础依赖，正在补装…")
    ok, detail, _installer = _run_dependency_install(
        py,
        missing,
        timeout=600,
        progress=progress,
        stage="bootstrap_install",
        label="安装缺失基础依赖",
    )
    if ok:
        return True, ""
    # 失败后尝试用户级强制修复：不卸载旧包，直接在 user site 覆盖安装。
    _emit_dependency_progress(progress, "bootstrap_repair", "常规安装失败，尝试用户级修复安装…", status="warn")
    repaired, repair_detail = _repair_python_packages_user_site(
        py,
        missing,
        progress=progress,
        label="修复基础依赖",
    )
    if repaired:
        remain = _missing_dependency_specs(py, list(_AUTO_BOOTSTRAP_PACKAGES), strict_version=False)
        if not remain:
            _emit_dependency_progress(progress, "bootstrap_repair_ok", "用户级修复安装完成。", status="ok")
            return True, ""
    merged_detail = detail or ""
    if repair_detail:
        merged_detail = (merged_detail + "；" + repair_detail).strip("；")
    return False, merged_detail or "基础依赖安装失败"


def _install_python_packages(py, packages, *, progress=None, label="安装依赖"):
    items = [str(item or "").strip() for item in (packages or []) if str(item or "").strip()]
    if not items:
        return True, ""
    ok, detail, _installer = _run_dependency_install(
        py,
        items,
        timeout=1800,
        progress=progress,
        stage="dep_install",
        label=f"{label}：{' '.join(items)}",
    )
    if ok:
        return True, ""
    return False, detail or f"{label}失败"


def _repair_python_packages_user_site(py, packages, *, progress=None, label="修复依赖"):
    items = [str(item or "").strip() for item in (packages or []) if str(item or "").strip()]
    if not items:
        return True, ""
    errors = []
    for spec in items:
        _emit_dependency_progress(progress, "repair_install", f"{label}：{spec}（pip --user --ignore-installed）")
        try:
            result = _run_command(
                [str(py or "").strip(), "-m", "pip", "install", "--user", "--ignore-installed", spec],
                timeout=1200,
            )
        except Exception as e:
            errors.append(f"{spec}: {e}")
            continue
        if result.returncode != 0:
            errors.append(f"{spec}: {_short_subprocess_detail(result, '修复安装失败')}")
    if errors:
        return False, "；".join(errors)
    return True, ""


def _install_python_requirements(py, requirements_path, *, progress=None):
    req_path = str(requirements_path or "").strip()
    if not req_path:
        return True, ""
    ok, detail, _installer = _run_dependency_install(
        py,
        ["-r", req_path],
        timeout=1800,
        progress=progress,
        stage="requirements_install",
        label=f"正在同步 GenericAgent requirements.txt：{req_path}",
    )
    if ok:
        return True, ""
    return False, detail or "requirements 安装失败"


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
        r = _run_external_subprocess(
            [py, "-c", code, agent_dir],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=agent_dir,
            encoding="utf-8",
            errors="replace",
            env=_external_subprocess_env(),
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


def _prepare_python_runtime_candidate(info, agent_dir, *, extra_packages=None, progress=None, force_sync=False):
    py = info["path"]
    label = _format_python_candidate_label(info)
    extra_packages = [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()]
    req_path = _agent_requirements_path(agent_dir)
    state_matches = _dependency_state_matches(agent_dir, py, extra_packages=extra_packages)
    core_ready = _core_runtime_packages_ready(py)
    core_import_ready = _core_runtime_packages_import_ready(py)

    _emit_dependency_progress(progress, "candidate", f"检查解释器：{label}")
    ok, detail = _probe_python_agent_compat(py, agent_dir)
    meta = {"python": py, "label": label, "bootstrapped": False, "requirements_synced": False, "extra_synced": False}

    need_bootstrap = (not ok) and _should_bootstrap_python_runtime(detail)
    need_sync = _should_sync_runtime_dependencies(
        state_matches=state_matches,
        extra_packages=extra_packages,
        requirements_path=req_path,
        force_sync=force_sync,
    )
    missing_extra = _missing_dependency_specs(py, extra_packages, strict_version=False)
    if (not missing_extra) and ok and core_import_ready and (not force_sync):
        need_sync = False
    if (not core_ready) and (not core_import_ready):
        need_sync = True
    if missing_extra:
        need_sync = True
    if (not ok) and req_path:
        need_sync = True

    if need_bootstrap:
        boot_ok, boot_detail = _bootstrap_python_runtime(py, progress=progress)
        if not boot_ok:
            return False, f"{detail}\n自动升级 requests/simplejson 失败：{boot_detail}", meta
        meta["bootstrapped"] = True
        ok, detail = _probe_python_agent_compat(py, agent_dir)

    if need_sync:
        boot_ok, boot_detail = _bootstrap_python_runtime(py, progress=progress)
        if not boot_ok:
            return False, f"基础依赖准备失败：{boot_detail}", meta
        meta["bootstrapped"] = True
        if req_path:
            req_ok, req_detail = _install_python_requirements(py, req_path, progress=progress)
            if not req_ok:
                return False, req_detail, meta
            meta["requirements_synced"] = True
        if missing_extra:
            extra_ok, extra_detail = _install_python_packages(py, missing_extra, progress=progress, label="安装渠道依赖")
            if not extra_ok:
                return False, extra_detail, meta
            meta["extra_synced"] = True
        ok, detail = _probe_python_agent_compat(py, agent_dir)

    if ok:
        _mark_dependency_state(agent_dir, py, extra_packages=extra_packages)
        _emit_dependency_progress(progress, "candidate_ok", f"解释器可用：{label}", status="ok")
        return True, "", meta

    _emit_dependency_progress(progress, "candidate_fail", f"解释器不可用：{label} -> {detail}", status="error")
    return False, detail, meta


def _configured_channel_ids(parsed_mykey):
    extras = dict((parsed_mykey or {}).get("extras") or {})
    configured = set()
    field_map = {}
    try:
        from .channels import COMM_CHANNEL_SPECS
    except Exception:
        return configured
    for spec in COMM_CHANNEL_SPECS:
        keys = [str(field.get("key") or "").strip() for field in spec.get("fields", []) if str(field.get("key") or "").strip()]
        field_map[spec.get("id")] = keys
    for channel_id, keys in field_map.items():
        for key in keys:
            value = extras.get(key)
            if isinstance(value, (list, tuple, set)):
                if value:
                    configured.add(channel_id)
                    break
            elif str(value or "").strip():
                configured.add(channel_id)
                break
    return configured


def _frontend_dependency_group_sections(py):
    if not py:
        return []
    sections = []
    for group in UPSTREAM_FRONTEND_DEPENDENCY_GROUPS:
        group_label = str(group.get("label") or group.get("id") or "").strip()
        group_desc = str(group.get("description") or "").strip()
        items = []
        for dep in group.get("items", []) or []:
            package = str(dep.get("package") or "").strip()
            import_name = str(dep.get("import") or "").strip() or _package_import_name(package)
            optional = bool(dep.get("optional", True))
            note = str(dep.get("note") or "").strip()
            ok, detail, _payload = _probe_python_dependency(py, package, import_name=import_name)
            if note:
                detail = f"{detail}；{note}" if detail else note
            items.append(
                _make_report_item(
                    package,
                    "ok" if ok else "info",
                    detail,
                    optional=optional,
                    fixed=_package_base_name(package).lower() in {_package_base_name(x).lower() for x in _AUTO_BOOTSTRAP_PACKAGES},
                )
            )
        if group_desc:
            items.insert(0, _make_report_item("说明", "info", group_desc, optional=True))
        sections.append({"title": group_label, "items": items})
    return sections


def _api_config_item_report(config):
    kind = str((config or {}).get("kind") or "unknown").strip()
    data = dict((config or {}).get("data") or {})
    name = str((config or {}).get("var") or "(未命名配置)").strip()
    if kind == "mixin":
        llm_nos = data.get("llm_nos")
        if isinstance(llm_nos, (list, tuple)) and llm_nos:
            return _make_report_item(f"API 配置 {name}", "ok", f"Mixin 配置，llm_nos={list(llm_nos)}")
        return _make_report_item(f"API 配置 {name}", "error", "Mixin 配置缺少有效的 llm_nos")
    missing = [key for key in ("apikey", "apibase", "model") if not str(data.get(key) or "").strip()]
    if missing:
        return _make_report_item(f"API 配置 {name}", "error", "缺少字段：" + "、".join(missing))
    return _make_report_item(f"API 配置 {name}", "ok", f"{kind} / model={data.get('model')}")


def _build_dependency_report(agent_dir, py, *, candidate_meta=None, failures=None, extra_packages=None, error=""):
    try:
        from .channels import COMM_CHANNEL_SPECS, parse_mykey_py
    except Exception:
        COMM_CHANNEL_SPECS = []
        parse_mykey_py = None

    sections = []
    now = time.time()
    candidate_meta = dict(candidate_meta or {})
    failures = list(failures or [])
    extra_packages = [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()]

    system_items = []
    git_item = _probe_command_version(["git", "--version"], "Git")
    if git_item.get("status") == "error":
        git_item["status"] = "warn"
        git_item["optional"] = True
        git_item["detail"] = (git_item.get("detail") or "") + "（仅下载/更新仓库时需要）"
    system_items.append(git_item)
    installer_mode = _dependency_installer_mode()
    uv_cmd = _detect_uv_command() if installer_mode in ("auto", "uv") else []
    system_items.append(_make_report_item("依赖安装器策略", "info", f"{installer_mode}（auto=优先 uv，失败回退 pip）", optional=True))
    if uv_cmd:
        uv_item = _probe_command_version([*uv_cmd, "--version"], "uv")
        uv_item["optional"] = True
        system_items.append(uv_item)
    elif installer_mode == "uv":
        system_items.append(
            _make_report_item(
                "uv",
                "error",
                "策略为 uv，但未找到 uv 命令。可安装 uv 或设置环境变量 GA_LAUNCHER_UV_EXE。",
            )
        )
    else:
        system_items.append(_make_report_item("uv", "info", "未检测到 uv，将使用 pip 安装依赖。", optional=True))
    if py:
        try:
            py_ver = _run_python_command(py, ["-c", "import sys;print(sys.version.split()[0])"], timeout=15)
            py_label = (py_ver.stdout or "").strip()
            system_items.append(_make_report_item("Python", "ok", f"{py} (Python {py_label})" if py_label else py))
        except Exception as e:
            system_items.append(_make_report_item("Python", "error", str(e)))
        try:
            pip_ver = _run_python_command(py, ["-m", "pip", "--version"], timeout=15)
            if pip_ver.returncode == 0:
                system_items.append(_make_report_item("pip", "ok", (pip_ver.stdout or "").strip()))
            else:
                system_items.append(_make_report_item("pip", "error", _short_subprocess_detail(pip_ver, "pip 不可用")))
        except Exception as e:
            system_items.append(_make_report_item("pip", "error", str(e)))
    sections.append({"title": "系统环境", "items": system_items})

    project_items = []
    expected_files = [
        ("launch.pyw", os.path.join(agent_dir, "launch.pyw")),
        ("agentmain.py", os.path.join(agent_dir, "agentmain.py")),
        ("llmcore.py", os.path.join(agent_dir, "llmcore.py")),
        ("ga.py", os.path.join(agent_dir, "ga.py")),
        ("bridge.py", _bridge_script_path()),
    ]
    for label, path in expected_files:
        project_items.append(_make_report_item(label, "ok" if os.path.isfile(path) else "error", path))
    req_path = _agent_requirements_path(agent_dir)
    if req_path:
        project_items.append(_make_report_item("requirements.txt", "ok", req_path))
    else:
        project_items.append(
            _make_report_item(
                "requirements.txt",
                "info",
                "上游未提供 requirements.txt；当前改用启动器维护的上游依赖表",
                optional=True,
            )
        )
    mykey_py = os.path.join(agent_dir, "mykey.py")
    mykey_json = os.path.join(agent_dir, "mykey.json")
    if os.path.isfile(mykey_py) or os.path.isfile(mykey_json):
        project_items.append(_make_report_item("mykey 配置", "ok", mykey_py if os.path.isfile(mykey_py) else mykey_json))
    else:
        project_items.append(_make_report_item("mykey 配置", "warn", "尚未创建 mykey.py / mykey.json", optional=True))
    sections.append({"title": "项目文件", "items": project_items})

    source_items = []
    for source in UPSTREAM_DEPENDENCY_SOURCES:
        source_items.append(
            _make_report_item(
                str(source.get("source") or "").strip(),
                "ok",
                str(source.get("evidence") or "").strip(),
                optional=True,
            )
        )
    sections.append({"title": "上游依赖来源", "items": source_items})

    core_items = []
    if py:
        for dep in LAUNCHER_BOOTSTRAP_DEPENDENCIES:
            package = str(dep.get("package") or "").strip()
            import_name = str(dep.get("import") or "").strip() or _package_import_name(package)
            ok, detail, _payload = _probe_python_dependency(py, package, import_name=import_name)
            core_items.append(
                _make_report_item(
                    package,
                    "ok" if ok else "error",
                    detail,
                    fixed=bool(candidate_meta.get("bootstrapped")),
                )
            )
        import_targets = [
            ("agentmain", "agentmain", [agent_dir]),
            ("llmcore", "llmcore", [agent_dir]),
            ("ga", "ga", [agent_dir]),
            ("bridge", "bridge", [os.path.dirname(_bridge_script_path())]),
        ]
        for label, module_name, search_paths in import_targets:
            ok, detail = _probe_python_import(py, module_name, search_paths=search_paths)
            core_items.append(_make_report_item(f"导入 {label}", "ok" if ok else "error", detail))
    sections.append({"title": "主聊天必需", "items": core_items})

    frontend_items = []
    if py:
        frontends_dir = os.path.join(agent_dir, "frontends")
        for filename in ("qtapp.py", "stapp.py", "stapp2.py", "desktop_pet.pyw", "desktop_pet_v2.pyw"):
            path = os.path.join(frontends_dir, filename)
            if not os.path.isfile(path):
                frontend_items.append(_make_report_item(filename, "warn", "文件不存在", optional=True))
                continue
            ok, detail = _probe_python_compile(py, path)
            frontend_items.append(_make_report_item(filename, "ok" if ok else "error", detail, optional=filename.startswith("desktop_pet")))
    sections.append({"title": "前端脚本", "items": frontend_items})
    sections.extend(_frontend_dependency_group_sections(py))

    parsed = {"configs": [], "extras": {}, "passthrough": [], "error": "未解析"}
    if parse_mykey_py and os.path.isfile(mykey_py):
        parsed = parse_mykey_py(mykey_py)
    api_items = []
    if parsed.get("error"):
        api_items.append(_make_report_item("解析 mykey.py", "error", str(parsed.get("error") or "").strip()))
    elif os.path.isfile(mykey_py):
        api_items.append(_make_report_item("解析 mykey.py", "ok", f"检测到 {len(parsed.get('configs') or [])} 个 API 配置"))
    else:
        api_items.append(_make_report_item("解析 mykey.py", "warn", "当前没有 mykey.py", optional=True))
    for config in parsed.get("configs") or []:
        api_items.append(_api_config_item_report(config))
    sections.append({"title": "API 配置", "items": api_items})

    configured_channel_items = []
    optional_channel_items = []
    configured_channels = _configured_channel_ids(parsed)
    if py:
        for spec in COMM_CHANNEL_SPECS:
            label = str(spec.get("label") or spec.get("id") or "").strip()
            script_path = os.path.join(agent_dir, "frontends", spec.get("script", ""))
            configured = spec.get("id") in configured_channels
            target_items = configured_channel_items if configured else optional_channel_items
            target_items.append(
                _make_report_item(
                    f"{label} 脚本",
                    "ok" if os.path.isfile(script_path) else "error",
                    script_path if os.path.isfile(script_path) else "脚本不存在",
                    optional=not configured,
                )
            )
            for pkg in _split_requirement_tokens(spec.get("pip", "")):
                import_name = _package_import_name(pkg)
                ok, detail, _payload = _probe_python_dependency(py, pkg, import_name=import_name)
                status = "ok" if ok else ("error" if configured else "info")
                prefix = f"{label} 依赖 {pkg}"
                target_items.append(_make_report_item(prefix, status, detail, optional=not configured, fixed=pkg in extra_packages))
    sections.append({"title": "已配置渠道", "items": configured_channel_items})
    sections.append({"title": "渠道专属可选", "items": optional_channel_items})

    candidate_items = []
    if candidate_meta:
        actions = []
        if candidate_meta.get("bootstrapped"):
            actions.append("升级 requests/simplejson")
        if candidate_meta.get("requirements_synced"):
            actions.append("同步 requirements.txt")
        if candidate_meta.get("extra_synced"):
            actions.append("同步额外渠道依赖")
        candidate_items.append(_make_report_item("当前解释器处理", "ok" if py else "error", "；".join(actions) if actions else "未触发自动修复", fixed=bool(actions)))
    for failure in failures:
        info = failure.get("info") or {}
        candidate_items.append(_make_report_item(_format_python_candidate_label(info), "warn", failure.get("detail", ""), optional=True))
    sections.append({"title": "解释器候选", "items": candidate_items})

    if error:
        sections.append({"title": "最终错误", "items": [_make_report_item("依赖检查结果", "error", error)]})

    counts = {"checked": 0, "ok": 0, "warn": 0, "error": 0, "fixed": 0}
    for section in sections:
        for item in section.get("items") or []:
            counts["checked"] += 1
            status = str(item.get("status") or "").strip().lower()
            if status in counts:
                counts[status] += 1
            if item.get("fixed"):
                counts["fixed"] += 1

    lines = [
        f"依赖检查时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
        f"检查项：{counts['checked']}  通过：{counts['ok']}  警告：{counts['warn']}  失败：{counts['error']}  自动修复：{counts['fixed']}",
    ]
    for section in sections:
        items = section.get("items") or []
        if not items:
            continue
        lines.append("")
        lines.append(f"[{section.get('title')}]")
        for item in items:
            mark = {"ok": "OK", "warn": "WARN", "error": "ERR", "info": "INFO"}.get(str(item.get("status") or "").strip().lower(), "INFO")
            fixed = " [fixed]" if item.get("fixed") else ""
            detail = f": {item.get('detail')}" if item.get("detail") else ""
            lines.append(f"- [{mark}]{fixed} {item.get('name')}{detail}")

    return {
        "generated_at": now,
        "summary": counts,
        "sections": sections,
        "text": "\n".join(lines).strip(),
    }


def _ensure_runtime_dependencies(agent_dir, *, extra_packages=None, progress=None, force_sync=False):
    candidates = _system_python_candidates()
    if not candidates:
        return {
            "ok": False,
            "python": "",
            "error": "未找到系统 Python。请先安装 Python 并加入 PATH，或在 launcher_config.json 中设置 python_exe。",
            "failures": [],
        }
    failures = []
    extra_packages = [str(item or "").strip() for item in (extra_packages or []) if str(item or "").strip()]
    if extra_packages:
        _emit_dependency_progress(progress, "extras", "需要额外检查的渠道依赖：" + "、".join(extra_packages))
    chosen_meta = {}
    for info in candidates:
        ok, detail, meta = _prepare_python_runtime_candidate(
            info,
            agent_dir,
            extra_packages=extra_packages,
            progress=progress,
            force_sync=force_sync,
        )
        if ok:
            chosen_meta = meta
            report = _build_dependency_report(
                agent_dir,
                info["path"],
                candidate_meta=chosen_meta,
                failures=failures,
                extra_packages=extra_packages,
                error="",
            )
            return {"ok": True, "python": info["path"], "error": "", "failures": failures, "report": report, "meta": chosen_meta}
        failures.append({"info": info, "detail": detail})
    lines = ["已找到系统 Python，但都无法载入 GenericAgent 内核。"]
    for item in failures[:3]:
        lines.append(f"- {_format_python_candidate_label(item.get('info'))}: {item.get('detail')}")
    lines.append("可在 launcher_config.json 中手动指定 python_exe。")
    lines.append("当前不会强制限制版本，但如果高版本解释器兼容性不稳，通常改用 Python 3.11 / 3.12 更稳。")
    error_text = "\n".join(lines)
    report = _build_dependency_report(agent_dir, "", candidate_meta=chosen_meta, failures=failures, extra_packages=extra_packages, error=error_text)
    return {"ok": False, "python": "", "error": error_text, "failures": failures, "report": report, "meta": chosen_meta}


def _find_compatible_system_python(agent_dir):
    result = _ensure_runtime_dependencies(agent_dir)
    return (result.get("python") or None), (result.get("error") or None)
