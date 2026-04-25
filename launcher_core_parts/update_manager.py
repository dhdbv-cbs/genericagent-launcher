from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from .constants import (
    BOOTSTRAP_EXE_NAME,
    LAUNCHER_REPO_URL,
    MAIN_EXE_NAME,
    UPDATE_JOBS_DIR,
    UPDATE_STAGING_DIR,
    UPDATES_DIR,
)
from .runtime import (
    _atomic_write_json,
    _read_json_file,
    bootstrap_executable_path,
    cleanup_old_versions,
    current_launcher_version,
    download_to_file,
    extract_zip_package,
    launch_installed_updater,
    launcher_data_path,
    load_version_state,
    resolved_versions_dir,
    save_version_state,
    updater_log,
    verify_authenticode_signature,
    verify_manifest_signature,
    verify_sha256,
)

_GITHUB_API_CANDIDATES = (
    "https://api.github.com{path}",
    "https://mirror.ghproxy.com/https://api.github.com{path}",
    "https://ghproxy.com/https://api.github.com{path}",
)

_MANIFEST_NAMES = ("manifest.json", "launcher-manifest.json")
_SIGNATURE_NAMES = ("manifest.sig", "launcher-manifest.sig")

ERR_REPO_INVALID = "UPD-E-REPO-INVALID"
ERR_RELEASE_FETCH = "UPD-E-RELEASE-FETCH"
ERR_MANIFEST_INVALID = "UPD-E-MANIFEST-INVALID"
ERR_MANIFEST_SIGNATURE = "UPD-E-MANIFEST-SIGNATURE"
ERR_PACKAGE_META = "UPD-E-PACKAGE-META"
ERR_JOB_MISSING = "UPD-E-JOB-MISSING"
ERR_JOB_INVALID = "UPD-E-JOB-INVALID"
ERR_LOCK_TIMEOUT = "UPD-E-LOCK-TIMEOUT"
ERR_DOWNLOAD = "UPD-E-DOWNLOAD"
ERR_PACKAGE_HASH = "UPD-E-PACKAGE-HASH"
ERR_PACKAGE_EXTRACT = "UPD-E-PACKAGE-EXTRACT"
ERR_PACKAGE_CONTENT = "UPD-E-PACKAGE-CONTENT"
ERR_INSTALL = "UPD-E-INSTALL"
ERR_AUTHENTICODE = "UPD-E-AUTHENTICODE-INVALID"
ERR_BOOTSTRAP = "UPD-E-BOOTSTRAP"
ERR_HEALTH_STARTUP_TIMEOUT = "UPD-E-HEALTH-STARTUP-TIMEOUT"
ERR_HEALTH_ALIVE_TIMEOUT = "UPD-E-HEALTH-ALIVE-TIMEOUT"
ERR_ROLLBACK = "UPD-E-ROLLBACK"
ERR_UNEXPECTED = "UPD-E-UNEXPECTED"


class UpdateError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: str = "",
        detail: str = "",
        retryable: bool = False,
    ):
        self.code = str(code or ERR_UNEXPECTED).strip() or ERR_UNEXPECTED
        self.phase = str(phase or "").strip() or "unknown"
        self.detail = str(detail or "").strip()
        self.retryable = bool(retryable)
        super().__init__(str(message or "").strip() or self.code)


def _repo_slug_from_url(repo_url: str) -> str:
    raw = str(repo_url or "").strip()
    if not raw:
        return ""
    direct = re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", raw)
    if direct:
        return raw
    m = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", raw, flags=re.IGNORECASE)
    if not m:
        return ""
    owner = str(m.group(1) or "").strip()
    repo = str(m.group(2) or "").strip()
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}" if owner and repo else ""


def _build_github_api_urls(path: str, custom_candidates=None):
    normalized_path = "/" + str(path or "").lstrip("/")
    full_url = f"https://api.github.com{normalized_path}"
    templates = list(_GITHUB_API_CANDIDATES) + [str(item or "").strip() for item in (custom_candidates or [])]
    out = []
    seen = set()
    for template in templates:
        text = str(template or "").strip()
        if not text:
            continue
        if "{path}" in text:
            url = text.replace("{path}", normalized_path)
        elif "{full_url}" in text:
            url = text.replace("{full_url}", full_url)
        elif "api.github.com" in text:
            url = text.rstrip("/") + normalized_path
        else:
            url = text.rstrip("/") + "/" + full_url
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _int_or(value, default, *, minimum=0, maximum=None):
    try:
        num = int(value)
    except Exception:
        num = int(default)
    if num < minimum:
        num = minimum
    if maximum is not None and num > maximum:
        num = maximum
    return num


def _bool_from_any(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off", ""):
        return False
    return bool(default)


def _trim_detail(text: str, *, limit=1200):
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(20, limit - 3)] + "..."


def _is_retryable_http_error(err: urllib.error.HTTPError) -> bool:
    code = int(getattr(err, "code", 0) or 0)
    return code in (408, 425, 429) or 500 <= code <= 599


def _is_retryable_error(err: Exception) -> bool:
    if isinstance(err, urllib.error.HTTPError):
        return _is_retryable_http_error(err)
    if isinstance(err, urllib.error.URLError):
        return True
    if isinstance(err, (TimeoutError, ConnectionError, OSError)):
        return True
    return False


def _retry_delay(attempt: int, *, base=0.8, factor=2.0, max_seconds=6.0):
    power = max(0, int(attempt) - 1)
    return min(float(max_seconds), float(base) * (float(factor) ** power))


def _call_with_retry(func, *, attempts=3, retry_if=None):
    final_err = None
    total = max(1, int(attempts or 1))
    predicate = retry_if or _is_retryable_error
    for attempt in range(1, total + 1):
        try:
            return func()
        except Exception as e:
            final_err = e
            if attempt >= total or (not predicate(e)):
                break
            time.sleep(_retry_delay(attempt))
    raise final_err if final_err is not None else RuntimeError("retry failed without exception")


def _http_json_with_fallback(path: str, *, custom_candidates=None, timeout=12, attempts_per_url=3):
    last_errors = []
    for url in _build_github_api_urls(path, custom_candidates=custom_candidates):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json, application/json",
                "User-Agent": "GenericAgentLauncher-Updater",
            },
            method="GET",
        )

        def _request():
            with urllib.request.urlopen(req, timeout=max(4, int(timeout or 12))) as resp:
                return resp.read().decode("utf-8", errors="replace")

        try:
            raw = _call_with_retry(_request, attempts=max(1, int(attempts_per_url or 3)))
            payload = json.loads(raw) if raw.strip() else {}
            return payload, url, last_errors
        except urllib.error.HTTPError as e:
            last_errors.append(f"{url} -> HTTP {int(getattr(e, 'code', 0) or 0)}")
        except urllib.error.URLError as e:
            last_errors.append(f"{url} -> {e.reason}")
        except Exception as e:
            last_errors.append(f"{url} -> {e}")
    return None, "", last_errors


def _fetch_text(url: str, *, timeout=20, attempts=3):
    req = urllib.request.Request(
        str(url or "").strip(),
        headers={"User-Agent": "GenericAgentLauncher-Updater", "Accept": "application/json, text/plain, */*"},
        method="GET",
    )

    def _request():
        with urllib.request.urlopen(req, timeout=max(5, int(timeout or 20))) as resp:
            return resp.read()

    return _call_with_retry(_request, attempts=max(1, int(attempts or 3)))


def _asset_by_name(release: dict, names):
    assets = list((release or {}).get("assets") or [])
    lookup = {str(item.get("name") or "").strip().lower(): item for item in assets if isinstance(item, dict)}
    for name in names:
        key = str(name or "").strip().lower()
        if key in lookup:
            return lookup[key]
    return None


def _version_tuple(version_text: str):
    text = str(version_text or "").strip().lower().lstrip("v")
    parts = []
    for chunk in re.split(r"[.+-]", text):
        if chunk.isdigit():
            parts.append(int(chunk))
        elif chunk:
            parts.append(chunk)
    return tuple(parts)


def _is_newer_version(target: str, current: str):
    return _version_tuple(target) > _version_tuple(current)


def _release_to_launcher_update_info(release: dict, *, public_key_pem: str):
    if not isinstance(release, dict):
        raise UpdateError(ERR_RELEASE_FETCH, "GitHub release payload 无效", phase="query")
    manifest_asset = _asset_by_name(release, _MANIFEST_NAMES)
    if manifest_asset is None:
        raise UpdateError(ERR_MANIFEST_INVALID, "release 缺少 manifest.json 资产", phase="manifest")
    manifest_url = str(manifest_asset.get("browser_download_url") or "").strip()
    if not manifest_url:
        raise UpdateError(ERR_MANIFEST_INVALID, "manifest 资产缺少下载地址", phase="manifest")
    try:
        manifest_bytes = _fetch_text(manifest_url, timeout=20, attempts=3)
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="replace"))
    except Exception as e:
        raise UpdateError(ERR_MANIFEST_INVALID, "读取或解析 manifest 失败", phase="manifest", detail=str(e)) from e
    if not isinstance(manifest, dict):
        raise UpdateError(ERR_MANIFEST_INVALID, "manifest 内容不是 JSON 对象", phase="manifest")

    signature_text = str(manifest.get("signature") or "").strip()
    if not signature_text:
        signature_asset = _asset_by_name(release, _SIGNATURE_NAMES)
        if signature_asset is None:
            raise UpdateError(ERR_MANIFEST_SIGNATURE, "release 缺少 manifest.sig", phase="manifest-signature")
        signature_url = str(signature_asset.get("browser_download_url") or "").strip()
        try:
            signature_text = _fetch_text(signature_url, timeout=20, attempts=3).decode("utf-8", errors="replace").strip()
        except Exception as e:
            raise UpdateError(ERR_MANIFEST_SIGNATURE, "下载 manifest.sig 失败", phase="manifest-signature", detail=str(e)) from e
    if not signature_text:
        raise UpdateError(ERR_MANIFEST_SIGNATURE, "manifest 签名为空", phase="manifest-signature")
    if not str(public_key_pem or "").strip():
        raise UpdateError(ERR_MANIFEST_SIGNATURE, "缺少更新公钥（update_public_key.pem）", phase="manifest-signature")
    try:
        verify_manifest_signature(manifest_bytes, signature_text, public_key_pem)
    except Exception as e:
        raise UpdateError(ERR_MANIFEST_SIGNATURE, "manifest 签名校验失败", phase="manifest-signature", detail=str(e)) from e

    package = dict(manifest.get("package") or {})
    package_name = str(package.get("name") or "").strip()
    package_sha256 = str(package.get("sha256") or "").strip().lower()
    package_url = str(package.get("url") or "").strip()
    if not package_url and package_name:
        asset = _asset_by_name(release, [package_name])
        if isinstance(asset, dict):
            package_url = str(asset.get("browser_download_url") or "").strip()
    if not package_url:
        raise UpdateError(ERR_PACKAGE_META, "manifest 中缺少更新包下载地址", phase="manifest")
    if not package_sha256:
        raise UpdateError(ERR_PACKAGE_META, "manifest 中缺少更新包 sha256", phase="manifest")

    target_version = str(manifest.get("version") or release.get("tag_name") or "").strip().lstrip("v")
    if not target_version:
        raise UpdateError(ERR_MANIFEST_INVALID, "manifest 缺少 version", phase="manifest")
    channel = str(manifest.get("channel") or "stable").strip().lower() or "stable"
    notes = str(release.get("body") or "").strip()

    security = dict(manifest.get("security") or {})
    require_authenticode = _bool_from_any(
        security.get("require_authenticode"),
        default=_bool_from_any(os.environ.get("GA_LAUNCHER_REQUIRE_AUTHENTICODE"), default=False),
    )
    health_min_alive = _int_or(security.get("health_min_alive_seconds"), 6, minimum=2, maximum=60)
    startup_timeout = _int_or(security.get("health_startup_timeout_seconds"), 45, minimum=8, maximum=120)

    return {
        "target_version": target_version,
        "channel": channel,
        "package_url": package_url,
        "package_sha256": package_sha256,
        "release_url": str(release.get("html_url") or "").strip(),
        "release_tag": str(release.get("tag_name") or "").strip(),
        "manifest_url": manifest_url,
        "notes": notes,
        "require_authenticode": require_authenticode,
        "health_min_alive_seconds": health_min_alive,
        "health_startup_timeout_seconds": startup_timeout,
    }


def query_launcher_update(*, repo_url: str = "", current_version: str = "", public_key_pem: str = "", api_candidates=None):
    repo = str(repo_url or "").strip() or LAUNCHER_REPO_URL
    slug = _repo_slug_from_url(repo)
    if not slug:
        raise UpdateError(ERR_REPO_INVALID, "launcher_repo_url 不是合法 GitHub 仓库地址", phase="query")
    payload, source, errors = _http_json_with_fallback(
        f"/repos/{slug}/releases/latest",
        custom_candidates=api_candidates,
        timeout=12,
        attempts_per_url=3,
    )
    if not isinstance(payload, dict):
        detail = "; ".join(errors[-3:]) if errors else "no_response"
        raise UpdateError(ERR_RELEASE_FETCH, "拉取最新 Release 失败", phase="query", detail=detail)
    info = _release_to_launcher_update_info(payload, public_key_pem=public_key_pem)
    cur = str(current_version or "").strip() or current_launcher_version()
    info["current_version"] = cur
    info["is_update_available"] = _is_newer_version(info["target_version"], cur)
    info["api_source"] = source
    return info


def create_update_job(update_info: dict):
    info = dict(update_info or {})
    target_version = str(info.get("target_version") or "").strip()
    if not target_version:
        raise UpdateError(ERR_JOB_INVALID, "target_version 不能为空", phase="create-job")
    os.makedirs(UPDATE_JOBS_DIR, exist_ok=True)
    job_id = f"upd-{int(time.time() * 1000)}-{os.getpid()}-{secrets.token_hex(2)}"
    timeout_seconds = _int_or(info.get("timeout_seconds", 120), 120, minimum=30, maximum=1200)
    startup_timeout = _int_or(
        info.get("health_startup_timeout_seconds"),
        min(60, timeout_seconds),
        minimum=8,
        maximum=max(8, timeout_seconds),
    )
    job = {
        "job_id": job_id,
        "created_at": float(time.time()),
        "started_at": 0.0,
        "completed_at": 0.0,
        "current_version": str(info.get("current_version") or current_launcher_version()),
        "target_version": target_version,
        "channel": str(info.get("channel") or "stable").strip().lower() or "stable",
        "package_url": str(info.get("package_url") or "").strip(),
        "package_sha256": str(info.get("package_sha256") or "").strip().lower(),
        "release_url": str(info.get("release_url") or "").strip(),
        "release_tag": str(info.get("release_tag") or "").strip(),
        "manifest_url": str(info.get("manifest_url") or "").strip(),
        "status": "queued",
        "phase": "queued",
        "error_code": "",
        "error_detail": "",
        "timeout_seconds": timeout_seconds,
        "download_attempts": _int_or(info.get("download_attempts"), 3, minimum=1, maximum=8),
        "manifest_attempts": _int_or(info.get("manifest_attempts"), 3, minimum=1, maximum=8),
        "health_startup_timeout_seconds": startup_timeout,
        "health_min_alive_seconds": _int_or(info.get("health_min_alive_seconds"), 6, minimum=2, maximum=60),
        "require_authenticode": _bool_from_any(info.get("require_authenticode"), default=False),
    }
    if not job["package_url"] or not job["package_sha256"]:
        raise UpdateError(ERR_JOB_INVALID, "更新任务缺少 package_url/package_sha256", phase="create-job")
    job_path = os.path.join(UPDATE_JOBS_DIR, f"{job_id}.json")
    _atomic_write_json(job_path, job)
    return {"job": job, "job_path": job_path}


def launch_update_job(job_path: str):
    return launch_installed_updater(str(job_path or "").strip())


def _save_job_state(job_file: str, job: dict, **patch):
    payload = dict(job or {})
    payload.update({k: v for k, v in patch.items() if v is not None})
    _atomic_write_json(job_file, payload)
    job.clear()
    job.update(payload)
    return payload


@contextmanager
def _update_lock(timeout_seconds=30):
    lock_path = launcher_data_path("updates", "update.lock")
    started = time.time()
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.time() - started > max(3, int(timeout_seconds or 30)):
                raise UpdateError(ERR_LOCK_TIMEOUT, "更新锁等待超时", phase="prepare")
            time.sleep(0.25)
            continue
        try:
            os.write(fd, f"{os.getpid()}".encode("utf-8"))
            yield lock_path
        finally:
            try:
                os.close(fd)
            except Exception:
                pass
            try:
                os.remove(lock_path)
            except Exception:
                pass
        return


def _find_main_exe_parent(extract_root: str):
    for dirpath, _dirs, files in os.walk(extract_root):
        for fn in files:
            if fn.lower() == MAIN_EXE_NAME.lower():
                return dirpath
    return ""


def _start_bootstrap():
    bootstrap = bootstrap_executable_path()
    if not os.path.isfile(bootstrap):
        raise UpdateError(ERR_BOOTSTRAP, f"{BOOTSTRAP_EXE_NAME} not found: {bootstrap}", phase="switch")
    from .runtime import _popen_external_subprocess

    try:
        return _popen_external_subprocess([bootstrap], cwd=os.path.dirname(bootstrap))
    except Exception as e:
        raise UpdateError(ERR_BOOTSTRAP, f"启动 {BOOTSTRAP_EXE_NAME} 失败", phase="switch", detail=str(e)) from e


def _wait_for_health_ack(ack_path: str, timeout_seconds: int):
    started = time.time()
    while time.time() - started < max(1, int(timeout_seconds or 1)):
        if os.path.isfile(ack_path):
            return True
        time.sleep(0.35)
    return False


def _install_version_dir(staging_main_parent: str, target_version: str):
    if not staging_main_parent or not target_version:
        raise UpdateError(ERR_INSTALL, "安装源目录或目标版本为空", phase="install")
    versions_dir = resolved_versions_dir()
    os.makedirs(versions_dir, exist_ok=True)
    target_dir = os.path.join(versions_dir, target_version)
    temp_dir = os.path.join(versions_dir, f".{target_version}.partial")
    if os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        shutil.copytree(staging_main_parent, temp_dir, dirs_exist_ok=True)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)
        os.replace(temp_dir, target_dir)
    except Exception as e:
        raise UpdateError(ERR_INSTALL, "写入版本目录失败", phase="install", detail=str(e)) from e
    return target_dir


def _rollback_to_previous(job_file: str, job: dict, *, previous_version: str, error_code: str, error_detail: str, phase: str):
    try:
        state = load_version_state()
        state["current_version"] = str(previous_version or "").strip()
        state["pending_update"] = {}
        save_version_state(state)
        _start_bootstrap()
    except Exception as e:
        detail = _trim_detail(f"{error_detail}; rollback failed: {e}")
        _save_job_state(
            job_file,
            job,
            status="failed",
            phase="rollback-failed",
            error_code=ERR_ROLLBACK,
            error_detail=detail,
            completed_at=float(time.time()),
        )
        return {
            "ok": False,
            "job_id": str(job.get("job_id") or ""),
            "rolled_back": False,
            "error_code": ERR_ROLLBACK,
            "phase": "rollback-failed",
        }
    _save_job_state(
        job_file,
        job,
        status="rolled_back",
        phase="rolled_back",
        error_code=str(error_code or "").strip(),
        error_detail=_trim_detail(error_detail),
        completed_at=float(time.time()),
    )
    updater_log(f"[{job.get('job_id')}] rolled back to {previous_version}, reason={error_code}")
    return {
        "ok": False,
        "job_id": str(job.get("job_id") or ""),
        "rolled_back": True,
        "error_code": str(error_code or "").strip(),
        "phase": str(phase or "").strip() or "rolled_back",
    }


def _download_package_with_retry(package_url: str, package_file: str, *, attempts: int, timeout_seconds: int):
    def _do_download():
        return download_to_file(package_url, package_file, timeout=timeout_seconds)

    try:
        return _call_with_retry(_do_download, attempts=max(1, int(attempts or 1)))
    except Exception as e:
        raise UpdateError(ERR_DOWNLOAD, "下载更新包失败", phase="download", detail=str(e), retryable=True) from e


def _validate_job_payload(job_file: str, job: dict):
    if not job_file or not os.path.isfile(job_file):
        raise UpdateError(ERR_JOB_MISSING, f"更新任务不存在: {job_file}", phase="prepare")
    if not isinstance(job, dict):
        raise UpdateError(ERR_JOB_INVALID, "更新任务 JSON 格式无效", phase="prepare")
    target_version = str(job.get("target_version") or "").strip()
    if not target_version:
        raise UpdateError(ERR_JOB_INVALID, "更新任务缺少 target_version", phase="prepare")
    package_url = str(job.get("package_url") or "").strip()
    package_sha256 = str(job.get("package_sha256") or "").strip().lower()
    if not package_url or not package_sha256:
        raise UpdateError(ERR_JOB_INVALID, "更新任务缺少 package_url/package_sha256", phase="prepare")


def apply_update_job(job_path: str):
    job_file = str(job_path or "").strip()
    if not job_file or not os.path.isfile(job_file):
        raise FileNotFoundError(f"update job not found: {job_file}")
    job = _read_json_file(job_file, {})
    if not isinstance(job, dict):
        raise ValueError("invalid update job payload")

    job_id = str(job.get("job_id") or os.path.splitext(os.path.basename(job_file))[0]).strip()
    job["job_id"] = job_id
    target_version = str(job.get("target_version") or "").strip()
    previous_state = load_version_state()
    previous_version = str(previous_state.get("current_version") or "").strip() or str(job.get("current_version") or "").strip()
    timeout_seconds = _int_or(job.get("timeout_seconds"), 120, minimum=30, maximum=1200)
    startup_timeout = _int_or(
        job.get("health_startup_timeout_seconds"),
        min(60, timeout_seconds),
        minimum=8,
        maximum=max(8, timeout_seconds),
    )
    alive_min_seconds = _int_or(job.get("health_min_alive_seconds"), 6, minimum=2, maximum=60)
    require_authenticode = _bool_from_any(job.get("require_authenticode"), default=False)
    state_switched = False

    try:
        _validate_job_payload(job_file, job)
        _save_job_state(
            job_file,
            job,
            status="running",
            phase="prepare",
            started_at=float(time.time()),
            error_code="",
            error_detail="",
        )
        updater_log(f"[{job_id}] update start: {previous_version} -> {target_version}")

        with _update_lock(timeout_seconds=40):
            os.makedirs(UPDATE_STAGING_DIR, exist_ok=True)
            os.makedirs(UPDATE_JOBS_DIR, exist_ok=True)
            os.makedirs(UPDATES_DIR, exist_ok=True)
            os.makedirs(resolved_versions_dir(), exist_ok=True)

            staging_root = os.path.join(UPDATE_STAGING_DIR, job_id)
            if os.path.isdir(staging_root):
                shutil.rmtree(staging_root, ignore_errors=True)
            os.makedirs(staging_root, exist_ok=True)

            package_url = str(job.get("package_url") or "").strip()
            package_sha256 = str(job.get("package_sha256") or "").strip().lower()
            package_file = os.path.join(launcher_data_path("updates", "downloads"), f"{job_id}.zip")
            _save_job_state(job_file, job, phase="download")
            updater_log(f"[{job_id}] downloading package: {package_url}")
            _download_package_with_retry(
                package_url,
                package_file,
                attempts=_int_or(job.get("download_attempts"), 3, minimum=1, maximum=8),
                timeout_seconds=180,
            )
            try:
                verify_sha256(package_file, package_sha256)
            except Exception as e:
                raise UpdateError(ERR_PACKAGE_HASH, "更新包 sha256 校验失败", phase="download", detail=str(e)) from e
            updater_log(f"[{job_id}] package sha256 verified")

            _save_job_state(job_file, job, phase="extract")
            extract_root = os.path.join(staging_root, "extract")
            try:
                extract_zip_package(package_file, extract_root)
            except Exception as e:
                raise UpdateError(ERR_PACKAGE_EXTRACT, "解压更新包失败", phase="extract", detail=str(e)) from e
            main_parent = _find_main_exe_parent(extract_root)
            if not main_parent:
                raise UpdateError(ERR_PACKAGE_CONTENT, f"更新包不包含 {MAIN_EXE_NAME}", phase="extract")

            _save_job_state(job_file, job, phase="install")
            installed_dir = _install_version_dir(main_parent, target_version)
            updater_log(f"[{job_id}] installed version directory: {target_version}")

            main_exe = os.path.join(installed_dir, MAIN_EXE_NAME)
            try:
                auth_result = verify_authenticode_signature(main_exe)
            except Exception as e:
                auth_result = {
                    "supported": False,
                    "status": "CheckFailed",
                    "is_valid": False,
                    "subject": "",
                    "issuer": "",
                    "thumbprint": "",
                    "status_message": str(e),
                }
            job["authenticode"] = auth_result
            _save_job_state(job_file, job)
            if require_authenticode and (not bool(auth_result.get("is_valid", False))):
                status = str(auth_result.get("status") or "Unknown")
                raise UpdateError(
                    ERR_AUTHENTICODE,
                    "更新包主程序 Authenticode 校验失败",
                    phase="install",
                    detail=f"status={status}",
                )
            if require_authenticode:
                updater_log(f"[{job_id}] Authenticode verified: {auth_result.get('status')}")
            else:
                msg = str(auth_result.get("status_message") or "").strip()
                if msg:
                    updater_log(f"[{job_id}] Authenticode status: {auth_result.get('status')} ({msg})")
                else:
                    updater_log(f"[{job_id}] Authenticode status: {auth_result.get('status')}")

            startup_ack_path = os.path.join(staging_root, "startup_ack.json")
            alive_ack_path = os.path.join(staging_root, "alive_ack.json")
            pending = {
                "job_id": job_id,
                "target_version": target_version,
                "startup_ack_path": startup_ack_path,
                "alive_ack_path": alive_ack_path,
                "min_alive_seconds": alive_min_seconds,
                "started_at": float(time.time()),
            }
            state = load_version_state()
            state["previous_version"] = previous_version
            state["current_version"] = target_version
            state["pending_update"] = pending
            save_version_state(state)
            state_switched = True
            _save_job_state(job_file, job, phase="switch")
            updater_log(f"[{job_id}] switched state current_version={target_version}, waiting for health ack")

            _start_bootstrap()
            _save_job_state(job_file, job, phase="health-startup")
            if not _wait_for_health_ack(startup_ack_path, startup_timeout):
                return _rollback_to_previous(
                    job_file,
                    job,
                    previous_version=previous_version,
                    error_code=ERR_HEALTH_STARTUP_TIMEOUT,
                    error_detail=f"未在 {startup_timeout}s 内收到启动确认",
                    phase="health-startup",
                )

            _save_job_state(job_file, job, phase="health-alive")
            alive_wait_seconds = max(5, timeout_seconds - startup_timeout)
            if not _wait_for_health_ack(alive_ack_path, alive_wait_seconds):
                return _rollback_to_previous(
                    job_file,
                    job,
                    previous_version=previous_version,
                    error_code=ERR_HEALTH_ALIVE_TIMEOUT,
                    error_detail=f"未在 {alive_wait_seconds}s 内收到存活确认",
                    phase="health-alive",
                )

            updater_log(f"[{job_id}] health ack received (startup + alive)")
            final_state = load_version_state()
            final_state["previous_version"] = previous_version
            final_state["current_version"] = target_version
            final_state["pending_update"] = {}
            save_version_state(final_state)
            cleanup_old_versions(keep_versions={previous_version, target_version}, keep_count=2)
            _save_job_state(
                job_file,
                job,
                status="completed",
                phase="completed",
                completed_at=float(time.time()),
                error_code="",
                error_detail="",
            )
            return {"ok": True, "job_id": job_id, "rolled_back": False, "phase": "completed", "error_code": ""}
    except UpdateError as e:
        updater_log(f"[{job_id}] update error: {e.code} phase={e.phase} detail={_trim_detail(e.detail or str(e))}")
        if state_switched:
            return _rollback_to_previous(
                job_file,
                job,
                previous_version=previous_version,
                error_code=e.code,
                error_detail=e.detail or str(e),
                phase=e.phase,
            )
        _save_job_state(
            job_file,
            job,
            status="failed",
            phase=e.phase,
            error_code=e.code,
            error_detail=_trim_detail(e.detail or str(e)),
            completed_at=float(time.time()),
        )
        return {"ok": False, "job_id": job_id, "rolled_back": False, "error_code": e.code, "phase": e.phase}
    except Exception as e:
        updater_log(f"[{job_id}] unexpected error: {_trim_detail(str(e))}")
        if state_switched:
            return _rollback_to_previous(
                job_file,
                job,
                previous_version=previous_version,
                error_code=ERR_UNEXPECTED,
                error_detail=str(e),
                phase="unexpected",
            )
        _save_job_state(
            job_file,
            job,
            status="failed",
            phase="unexpected",
            error_code=ERR_UNEXPECTED,
            error_detail=_trim_detail(str(e)),
            completed_at=float(time.time()),
        )
        return {"ok": False, "job_id": job_id, "rolled_back": False, "error_code": ERR_UNEXPECTED, "phase": "unexpected"}
