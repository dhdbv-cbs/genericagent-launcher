from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import subprocess
import sys


APP_NAME = "GenericAgent Launcher"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_BUNDLE_ID = "com.dhdbv.genericagentlauncher"
INSTALL_TARGET = f"/Applications/{APP_BUNDLE_NAME}"
USER_INSTALL_TARGET = f"~/Applications/{APP_BUNDLE_NAME}"
DATA_ROOT = "~/Library/Application Support/GenericAgentLauncher"
CONFIG_PATH = f"{DATA_ROOT}/config/launcher_config.json"
MACOS_VERSION_JSON_RELATIVE_PATH = "Contents/Resources/version.json"


def _parse_args():
    parser = argparse.ArgumentParser(description="Validate macOS GenericAgent Launcher release artifacts")
    parser.add_argument("--version", required=True, help="Release version, e.g. 1.2.3")
    parser.add_argument("--out", default="release", help="Release output root")
    parser.add_argument("--expected-commit", default="", help="Expected commit sha recorded in metadata")
    parser.add_argument("--expected-arch", default="", help="Expected packaged app architecture, e.g. arm64")
    parser.add_argument("--expected-runner-label", default="", help="Expected runner label recorded in metadata")
    return parser.parse_args()


def _die(message: str):
    raise SystemExit(str(message or "").strip() or "validation failed")


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
        _die(detail or f"command failed: {' '.join(cmd)}")
    return result


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_plist(path: str):
    with open(path, "rb") as f:
        return plistlib.load(f)


def _normalized_arch(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    if "arm64" in value and "x86_64" in value:
        return "universal2"
    return value


def _expected_artifact_names(version: str) -> dict:
    resolved = str(version or "").strip().lstrip("v")
    return {
        "app_bundle": APP_BUNDLE_NAME,
        "dmg": f"GenericAgentLauncher-macos-{resolved}.dmg",
        "sha256": f"GenericAgentLauncher-macos-{resolved}.sha256",
        "readme": "README-macOS.txt",
        "metadata": "install-metadata.json",
        "version_json": MACOS_VERSION_JSON_RELATIVE_PATH,
    }


def _parse_sha256_file(path: str) -> tuple[str, str]:
    text = ""
    with open(path, "r", encoding="utf-8") as f:
        text = (f.read() or "").strip()
    if not text:
        _die(f"empty sha256 file: {path}")
    parts = text.split()
    if len(parts) < 2:
        _die(f"invalid sha256 format: {path}")
    return parts[0].strip().lower(), parts[-1].strip()


def _assert_version_meta(payload: dict, *, version: str, expected_commit: str = ""):
    if str(payload.get("version") or "").strip() != version:
        _die(f"version mismatch: expected {version}, got {payload.get('version')}")
    if str(payload.get("channel") or "").strip() != "stable":
        _die(f"unexpected channel in version metadata: {payload.get('channel')}")
    if not str(payload.get("build_time") or "").strip():
        _die("missing build_time in version metadata")
    if expected_commit and str(payload.get("commit") or "").strip() != expected_commit:
        _die(f"commit mismatch: expected {expected_commit}, got {payload.get('commit')}")


def _assert_install_metadata(payload: dict, *, version: str, expected_commit: str = "", expected_arch: str = "", expected_runner_label: str = ""):
    expected_names = _expected_artifact_names(version)
    if str(payload.get("platform") or "").strip() != "macos":
        _die(f"unexpected platform: {payload.get('platform')}")
    if str(payload.get("version") or "").strip() != version:
        _die(f"metadata version mismatch: expected {version}, got {payload.get('version')}")
    if str(payload.get("channel") or "").strip() != "stable":
        _die(f"unexpected metadata channel: {payload.get('channel')}")
    if expected_commit and str(payload.get("commit") or "").strip() != expected_commit:
        _die(f"metadata commit mismatch: expected {expected_commit}, got {payload.get('commit')}")
    if not str(payload.get("build_time") or "").strip():
        _die("missing build_time in install metadata")
    if str(payload.get("app_name") or "").strip() != APP_NAME:
        _die(f"app_name mismatch: {payload.get('app_name')}")
    if str(payload.get("bundle_name") or "").strip() != APP_BUNDLE_NAME:
        _die(f"bundle_name mismatch: {payload.get('bundle_name')}")
    if str(payload.get("bundle_identifier") or "").strip() != APP_BUNDLE_ID:
        _die(f"bundle identifier mismatch: {payload.get('bundle_identifier')}")
    if str(payload.get("install_mode") or "").strip() != "manual_dmg":
        _die(f"install_mode mismatch: {payload.get('install_mode')}")
    if str(payload.get("install_target") or "").strip() != INSTALL_TARGET:
        _die(f"install_target mismatch: {payload.get('install_target')}")
    if str(payload.get("recommended_install_target") or "").strip() != INSTALL_TARGET:
        _die(f"recommended_install_target mismatch: {payload.get('recommended_install_target')}")
    if str(payload.get("user_install_target") or "").strip() != USER_INSTALL_TARGET:
        _die(f"user_install_target mismatch: {payload.get('user_install_target')}")
    if str(payload.get("data_root") or "").strip() != DATA_ROOT:
        _die(f"data_root mismatch: {payload.get('data_root')}")
    if str(payload.get("config_path") or "").strip() != CONFIG_PATH:
        _die(f"config_path mismatch: {payload.get('config_path')}")
    if bool(payload.get("supports_internal_updater")):
        _die("supports_internal_updater must be false for mac release")
    if not bool(payload.get("requires_system_python")):
        _die("requires_system_python must be true for mac release")
    build_arch = _normalized_arch(payload.get("build_arch"))
    if not build_arch:
        _die("missing build_arch in install metadata")
    if expected_arch and build_arch != _normalized_arch(expected_arch):
        _die(f"build_arch mismatch: expected {_normalized_arch(expected_arch)}, got {build_arch}")
    runner_label = str(payload.get("runner_label") or "").strip()
    if expected_runner_label and runner_label != expected_runner_label:
        _die(f"runner_label mismatch: expected {expected_runner_label}, got {runner_label}")
    if bool(payload.get("developer_id_signed")):
        _die("developer_id_signed must be false for mac release")
    if bool(payload.get("apple_developer_signed")):
        _die("apple_developer_signed must be false for mac release")
    if bool(payload.get("notarized")):
        _die("notarized must be false for mac release")
    if not bool(payload.get("pyinstaller_may_ad_hoc_sign")):
        _die("pyinstaller_may_ad_hoc_sign must be true for mac release")
    artifact_names = dict(payload.get("artifact_names") or {})
    for key, value in expected_names.items():
        if str(artifact_names.get(key) or "").strip() != value:
            _die(f"artifact_names.{key} mismatch: expected {value}, got {artifact_names.get(key)}")


def _assert_info_plist(payload: dict, *, version: str):
    if str(payload.get("CFBundleIdentifier") or "").strip() != APP_BUNDLE_ID:
        _die(f"Info.plist bundle identifier mismatch: {payload.get('CFBundleIdentifier')}")
    if str(payload.get("CFBundleShortVersionString") or "").strip() != version:
        _die(f"CFBundleShortVersionString mismatch: {payload.get('CFBundleShortVersionString')}")
    if str(payload.get("CFBundleVersion") or "").strip() != version:
        _die(f"CFBundleVersion mismatch: {payload.get('CFBundleVersion')}")


def _bundle_symlink_entries(app_path: str) -> list[str]:
    found = []
    for root, dirs, files in os.walk(app_path):
        for name in list(dirs) + list(files):
            path = os.path.join(root, name)
            if not os.path.islink(path):
                continue
            found.append(os.path.relpath(path, app_path))
    return sorted(found)


def _assert_preserved_bundle_symlinks(app_path: str):
    symlink_entries = _bundle_symlink_entries(app_path)
    if not symlink_entries:
        _die(f"app bundle is missing preserved internal symlinks: {app_path}")
    expected_prefixes = ("Contents/Frameworks/", "Contents/Resources/")
    if not any(entry.startswith(expected_prefixes) for entry in symlink_entries):
        _die(
            "app bundle is missing preserved Frameworks/Resources symlinks: "
            f"{app_path} -> {symlink_entries}"
        )


def _assert_codesign_integrity(app_path: str):
    _run(["codesign", "--verify", "--deep", "--strict", app_path])


def _detect_binary_arch(exe_path: str) -> str:
    lipo = _run(["lipo", "-info", exe_path])
    output = (lipo.stdout or b"").decode("utf-8", "replace").strip()
    lowered = output.lower()
    if "are: x86_64 arm64" in lowered or "are: arm64 x86_64" in lowered:
        return "universal2"
    if "architecture: arm64" in lowered or lowered.endswith(" arm64"):
        return "arm64"
    if "architecture: x86_64" in lowered or lowered.endswith(" x86_64"):
        return "x86_64"
    _die(f"unable to determine macOS binary architecture from lipo output: {output}")


def _assert_release_bundle(app_path: str, *, version: str, expected_commit: str = "", expected_arch: str = ""):
    if not os.path.isdir(app_path):
        _die(f"app bundle not found: {app_path}")
    exe = os.path.join(app_path, "Contents", "MacOS", "GenericAgentLauncher")
    version_json = os.path.join(app_path, *MACOS_VERSION_JSON_RELATIVE_PATH.split("/"))
    info_plist = os.path.join(app_path, "Contents", "Info.plist")
    if not os.path.isfile(exe):
        _die(f"app executable missing: {exe}")
    if not os.path.isfile(version_json):
        _die(f"version.json missing: {version_json}")
    if not os.path.isfile(info_plist):
        _die(f"Info.plist missing: {info_plist}")
    _assert_version_meta(_load_json(version_json), version=version, expected_commit=expected_commit)
    _assert_info_plist(_load_plist(info_plist), version=version)
    detected_arch = _detect_binary_arch(exe)
    if expected_arch and detected_arch != _normalized_arch(expected_arch):
        _die(f"packaged executable architecture mismatch: expected {_normalized_arch(expected_arch)}, got {detected_arch}")
    _assert_preserved_bundle_symlinks(app_path)
    _assert_codesign_integrity(app_path)


def _attach_dmg(dmg_path: str) -> str:
    result = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", dmg_path])
    payload = plistlib.loads(result.stdout)
    for entity in payload.get("system-entities", []) or []:
        mount_point = str(entity.get("mount-point") or "").strip()
        if mount_point:
            return mount_point
    _die(f"failed to resolve mount point from dmg attach output: {dmg_path}")


def _detach_dmg(mount_point: str):
    if not mount_point:
        return
    try:
        _run(["hdiutil", "detach", mount_point])
    except SystemExit:
        _run(["hdiutil", "detach", "-force", mount_point])


def _assert_mounted_layout(
    mount_point: str,
    *,
    version: str,
    expected_commit: str,
    expected_arch: str = "",
    readme_bytes: bytes,
    metadata_bytes: bytes,
):
    expected_names = _expected_artifact_names(version)
    app_path = os.path.join(mount_point, expected_names["app_bundle"])
    applications_alias = os.path.join(mount_point, "Applications")
    readme_path = os.path.join(mount_point, expected_names["readme"])
    metadata_path = os.path.join(mount_point, expected_names["metadata"])

    if not os.path.isdir(app_path):
        _die(f"mounted dmg is missing app bundle: {app_path}")
    if not os.path.exists(applications_alias):
        _die(f"mounted dmg is missing Applications alias: {applications_alias}")
    if not os.path.islink(applications_alias):
        _die(f"Applications alias is not a symlink: {applications_alias}")
    if os.readlink(applications_alias) != "/Applications":
        _die(f"Applications alias points to unexpected target: {os.readlink(applications_alias)}")
    if not os.path.isfile(readme_path):
        _die(f"mounted dmg is missing README-macOS.txt: {readme_path}")
    if not os.path.isfile(metadata_path):
        _die(f"mounted dmg is missing install-metadata.json: {metadata_path}")
    if open(readme_path, "rb").read() != readme_bytes:
        _die("mounted README-macOS.txt does not match release copy")
    if open(metadata_path, "rb").read() != metadata_bytes:
        _die("mounted install-metadata.json does not match release copy")
    _assert_release_bundle(
        app_path,
        version=version,
        expected_commit=expected_commit,
        expected_arch=expected_arch,
    )


def main() -> int:
    if sys.platform != "darwin":
        _die("tools/validate_macos_release.py must run on macOS")

    args = _parse_args()
    version = str(args.version or "").strip().lstrip("v")
    expected_commit = str(args.expected_commit or "").strip()
    expected_arch = _normalized_arch(args.expected_arch)
    expected_runner_label = str(args.expected_runner_label or "").strip()
    if not version:
        _die("missing --version")

    macos_dir = os.path.join(os.path.abspath(str(args.out or "release")), version, "macos")
    expected_names = _expected_artifact_names(version)
    required = {
        "app_bundle": os.path.join(macos_dir, expected_names["app_bundle"]),
        "dmg": os.path.join(macos_dir, expected_names["dmg"]),
        "sha256": os.path.join(macos_dir, expected_names["sha256"]),
        "readme": os.path.join(macos_dir, expected_names["readme"]),
        "metadata": os.path.join(macos_dir, expected_names["metadata"]),
    }
    for label, path in required.items():
        if not os.path.exists(path):
            _die(f"missing macOS release asset ({label}): {path}")

    metadata = _load_json(required["metadata"])
    _assert_install_metadata(
        metadata,
        version=version,
        expected_commit=expected_commit,
        expected_arch=expected_arch,
        expected_runner_label=expected_runner_label,
    )
    _assert_release_bundle(
        required["app_bundle"],
        version=version,
        expected_commit=expected_commit,
        expected_arch=expected_arch,
    )

    expected_sha256, filename = _parse_sha256_file(required["sha256"])
    if filename != expected_names["dmg"]:
        _die(f"sha256 file references unexpected dmg name: {filename}")
    actual_sha256 = _sha256_file(required["dmg"])
    if actual_sha256 != expected_sha256:
        _die(f"dmg sha256 mismatch: expected {expected_sha256}, got {actual_sha256}")

    mount_point = ""
    try:
        mount_point = _attach_dmg(required["dmg"])
        with open(required["readme"], "rb") as f:
            readme_bytes = f.read()
        with open(required["metadata"], "rb") as f:
            metadata_bytes = f.read()
        _assert_mounted_layout(
            mount_point,
            version=version,
            expected_commit=expected_commit,
            expected_arch=expected_arch,
            readme_bytes=readme_bytes,
            metadata_bytes=metadata_bytes,
        )
    finally:
        _detach_dmg(mount_point)

    print(f"macOS release contract validated: {macos_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
