from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import tempfile
import types
import unittest
from unittest import mock


def _load_build_macos_release_module():
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "tools", "build_macos_release.py")
    spec = importlib.util.spec_from_file_location("build_macos_release_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BuildMacOSReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_build_macos_release_module()

    def test_install_readme_mentions_manual_dmg_flow(self):
        text = self.mod._install_readme_text(
            "1.2.3",
            version_meta={"version": "1.2.3", "commit": "abc123", "build_time": "2026-04-27T12:34:56+00:00"},
        )
        self.assertIn("Version: 1.2.3", text)
        self.assertIn("Commit: abc123", text)
        self.assertIn("Build Time (UTC): 2026-04-27T12:34:56+00:00", text)
        self.assertIn("drag GenericAgent Launcher.app into the Applications alias", text)
        self.assertIn("/Applications/GenericAgent Launcher.app", text)
        self.assertIn("~/Applications/GenericAgent Launcher.app", text)
        self.assertIn("actual install path", text)
        self.assertIn("System Settings -> Privacy & Security -> Open Anyway", text)
        self.assertIn("not Apple Developer signed and is not notarized", text)
        self.assertIn("ad-hoc signing for runtime compatibility", text)
        self.assertIn("~/Library/Application Support/GenericAgentLauncher", text)
        self.assertIn("system Python", text)
        self.assertIn("manual upgrades", text)

    def test_install_metadata_captures_install_contract(self):
        with mock.patch.object(self.mod, "_current_build_arch", return_value="x86_64"):
            with mock.patch.dict(self.mod.os.environ, {"GA_MACOS_RUNNER_LABEL": "macos-15-intel"}, clear=False):
                payload = self.mod._install_metadata(
                    "1.2.3",
                    dmg_name="GenericAgentLauncher-macos-1.2.3.dmg",
                    version_meta={"version": "1.2.3", "channel": "stable", "commit": "abc123", "build_time": "2026-04-27T12:34:56+00:00"},
                )
        self.assertEqual(payload["platform"], "macos")
        self.assertEqual(payload["channel"], "stable")
        self.assertEqual(payload["commit"], "abc123")
        self.assertEqual(payload["build_time"], "2026-04-27T12:34:56+00:00")
        self.assertEqual(payload["install_mode"], "manual_dmg")
        self.assertEqual(payload["install_target"], "/Applications/GenericAgent Launcher.app")
        self.assertEqual(payload["recommended_install_target"], "/Applications/GenericAgent Launcher.app")
        self.assertEqual(payload["user_install_target"], "~/Applications/GenericAgent Launcher.app")
        self.assertEqual(payload["data_root"], "~/Library/Application Support/GenericAgentLauncher")
        self.assertFalse(payload["supports_internal_updater"])
        self.assertTrue(payload["requires_system_python"])
        self.assertEqual(payload["build_arch"], "x86_64")
        self.assertEqual(payload["runner_label"], "macos-15-intel")
        self.assertFalse(payload["developer_id_signed"])
        self.assertFalse(payload["apple_developer_signed"])
        self.assertFalse(payload["notarized"])
        self.assertTrue(payload["pyinstaller_may_ad_hoc_sign"])
        self.assertEqual(payload["artifact_names"]["sha256"], "GenericAgentLauncher-macos-1.2.3.sha256")
        self.assertEqual(payload["artifact_names"]["readme"], "README-macOS.txt")
        self.assertEqual(payload["artifact_names"]["metadata"], "install-metadata.json")
        self.assertEqual(payload["artifact_names"]["version_json"], "Contents/Resources/version.json")

    def test_copy_app_bundle_uses_copytree_with_symlink_preservation(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src.app")
            dst = os.path.join(td, "dst.app")
            os.makedirs(src, exist_ok=True)
            with mock.patch.object(self.mod.shutil, "copytree", return_value=dst) as copytree:
                out = self.mod._copy_app_bundle(src, dst, dirs_exist_ok=True)
            copytree.assert_called_once_with(src, dst, symlinks=True, dirs_exist_ok=True)
            self.assertEqual(out, dst)

    def test_copy_app_bundle_preserves_internal_symlinks_when_supported(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src.app")
            dst = os.path.join(td, "dst.app")
            os.makedirs(os.path.join(src, "Contents", "Resources"), exist_ok=True)
            target = os.path.join(src, "Contents", "Resources", "payload.txt")
            link = os.path.join(src, "Contents", "Frameworks", "payload-link.txt")
            os.makedirs(os.path.dirname(link), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write("payload")
            try:
                os.symlink(target, link)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable in this environment: {exc}")

            self.mod._copy_app_bundle(src, dst)
            copied_link = os.path.join(dst, "Contents", "Frameworks", "payload-link.txt")
            self.assertTrue(os.path.islink(copied_link))
            self.assertEqual(os.readlink(copied_link), target)

    def test_write_release_support_files_creates_readme_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            readme_path, metadata_path = self.mod._write_release_support_files(
                td,
                version="9.9.9-test",
                dmg_name="GenericAgentLauncher-macos-9.9.9-test.dmg",
                version_meta={"version": "9.9.9-test", "channel": "stable", "commit": "deadbeef", "build_time": "2026-04-27T12:00:00+00:00"},
            )
            self.assertTrue(os.path.isfile(readme_path))
            self.assertTrue(os.path.isfile(metadata_path))
            with open(readme_path, "r", encoding="utf-8") as f:
                readme = f.read()
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            self.assertIn("Version: 9.9.9-test", readme)
            self.assertIn("Commit: deadbeef", readme)
            self.assertEqual(metadata["version"], "9.9.9-test")
            self.assertEqual(metadata["commit"], "deadbeef")
            self.assertEqual(metadata["artifact_names"]["dmg"], "GenericAgentLauncher-macos-9.9.9-test.dmg")

    def test_write_bundle_info_plist_versions_updates_finder_version_fields(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            contents_dir = os.path.join(app_dir, "Contents")
            os.makedirs(contents_dir, exist_ok=True)
            info_plist = os.path.join(contents_dir, "Info.plist")
            with open(info_plist, "wb") as f:
                plistlib.dump({"CFBundleName": "GenericAgent Launcher"}, f)
            written = self.mod._write_bundle_info_plist_versions(app_dir, "2.3.4")
            self.assertEqual(written, info_plist)
            with open(info_plist, "rb") as f:
                payload = plistlib.load(f)
            self.assertEqual(payload["CFBundleShortVersionString"], "2.3.4")
            self.assertEqual(payload["CFBundleVersion"], "2.3.4")

    def test_ad_hoc_codesign_bundle_reapplies_signature_after_bundle_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            os.makedirs(app_dir, exist_ok=True)
            with mock.patch.object(self.mod, "_run", return_value=mock.Mock()) as run:
                signed = self.mod._ad_hoc_codesign_bundle(app_dir)
            expected = os.path.abspath(app_dir)
            self.assertEqual(signed, expected)
            run.assert_called_once_with(["codesign", "--force", "--deep", "--sign", "-", expected])

    def test_ad_hoc_codesign_bundle_requires_existing_app_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            missing = os.path.join(td, "Missing.app")
            with self.assertRaises(SystemExit) as ctx:
                self.mod._ad_hoc_codesign_bundle(missing)
        self.assertIn("app bundle not found for codesign", str(ctx.exception))

    def test_resolve_path_anchors_relative_paths_to_repo_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.join(td, "repo")
            custom = os.path.join(td, "custom", "release")
            self.assertEqual(self.mod._resolve_path(root, "dist"), os.path.join(root, "dist"))
            self.assertEqual(self.mod._resolve_path(root, ""), os.path.abspath(root))
            self.assertEqual(self.mod._resolve_path(root, custom), os.path.abspath(custom))

    def test_prepare_macos_bundle_icon_uses_shared_icon_builder(self):
        calls = {}
        original = self.mod.macos_icon_assets

        class DummyMacOSIconAssets:
            @staticmethod
            def default_icon_svg_path(root):
                calls["svg_root"] = root
                return os.path.join(root, "assets", "launcher_app_icon.svg")

            @staticmethod
            def default_icns_output_path(root):
                calls["icns_root"] = root
                return os.path.join(root, "build", "macos-icon", "GenericAgentLauncher.icns")

            @staticmethod
            def build_icns(*, svg_path, icns_path):
                calls["svg_path"] = svg_path
                calls["icns_path"] = icns_path
                return icns_path

        with tempfile.TemporaryDirectory() as td:
            root = os.path.join(td, "repo")
            self.mod.macos_icon_assets = DummyMacOSIconAssets
            try:
                out = self.mod._prepare_macos_bundle_icon(root)
            finally:
                self.mod.macos_icon_assets = original

            self.assertEqual(calls["svg_root"], os.path.abspath(root))
            self.assertEqual(calls["icns_root"], os.path.abspath(root))
            self.assertEqual(calls["svg_path"], os.path.join(os.path.abspath(root), "assets", "launcher_app_icon.svg"))
            self.assertEqual(calls["icns_path"], os.path.join(os.path.abspath(root), "build", "macos-icon", "GenericAgentLauncher.icns"))
            self.assertEqual(out, os.path.join(os.path.abspath(root), "build", "macos-icon", "GenericAgentLauncher.icns"))

    def test_main_routes_custom_dist_dir_into_pyinstaller(self):
        with tempfile.TemporaryDirectory() as td:
            root = os.path.join(td, "repo")
            custom_dist = os.path.join(td, "artifacts", "dist")
            captured = {}

            def fake_run(cmd, *, cwd=None):
                captured["cmd"] = list(cmd)
                captured["cwd"] = cwd
                raise SystemExit("stop-after-pyinstaller")

            args = types.SimpleNamespace(version="1.2.3", dist=custom_dist, out="release", commit="abc123")
            with mock.patch.object(self.mod, "_repo_root", return_value=root), mock.patch.object(
                self.mod, "_parse_args", return_value=args
            ), mock.patch.object(
                self.mod, "_prepare_macos_bundle_icon", return_value=os.path.join(root, "build", "GenericAgentLauncher.icns")
            ), mock.patch.object(
                self.mod, "_run", side_effect=fake_run
            ), mock.patch.object(self.mod.sys, "platform", "darwin"):
                with self.assertRaises(SystemExit) as ctx:
                    self.mod.main()

        self.assertEqual(str(ctx.exception), "stop-after-pyinstaller")
        self.assertEqual(captured["cwd"], root)
        self.assertEqual(
            captured["cmd"],
            [
                self.mod.sys.executable,
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                "--distpath",
                os.path.abspath(custom_dist),
                os.path.join(root, "GenericAgentLauncher.mac.spec"),
            ],
        )
