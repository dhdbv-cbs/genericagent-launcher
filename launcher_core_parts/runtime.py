from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from contextlib import contextmanager

from .constants import (
    APP_DIR,
    BOOTSTRAP_EXE_NAME,
    CONFIG_PATH,
    CURRENT_STATE_PATH,
    DATA_ROOT,
    LEGACY_CONFIG_PATH,
    MAIN_EXE_NAME,
    PROGRAMS_ROOT,
    STATE_DIR,
    UPDATE_DOWNLOADS_DIR,
    UPDATE_JOBS_DIR,
    UPDATE_LOG_PATH,
    UPDATE_STAGING_DIR,
    UPDATER_EXE_NAME,
    UPDATES_DIR,
    VERSIONS_DIR,
)


def _bridge_script_path():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # runtime.py lives under launcher_core_parts/, while bridge.py stays at
        # the project root next to launcher.py.
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "bridge.py")


def _python_creationflags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _ensure_windows_no_window_creationflags(kwargs):
    if os.name != "nt":
        return
    if "creationflags" in kwargs and kwargs.get("creationflags") is not None:
        return
    kwargs["creationflags"] = _python_creationflags()


def _python_utf8_subprocess_env(base_env=None):
    env = dict(base_env or os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONLEGACYWINDOWSSTDIO", None)
    return env


def _pyinstaller_runtime_root():
    if not getattr(sys, "frozen", False):
        return ""
    return os.path.normcase(os.path.normpath(str(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)) or "")))


def _path_is_under(child, parent):
    left = os.path.normcase(os.path.normpath(str(child or "").strip()))
    right = os.path.normcase(os.path.normpath(str(parent or "").strip()))
    if not left or not right:
        return False
    try:
        return os.path.commonpath([left, right]) == right
    except Exception:
        return left == right


def _external_subprocess_env(base_env=None):
    env = _python_utf8_subprocess_env(base_env)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
    ):
        env.pop(key, None)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONUSERBASE",
        "PYTHONNOUSERSITE",
    ):
        env.pop(key, None)
    runtime_root = _pyinstaller_runtime_root()
    if runtime_root and os.name == "nt":
        raw_path = str(env.get("PATH") or "")
        kept = []
        for item in raw_path.split(os.pathsep):
            text = str(item or "").strip()
            if not text:
                continue
            if _path_is_under(text, runtime_root):
                continue
            kept.append(text)
        env["PATH"] = os.pathsep.join(kept)
        env.pop("_MEIPASS2", None)
    return env


@contextmanager
def _external_subprocess_runtime():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    runtime_root = _pyinstaller_runtime_root()
    kernel32 = None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetDllDirectoryW(None)
    except Exception:
        kernel32 = None
    try:
        yield
    finally:
        if kernel32 is not None and runtime_root:
            try:
                kernel32.SetDllDirectoryW(runtime_root)
            except Exception:
                pass


def _run_external_subprocess(args, **kwargs):
    kwargs["env"] = _external_subprocess_env(kwargs.get("env"))
    _ensure_windows_no_window_creationflags(kwargs)
    with _external_subprocess_runtime():
        return subprocess.run(args, **kwargs)


def _popen_external_subprocess(args, **kwargs):
    kwargs["env"] = _external_subprocess_env(kwargs.get("env"))
    _ensure_windows_no_window_creationflags(kwargs)
    with _external_subprocess_runtime():
        return subprocess.Popen(args, **kwargs)


def _close_process_stream(stream):
    if stream is None:
        return
    try:
        stream.close()
    except Exception:
        pass


def _normalize_process_pid(proc_or_pid):
    if proc_or_pid is None:
        return 0
    if hasattr(proc_or_pid, "pid"):
        try:
            return int(getattr(proc_or_pid, "pid", 0) or 0)
        except Exception:
            return 0
    try:
        return int(proc_or_pid or 0)
    except Exception:
        return 0


def terminate_process_tree(proc_or_pid, *, quit_line="", terminate_timeout=1.2, kill_timeout=1.2):
    proc = proc_or_pid if hasattr(proc_or_pid, "poll") and hasattr(proc_or_pid, "wait") else None
    pid = _normalize_process_pid(proc_or_pid)
    if pid <= 0:
        return True
    text_quit = str(quit_line or "")
    if proc is not None:
        try:
            if text_quit and proc.poll() is None and getattr(proc, "stdin", None) is not None:
                proc.stdin.write(text_quit)
                proc.stdin.flush()
        except Exception:
            pass
        _close_process_stream(getattr(proc, "stdin", None))
        try:
            if proc.poll() is not None:
                _close_process_stream(getattr(proc, "stdout", None))
                _close_process_stream(getattr(proc, "stderr", None))
                return True
        except Exception:
            pass
    if os.name == "nt":
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            if proc is not None:
                proc.wait(timeout=max(0.1, float(terminate_timeout or 1.2)))
                _close_process_stream(getattr(proc, "stdout", None))
                _close_process_stream(getattr(proc, "stderr", None))
                return True
        except Exception:
            pass
        try:
            result = _run_external_subprocess(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(4, int((float(terminate_timeout or 1.2) + float(kill_timeout or 1.2)) * 2)),
            )
            ok = int(getattr(result, "returncode", 1) or 1) == 0
        except Exception:
            ok = False
        if proc is not None:
            try:
                proc.wait(timeout=max(0.2, float(kill_timeout or 1.2)))
            except Exception:
                pass
            _close_process_stream(getattr(proc, "stdout", None))
            _close_process_stream(getattr(proc, "stderr", None))
            try:
                return proc.poll() is not None
            except Exception:
                return ok
        return ok
    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
    except Exception:
        pass
    try:
        if proc is not None:
            proc.wait(timeout=max(0.1, float(terminate_timeout or 1.2)))
            _close_process_stream(getattr(proc, "stdout", None))
            _close_process_stream(getattr(proc, "stderr", None))
            return True
    except Exception:
        pass
    try:
        if proc is not None and proc.poll() is None:
            proc.kill()
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    if proc is not None:
        try:
            proc.wait(timeout=max(0.2, float(kill_timeout or 1.2)))
        except Exception:
            pass
        _close_process_stream(getattr(proc, "stdout", None))
        _close_process_stream(getattr(proc, "stderr", None))
        try:
            return proc.poll() is not None
        except Exception:
            return False
    return False


def _launcher_data_dirs():
    return (
        DATA_ROOT,
        os.path.dirname(CONFIG_PATH),
        STATE_DIR,
        UPDATES_DIR,
        UPDATE_JOBS_DIR,
        UPDATE_DOWNLOADS_DIR,
        UPDATE_STAGING_DIR,
    )


def _ensure_launcher_data_dirs():
    for d in _launcher_data_dirs():
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def _read_json_file(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if default is None:
            return payload
        if isinstance(payload, type(default)):
            return payload
    except Exception:
        pass
    return default


def _atomic_write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _legacy_config_candidates():
    out = []
    for path in (
        LEGACY_CONFIG_PATH,
        os.path.join(APP_DIR, "launcher_config.json"),
        os.path.join(resolved_programs_root(), "launcher_config.json"),
    ):
        text = str(path or "").strip()
        if not text:
            continue
        norm = os.path.normcase(os.path.normpath(text))
        if norm in out:
            continue
        out.append(norm)
    return out


def _migrate_legacy_config_if_needed():
    if os.path.isfile(CONFIG_PATH):
        return _read_json_file(CONFIG_PATH, {})
    for candidate in _legacy_config_candidates():
        if os.path.normcase(os.path.normpath(CONFIG_PATH)) == candidate:
            continue
        src = candidate
        if not os.path.isfile(src):
            continue
        data = _read_json_file(src, None)
        if not isinstance(data, dict):
            continue
        _atomic_write_json(CONFIG_PATH, data)
        report = {
            "migrated_at": float(time.time()),
            "from": src,
            "to": CONFIG_PATH,
            "keys": sorted(list(data.keys())),
        }
        try:
            migration_report = os.path.join(DATA_ROOT, "migration", "config_migration.json")
            _atomic_write_json(migration_report, report)
        except Exception:
            pass
        return data
    return {}


def load_config():
    _ensure_launcher_data_dirs()
    if os.path.isfile(CONFIG_PATH):
        payload = _read_json_file(CONFIG_PATH, {})
        if isinstance(payload, dict):
            return payload
    migrated = _migrate_legacy_config_if_needed()
    if isinstance(migrated, dict):
        return migrated
    return {}


def save_config(cfg):
    _ensure_launcher_data_dirs()
    try:
        _atomic_write_json(CONFIG_PATH, cfg if isinstance(cfg, dict) else {})
    except Exception as e:
        print(f"[Config] save failed: {e}")


def _config_relative_roots():
    roots = []
    seen = set()
    for d in (DATA_ROOT, APP_DIR):
        text = str(d or "").strip()
        if not text:
            continue
        norm = os.path.normcase(os.path.normpath(text))
        if norm in seen:
            continue
        seen.add(norm)
        roots.append(os.path.normpath(text))
    return roots


def _resolve_config_path(path):
    raw = str(path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    candidates = [os.path.normpath(os.path.join(root, raw)) for root in _config_relative_roots()]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0] if candidates else os.path.normpath(raw)


def _make_config_relative_path(path):
    raw = str(path or "").strip()
    if not raw:
        return ""
    abs_path = os.path.abspath(raw)
    for root in _config_relative_roots():
        try:
            rel = os.path.relpath(abs_path, root)
        except Exception:
            continue
        if not rel.startswith(".."):
            return rel
    return abs_path


def launcher_data_path(*parts):
    _ensure_launcher_data_dirs()
    return os.path.normpath(os.path.join(DATA_ROOT, *[str(p or "").strip() for p in parts if str(p or "").strip()]))


def launcher_program_path(*parts):
    return os.path.normpath(
        os.path.join(resolved_programs_root(), *[str(p or "").strip() for p in parts if str(p or "").strip()])
    )


def _candidate_program_root_from_dir(path):
    raw = os.path.normpath(str(path or "").strip())
    if not raw:
        return ""
    if os.path.isdir(os.path.join(raw, "app", "versions")):
        return raw
    p1 = os.path.dirname(raw)
    p2 = os.path.dirname(p1)
    if os.path.basename(p1).lower() == "versions" and os.path.basename(p2).lower() == "app":
        return os.path.dirname(p2)
    return ""


def resolved_programs_root():
    candidates = []

    def _push(path):
        text = os.path.normpath(str(path or "").strip())
        if text and text not in candidates:
            candidates.append(text)

    _push(os.environ.get("GA_LAUNCHER_PROGRAMS_ROOT"))
    _push(APP_DIR)
    if getattr(sys, "frozen", False):
        _push(os.path.dirname(os.path.abspath(sys.executable)))
    _push(PROGRAMS_ROOT)

    derived = []
    for item in candidates:
        root = _candidate_program_root_from_dir(item)
        if root and root not in derived:
            derived.append(root)

    for root in derived:
        if os.path.isdir(os.path.join(root, "app", "versions")):
            return root
    if derived:
        return derived[0]
    return os.path.normpath(PROGRAMS_ROOT)


def resolved_versions_dir():
    return os.path.join(resolved_programs_root(), "app", "versions")


def _state_default():
    return {
        "current_version": "",
        "previous_version": "",
        "pending_update": {},
        "updated_at": 0.0,
    }


def load_version_state():
    _ensure_launcher_data_dirs()
    state = _read_json_file(CURRENT_STATE_PATH, _state_default())
    if not isinstance(state, dict):
        state = _state_default()
    for key, val in _state_default().items():
        state.setdefault(key, val)
    return state


def save_version_state(state):
    payload = _state_default()
    if isinstance(state, dict):
        payload.update(state)
    payload["updated_at"] = float(time.time())
    _atomic_write_json(CURRENT_STATE_PATH, payload)
    return payload


def launcher_version_info():
    defaults = {"version": "0.0.0-dev", "channel": "stable", "commit": "", "build_time": ""}
    candidates = [
        os.path.join(APP_DIR, "version.json"),
        os.path.join(os.path.dirname(APP_DIR), "version.json"),
    ]
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "version.json"))
    for fp in candidates:
        if not os.path.isfile(fp):
            continue
        payload = _read_json_file(fp, None)
        if not isinstance(payload, dict):
            continue
        out = dict(defaults)
        out.update({k: str(payload.get(k) or "").strip() for k in defaults.keys()})
        if out["version"]:
            return out
    return defaults


def current_launcher_version():
    resolved = str(launcher_version_info().get("version") or "0.0.0-dev").strip() or "0.0.0-dev"
    if resolved and resolved != "0.0.0-dev":
        return resolved
    # 开发态或缺失 version.json 时，回退到版本状态文件，避免更新检查一直显示 dev。
    try:
        state = load_version_state()
        state_ver = str((state or {}).get("current_version") or "").strip()
        if state_ver:
            return state_ver
    except Exception:
        pass
    return resolved


def _version_dir(version):
    v = str(version or "").strip()
    if not v:
        return ""
    return os.path.join(resolved_versions_dir(), v)


def _main_exe_for_version(version):
    d = _version_dir(version)
    if not d:
        return ""
    return os.path.join(d, MAIN_EXE_NAME)


def current_version_main_exe():
    state = load_version_state()
    version = str(state.get("current_version") or "").strip()
    if version:
        target = _main_exe_for_version(version)
        if os.path.isfile(target):
            return target
    direct = os.path.join(APP_DIR, MAIN_EXE_NAME)
    if os.path.isfile(direct):
        return direct
    return ""


def set_current_version(version, *, previous_version="", pending_update=None):
    state = load_version_state()
    state["current_version"] = str(version or "").strip()
    state["previous_version"] = str(previous_version or "").strip()
    state["pending_update"] = dict(pending_update or {})
    return save_version_state(state)


def acknowledge_pending_update_startup():
    state = load_version_state()
    pending = state.get("pending_update")
    if not isinstance(pending, dict) or not pending:
        return False
    ack_path = str(pending.get("startup_ack_path") or pending.get("ack_path") or "").strip()
    if not ack_path:
        return False
    try:
        os.makedirs(os.path.dirname(ack_path), exist_ok=True)
        with open(ack_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ack_at": float(time.time()),
                    "version": str(state.get("current_version") or ""),
                    "pid": int(os.getpid()),
                    "phase": "startup",
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        return False
    pending["startup_acked_at"] = float(time.time())
    state["pending_update"] = pending
    save_version_state(state)
    return True


def acknowledge_pending_update_alive():
    state = load_version_state()
    pending = state.get("pending_update")
    if not isinstance(pending, dict) or not pending:
        return False
    ack_path = str(pending.get("alive_ack_path") or pending.get("ack_path") or "").strip()
    if not ack_path:
        return False
    now = float(time.time())
    started_at = float(pending.get("started_at") or 0.0)
    try:
        os.makedirs(os.path.dirname(ack_path), exist_ok=True)
        with open(ack_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "ack_at": now,
                    "version": str(state.get("current_version") or ""),
                    "pid": int(os.getpid()),
                    "phase": "alive",
                    "uptime_seconds": max(0.0, now - started_at),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        return False
    state["pending_update"] = {}
    save_version_state(state)
    return True


def start_pending_update_alive_probe(delay_seconds=6):
    state = load_version_state()
    pending = state.get("pending_update")
    if not isinstance(pending, dict) or not pending:
        return False
    delay = max(2, int(delay_seconds or pending.get("min_alive_seconds") or 6))

    def _worker():
        try:
            time.sleep(delay)
            acknowledge_pending_update_alive()
        except Exception:
            return

    threading.Thread(target=_worker, name="launcher-update-alive-ack", daemon=True).start()
    return True


def cleanup_old_versions(*, keep_versions=None, keep_count=2):
    keep = {str(item or "").strip() for item in (keep_versions or []) if str(item or "").strip()}
    state = load_version_state()
    keep.add(str(state.get("current_version") or "").strip())
    keep.add(str(state.get("previous_version") or "").strip())
    keep = {k for k in keep if k}
    versions_dir = resolved_versions_dir()
    if not os.path.isdir(versions_dir):
        return []
    removed = []
    names = sorted(os.listdir(versions_dir))
    protected = set(keep)
    if keep_count > 0 and len(protected) > keep_count:
        protected = set(sorted(protected)[-keep_count:])
    for name in names:
        target = os.path.join(versions_dir, name)
        if not os.path.isdir(target):
            continue
        if name in protected:
            continue
        try:
            shutil.rmtree(target, ignore_errors=True)
            removed.append(name)
        except Exception:
            continue
    return removed


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def download_to_file(url, dest_path, *, timeout=120):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    request = urllib.request.Request(str(url or "").strip(), headers={"User-Agent": "GenericAgentLauncher-Updater"})
    with urllib.request.urlopen(request, timeout=max(10, int(timeout or 120))) as resp:
        fd, temp_path = tempfile.mkstemp(prefix=".part-", suffix=".tmp", dir=os.path.dirname(dest_path))
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(temp_path, dest_path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
    return dest_path


def extract_zip_package(zip_path, target_dir):
    os.makedirs(target_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_dir)
    return target_dir


def verify_sha256(path, expected_sha256):
    expected = str(expected_sha256 or "").strip().lower()
    if not expected:
        raise ValueError("missing expected sha256")
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(f"sha256 mismatch: expected {expected}, got {actual}")
    return actual


def verify_manifest_signature(manifest_bytes, signature_b64, public_key_pem):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception as e:
        raise RuntimeError("cryptography is required for signature verification") from e
    payload = bytes(manifest_bytes or b"")
    signature = base64.b64decode(str(signature_b64 or "").strip())
    key_text = str(public_key_pem or "").strip().encode("utf-8")
    pub = serialization.load_pem_public_key(key_text)
    if not isinstance(pub, Ed25519PublicKey):
        raise ValueError("manifest public key must be Ed25519")
    pub.verify(signature, payload)
    return True


def updater_log(message):
    _ensure_launcher_data_dirs()
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {str(message or '').strip()}\n"
    try:
        with open(UPDATE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read_updater_log_tail(*, max_lines=80, max_chars=10000):
    try:
        if not os.path.isfile(UPDATE_LOG_PATH):
            return ""
        with open(UPDATE_LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = "".join(lines[-max(1, int(max_lines or 80)) :])
        text = tail.strip()
        if len(text) > max_chars:
            text = text[-int(max_chars) :]
        return text
    except Exception:
        return ""


def list_update_jobs(*, limit=20):
    if not os.path.isdir(UPDATE_JOBS_DIR):
        return []
    rows = []
    for fn in os.listdir(UPDATE_JOBS_DIR):
        if not str(fn).lower().endswith(".json"):
            continue
        fp = os.path.join(UPDATE_JOBS_DIR, fn)
        if not os.path.isfile(fp):
            continue
        payload = _read_json_file(fp, {})
        if not isinstance(payload, dict):
            continue
        payload["job_path"] = fp
        payload["_mtime"] = float(os.path.getmtime(fp))
        rows.append(payload)
    rows.sort(key=lambda item: float(item.get("_mtime") or 0.0), reverse=True)
    return rows[: max(1, int(limit or 20))]


def latest_update_job():
    rows = list_update_jobs(limit=1)
    if rows:
        return rows[0]
    return {}


def verify_authenticode_signature(file_path):
    target = os.path.abspath(str(file_path or "").strip())
    if not target or not os.path.isfile(target):
        raise FileNotFoundError(f"file not found for authenticode verification: {target}")
    if os.name != "nt":
        return {
            "supported": False,
            "status": "UnsupportedPlatform",
            "is_valid": False,
            "subject": "",
            "issuer": "",
            "thumbprint": "",
        }

    script = (
        "$ErrorActionPreference='Stop';"
        "Import-Module Microsoft.PowerShell.Security -ErrorAction SilentlyContinue;"
        "$sig=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "$cert=$sig.SignerCertificate;"
        "$obj=[pscustomobject]@{"
        "status=[string]$sig.Status;"
        "status_message=[string]$sig.StatusMessage;"
        "subject=if($cert){[string]$cert.Subject}else{''};"
        "issuer=if($cert){[string]$cert.Issuer}else{''};"
        "thumbprint=if($cert){[string]$cert.Thumbprint}else{''}"
        "};"
        "$obj | ConvertTo-Json -Compress"
    )
    cmd = [
        "powershell",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
        target,
    ]
    try:
        result = _run_external_subprocess(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return {
            "supported": False,
            "status": "PowerShellNotFound",
            "is_valid": False,
            "subject": "",
            "issuer": "",
            "thumbprint": "",
        }
    rc = getattr(result, "returncode", 1)
    if int(1 if rc is None else rc) != 0:
        stderr = str(getattr(result, "stderr", "") or "").strip()
        lower = stderr.lower()
        if (
            "get-authenticodesignature" in lower
            and (
                "could not be loaded" in lower
                or "couldnotautoloadmatchingmodule" in lower
                or "is not recognized" in lower
                or "term 'get-authenticodesignature' is not recognized" in lower
            )
        ):
            return {
                "supported": False,
                "status": "ToolUnavailable",
                "is_valid": False,
                "subject": "",
                "issuer": "",
                "thumbprint": "",
                "status_message": stderr,
            }
        raise RuntimeError(f"Get-AuthenticodeSignature failed: {stderr or result.returncode}")
    raw = str(getattr(result, "stdout", "") or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except Exception:
        payload = {}
    status = str(payload.get("status") or "Unknown").strip() or "Unknown"
    out = {
        "supported": True,
        "status": status,
        "status_message": str(payload.get("status_message") or "").strip(),
        "is_valid": status.lower() == "valid",
        "subject": str(payload.get("subject") or "").strip(),
        "issuer": str(payload.get("issuer") or "").strip(),
        "thumbprint": str(payload.get("thumbprint") or "").strip(),
    }
    return out


def bootstrap_executable_path():
    roots = [APP_DIR, resolved_programs_root(), PROGRAMS_ROOT]
    seen = set()
    for root in roots:
        text = os.path.normpath(str(root or "").strip())
        if not text or text in seen:
            continue
        seen.add(text)
        fp = os.path.join(text, BOOTSTRAP_EXE_NAME)
        if os.path.isfile(fp):
            return fp
    return os.path.join(resolved_programs_root(), BOOTSTRAP_EXE_NAME)


def updater_executable_path():
    roots = [APP_DIR, resolved_programs_root(), PROGRAMS_ROOT]
    seen = set()
    for root in roots:
        text = os.path.normpath(str(root or "").strip())
        if not text or text in seen:
            continue
        seen.add(text)
        fp = os.path.join(text, UPDATER_EXE_NAME)
        if os.path.isfile(fp):
            return fp
    return os.path.join(resolved_programs_root(), UPDATER_EXE_NAME)


def launch_installed_updater(job_path):
    updater = updater_executable_path()
    if not os.path.isfile(updater):
        raise FileNotFoundError(f"Updater.exe 不存在：{updater}")
    return _popen_external_subprocess([updater, "--job", str(job_path or "").strip()], cwd=os.path.dirname(updater))


def is_valid_agent_dir(path):
    return bool(
        path
        and os.path.isdir(path)
        and os.path.isfile(os.path.join(path, "launch.pyw"))
        and os.path.isfile(os.path.join(path, "agentmain.py"))
    )


def _ensure_mykey_file(agent_dir):
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
