from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


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


def _int_env(name: str, default: int, *, minimum=0, maximum=None) -> int:
    raw = os.environ.get(name)
    try:
        num = int(raw) if raw is not None else int(default)
    except Exception:
        num = int(default)
    if num < minimum:
        num = minimum
    if maximum is not None and num > maximum:
        num = maximum
    return num


def _copytree(src: str, dst: str) -> None:
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _zip_dir(source_dir: str, zip_path: str) -> None:
    parent = os.path.dirname(source_dir)
    prefix = os.path.basename(source_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirs, files in os.walk(source_dir):
            for fn in files:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, parent)
                # Keep the top-level version directory in the package
                if not rel.startswith(prefix):
                    rel = os.path.join(prefix, rel)
                zf.write(full, rel)


def _load_private_key_pem() -> str:
    inline = str(os.environ.get("GA_LAUNCHER_UPDATE_PRIVATE_KEY_PEM") or "").strip()
    if inline:
        return inline
    key_file = str(os.environ.get("GA_LAUNCHER_UPDATE_PRIVATE_KEY_FILE") or "").strip()
    if key_file and os.path.isfile(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _load_public_key_pem() -> str:
    inline = str(os.environ.get("GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM") or "").strip()
    if inline:
        return inline
    key_file = str(os.environ.get("GA_LAUNCHER_UPDATE_PUBLIC_KEY_FILE") or "").strip()
    if key_file and os.path.isfile(key_file):
        with open(key_file, "r", encoding="utf-8") as f:
            return f.read()
    repo_key = os.path.join(os.getcwd(), "update_public_key.pem")
    if os.path.isfile(repo_key):
        with open(repo_key, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _remove_file_if_exists(path: str) -> None:
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _sign_manifest_bytes(manifest_bytes: bytes, private_key_pem: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("update private key must be Ed25519")
    signature = key.sign(manifest_bytes)
    return base64.b64encode(signature).decode("utf-8")


def _parse_args():
    parser = argparse.ArgumentParser(description="Build release/install bundles for GenericAgent Launcher")
    parser.add_argument("--version", required=True, help="Release version, e.g. 1.2.3")
    parser.add_argument("--dist", default="dist", help="PyInstaller dist directory")
    parser.add_argument("--out", default="release", help="Output root directory")
    parser.add_argument("--channel", default="stable", help="Release channel")
    parser.add_argument("--commit", default="", help="Commit sha for version metadata")
    parser.add_argument("--allow-unsigned", action="store_true", help="Allow unsigned manifest in local builds")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    version = str(args.version or "").strip().lstrip("v")
    if not version:
        raise SystemExit("missing --version")
    dist_dir = os.path.abspath(str(args.dist or "dist"))
    out_root = os.path.abspath(str(args.out or "release"))

    app_dir = os.path.join(dist_dir, "GenericAgentLauncher")
    bootstrap_exe = os.path.join(dist_dir, "LauncherBootstrap.exe")
    updater_exe = os.path.join(dist_dir, "Updater.exe")
    if not os.path.isdir(app_dir):
        raise SystemExit(f"app directory not found: {app_dir}")
    if not os.path.isfile(bootstrap_exe):
        raise SystemExit(f"bootstrap exe not found: {bootstrap_exe}")
    if not os.path.isfile(updater_exe):
        raise SystemExit(f"updater exe not found: {updater_exe}")

    release_root = os.path.join(out_root, version)
    install_root = os.path.join(release_root, "install")
    app_version_root = os.path.join(install_root, "app", "versions", version)
    update_root = os.path.join(release_root, "update")
    os.makedirs(update_root, exist_ok=True)
    os.makedirs(app_version_root, exist_ok=True)

    _copytree(app_dir, app_version_root)
    shutil.copy2(bootstrap_exe, os.path.join(install_root, "LauncherBootstrap.exe"))
    shutil.copy2(updater_exe, os.path.join(install_root, "Updater.exe"))

    version_meta = {
        "version": version,
        "channel": str(args.channel or "stable").strip().lower() or "stable",
        "commit": str(args.commit or "").strip(),
        "build_time": datetime.now(timezone.utc).isoformat(),
    }
    with open(os.path.join(app_version_root, "version.json"), "w", encoding="utf-8") as f:
        json.dump(version_meta, f, ensure_ascii=False, indent=2)

    package_name = f"GenericAgentLauncher-app-{version}.zip"
    package_path = os.path.join(update_root, package_name)
    _zip_dir(app_version_root, package_path)
    package_sha256 = _sha256_file(package_path)

    manifest = {
        "version": version,
        "channel": version_meta["channel"],
        "package": {"name": package_name, "sha256": package_sha256},
        "security": {
            "manifest_signature": "ed25519",
            "package_hash": "sha256",
            "require_authenticode": _bool_from_any(os.environ.get("GA_LAUNCHER_REQUIRE_AUTHENTICODE"), default=False),
            "health_min_alive_seconds": _int_env("GA_LAUNCHER_HEALTH_MIN_ALIVE_SECONDS", 6, minimum=2, maximum=60),
            "health_startup_timeout_seconds": _int_env("GA_LAUNCHER_HEALTH_STARTUP_TIMEOUT_SECONDS", 45, minimum=8, maximum=120),
        },
    }
    manifest_path = os.path.join(update_root, "manifest.json")
    manifest_sig_path = os.path.join(update_root, "manifest.sig")
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with open(manifest_path, "wb") as f:
        f.write(manifest_bytes)

    key_pem = _load_private_key_pem()
    public_key_pem = _load_public_key_pem()
    signature = ""
    if key_pem:
        if not public_key_pem:
            raise SystemExit("update signing public key is missing (set GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM or *_FILE)")
        signature = _sign_manifest_bytes(manifest_bytes, key_pem)
    elif not args.allow_unsigned:
        raise SystemExit("update signing key is missing (set GA_LAUNCHER_UPDATE_PRIVATE_KEY_PEM or *_FILE)")

    if signature.strip():
        with open(manifest_sig_path, "w", encoding="utf-8") as f:
            f.write(signature.strip())
    else:
        _remove_file_if_exists(manifest_sig_path)

    if public_key_pem:
        with open(os.path.join(install_root, "update_public_key.pem"), "w", encoding="utf-8") as f:
            f.write(public_key_pem.strip() + "\n")

    with open(os.path.join(update_root, "sha256sums.txt"), "w", encoding="utf-8") as f:
        f.write(f"{package_sha256}  {package_name}\n")
        f.write(f"{_sha256_file(manifest_path)}  manifest.json\n")
        if os.path.isfile(manifest_sig_path):
            f.write(f"{_sha256_file(manifest_sig_path)}  manifest.sig\n")

    print(f"Release bundle ready: {release_root}")
    print(f"- install: {install_root}")
    print(f"- update package: {package_path}")
    print(f"- manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
