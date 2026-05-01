from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock


def _load_validate_macos_release_module():
    root = os.path.dirname(os.path.dirname(__file__))
    path = os.path.join(root, "tools", "validate_macos_release.py")
    spec = importlib.util.spec_from_file_location("validate_macos_release_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ValidateMacOSReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_validate_macos_release_module()

    def test_expected_artifact_names_match_public_contract(self):
        names = self.mod._expected_artifact_names("1.2.3")
        self.assertEqual(names["app_bundle"], "GenericAgent Launcher.app")
        self.assertEqual(names["dmg"], "GenericAgentLauncher-macos-1.2.3.dmg")
        self.assertEqual(names["sha256"], "GenericAgentLauncher-macos-1.2.3.sha256")
        self.assertEqual(names["readme"], "README-macOS.txt")
        self.assertEqual(names["metadata"], "install-metadata.json")
        self.assertEqual(names["version_json"], "Contents/Resources/version.json")

    def test_parse_sha256_file_reads_hash_and_filename(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "sample.sha256")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("abc123  GenericAgentLauncher-macos-1.2.3.dmg\n")
            digest, filename = self.mod._parse_sha256_file(fp)
        self.assertEqual(digest, "abc123")
        self.assertEqual(filename, "GenericAgentLauncher-macos-1.2.3.dmg")

    def test_assert_install_metadata_accepts_expected_contract(self):
        payload = {
            "platform": "macos",
            "version": "1.2.3",
            "channel": "stable",
            "commit": "abc123",
            "build_time": "2026-04-27T12:34:56+00:00",
            "app_name": "GenericAgent Launcher",
            "bundle_name": "GenericAgent Launcher.app",
            "bundle_identifier": "com.dhdbv.genericagentlauncher",
            "install_mode": "manual_dmg",
            "install_target": "/Applications/GenericAgent Launcher.app",
            "recommended_install_target": "/Applications/GenericAgent Launcher.app",
            "user_install_target": "~/Applications/GenericAgent Launcher.app",
            "data_root": "~/Library/Application Support/GenericAgentLauncher",
            "config_path": "~/Library/Application Support/GenericAgentLauncher/config/launcher_config.json",
            "supports_internal_updater": False,
            "requires_system_python": True,
            "build_arch": "x86_64",
            "runner_label": "macos-15-intel",
            "developer_id_signed": False,
            "apple_developer_signed": False,
            "notarized": False,
            "pyinstaller_may_ad_hoc_sign": True,
            "artifact_names": self.mod._expected_artifact_names("1.2.3"),
        }
        self.mod._assert_install_metadata(
            payload,
            version="1.2.3",
            expected_commit="abc123",
            expected_arch="x86_64",
            expected_runner_label="macos-15-intel",
        )

    def test_assert_install_metadata_rejects_app_name_and_bundle_name_drift(self):
        payload = {
            "platform": "macos",
            "version": "1.2.3",
            "channel": "stable",
            "commit": "abc123",
            "build_time": "2026-04-27T12:34:56+00:00",
            "app_name": "Wrong Name",
            "bundle_name": "Wrong Name.app",
            "bundle_identifier": "com.dhdbv.genericagentlauncher",
            "install_mode": "manual_dmg",
            "install_target": "/Applications/GenericAgent Launcher.app",
            "recommended_install_target": "/Applications/GenericAgent Launcher.app",
            "user_install_target": "~/Applications/GenericAgent Launcher.app",
            "data_root": "~/Library/Application Support/GenericAgentLauncher",
            "config_path": "~/Library/Application Support/GenericAgentLauncher/config/launcher_config.json",
            "supports_internal_updater": False,
            "requires_system_python": True,
            "build_arch": "x86_64",
            "runner_label": "macos-15-intel",
            "developer_id_signed": False,
            "apple_developer_signed": False,
            "notarized": False,
            "pyinstaller_may_ad_hoc_sign": True,
            "artifact_names": self.mod._expected_artifact_names("1.2.3"),
        }
        with self.assertRaises(SystemExit) as ctx:
            self.mod._assert_install_metadata(
                payload,
                version="1.2.3",
                expected_commit="abc123",
                expected_arch="x86_64",
                expected_runner_label="macos-15-intel",
            )
        self.assertIn("app_name mismatch", str(ctx.exception))

    def test_assert_version_meta_accepts_expected_contract(self):
        payload = {
            "version": "1.2.3",
            "channel": "stable",
            "commit": "abc123",
            "build_time": "2026-04-27T12:34:56+00:00",
        }
        self.mod._assert_version_meta(payload, version="1.2.3", expected_commit="abc123")

    def test_assert_release_bundle_requires_version_json_and_plist(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            contents = os.path.join(app_dir, "Contents")
            macos = os.path.join(contents, "MacOS")
            resources = os.path.join(contents, "Resources")
            frameworks = os.path.join(contents, "Frameworks")
            os.makedirs(macos, exist_ok=True)
            os.makedirs(frameworks, exist_ok=True)
            os.makedirs(resources, exist_ok=True)
            with open(os.path.join(macos, "GenericAgentLauncher"), "w", encoding="utf-8") as f:
                f.write("stub")
            with open(os.path.join(resources, "version.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "1.2.3",
                        "channel": "stable",
                        "commit": "abc123",
                        "build_time": "2026-04-27T12:34:56+00:00",
                    },
                    f,
                )
            with open(os.path.join(contents, "Info.plist"), "wb") as f:
                import plistlib

                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.dhdbv.genericagentlauncher",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "1.2.3",
                    },
                    f,
                )
            symlink_target = os.path.join(resources, "payload.txt")
            symlink_path = os.path.join(frameworks, "payload-link.txt")
            with open(symlink_target, "w", encoding="utf-8") as f:
                f.write("payload")
            try:
                os.symlink(symlink_target, symlink_path)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable in this environment: {exc}")
            with mock.patch.object(self.mod, "_detect_binary_arch", return_value="x86_64"), mock.patch.object(
                self.mod, "_assert_codesign_integrity"
            ) as codesign_check:
                self.mod._assert_release_bundle(app_dir, version="1.2.3", expected_commit="abc123", expected_arch="x86_64")
            codesign_check.assert_called_once_with(app_dir)

    def test_assert_release_bundle_requires_preserved_internal_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            contents = os.path.join(app_dir, "Contents")
            macos = os.path.join(contents, "MacOS")
            resources = os.path.join(contents, "Resources")
            os.makedirs(macos, exist_ok=True)
            os.makedirs(resources, exist_ok=True)
            with open(os.path.join(macos, "GenericAgentLauncher"), "w", encoding="utf-8") as f:
                f.write("stub")
            with open(os.path.join(resources, "version.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "1.2.3",
                        "channel": "stable",
                        "commit": "abc123",
                        "build_time": "2026-04-27T12:34:56+00:00",
                    },
                    f,
                )
            with open(os.path.join(contents, "Info.plist"), "wb") as f:
                import plistlib

                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.dhdbv.genericagentlauncher",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "1.2.3",
                    },
                    f,
                )

            with self.assertRaises(SystemExit) as ctx:
                with mock.patch.object(self.mod, "_detect_binary_arch", return_value="x86_64"), mock.patch.object(
                    self.mod, "_assert_codesign_integrity"
                ):
                    self.mod._assert_release_bundle(app_dir, version="1.2.3", expected_commit="abc123", expected_arch="x86_64")

        self.assertIn("missing preserved internal symlinks", str(ctx.exception))

    def test_assert_release_bundle_requires_frameworks_or_resources_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            contents = os.path.join(app_dir, "Contents")
            macos = os.path.join(contents, "MacOS")
            resources = os.path.join(contents, "Resources")
            helpers = os.path.join(contents, "Helpers")
            os.makedirs(macos, exist_ok=True)
            os.makedirs(resources, exist_ok=True)
            os.makedirs(helpers, exist_ok=True)
            with open(os.path.join(macos, "GenericAgentLauncher"), "w", encoding="utf-8") as f:
                f.write("stub")
            with open(os.path.join(resources, "version.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": "1.2.3",
                        "channel": "stable",
                        "commit": "abc123",
                        "build_time": "2026-04-27T12:34:56+00:00",
                    },
                    f,
                )
            with open(os.path.join(contents, "Info.plist"), "wb") as f:
                import plistlib

                plistlib.dump(
                    {
                        "CFBundleIdentifier": "com.dhdbv.genericagentlauncher",
                        "CFBundleShortVersionString": "1.2.3",
                        "CFBundleVersion": "1.2.3",
                    },
                    f,
                )
            helper_target = os.path.join(helpers, "payload.txt")
            helper_link = os.path.join(helpers, "payload-link.txt")
            with open(helper_target, "w", encoding="utf-8") as f:
                f.write("payload")
            try:
                os.symlink(helper_target, helper_link)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable in this environment: {exc}")

            with self.assertRaises(SystemExit) as ctx:
                with mock.patch.object(self.mod, "_detect_binary_arch", return_value="x86_64"), mock.patch.object(
                    self.mod, "_assert_codesign_integrity"
                ):
                    self.mod._assert_release_bundle(app_dir, version="1.2.3", expected_commit="abc123", expected_arch="x86_64")

        self.assertIn("missing preserved Frameworks/Resources symlinks", str(ctx.exception))

    def test_assert_codesign_integrity_runs_strict_verify(self):
        with mock.patch.object(self.mod, "_run", return_value=mock.Mock()) as run:
            self.mod._assert_codesign_integrity("/tmp/GenericAgent Launcher.app")
        run.assert_called_once_with(["codesign", "--verify", "--deep", "--strict", "/tmp/GenericAgent Launcher.app"])

    def test_assert_mounted_layout_propagates_expected_arch_to_bundle_check(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app")
            os.makedirs(app_dir, exist_ok=True)
            applications_link = os.path.join(td, "Applications")
            readme_path = os.path.join(td, "README-macOS.txt")
            metadata_path = os.path.join(td, "install-metadata.json")
            with open(readme_path, "wb") as f:
                f.write(b"readme")
            with open(metadata_path, "wb") as f:
                f.write(b"metadata")
            try:
                os.symlink("/Applications", applications_link)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symlink creation is unavailable in this environment: {exc}")

            with mock.patch.object(self.mod, "_assert_release_bundle") as bundle_check:
                self.mod._assert_mounted_layout(
                    td,
                    version="1.2.3",
                    expected_commit="abc123",
                    expected_arch="x86_64",
                    readme_bytes=b"readme",
                    metadata_bytes=b"metadata",
                )

            bundle_check.assert_called_once_with(
                app_dir,
                version="1.2.3",
                expected_commit="abc123",
                expected_arch="x86_64",
            )
