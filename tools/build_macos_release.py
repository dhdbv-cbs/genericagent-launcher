from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

try:
    from tools import build_macos_icon_assets as macos_icon_assets
except ModuleNotFoundError:
    import build_macos_icon_assets as macos_icon_assets

APP_NAME = "GenericAgent Launcher"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_BUNDLE_ID = "com.dhdbv.genericagentlauncher"
MACOS_INSTALL_TARGET = f"/Applications/{APP_BUNDLE_NAME}"
MACOS_USER_INSTALL_TARGET = f"~/Applications/{APP_BUNDLE_NAME}"
MACOS_DATA_ROOT = "~/Library/Application Support/GenericAgentLauncher"
MACOS_CONFIG_PATH = f"{MACOS_DATA_ROOT}/config/launcher_config.json"
MACOS_VERSION_JSON_RELATIVE_PATH = "Contents/Resources/version.json"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_path(root: str, path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return os.path.abspath(root)
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(root, raw))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _parse_args():
    parser = argparse.ArgumentParser(description="Build macOS app/dmg bundle for GenericAgent Launcher")
    parser.add_argument("--version", required=True, help="Release version, e.g. 1.2.3")
    parser.add_argument("--dist", default="dist", help="PyInstaller dist directory")
    parser.add_argument("--out", default="release", help="Output root directory")
    parser.add_argument("--commit", default="", help="Commit sha for version metadata")
    return parser.parse_args()


def _run(cmd, *, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(detail or f"command failed: {' '.join(cmd)}")
    return result


def _version_metadata(version: str, *, commit: str = "") -> dict:
    return {
        "version": str(version or "").strip(),
        "channel": "stable",
        "commit": str(commit or "").strip(),
        "build_time": datetime.now(timezone.utc).isoformat(),
    }


def _normalized_build_arch(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    return value


def _current_build_arch() -> str:
    return _normalized_build_arch(platform.machine())


def _artifact_names(version: str) -> dict:
    resolved_version = str(version or "").strip() or "unknown"
    return {
        "app_bundle": APP_BUNDLE_NAME,
        "dmg": f"GenericAgentLauncher-macos-{resolved_version}.dmg",
        "sha256": f"GenericAgentLauncher-macos-{resolved_version}.sha256",
        "readme": "README-macOS.txt",
        "metadata": "install-metadata.json",
        "version_json": MACOS_VERSION_JSON_RELATIVE_PATH,
    }


def _copy_app_bundle(src: str, dst: str, *, dirs_exist_ok: bool = False) -> str:
    # Preserve macOS bundle symlinks instead of flattening PyInstaller's cross-linked layout.
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=dirs_exist_ok)
    return dst


def _install_readme_text(version: str, *, version_meta: dict | None = None) -> str:
    meta = dict(version_meta or {})
    resolved_version = str(meta.get("version") or version or "").strip() or "unknown"
    commit = str(meta.get("commit") or "").strip()
    build_time = str(meta.get("build_time") or "").strip()
    lines = [
        f"GenericAgent Launcher for macOS\n"
        f"Version: {resolved_version}\n"
    ]
    if commit:
        lines.append(f"Commit: {commit}\n")
    if build_time:
        lines.append(f"Build Time (UTC): {build_time}\n")
    lines.append("\n")
    lines.extend(
        [
            "Install\n",
            f"1. Open the dmg and drag {APP_BUNDLE_NAME} into the Applications alias.\n",
            f"2. If you prefer a user-only install, copy the app bundle to {MACOS_USER_INSTALL_TARGET} instead.\n",
            f"3. Launch the app from its actual install path: {MACOS_INSTALL_TARGET} (recommended)\n",
            f"   or {MACOS_USER_INSTALL_TARGET} (user-only install).\n",
            "4. If Gatekeeper blocks the first launch, try opening the app once,\n",
            "   then go to System Settings -> Privacy & Security -> Open Anyway and confirm.\n",
            "5. If Open Anyway is not shown yet, Finder -> right-click -> Open may still work\n",
            "   as a fallback on some macOS versions.\n\n",
            "First launch\n",
            "- This release is not Apple Developer signed and is not notarized.\n",
            "- PyInstaller may still apply ad-hoc signing for runtime compatibility.\n",
            f"- User data directory: {MACOS_DATA_ROOT}\n",
            f"- Launcher config path: {MACOS_CONFIG_PATH}\n",
            "- The launcher currently uses system Python on macOS.\n",
            "- If GenericAgent needs dependencies, the launcher will check and prompt at runtime.\n\n",
            "Upgrade\n",
            "- macOS currently uses manual upgrades.\n",
            f"- Replace the existing app bundle at its actual install path: {MACOS_INSTALL_TARGET}\n",
            f"  or {MACOS_USER_INSTALL_TARGET}.\n",
            "- Your launcher data stays in Application Support and is kept across app replacement.\n\n",
            "Uninstall\n",
            f"- Remove {MACOS_INSTALL_TARGET} or {MACOS_USER_INSTALL_TARGET}\n",
            f"- Optional cleanup: remove {MACOS_DATA_ROOT}\n",
        ]
    )
    return "".join(lines)


def _install_metadata(version: str, *, dmg_name: str, version_meta: dict | None = None) -> dict:
    meta = dict(version_meta or {})
    resolved_version = str(meta.get("version") or version or "").strip() or "unknown"
    artifact_names = _artifact_names(resolved_version)
    artifact_names["dmg"] = str(dmg_name or artifact_names["dmg"]).strip()
    return {
        "platform": "macos",
        "version": resolved_version,
        "channel": str(meta.get("channel") or "stable").strip() or "stable",
        "commit": str(meta.get("commit") or "").strip(),
        "build_time": str(meta.get("build_time") or "").strip(),
        "app_name": APP_NAME,
        "bundle_name": APP_BUNDLE_NAME,
        "bundle_identifier": APP_BUNDLE_ID,
        "install_mode": "manual_dmg",
        "install_target": MACOS_INSTALL_TARGET,
        "recommended_install_target": MACOS_INSTALL_TARGET,
        "user_install_target": MACOS_USER_INSTALL_TARGET,
        "data_root": MACOS_DATA_ROOT,
        "config_path": MACOS_CONFIG_PATH,
        "artifact_names": artifact_names,
        "supports_internal_updater": False,
        "requires_system_python": True,
        "build_arch": _current_build_arch(),
        "runner_label": str(os.environ.get("GA_MACOS_RUNNER_LABEL") or "").strip(),
        "developer_id_signed": False,
        "apple_developer_signed": False,
        "notarized": False,
        "pyinstaller_may_ad_hoc_sign": True,
        "first_launch_notes": [
            f"Install by dragging the app into /Applications, or copy it to {MACOS_USER_INSTALL_TARGET} for a user-only install.",
            "If Gatekeeper blocks launch, use System Settings -> Privacy & Security -> Open Anyway after the first blocked launch attempt.",
            "Finder -> right click -> Open may still work as a fallback on some macOS versions.",
            "This release is not Apple Developer signed and is not notarized.",
            "PyInstaller may still apply ad-hoc signing for runtime compatibility.",
            "Launcher state is initialized under Application Support on first launch.",
            "GenericAgent runtime still depends on system Python on macOS.",
        ],
    }


def _write_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(str(text or ""))
    return path


def _write_json(path: str, payload: dict) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def _write_release_support_files(macos_dir: str, *, version: str, dmg_name: str, version_meta: dict | None = None) -> tuple[str, str]:
    readme_path = os.path.join(macos_dir, "README-macOS.txt")
    metadata_path = os.path.join(macos_dir, "install-metadata.json")
    _write_text(readme_path, _install_readme_text(version, version_meta=version_meta))
    _write_json(metadata_path, _install_metadata(version, dmg_name=dmg_name, version_meta=version_meta))
    return readme_path, metadata_path


def _write_bundle_info_plist_versions(app_path: str, version: str) -> str:
    info_plist = os.path.join(str(app_path or "").strip(), "Contents", "Info.plist")
    if not os.path.isfile(info_plist):
        raise SystemExit(f"Info.plist not found: {info_plist}")
    with open(info_plist, "rb") as f:
        payload = plistlib.load(f)
    payload["CFBundleShortVersionString"] = str(version or "").strip()
    payload["CFBundleVersion"] = str(version or "").strip()
    with open(info_plist, "wb") as f:
        plistlib.dump(payload, f, sort_keys=False)
    return info_plist


def _ad_hoc_codesign_bundle(app_path: str) -> str:
    bundle_path = os.path.abspath(str(app_path or "").strip())
    if not os.path.isdir(bundle_path):
        raise SystemExit(f"app bundle not found for codesign: {bundle_path}")
    _run(["codesign", "--force", "--deep", "--sign", "-", bundle_path])
    return bundle_path


def _prepare_macos_bundle_icon(root: str) -> str:
    resolved_root = os.path.abspath(str(root or os.getcwd()))
    return macos_icon_assets.build_icns(
        svg_path=macos_icon_assets.default_icon_svg_path(resolved_root),
        icns_path=macos_icon_assets.default_icns_output_path(resolved_root),
    )


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("tools/build_macos_release.py must run on macOS")

    args = _parse_args()
    version = str(args.version or "").strip().lstrip("v")
    if not version:
        raise SystemExit("missing --version")

    root = _repo_root()
    dist_dir = _resolve_path(root, str(args.dist or "dist"))
    out_root = _resolve_path(root, str(args.out or "release"))
    spec_path = os.path.join(root, "GenericAgentLauncher.mac.spec")
    dmg_name = f"GenericAgentLauncher-macos-{version}.dmg"
    sha_name = f"GenericAgentLauncher-macos-{version}.sha256"
    bundle_icon_path = _prepare_macos_bundle_icon(root)

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            dist_dir,
            spec_path,
        ],
        cwd=root,
    )

    app_path = os.path.join(dist_dir, APP_BUNDLE_NAME)
    if not os.path.isdir(app_path):
        raise SystemExit(f"app bundle not found: {app_path}")

    version_meta = _version_metadata(version, commit=str(args.commit or "").strip())

    version_json = os.path.join(app_path, *MACOS_VERSION_JSON_RELATIVE_PATH.split("/"))
    _write_json(version_json, version_meta)
    _write_bundle_info_plist_versions(app_path, version)
    _ad_hoc_codesign_bundle(app_path)

    macos_dir = os.path.join(out_root, version, "macos")
    os.makedirs(macos_dir, exist_ok=True)
    release_app_path = os.path.join(macos_dir, APP_BUNDLE_NAME)
    if os.path.isdir(release_app_path):
        shutil.rmtree(release_app_path, ignore_errors=True)
    _copy_app_bundle(app_path, release_app_path, dirs_exist_ok=True)
    readme_path, metadata_path = _write_release_support_files(
        macos_dir,
        version=version,
        dmg_name=dmg_name,
        version_meta=version_meta,
    )

    staging_dir = tempfile.mkdtemp(prefix="ga-launcher-dmg-")
    try:
        staged_app = os.path.join(staging_dir, APP_BUNDLE_NAME)
        _copy_app_bundle(release_app_path, staged_app, dirs_exist_ok=True)
        os.symlink("/Applications", os.path.join(staging_dir, "Applications"))
        shutil.copy2(readme_path, os.path.join(staging_dir, os.path.basename(readme_path)))
        shutil.copy2(metadata_path, os.path.join(staging_dir, os.path.basename(metadata_path)))

        dmg_path = os.path.join(macos_dir, dmg_name)
        if os.path.isfile(dmg_path):
            os.remove(dmg_path)
        _run(
            [
                "hdiutil",
                "create",
                "-volname",
                APP_NAME,
                "-srcfolder",
                staging_dir,
                "-format",
                "UDZO",
                dmg_path,
            ]
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    sha256 = _sha256_file(dmg_path)
    sha_path = os.path.join(macos_dir, sha_name)
    with open(sha_path, "w", encoding="utf-8") as f:
        f.write(f"{sha256}  {os.path.basename(dmg_path)}\n")

    print(f"macOS release bundle ready: {macos_dir}")
    print(f"- app: {release_app_path}")
    print(f"- dmg: {dmg_path}")
    print(f"- sha256: {sha_path}")
    print(f"- install readme: {readme_path}")
    print(f"- install metadata: {metadata_path}")
    print(f"- bundle icon: {bundle_icon_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
