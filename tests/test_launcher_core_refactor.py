from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import bridge
import launcher_bootstrap
from PySide6.QtCore import QRect
from PySide6.QtTest import QTest
from launcher_app import core as lz
from launcher_app import window as launcher_window
from launcher_core_parts import constants
from launcher_core_parts import model_api, runtime, update_manager
from qt_chat_parts import api_editor
from qt_chat_parts import common as chat_common
from qt_chat_parts import bridge_runtime
from qt_chat_parts import dependency_runtime
from qt_chat_parts.api_editor import ApiEditorMixin
from qt_chat_parts.bridge_runtime import BridgeRuntimeMixin
from qt_chat_parts.chat_view import ChatViewMixin
from qt_chat_parts import channel_runtime
from qt_chat_parts.dependency_runtime import DependencyRuntimeMixin
from qt_chat_parts import personal_usage
from qt_chat_parts import schedule_runtime
from qt_chat_parts import settings_panel
from qt_chat_parts import navigation
from qt_chat_parts import sidebar_sessions
from qt_chat_parts.downloads import DownloadMixin
from qt_chat_parts.navigation import NavigationMixin
from qt_chat_parts.channel_runtime import ChannelRuntimeMixin
from qt_chat_parts.personal_usage import PersonalUsageMixin
from qt_chat_parts.schedule_runtime import ScheduleRuntimeMixin
from qt_chat_parts.settings_panel import SettingsPanelMixin
from qt_chat_parts.session_shell import SessionShellMixin
from qt_chat_parts.sidebar_sessions import SidebarSessionsMixin


class LauncherCoreFacadeTests(unittest.TestCase):
    @staticmethod
    def _pid_exists(pid: int) -> bool:
        target = int(pid or 0)
        if target <= 0:
            return False
        try:
            os.kill(target, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def test_facade_exports_expected_symbols(self):
        required = [
            "load_config",
            "save_config",
            "_resolve_config_path",
            "_make_config_relative_path",
            "_resolve_configured_python_exe",
            "_make_python_exe_config_path",
            "_normalize_token_usage_inplace",
            "terminate_process_tree",
            "list_scheduled_tasks",
            "tail_scheduler_log",
            "fold_turns",
            "serialize_mykey_py",
            "SIMPLE_FORMAT_RULES",
            "qrcode",
            "requests",
            "urlparse",
        ]
        for name in required:
            self.assertTrue(hasattr(lz, name), msg=f"missing symbol: {name}")

    def test_create_update_job_normalizes_proxy_url(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(update_manager, "UPDATE_JOBS_DIR", td):
            created = update_manager.create_update_job(
                {
                    "target_version": "1.2.4",
                    "package_url": "https://example.com/update.zip",
                    "package_sha256": "a" * 64,
                    "proxy_url": "127.0.0.1:7890",
                }
            )

            self.assertEqual(created["job"]["proxy_url"], "http://127.0.0.1:7890")
            with open(created["job_path"], "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["proxy_url"], "http://127.0.0.1:7890")

    def test_runtime_path_helpers_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            original_app_dir = runtime.APP_DIR
            runtime.APP_DIR = td
            try:
                nested = os.path.join(td, "agent", "launch.pyw")
                os.makedirs(os.path.dirname(nested), exist_ok=True)
                with open(nested, "w", encoding="utf-8") as f:
                    f.write("# test")

                rel = runtime._make_config_relative_path(nested)
                self.assertEqual(rel, os.path.join("agent", "launch.pyw"))

                resolved = runtime._resolve_config_path(rel)
                self.assertEqual(os.path.normpath(resolved), os.path.normpath(nested))
            finally:
                runtime.APP_DIR = original_app_dir

    def test_runtime_python_exe_helpers_round_trip_project_relative_path(self):
        with tempfile.TemporaryDirectory() as td:
            original_app_dir = runtime.APP_DIR
            original_data_root = runtime.DATA_ROOT
            runtime.APP_DIR = os.path.join(td, "launcher")
            runtime.DATA_ROOT = os.path.join(td, "data")
            agent_dir = os.path.join(td, "agent")
            python_exe = os.path.join(agent_dir, "venv", "bin", "python")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")
            try:
                rel = runtime._make_python_exe_config_path(python_exe, agent_dir=agent_dir)
                self.assertEqual(rel, os.path.join("venv", "bin", "python"))

                resolved = runtime._resolve_configured_python_exe(rel, agent_dir=agent_dir)
                self.assertEqual(os.path.normpath(resolved), os.path.normpath(python_exe))
            finally:
                runtime.APP_DIR = original_app_dir
                runtime.DATA_ROOT = original_data_root

    def test_runtime_python_exe_helpers_expand_user_home_path(self):
        with tempfile.TemporaryDirectory() as td:
            original_app_dir = runtime.APP_DIR
            original_data_root = runtime.DATA_ROOT
            runtime.APP_DIR = os.path.join(td, "launcher")
            runtime.DATA_ROOT = os.path.join(td, "data")
            agent_dir = os.path.join(td, "agent")
            home_dir = os.path.join(td, "home")
            python_exe = os.path.join(home_dir, "miniforge3", "bin", "python")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")
            try:
                with mock.patch.dict(
                    runtime.os.environ,
                    {"HOME": home_dir, "USERPROFILE": home_dir},
                    clear=False,
                ):
                    resolved = runtime._resolve_configured_python_exe("~/miniforge3/bin/python", agent_dir=agent_dir)
                    stored = runtime._make_python_exe_config_path("~/miniforge3/bin/python", agent_dir=agent_dir)
            finally:
                runtime.APP_DIR = original_app_dir
                runtime.DATA_ROOT = original_data_root

        self.assertEqual(os.path.normpath(resolved), os.path.normpath(python_exe))
        self.assertEqual(os.path.normpath(stored), os.path.normpath(python_exe))

    def test_runtime_python_exe_helpers_resolve_path_command_name(self):
        with tempfile.TemporaryDirectory() as td:
            original_app_dir = runtime.APP_DIR
            original_data_root = runtime.DATA_ROOT
            runtime.APP_DIR = os.path.join(td, "launcher")
            runtime.DATA_ROOT = os.path.join(td, "data")
            agent_dir = os.path.join(td, "agent")
            python_exe = os.path.join(td, "homebrew", "bin", "python3")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")
            try:
                with mock.patch.object(runtime.shutil, "which", return_value=python_exe):
                    resolved = runtime._resolve_configured_python_exe("python3", agent_dir=agent_dir)
                    stored = runtime._make_python_exe_config_path("python3", agent_dir=agent_dir)
            finally:
                runtime.APP_DIR = original_app_dir
                runtime.DATA_ROOT = original_data_root

        self.assertEqual(os.path.normpath(resolved), os.path.normpath(python_exe))
        self.assertEqual(os.path.normpath(stored), os.path.normpath(python_exe))

    def test_runtime_python_exe_helpers_ignore_non_python_command_name(self):
        with tempfile.TemporaryDirectory() as td:
            original_app_dir = runtime.APP_DIR
            original_data_root = runtime.DATA_ROOT
            runtime.APP_DIR = os.path.join(td, "launcher")
            runtime.DATA_ROOT = os.path.join(td, "data")
            agent_dir = os.path.join(td, "agent")
            try:
                with mock.patch.object(runtime.shutil, "which", return_value=os.path.join(td, "bin", "uv")) as which_mock:
                    resolved = runtime._resolve_configured_python_exe("uv", agent_dir=agent_dir)
            finally:
                runtime.APP_DIR = original_app_dir
                runtime.DATA_ROOT = original_data_root

        self.assertEqual(os.path.normpath(resolved), os.path.normpath(os.path.join(agent_dir, "uv")))
        which_mock.assert_not_called()

    def test_launcher_version_info_prefers_macos_resources_version_json(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app", "Contents", "MacOS")
            resources_dir = os.path.join(td, "GenericAgent Launcher.app", "Contents", "Resources")
            os.makedirs(app_dir, exist_ok=True)
            os.makedirs(resources_dir, exist_ok=True)
            with open(os.path.join(app_dir, "version.json"), "w", encoding="utf-8") as f:
                json.dump({"version": "0.9.0", "channel": "stable", "commit": "old", "build_time": "old-time"}, f)
            with open(os.path.join(resources_dir, "version.json"), "w", encoding="utf-8") as f:
                json.dump({"version": "1.2.3", "channel": "stable", "commit": "new", "build_time": "new-time"}, f)

            original_app_dir = runtime.APP_DIR
            runtime.APP_DIR = app_dir
            try:
                with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(runtime.sys, "frozen", False, create=True):
                    info = runtime.launcher_version_info()
            finally:
                runtime.APP_DIR = original_app_dir

        self.assertEqual(info["version"], "1.2.3")
        self.assertEqual(info["commit"], "new")

    def test_check_runtime_dependencies_from_locate_syncs_python_input_before_check(self):
        class DummyEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

            def setText(self, value):
                self._text = str(value)

        class DummyCombo:
            def currentData(self):
                return "uv"

        class DummyDependency(DependencyRuntimeMixin):
            _check_runtime_dependencies_from_locate = DependencyRuntimeMixin._check_runtime_dependencies_from_locate

            def __init__(self, agent_dir, python_text):
                self.agent_dir = agent_dir
                self.cfg = {}
                self.locate_path_edit = DummyEdit(agent_dir)
                self.locate_python_edit = DummyEdit(python_text)
                self.locate_dependency_installer_combo = DummyCombo()
                self.calls = []

            def _set_agent_dir(self, value):
                self.agent_dir = str(value)

            def _check_runtime_dependencies(self, **kwargs):
                self.calls.append(dict(kwargs))
                return True

        with tempfile.TemporaryDirectory() as td:
            python_exe = os.path.join(td, "venv", "bin", "python")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")

            dummy = DummyDependency(td, os.path.join("venv", "bin", "python"))
            saved = []
            with mock.patch.object(lz, "save_config", side_effect=lambda cfg: saved.append(dict(cfg))), mock.patch.object(
                lz, "is_valid_agent_dir", return_value=True
            ), mock.patch.object(
                dependency_runtime.QMessageBox, "warning"
            ) as warning_box:
                dummy._check_runtime_dependencies_from_locate()

        self.assertEqual(dummy.cfg["dependency_installer"], "uv")
        self.assertEqual(dummy.cfg["python_exe"], os.path.join("venv", "bin", "python"))
        self.assertEqual(dummy.locate_python_edit.text(), os.path.join("venv", "bin", "python"))
        self.assertTrue(saved)
        self.assertEqual(len(dummy.calls), 1)
        self.assertEqual(dummy.calls[0]["purpose"], "载入内核")
        self.assertTrue(dummy.calls[0]["ignore_cache"])
        warning_box.assert_not_called()

    def test_check_runtime_dependencies_from_locate_accepts_python3_command_name(self):
        class DummyEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

            def setText(self, value):
                self._text = str(value)

        class DummyCombo:
            def currentData(self):
                return "auto"

        class DummyDependency(DependencyRuntimeMixin):
            _check_runtime_dependencies_from_locate = DependencyRuntimeMixin._check_runtime_dependencies_from_locate

            def __init__(self, agent_dir, python_text):
                self.agent_dir = agent_dir
                self.cfg = {}
                self.locate_path_edit = DummyEdit(agent_dir)
                self.locate_python_edit = DummyEdit(python_text)
                self.locate_dependency_installer_combo = DummyCombo()
                self.calls = []

            def _set_agent_dir(self, value):
                self.agent_dir = str(value)

            def _check_runtime_dependencies(self, **kwargs):
                self.calls.append(dict(kwargs))
                return True

        with tempfile.TemporaryDirectory() as td:
            python_exe = os.path.join(td, "homebrew", "bin", "python3")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")

            dummy = DummyDependency(td, "python3")
            expected_cfg_path = os.path.join("homebrew", "bin", "python3")
            saved = []
            with mock.patch.object(runtime.shutil, "which", return_value=python_exe), mock.patch.object(
                lz, "save_config", side_effect=lambda cfg: saved.append(dict(cfg))
            ), mock.patch.object(
                lz, "is_valid_agent_dir", return_value=True
            ), mock.patch.object(
                dependency_runtime.QMessageBox, "warning"
            ) as warning_box:
                dummy._check_runtime_dependencies_from_locate()

        self.assertEqual(dummy.cfg["python_exe"], expected_cfg_path)
        self.assertEqual(dummy.locate_python_edit.text(), expected_cfg_path)
        self.assertTrue(saved)
        self.assertEqual(saved[-1]["python_exe"], expected_cfg_path)
        self.assertNotEqual(saved[-1]["python_exe"], "python3")
        self.assertEqual(len(dummy.calls), 1)
        warning_box.assert_not_called()

    def test_resolve_bridge_python_supports_project_relative_python_exe(self):
        class DummyBridge(BridgeRuntimeMixin):
            _resolve_bridge_python = BridgeRuntimeMixin._resolve_bridge_python

            def __init__(self, agent_dir, python_exe):
                self.agent_dir = agent_dir
                self.cfg = {"python_exe": os.path.join("venv", "bin", "python")}
                self._last_dependency_check = {"ok": True, "python": python_exe}

        with tempfile.TemporaryDirectory() as td:
            python_exe = os.path.join(td, "venv", "bin", "python")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")

            dummy = DummyBridge(td, python_exe)
            py, detail = dummy._resolve_bridge_python()

        self.assertEqual(os.path.normpath(py), os.path.normpath(python_exe))
        self.assertIsNone(detail)

    def test_resolve_bridge_python_revalidates_stale_configured_python_before_reuse(self):
        class DummyBridge(BridgeRuntimeMixin):
            _resolve_bridge_python = BridgeRuntimeMixin._resolve_bridge_python

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir
                self.cfg = {"python_exe": os.path.join("venv", "bin", "python")}
                self._last_dependency_check = {}

        with tempfile.TemporaryDirectory() as td:
            configured_python = os.path.join(td, "venv", "bin", "python")
            fallback_python = os.path.join(td, "fallback", "bin", "python3")
            os.makedirs(os.path.dirname(configured_python), exist_ok=True)
            os.makedirs(os.path.dirname(fallback_python), exist_ok=True)
            for path in (configured_python, fallback_python):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("#!/usr/bin/env python3\n")

            dummy = DummyBridge(td)
            with mock.patch.object(bridge_runtime.lz, "_probe_python_agent_compat", return_value=(False, "bad python")), mock.patch.object(
                bridge_runtime.lz, "_find_compatible_system_python", return_value=(fallback_python, None)
            ):
                py, detail = dummy._resolve_bridge_python()

        self.assertEqual(os.path.normpath(py), os.path.normpath(fallback_python))
        self.assertIsNone(detail)

    def test_launcher_version_info_falls_back_to_legacy_macos_version_json(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = os.path.join(td, "GenericAgent Launcher.app", "Contents", "MacOS")
            os.makedirs(app_dir, exist_ok=True)
            with open(os.path.join(app_dir, "version.json"), "w", encoding="utf-8") as f:
                json.dump({"version": "1.0.1", "channel": "stable", "commit": "legacy", "build_time": "legacy-time"}, f)

            original_app_dir = runtime.APP_DIR
            runtime.APP_DIR = app_dir
            try:
                with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(runtime.sys, "frozen", False, create=True):
                    info = runtime.launcher_version_info()
            finally:
                runtime.APP_DIR = original_app_dir

        self.assertEqual(info["version"], "1.0.1")
        self.assertEqual(info["commit"], "legacy")

    def test_macos_installation_status_warns_when_running_from_disk_image(self):
        bundle = f"/Volumes/GenericAgentLauncher/{runtime.APP_DISPLAY_NAME}.app"
        executable = f"{bundle}/Contents/MacOS/GenericAgentLauncher"
        with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(
            runtime.sys, "frozen", True, create=True
        ), mock.patch.object(
            runtime, "current_launcher_bundle_path", return_value=bundle
        ), mock.patch.object(
            runtime, "current_launcher_executable_path", return_value=executable
        ), mock.patch.object(
            runtime.os.path, "expanduser", return_value="/Users/tester"
        ):
            info = runtime.macos_installation_status()

        self.assertEqual(info["status"], "warn")
        self.assertTrue(info["running_from_disk_image"])
        self.assertTrue(info["needs_relocation"])
        self.assertIn("dmg", info["summary"])

    def test_macos_installation_status_marks_system_applications_install_as_ok(self):
        bundle = os.path.join("/Applications", f"{runtime.APP_DISPLAY_NAME}.app")
        executable = f"{bundle}/Contents/MacOS/GenericAgentLauncher"
        with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(
            runtime.sys, "frozen", True, create=True
        ), mock.patch.object(
            runtime, "current_launcher_bundle_path", return_value=bundle
        ), mock.patch.object(
            runtime, "current_launcher_executable_path", return_value=executable
        ), mock.patch.object(
            runtime.os.path, "expanduser", return_value="/Users/tester"
        ):
            info = runtime.macos_installation_status()

        self.assertEqual(info["status"], "ok")
        self.assertTrue(info["installed_to_system_applications"])
        self.assertFalse(info["needs_relocation"])
        self.assertEqual(info["recommended_install_target"], bundle)

    def test_macos_installation_status_marks_user_applications_install_as_ok(self):
        bundle = f"/Users/tester/Applications/{runtime.APP_DISPLAY_NAME}.app"
        executable = f"{bundle}/Contents/MacOS/GenericAgentLauncher"
        with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(
            runtime.sys, "frozen", True, create=True
        ), mock.patch.object(
            runtime, "current_launcher_bundle_path", return_value=bundle
        ), mock.patch.object(
            runtime, "current_launcher_executable_path", return_value=executable
        ), mock.patch.object(
            runtime.os.path, "expanduser", return_value="/Users/tester"
        ):
            info = runtime.macos_installation_status()

        self.assertEqual(info["status"], "ok")
        self.assertTrue(info["installed_to_user_applications"])
        self.assertFalse(info["needs_relocation"])
        self.assertEqual(info["recommended_install_target"], bundle)
        self.assertIn("~/Applications", info["summary"])

    def test_macos_installation_status_warns_when_running_from_app_translocation(self):
        bundle = f"/private/var/folders/test/AppTranslocation/demo/d/{runtime.APP_DISPLAY_NAME}.app"
        executable = f"{bundle}/Contents/MacOS/GenericAgentLauncher"
        with mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(
            runtime.sys, "frozen", True, create=True
        ), mock.patch.object(
            runtime, "current_launcher_bundle_path", return_value=bundle
        ), mock.patch.object(
            runtime, "current_launcher_executable_path", return_value=executable
        ), mock.patch.object(
            runtime.os.path, "expanduser", return_value="/Users/tester"
        ):
            info = runtime.macos_installation_status()

        self.assertEqual(info["status"], "warn")
        self.assertTrue(info["running_from_translocation"])
        self.assertTrue(info["needs_relocation"])
        self.assertIn("App Translocation", info["summary"])
        self.assertIn("~/Applications", info["summary"])

    def test_build_launcher_external_update_info_keeps_macos_companion_assets(self):
        class DummyUsage(PersonalUsageMixin):
            _build_launcher_external_update_info = PersonalUsageMixin._build_launcher_external_update_info
            _compare_versions = PersonalUsageMixin._compare_versions
            _version_tuple = PersonalUsageMixin._version_tuple

        release = {
            "tag_name": "v1.2.4",
            "html_url": "https://github.com/example/release/v1.2.4",
            "assets": [
                {"name": "GenericAgentLauncher-Setup-1.2.4.exe", "browser_download_url": "https://example.com/Setup.exe"},
                {
                    "name": "GenericAgentLauncher-macos-arm64-1.2.4.dmg",
                    "browser_download_url": "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.dmg",
                },
                {
                    "name": "GenericAgentLauncher-macos-arm64-1.2.4.sha256",
                    "browser_download_url": "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.sha256",
                },
                {"name": "README-macOS-arm64.txt", "browser_download_url": "https://example.com/README-macOS-arm64.txt"},
                {"name": "install-metadata-arm64.json", "browser_download_url": "https://example.com/install-metadata-arm64.json"},
            ],
        }
        dummy = DummyUsage()
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(personal_usage.platform, "machine", return_value="arm64"):
            info = dummy._build_launcher_external_update_info(release, local_version="1.2.3")

        self.assertIsInstance(info, dict)
        self.assertEqual(info["install_mode"], "external")
        self.assertEqual(info["external_asset_name"], "GenericAgentLauncher-macos-arm64-1.2.4.dmg")
        self.assertEqual(info["external_url"], "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.dmg")
        self.assertEqual(info["readme_url"], "https://example.com/README-macOS-arm64.txt")
        self.assertEqual(info["sha256_url"], "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.sha256")
        self.assertEqual(info["metadata_url"], "https://example.com/install-metadata-arm64.json")

    def test_set_agent_dir_triggers_scheduler_autostart_for_valid_agent(self):
        class DummyList:
            def clear(self):
                return None

        class DummyNav(NavigationMixin):
            _set_agent_dir = NavigationMixin._set_agent_dir

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self.current_session = None
                self._selected_session_id = None
                self._pending_state_session = None
                self._ignore_session_select = False
                self._last_session_list_signature = None
                self.session_list = DummyList()
                self.calls = []
                self.pages = None
                self._settings_page = None

            def _refresh_welcome_state(self):
                self.calls.append("refresh_welcome")

            def _settings_reload(self, categories=None, force=False):
                self.calls.append(("settings_reload", list(categories or []), bool(force)))

            def _schedule_session_index_warmup(self):
                self.calls.append("warmup")

            def _enforce_session_archive_limits(self, refresh=False):
                self.calls.append(("enforce_archive_limits", bool(refresh)))

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _schedule_local_channel_autostart(self):
                self.calls.append("autostart_channels")

            def _start_autostart_scheduler(self):
                self.calls.append("autostart_scheduler")

            def _schedule_lan_interface_autostart(self):
                self.calls.append("autostart_lan")

            def _stop_bridge(self):
                self.calls.append("stop_bridge")

            def _stop_all_managed_channels(self, refresh=False):
                self.calls.append(("stop_channels", bool(refresh)))

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "purge_archived_sessions", return_value=0
        ):
            dummy._set_agent_dir("C:\\demo\\agent", persist=False)

        self.assertEqual(dummy.agent_dir, os.path.abspath("C:\\demo\\agent"))
        self.assertIn("autostart_channels", dummy.calls)
        self.assertIn("autostart_scheduler", dummy.calls)
        self.assertIn("autostart_lan", dummy.calls)
        self.assertLess(dummy.calls.index("autostart_channels"), dummy.calls.index("autostart_scheduler"))
        self.assertLess(dummy.calls.index("autostart_scheduler"), dummy.calls.index("autostart_lan"))

    def test_choose_python_executable_uses_all_files_filter_on_non_windows(self):
        class DummyNav(NavigationMixin):
            _choose_python_executable = NavigationMixin._choose_python_executable

            def __init__(self):
                self.locate_python_edit = mock.Mock()
                self.locate_python_edit.text.return_value = ""

        dummy = DummyNav()
        with mock.patch("qt_chat_parts.navigation.os.name", "posix"), mock.patch(
            "qt_chat_parts.navigation.QFileDialog.getOpenFileName",
            return_value=("/usr/local/bin/python3", "All Files (*)"),
        ) as picker, mock.patch.object(
            lz,
            "_make_python_exe_config_path",
            side_effect=lambda value, **_kwargs: value,
        ):
            dummy._choose_python_executable()

        _args, kwargs = picker.call_args
        self.assertEqual(kwargs, {})
        self.assertEqual(_args[3], "All Files (*)")
        dummy.locate_python_edit.setText.assert_called_once_with("/usr/local/bin/python3")

    def test_bridge_build_multimodal_user_content_supports_image_only_send(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"\x89PNG\r\n\x1a\nlauncher-image")
            image_path = tmp.name
        self.addCleanup(lambda: os.path.exists(image_path) and os.remove(image_path))

        content = bridge._build_multimodal_user_content("", [image_path])
        content_with_text = bridge._build_multimodal_user_content("hello", [image_path])

        self.assertEqual(content[0]["type"], "text")
        self.assertIn("请结合图片内容回答", content[0]["text"])
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn("data:image/png;base64,", content[1]["image_url"]["url"])
        self.assertEqual(content_with_text[0], {"type": "text", "text": "hello"})

    def test_bridge_scrub_last_user_history_replaces_image_rich_content_with_text_only_content(self):
        class DummyBackend:
            def __init__(self):
                self.history = [
                    {"role": "user", "content": [{"type": "text", "text": "older"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                        ],
                    },
                ]

        class DummyClient:
            def __init__(self):
                self.backend = DummyBackend()

        client = DummyClient()
        replaced = bridge._scrub_last_user_history(client, [{"type": "text", "text": "hello"}], start_len=2)

        self.assertTrue(replaced)
        self.assertEqual(client.backend.history[-1], {"role": "user", "content": [{"type": "text", "text": "hello"}]})

    def test_bridge_scrub_last_user_history_only_replaces_current_turn_image_message(self):
        class DummyBackend:
            def __init__(self):
                self.history = [
                    {"role": "user", "content": [{"type": "text", "text": "older"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "older reply"}]},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                        ],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "done"}],
                    },
                ]

        class DummyClient:
            def __init__(self):
                self.backend = DummyBackend()

        client = DummyClient()
        replaced = bridge._scrub_last_user_history(client, [{"type": "text", "text": "look"}], start_len=2)

        self.assertTrue(replaced)
        self.assertEqual(client.backend.history[2]["content"], [{"type": "text", "text": "look"}])
        self.assertEqual(
            client.backend.history[3]["content"],
            [{"type": "tool_result", "tool_use_id": "call_1", "content": "done"}],
        )

    def test_bridge_scrub_last_user_history_uses_image_only_placeholder(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake-image-bytes")
            image_path = tmp.name
        self.addCleanup(lambda: os.path.exists(image_path) and os.remove(image_path))

        class DummyBackend:
            def __init__(self):
                self.history = [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "用户发送了 1 张图片\n[用户上传图片附件]\ndata:image/png;base64,AAAA"}],
                    }
                ]

        class DummyClient:
            def __init__(self):
                self.backend = DummyBackend()

        client = DummyClient()
        replacement = bridge._build_scrubbed_user_content("", [image_path])
        replaced = bridge._scrub_last_user_history(client, replacement, start_len=0)

        self.assertTrue(replaced)
        self.assertEqual(client.backend.history[0]["content"], [{"type": "text", "text": "[用户发送了 1 张图片]"}])

    def test_notify_reply_done_prefers_launcher_tray_icon(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {}
                self.statuses = []
                self.sound_calls = 0
                self.launcher_tray_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_launcher_tray_icon(self):
                self.launcher_tray_calls += 1
                return tray

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return None

            def _set_status(self, text):
                self.statuses.append(str(text))

        tray = mock.Mock()
        dummy = DummyBridge()
        dummy._notify_reply_done("hello\nworld")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.statuses, [])
        self.assertEqual(dummy.launcher_tray_calls, 1)
        self.assertEqual(dummy.reply_tray_calls, 0)
        tray.show.assert_called_once()
        tray.showMessage.assert_called_once()
        args = tray.showMessage.call_args.args
        self.assertEqual(args[0], "GenericAgent 启动器")
        self.assertEqual(args[1], "AI 回复已完成：hello world")

    def test_notify_reply_done_falls_back_to_reply_notify_tray(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {}
                self.statuses = []
                self.sound_calls = 0
                self.launcher_tray_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_launcher_tray_icon(self):
                self.launcher_tray_calls += 1
                return None

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return tray

            def _set_status(self, text):
                self.statuses.append(str(text))

        tray = mock.Mock()
        dummy = DummyBridge()
        dummy._notify_reply_done("tray preview")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.statuses, [])
        self.assertEqual(dummy.launcher_tray_calls, 1)
        self.assertEqual(dummy.reply_tray_calls, 1)
        tray.show.assert_called_once()
        tray.showMessage.assert_called_once()
        args = tray.showMessage.call_args.args
        self.assertEqual(args[0], "GenericAgent 启动器")
        self.assertEqual(args[1], "AI 回复已完成：tray preview")

    def test_notify_reply_done_skips_system_notification_when_message_disabled(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {"disable_reply_message": True}
                self.sound_calls = 0
                self.launcher_tray_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_launcher_tray_icon(self):
                self.launcher_tray_calls += 1
                return mock.Mock()

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return mock.Mock()

        dummy = DummyBridge()
        dummy._notify_reply_done("ignored")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.launcher_tray_calls, 0)
        self.assertEqual(dummy.reply_tray_calls, 0)

    def test_notify_reply_done_without_tray_keeps_sound_independent_on_non_windows(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {}
                self.statuses = []
                self.sound_calls = 0
                self.launcher_tray_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_launcher_tray_icon(self):
                self.launcher_tray_calls += 1
                return None

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return None

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        with mock.patch.object(bridge_runtime.os, "name", "posix"), mock.patch.object(personal_usage.lz, "IS_MACOS", False):
            dummy._notify_reply_done("windows stays silent")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.statuses, [])
        self.assertEqual(dummy.launcher_tray_calls, 1)
        self.assertEqual(dummy.reply_tray_calls, 1)

    def test_notify_reply_done_uses_tray_on_non_windows(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {}
                self.sound_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return tray

        tray = mock.Mock()
        dummy = DummyBridge()
        with mock.patch.object(bridge_runtime.os, "name", "posix"):
            dummy._notify_reply_done("linux tray")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.reply_tray_calls, 1)
        tray.showMessage.assert_called_once()
        args = tray.showMessage.call_args.args
        self.assertEqual(args[0], "GenericAgent 启动器")
        self.assertEqual(args[1], "AI 回复已完成：linux tray")

    def test_notify_reply_done_stays_nonfatal_when_tray_raises(self):
        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done

            def __init__(self):
                self.cfg = {}
                self.sound_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_reply_notify_tray(self):
                return tray

        tray = mock.Mock()
        tray.showMessage.side_effect = RuntimeError("tray failed")
        dummy = DummyBridge()
        dummy._notify_reply_done("tray failed")

        self.assertEqual(dummy.sound_calls, 1)
        tray.showMessage.assert_called_once()

    def test_notify_reply_done_delays_fresh_tray_message_on_windows(self):
        class DummyTray:
            def __init__(self):
                self._props = {}
                self.calls = []

            def setProperty(self, key, value):
                self._props[str(key)] = value

            def property(self, key):
                return self._props.get(str(key))

            def show(self):
                self.calls.append("show")

            def showMessage(self, *args):
                self.calls.append(("showMessage", args))

            def isVisible(self):
                return True

        class DummyBridge(BridgeRuntimeMixin):
            _notify_reply_done = BridgeRuntimeMixin._notify_reply_done
            _set_tray_message_ready = BridgeRuntimeMixin._set_tray_message_ready
            _tray_message_ready = BridgeRuntimeMixin._tray_message_ready
            _show_tray_message = BridgeRuntimeMixin._show_tray_message

            def __init__(self):
                self.cfg = {}
                self.sound_calls = 0
                self.reply_tray_calls = 0

            def _play_reply_done_sound(self):
                self.sound_calls += 1

            def _ensure_launcher_tray_icon(self):
                return None

            def _ensure_reply_notify_tray(self):
                self.reply_tray_calls += 1
                return tray

        tray = DummyTray()
        dummy = DummyBridge()
        dummy._set_tray_message_ready(tray, False)
        delays = []

        with mock.patch.object(bridge_runtime.os, "name", "nt"), mock.patch.object(
            bridge_runtime.QTimer, "singleShot", side_effect=lambda ms, cb: delays.append(int(ms)) or cb()
        ):
            dummy._notify_reply_done("fresh tray")

        self.assertEqual(dummy.sound_calls, 1)
        self.assertEqual(dummy.reply_tray_calls, 1)
        self.assertEqual(delays, [180])
        self.assertEqual(tray.calls[0], "show")
        self.assertEqual(tray.calls[1][0], "showMessage")
        self.assertEqual(tray.calls[1][1][0], "GenericAgent 启动器")
        self.assertEqual(tray.calls[1][1][1], "AI 回复已完成：fresh tray")
        self.assertTrue(dummy._tray_message_ready(tray))

    def test_real_qt_chat_window_notify_reply_done_uses_system_notification_path_when_hidden(self):
        app = launcher_window.QApplication.instance() or launcher_window.QApplication([])
        win = None
        with mock.patch.object(launcher_window.lz, "load_config", return_value={}), mock.patch.object(
            launcher_window.QtChatWindow, "_build_shell", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_session_index_warmup", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_refresh_welcome_state", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_show_welcome", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_local_channel_autostart", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_start_autostart_scheduler", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_lan_interface_autostart", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_startup_update_check", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_startup_install_hint", autospec=True, side_effect=lambda self: None
        ), mock.patch.object(
            launcher_window.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ):
            win = launcher_window.QtChatWindow(r"E:\\GenericAgent")
            win.hide()
            win._tray_mode_active = True
            win._drain_timer.stop()
            win._server_status_timer.stop()
            win._stream_flush_timer.stop()

            tray = mock.Mock()
            with mock.patch.object(
                win, "_play_reply_done_sound", return_value=None
            ), mock.patch.object(
                win,
                "_ensure_launcher_tray_icon",
                return_value=tray,
            ), mock.patch.object(
                win, "_ensure_reply_notify_tray"
            ) as fallback_tray:
                win._notify_reply_done("real window self-test")
                app.processEvents()

            self.assertFalse(win.isVisible())
            fallback_tray.assert_not_called()
            tray.show.assert_called_once()
            tray.showMessage.assert_called_once()
            args = tray.showMessage.call_args.args
            self.assertEqual(args[0], "GenericAgent 启动器")
            self.assertEqual(args[1], "AI 回复已完成：real window self-test")
            self.assertEqual(getattr(win, "_reply_done_popups", []), [])

        if win is not None:
            try:
                app.removeEventFilter(win)
            except Exception:
                pass
            win.deleteLater()
            app.processEvents()

    def test_launcher_single_instance_guard_queues_activation_until_handler_attached(self):
        app = launcher_window.QApplication.instance() or launcher_window.QApplication([])
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            launcher_window.lz, "STATE_DIR", td
        ), mock.patch.object(
            launcher_window.lz, "_ensure_launcher_data_dirs", side_effect=lambda: os.makedirs(td, exist_ok=True)
        ), mock.patch.object(
            launcher_window, "_launcher_single_instance_server_name", return_value=f"ga-launcher-test-{abs(hash(td))}"
        ):
            primary = launcher_window._LauncherSingleInstanceGuard(app)
            secondary = launcher_window._LauncherSingleInstanceGuard(app)
            try:
                self.assertTrue(primary.try_claim_primary())
                self.assertTrue(secondary.request_existing_activation(wait_ms=600))

                app.processEvents()
                QTest.qWait(80)
                app.processEvents()

                calls = []
                primary.set_activation_handler(lambda: calls.append("activate"))

                app.processEvents()
                QTest.qWait(80)
                app.processEvents()

                self.assertEqual(calls, ["activate"])
            finally:
                secondary.close()
                primary.close()

    def test_activate_from_secondary_launch_restores_hidden_window(self):
        class DummyHost:
            _activate_from_secondary_launch = launcher_window.QtChatWindow._activate_from_secondary_launch

            def __init__(self):
                self._tray_mode_active = True
                self.calls = []

            def isVisible(self):
                return False

            def _restore_from_tray_mode(self):
                self.calls.append("restore")

        dummy = DummyHost()
        dummy._activate_from_secondary_launch()

        self.assertEqual(dummy.calls, ["restore"])

    def test_launcher_main_returns_early_when_existing_instance_was_signaled(self):
        app = mock.Mock()
        app.exec.side_effect = AssertionError("secondary launch must not enter app.exec")

        with mock.patch.object(launcher_window.QApplication, "instance", return_value=app), mock.patch.object(
            launcher_window, "launcher_icon", return_value=mock.Mock()
        ), mock.patch.object(
            launcher_window, "_ensure_single_launcher_instance", return_value=None
        ), mock.patch.object(
            launcher_window, "QtChatWindow"
        ) as window_ctor:
            result = launcher_window.main(r"E:\\GenericAgent")

        self.assertEqual(result, 0)
        window_ctor.assert_not_called()
        app.exec.assert_not_called()

    def test_stream_done_triggers_reply_notification_once(self):
        class Button:
            def __init__(self):
                self.values = []

            def setEnabled(self, value):
                self.values.append(bool(value))

        class Row:
            def __init__(self):
                self.updates = []

            def update_content(self, text, *, finished):
                self.updates.append((text, bool(finished)))

        class DummyBridge(BridgeRuntimeMixin):
            _stream_done = BridgeRuntimeMixin._stream_done

            def __init__(self):
                self._abort_requested = False
                self._stream_row = Row()
                self._busy = True
                self._current_stream_text = ""
                self._pending_stream_text = None
                self.send_btn = Button()
                self.stop_btn = Button()
                self.statuses = []
                self.notifications = []
                self.current_session = None

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _refresh_composer_enabled(self):
                pass

            def _clear_active_turn_attachments(self):
                pass

            def _refresh_token_label(self):
                pass

            def _scroll_to_bottom(self):
                pass

            def _notify_reply_done(self, final_text):
                self.notifications.append(str(final_text))

        dummy = DummyBridge()
        row = dummy._stream_row
        dummy._stream_done("final answer")

        self.assertEqual(row.updates, [("final answer", True)])
        self.assertEqual(dummy.notifications, ["final answer"])
        self.assertFalse(dummy._busy)
        self.assertEqual(dummy.statuses[-1], "已完成。")

    def test_stream_done_skips_reply_notification_after_abort(self):
        class Button:
            def setEnabled(self, _value):
                pass

        class DummyBridge(BridgeRuntimeMixin):
            _stream_done = BridgeRuntimeMixin._stream_done
            _format_interrupted_text = BridgeRuntimeMixin._format_interrupted_text

            def __init__(self):
                self._abort_requested = True
                self._stream_row = None
                self._busy = True
                self._current_stream_text = "partial"
                self._pending_stream_text = None
                self.send_btn = Button()
                self.stop_btn = Button()
                self.notifications = []
                self.current_session = None

            def _set_status(self, _text):
                pass

            def _refresh_composer_enabled(self):
                pass

            def _clear_active_turn_attachments(self):
                pass

            def _refresh_token_label(self):
                pass

            def _scroll_to_bottom(self):
                pass

            def _notify_reply_done(self, final_text):
                self.notifications.append(str(final_text))

        dummy = DummyBridge()
        dummy._stream_done("")

        self.assertEqual(dummy.notifications, [])
        self.assertFalse(dummy._abort_requested)

    def test_handle_event_aborted_finishes_turn_and_restores_controls(self):
        class Button:
            def __init__(self):
                self.enabled = None

            def setEnabled(self, value):
                self.enabled = bool(value)

        class Row:
            def __init__(self):
                self.updates = []

            def update_content(self, text, *, finished):
                self.updates.append((str(text), bool(finished)))

        class DummyBridge(BridgeRuntimeMixin):
            _handle_event = BridgeRuntimeMixin._handle_event
            _stream_done = BridgeRuntimeMixin._stream_done
            _format_interrupted_text = BridgeRuntimeMixin._format_interrupted_text

            def __init__(self):
                self._abort_requested = True
                self._suppress_next_done_after_abort = False
                self._stream_row = Row()
                self._busy = True
                self._current_stream_text = "partial output"
                self._pending_stream_text = None
                self.send_btn = Button()
                self.stop_btn = Button()
                self.statuses = []
                self.current_session = None
                self.cfg = {}

            def _handle_download_event(self, _ev):
                return False

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _refresh_composer_enabled(self):
                pass

            def _clear_active_turn_attachments(self):
                pass

            def _refresh_token_label(self):
                pass

            def _scroll_to_bottom(self):
                pass

            def _notify_reply_done(self, _final_text):
                raise AssertionError("abort must not trigger reply-done notification")

        dummy = DummyBridge()
        row = dummy._stream_row

        dummy._handle_event({"event": "aborted"})

        self.assertFalse(dummy._busy)
        self.assertFalse(dummy._abort_requested)
        self.assertTrue(dummy.send_btn.enabled)
        self.assertFalse(dummy.stop_btn.enabled)
        self.assertEqual(dummy.statuses[-1], "已中断。")
        self.assertTrue(dummy._suppress_next_done_after_abort)
        self.assertEqual(len(row.updates), 1)
        self.assertIn("已按用户请求中断", row.updates[0][0])

        dummy._handle_event({"event": "done", "text": "late backend text"})

        self.assertEqual(len(row.updates), 1)
        self.assertFalse(dummy._suppress_next_done_after_abort)

    def test_stream_done_clears_active_turn_attachments(self):
        class Button:
            def setEnabled(self, _value):
                pass

        class DummyBridge(BridgeRuntimeMixin):
            _stream_done = BridgeRuntimeMixin._stream_done
            _clear_active_turn_attachments = BridgeRuntimeMixin._clear_active_turn_attachments
            _active_turn_attachments = BridgeRuntimeMixin._active_turn_attachments

            def __init__(self):
                self._abort_requested = False
                self._stream_row = None
                self._busy = True
                self._current_stream_text = ""
                self._pending_stream_text = None
                self.send_btn = Button()
                self.stop_btn = Button()
                self.current_session = None
                self._active_turn_attachments_data = [{"path": "done.png", "name": "done", "owned": True}]
                self.released = []
                self.anchor_clear_calls = 0

            def _set_status(self, _text):
                pass

            def _refresh_composer_enabled(self):
                pass

            def _release_attachment_files(self, items):
                self.released = [dict(item) for item in list(items or [])]

            def _refresh_token_label(self):
                pass

            def _scroll_to_bottom(self):
                pass

            def _notify_reply_done(self, _final_text):
                pass

            def _clear_current_turn_user_row(self):
                self.anchor_clear_calls += 1

        dummy = DummyBridge()
        dummy._stream_done("done")

        self.assertEqual(dummy._active_turn_attachments_data, [])
        self.assertEqual(dummy.released, [{"path": "done.png", "name": "done", "owned": True}])
        self.assertEqual(dummy.anchor_clear_calls, 1)

    def test_flush_stream_render_skips_auto_jump_when_theme_toggle_disabled(self):
        class Row:
            def __init__(self):
                self._text = ""
                self._finished = False
                self.updates = []

            def update_content(self, text, *, finished):
                self._text = str(text)
                self._finished = bool(finished)
                self.updates.append((self._text, self._finished))

        class DummyBridge(BridgeRuntimeMixin):
            _flush_stream_render = BridgeRuntimeMixin._flush_stream_render

            def __init__(self):
                self.cfg = {"theme_chat_auto_jump_latest": False}
                self._turn_auto_jump_latest = True
                self._stream_row = Row()
                self._pending_stream_text = "part 1"
                self.sync_calls = []
                self.scroll_calls = 0
                self.refresh_calls = 0
                self.token_updates = []

            def _update_stream_row_tokens(self, *, live):
                self.token_updates.append(bool(live))

            def _sync_current_turn_view(self, *, force=False):
                self.sync_calls.append(bool(force))

            def _scroll_to_bottom(self):
                self.scroll_calls += 1

            def _refresh_floating_chat_window(self):
                self.refresh_calls += 1

        dummy = DummyBridge()
        dummy._flush_stream_render()

        self.assertEqual(dummy._stream_row.updates, [("part 1", False)])
        self.assertEqual(dummy.token_updates, [True])
        self.assertEqual(dummy.sync_calls, [])
        self.assertEqual(dummy.scroll_calls, 0)
        self.assertEqual(dummy.refresh_calls, 1)

    def test_stream_done_skips_auto_jump_when_theme_toggle_disabled(self):
        class Button:
            def __init__(self):
                self.values = []

            def setEnabled(self, value):
                self.values.append(bool(value))

        class Row:
            def __init__(self):
                self.updates = []

            def update_content(self, text, *, finished):
                self.updates.append((str(text), bool(finished)))

        class DummyBridge(BridgeRuntimeMixin):
            _stream_done = BridgeRuntimeMixin._stream_done

            def __init__(self):
                self.cfg = {"theme_chat_auto_jump_latest": False}
                self._turn_auto_jump_latest = True
                self._abort_requested = False
                self._stream_row = Row()
                self._busy = True
                self._current_stream_text = ""
                self._pending_stream_text = None
                self.send_btn = Button()
                self.stop_btn = Button()
                self.current_session = None
                self.follow_calls = []
                self.sync_calls = []
                self.scroll_calls = 0
                self.refresh_calls = 0

            def _set_status(self, _text):
                return None

            def _refresh_composer_enabled(self):
                return None

            def _clear_active_turn_attachments(self):
                return None

            def _refresh_token_label(self):
                return None

            def _notify_reply_done(self, _final_text):
                return None

            def _set_follow_latest_user(self, value):
                self.follow_calls.append(bool(value))

            def _sync_current_turn_view(self, *, force=False):
                self.sync_calls.append(bool(force))

            def _scroll_to_bottom(self):
                self.scroll_calls += 1

            def _refresh_floating_chat_window(self):
                self.refresh_calls += 1

        dummy = DummyBridge()
        row = dummy._stream_row
        dummy._stream_done("done")

        self.assertEqual(row.updates, [("done", True)])
        self.assertEqual(dummy.follow_calls, [False])
        self.assertEqual(dummy.sync_calls, [])
        self.assertEqual(dummy.scroll_calls, 0)
        self.assertEqual(dummy.refresh_calls, 1)
        self.assertFalse(getattr(dummy, "_turn_auto_jump_latest", True))

    def test_bridge_runtime_state_helpers_explain_attachment_and_llm_disable_reasons(self):
        class DummyBridge(BridgeRuntimeMixin):
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_attachment_remove_disabled_reason = BridgeRuntimeMixin._bridge_attachment_remove_disabled_reason
            _bridge_llm_combo_disabled_reason = BridgeRuntimeMixin._bridge_llm_combo_disabled_reason
            _sync_llm_combo = BridgeRuntimeMixin._sync_llm_combo

            def __init__(self, llms=None):
                self.llms = list(llms or [])
                self._ignore_llm_change = False
                self.sync_calls = 0
                self.llm_combo = DummyCombo()

            def _sync_floating_llm_combo(self):
                self.sync_calls += 1

        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = -1
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items.clear()
                self.current_index = -1

            def addItem(self, label, data):
                self.items.append((str(label), data))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        dummy = DummyBridge()
        self.assertEqual(
            dummy._bridge_attachment_remove_disabled_reason(active_mode=True),
            "当前这一轮还没有结束；本轮已附带图片会在回复完成后自动清除。",
        )
        self.assertEqual(dummy._bridge_llm_combo_disabled_reason(), "当前还没有可用的 LLM 配置。")
        dummy._sync_llm_combo()
        self.assertEqual(dummy.llm_combo.items, [("未配置 LLM", -1)])
        self.assertFalse(dummy.llm_combo.enabled)
        self.assertEqual(dummy.llm_combo.tooltip, "当前还没有可用的 LLM 配置。")
        self.assertEqual(dummy.sync_calls, 1)

        ready_dummy = DummyBridge(llms=[{"idx": 7, "name": "Claude", "current": True}])
        ready_dummy._sync_llm_combo()
        self.assertEqual(ready_dummy.llm_combo.items, [("Claude", 7)])
        self.assertEqual(ready_dummy.llm_combo.current_index, 0)
        self.assertTrue(ready_dummy.llm_combo.enabled)
        self.assertEqual(ready_dummy.llm_combo.tooltip, "切换当前会话使用的模型。")

    def test_attachment_bar_display_items_only_use_pending_attachments(self):
        class DummyBridge(BridgeRuntimeMixin):
            _attachment_bar_display_items = BridgeRuntimeMixin._attachment_bar_display_items
            _pending_input_attachments = BridgeRuntimeMixin._pending_input_attachments

            def __init__(self):
                self._pending_input_attachments_data = [{"path": "pending.png", "name": "pending"}]
                self._active_turn_attachments_data = [{"path": "active.png", "name": "active"}]

        dummy = DummyBridge()

        self.assertEqual(dummy._attachment_bar_display_items(), [{"path": "pending.png", "name": "pending"}])
        self.assertEqual(dummy._active_turn_attachments_data, [{"path": "active.png", "name": "active"}])

    def test_bridge_runtime_sync_reasoning_effort_combo_tracks_runtime_state(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = -1
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items.clear()
                self.current_index = -1

            def addItem(self, label, data):
                self.items.append((str(label), data))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyBridge(BridgeRuntimeMixin):
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_reasoning_effort_combo_disabled_reason = BridgeRuntimeMixin._bridge_reasoning_effort_combo_disabled_reason
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _current_session_reasoning_effort_override = BridgeRuntimeMixin._current_session_reasoning_effort_override
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _current_reasoning_effort_selection = BridgeRuntimeMixin._current_reasoning_effort_selection
            _sync_reasoning_effort_combo = BridgeRuntimeMixin._sync_reasoning_effort_combo

            def __init__(self, *, llms=None, reasoning_effort=""):
                self.llms = list(llms or [])
                self.current_session = None
                self._pending_reasoning_effort_override = None
                self._bridge_reasoning_effort = reasoning_effort
                self._ignore_reasoning_effort_change = False
                self.reasoning_effort_combo = DummyCombo()
                self.sync_calls = 0

            def _sync_floating_reasoning_effort_combo(self):
                self.sync_calls += 1

        dummy = DummyBridge()
        dummy._sync_reasoning_effort_combo()
        self.assertEqual(dummy.reasoning_effort_combo.items[0], ("跟随配置", ""))
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 0)
        self.assertFalse(dummy.reasoning_effort_combo.enabled)
        self.assertEqual(dummy.reasoning_effort_combo.tooltip, "当前还没有可用的 LLM 配置。")

        ready_dummy = DummyBridge(llms=[{"idx": 0, "name": "GPT", "current": True}], reasoning_effort="high")
        ready_dummy._sync_reasoning_effort_combo()
        self.assertEqual(ready_dummy.reasoning_effort_combo.items[5], ("high", "high"))
        self.assertEqual(ready_dummy.reasoning_effort_combo.current_index, 5)
        self.assertTrue(ready_dummy.reasoning_effort_combo.enabled)
        self.assertEqual(ready_dummy.reasoning_effort_combo.tooltip, "切换当前会话使用的思考强度。")
        self.assertEqual(ready_dummy.sync_calls, 1)

        restored_dummy = DummyBridge(llms=[{"idx": 0, "name": "GPT", "current": True}])
        restored_dummy.current_session = {"id": "sess-1", "snapshot": {"reasoning_effort": "medium", "reasoning_effort_source": "runtime"}}
        self.assertEqual(restored_dummy._current_session_reasoning_effort_override(), "")
        self.assertEqual(restored_dummy._current_reasoning_effort_selection(), "")
        restored_dummy._sync_reasoning_effort_combo()
        self.assertEqual(restored_dummy.reasoning_effort_combo.current_index, 0)

        explicit_dummy = DummyBridge(llms=[{"idx": 0, "name": "GPT", "current": True}])
        explicit_dummy.current_session = {
            "id": "sess-2",
            "reasoning_effort": "medium",
            "snapshot": {"reasoning_effort": "medium", "reasoning_effort_source": "override"},
        }
        self.assertEqual(explicit_dummy._current_session_reasoning_effort_override(), "medium")
        self.assertEqual(explicit_dummy._current_reasoning_effort_selection(), "medium")

    def test_bridge_runtime_reasoning_effort_change_persists_session_and_sends_command(self):
        class DummyCombo:
            def __init__(self):
                self.items = []

            def addItem(self, label, data):
                self.items.append((str(label), data))

            def itemData(self, index):
                return self.items[int(index)][1]

        class DummyBridge(BridgeRuntimeMixin):
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _on_reasoning_effort_changed = BridgeRuntimeMixin._on_reasoning_effort_changed

            def __init__(self):
                self.current_session = {"id": "sess-1", "snapshot": {}}
                self.reasoning_effort_combo = DummyCombo()
                self.reasoning_effort_combo.addItem("跟随配置", "")
                self.reasoning_effort_combo.addItem("high", "high")
                self._ignore_reasoning_effort_change = False
                self._pending_reasoning_effort_override = None
                self._bridge_reasoning_effort = ""
                self._bridge_ready = True
                self.persisted = []
                self.sent = []
                self.statuses = []

            def _persist_session(self, session):
                self.persisted.append(dict(session))

            def _is_remote_session(self, session=None):
                return False

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

            def _sync_floating_reasoning_effort_combo(self):
                return None

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        dummy._on_reasoning_effort_changed(1)

        self.assertEqual(dummy.current_session["reasoning_effort"], "high")
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort"], "high")
        self.assertEqual(dummy._pending_reasoning_effort_override, "high")
        self.assertEqual(len(dummy.persisted), 1)
        self.assertEqual(dummy.sent, [{"cmd": "switch_reasoning_effort", "reasoning_effort": "high"}])
        self.assertEqual(dummy._bridge_reasoning_effort, "high")

    def test_apply_state_to_session_keeps_runtime_reasoning_out_of_top_level_override(self):
        class DummyBridge(BridgeRuntimeMixin):
            _apply_state_to_session = BridgeRuntimeMixin._apply_state_to_session
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.current_session = {"id": "sess-1", "snapshot": {}}
                self._bridge_reasoning_effort = ""
                self.persisted = []
                self.sync_calls = 0

            def _sync_reasoning_effort_combo(self):
                self.sync_calls += 1

            def _persist_session(self, session):
                self.persisted.append(dict(session))

        dummy = DummyBridge()
        dummy._apply_state_to_session("sess-1", [{"role": "user", "content": "hi"}], [], reasoning_effort="high")

        self.assertNotIn("reasoning_effort", dummy.current_session)
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort"], "high")
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort_source"], "runtime")
        self.assertEqual(dummy._bridge_reasoning_effort, "high")
        self.assertEqual(dummy.sync_calls, 1)
        self.assertNotIn("reasoning_effort", dummy.persisted[-1])

    def test_apply_state_to_session_persists_context_window_metrics(self):
        class DummyBridge(BridgeRuntimeMixin):
            _apply_state_to_session = BridgeRuntimeMixin._apply_state_to_session
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.current_session = {"id": "sess-ctx", "snapshot": {}}
                self._bridge_reasoning_effort = ""
                self.persisted = []

            def _sync_reasoning_effort_combo(self):
                return None

            def _persist_session(self, session):
                self.persisted.append(dict(session))

        dummy = DummyBridge()
        dummy._apply_state_to_session(
            "sess-ctx",
            [{"role": "user", "content": "hi"}],
            [],
            context_window_chars=1200,
            current_input_chars=300,
        )

        self.assertEqual(dummy.current_session["snapshot"]["context_window_chars"], 1200)
        self.assertEqual(dummy.current_session["snapshot"]["current_input_chars"], 300)
        self.assertEqual(dummy.current_session["snapshot"]["context_window_left_pct"], 75.0)
        self.assertEqual(dummy.persisted[-1]["snapshot"]["context_window_left_pct"], 75.0)

    def test_handle_event_reasoning_effort_switched_persists_current_session(self):
        class DummyBridge(BridgeRuntimeMixin):
            _handle_event = BridgeRuntimeMixin._handle_event
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source

            def __init__(self):
                self.current_session = {
                    "id": "sess-1",
                    "reasoning_effort": "medium",
                    "snapshot": {"reasoning_effort": "medium", "reasoning_effort_source": "override"},
                }
                self._bridge_reasoning_effort = ""
                self.persisted = []
                self.sync_calls = 0
                self.statuses = []
                self.refresh_calls = 0

            def _persist_session(self, session):
                self.persisted.append(dict(session))

            def _handle_download_event(self, ev):
                return False

            def _sync_reasoning_effort_combo(self):
                self.sync_calls += 1

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _refresh_composer_enabled(self):
                self.refresh_calls += 1

        dummy = DummyBridge()
        dummy._handle_event({"event": "reasoning_effort_switched", "reasoning_effort": "high"})

        self.assertEqual(dummy._bridge_reasoning_effort, "high")
        self.assertEqual(dummy.current_session["reasoning_effort"], "high")
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort"], "high")
        self.assertEqual(dummy.persisted[-1]["reasoning_effort"], "high")
        self.assertEqual(dummy.sync_calls, 1)
        self.assertEqual(dummy.statuses, ["思考强度已切换。"])
        self.assertEqual(dummy.refresh_calls, 1)

    def test_submit_user_message_routes_slash_new_before_channel_process_block(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _submit_user_message = BridgeRuntimeMixin._submit_user_message
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input

            def __init__(self):
                self.current_session = {"id": "snap-1", "session_kind": "channel_process"}
                self._pending_input_attachments_data = []
                self.new_session_calls = []

            def _handle_input_image_attachments(self, *_args, **_kwargs):
                return None

            def _is_channel_process_session(self):
                return True

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_from_floating(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _current_device_context(self):
                return "local", "local"

            def _new_session(self, checked=False, *, scope="", device_id="", prompt_device=True):
                self.new_session_calls.append((bool(checked), str(scope), str(device_id), bool(prompt_device)))

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "information") as info_box:
            result = dummy._submit_user_message("/new", attachments=None, source_editor=editor)

        self.assertFalse(result)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.new_session_calls, [(False, "local", "local", False)])
        info_box.assert_not_called()

    def test_input_text_edit_keeps_focus_and_typing_when_slash_popup_visible(self):
        app = launcher_window.QApplication.instance() or launcher_window.QApplication([])
        editor = chat_common.InputTextEdit(lambda: None)

        def provider(query, editor=None):
            text = str(query or "").strip().lower()
            if text.startswith("/h"):
                return [{"command": "/help", "insert_text": "/help", "description": "显示帮助"}]
            return [
                {"command": "/help", "insert_text": "/help", "description": "显示帮助"},
                {"command": "/history", "insert_text": "/history", "description": "查看历史"},
            ]

        host = chat_common.QWidget()
        layout = chat_common.QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor)
        host.resize(420, 120)
        host.show()
        try:
            editor.resize(420, 96)
            editor.set_slash_command_provider(provider)
            editor.setFocus(chat_common.Qt.OtherFocusReason)
            app.processEvents()

            QTest.keyClicks(editor, "/")
            app.processEvents()

            popup = editor._ensure_slash_popup()
            self.assertTrue(popup.isVisible())
            self.assertIs(app.focusWidget(), editor)
            self.assertEqual(popup.count(), 2)

            QTest.keyClicks(editor, "h")
            app.processEvents()

            self.assertEqual(editor.toPlainText(), "/h")
            self.assertIs(app.focusWidget(), editor)
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.count(), 1)
        finally:
            popup = getattr(editor, "_slash_popup", None)
            if popup is not None:
                popup.hide()
                popup.deleteLater()
            host.close()
            host.deleteLater()
            app.processEvents()

    def test_input_text_edit_positions_slash_popup_above_editor_when_space_allows(self):
        editor = chat_common.InputTextEdit(lambda: None)
        editor.resize(420, 96)
        popup = chat_common.QListWidget()

        class DummyScreen:
            def availableGeometry(self):
                return QRect(0, 0, 1280, 720)

        try:
            with mock.patch.object(chat_common.QApplication, "screenAt", return_value=DummyScreen()), mock.patch.object(
                editor, "mapToGlobal", return_value=chat_common.QPoint(100, 300)
            ):
                editor._position_slash_popup(popup, width=320, height=90)
        finally:
            popup.hide()
            popup.deleteLater()

        self.assertEqual(popup.x(), 100)
        self.assertEqual(popup.y(), 206)

    def test_input_text_edit_tab_accepts_slash_suggestion_and_enter_still_submits(self):
        app = launcher_window.QApplication.instance() or launcher_window.QApplication([])
        submitted = []

        editor = chat_common.InputTextEdit(lambda: submitted.append("sent"))

        def provider(query, editor=None):
            text = str(query or "").strip().lower()
            if text.startswith("/ll"):
                return [{"command": "/llm N", "insert_text": "/llm ", "description": "切换到第 N 个模型"}]
            return [
                {"command": "/help", "insert_text": "/help", "description": "显示帮助"},
                {"command": "/llm N", "insert_text": "/llm ", "description": "切换到第 N 个模型"},
            ]

        host = chat_common.QWidget()
        layout = chat_common.QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor)
        host.resize(420, 120)
        host.show()
        try:
            editor.resize(420, 96)
            editor.set_slash_command_provider(provider)
            editor.setFocus(chat_common.Qt.OtherFocusReason)
            app.processEvents()

            QTest.keyClicks(editor, "/ll")
            app.processEvents()

            popup = editor._ensure_slash_popup()
            self.assertTrue(popup.isVisible())
            self.assertEqual(popup.count(), 1)
            self.assertIs(app.focusWidget(), editor)

            QTest.keyClick(editor, chat_common.Qt.Key_Tab)
            app.processEvents()

            self.assertEqual(editor.toPlainText(), "/llm ")
            self.assertEqual(submitted, [])
            self.assertIs(app.focusWidget(), editor)

            editor.setPlainText("/")
            editor.moveCursor(chat_common.QTextCursor.End)
            editor.setFocus(chat_common.Qt.OtherFocusReason)
            app.processEvents()
            self.assertTrue(popup.isVisible())

            QTest.keyClick(editor, chat_common.Qt.Key_Return)
            app.processEvents()

            self.assertEqual(submitted, ["sent"])
        finally:
            popup = getattr(editor, "_slash_popup", None)
            if popup is not None:
                popup.hide()
                popup.deleteLater()
            host.close()
            host.deleteLater()
            app.processEvents()

    def test_submit_user_message_sends_images_and_clears_input_attachment_display_state(self):
        class DummyEditor:
            def __init__(self, text="hello"):
                self.text = str(text)
                self.cleared = 0

            def clear(self):
                self.text = ""
                self.cleared += 1

        class DummyButton:
            def __init__(self):
                self.values = []

            def setEnabled(self, value):
                self.values.append(bool(value))

        class DummyRow:
            def __init__(self, role, text, finished, auto_scroll):
                self.role = str(role)
                self.text = str(text)
                self.finished = bool(finished)
                self.auto_scroll = bool(auto_scroll)

        class DummyBridge(BridgeRuntimeMixin):
            _submit_user_message = BridgeRuntimeMixin._submit_user_message
            _attachment_bar_display_items = BridgeRuntimeMixin._attachment_bar_display_items
            _pending_input_attachments = BridgeRuntimeMixin._pending_input_attachments

            def __init__(self):
                self.current_session = {"id": "sess-1", "bubbles": [], "channel_id": "launcher"}
                self._pending_input_attachments_data = []
                self._active_turn_attachments_data = []
                self._current_turn_user_row = None
                self._busy = False
                self._abort_requested = False
                self._bridge_ready = True
                self.send_btn = DummyButton()
                self.stop_btn = DummyButton()
                self.sent = []
                self.statuses = []
                self.rows = []
                self.scroll_calls = []
                self.persisted = []
                self.token_updates = []
                self.attachment_refreshes = 0
                self.floating_refreshes = 0

            def _handle_local_slash_command(self, *_args, **_kwargs):
                return False

            def _is_channel_process_session(self):
                return False

            def _is_remote_session(self, _session=None):
                return False

            def _ensure_session(self, text):
                self.ensured_text = str(text)

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                row = DummyRow(role, text, finished, auto_scroll)
                self.rows.append(row)
                return row

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _refresh_composer_enabled(self):
                pass

            def _persist_session(self, session):
                self.persisted.append(dict(session))

            def _refresh_token_label(self):
                pass

            def _update_stream_row_tokens(self, *, live):
                self.token_updates.append(bool(live))

            def _current_llm_name(self):
                return "test-llm"

            def _send_cmd(self, obj):
                self.sent.append(dict(obj))

            def _scroll_row_to_top(self, row, preserve_scroll_state=False):
                self.scroll_calls.append((row.role, bool(preserve_scroll_state)))

            def _refresh_input_attachment_bar(self):
                self.attachment_refreshes += 1

            def _refresh_floating_chat_window(self):
                self.floating_refreshes += 1

            def _set_current_turn_user_row(self, row):
                self._current_turn_user_row = row

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            attachment_path = tmp.name
        self.addCleanup(lambda: os.path.exists(attachment_path) and os.remove(attachment_path))

        attachment = {"path": attachment_path, "name": "demo.png", "owned": False}
        dummy = DummyBridge()
        dummy._pending_input_attachments_data = [dict(attachment)]
        editor = DummyEditor("hello")

        result = dummy._submit_user_message("hello", attachments=[dict(attachment)], source_editor=editor)

        self.assertTrue(result)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.sent, [{"cmd": "send", "text": "hello", "images": [attachment_path], "session_id": "sess-1"}])
        self.assertEqual(dummy._pending_input_attachments_data, [])
        self.assertEqual(dummy._active_turn_attachments_data, [attachment])
        self.assertEqual(dummy._attachment_bar_display_items(), [])
        self.assertEqual(dummy.attachment_refreshes, 1)
        self.assertEqual(dummy.floating_refreshes, 1)
        self.assertEqual(dummy.scroll_calls, [("user", True)])
        self.assertIs(dummy._current_turn_user_row, dummy.rows[0])

    def test_submit_user_message_with_images_only_keeps_current_turn_anchor(self):
        class DummyEditor:
            def __init__(self, text=""):
                self.text = str(text)
                self.cleared = 0

            def clear(self):
                self.text = ""
                self.cleared += 1

        class DummyButton:
            def __init__(self):
                self.values = []

            def setEnabled(self, value):
                self.values.append(bool(value))

        class DummyRow:
            def __init__(self, role, text, finished, auto_scroll):
                self.role = str(role)
                self.text = str(text)
                self.finished = bool(finished)
                self.auto_scroll = bool(auto_scroll)

        class DummyBridge(BridgeRuntimeMixin):
            _submit_user_message = BridgeRuntimeMixin._submit_user_message
            _attachment_bar_display_items = BridgeRuntimeMixin._attachment_bar_display_items
            _pending_input_attachments = BridgeRuntimeMixin._pending_input_attachments

            def __init__(self):
                self.current_session = {"id": "sess-1", "bubbles": [], "channel_id": "launcher"}
                self._pending_input_attachments_data = []
                self._active_turn_attachments_data = []
                self._current_turn_user_row = None
                self._busy = False
                self._abort_requested = False
                self._bridge_ready = True
                self.send_btn = DummyButton()
                self.stop_btn = DummyButton()
                self.sent = []
                self.rows = []
                self.scroll_calls = []
                self.attachment_refreshes = 0

            def _handle_local_slash_command(self, *_args, **_kwargs):
                return False

            def _is_channel_process_session(self):
                return False

            def _is_remote_session(self, _session=None):
                return False

            def _ensure_session(self, _text):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                row = DummyRow(role, text, finished, auto_scroll)
                self.rows.append(row)
                return row

            def _set_status(self, _text):
                return None

            def _refresh_composer_enabled(self):
                return None

            def _persist_session(self, _session):
                return None

            def _refresh_token_label(self):
                return None

            def _update_stream_row_tokens(self, *, live):
                return None

            def _current_llm_name(self):
                return "test-llm"

            def _send_cmd(self, obj):
                self.sent.append(dict(obj))

            def _scroll_row_to_top(self, row, preserve_scroll_state=False):
                self.scroll_calls.append((row.role, bool(preserve_scroll_state)))

            def _refresh_input_attachment_bar(self):
                self.attachment_refreshes += 1

            def _set_current_turn_user_row(self, row):
                self._current_turn_user_row = row

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            attachment_path = tmp.name
        self.addCleanup(lambda: os.path.exists(attachment_path) and os.remove(attachment_path))

        attachment = {"path": attachment_path, "name": "demo.png", "owned": False}
        dummy = DummyBridge()
        dummy._pending_input_attachments_data = [dict(attachment)]
        editor = DummyEditor("")

        result = dummy._submit_user_message("", attachments=[dict(attachment)], source_editor=editor)

        self.assertTrue(result)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.sent, [{"cmd": "send", "text": "", "images": [attachment_path], "session_id": "sess-1"}])
        self.assertEqual(dummy.current_session["bubbles"][-1]["text"], "[已发送 1 张图片]")
        self.assertEqual(dummy._attachment_bar_display_items(), [])
        self.assertEqual(dummy.attachment_refreshes, 1)
        self.assertEqual(dummy.scroll_calls, [("user", True)])
        self.assertIs(dummy._current_turn_user_row, dummy.rows[0])

    def test_submit_user_message_skips_auto_jump_when_theme_toggle_disabled(self):
        class DummyEditor:
            def __init__(self, text=""):
                self.text = str(text)
                self.cleared = 0

            def clear(self):
                self.text = ""
                self.cleared += 1

        class DummyButton:
            def __init__(self):
                self.values = []

            def setEnabled(self, value):
                self.values.append(bool(value))

        class DummyRow:
            def __init__(self, role, text, finished, auto_scroll):
                self.role = str(role)
                self.text = str(text)
                self.finished = bool(finished)
                self.auto_scroll = bool(auto_scroll)

        class DummyBridge(BridgeRuntimeMixin):
            _submit_user_message = BridgeRuntimeMixin._submit_user_message

            def __init__(self):
                self.cfg = {"theme_chat_auto_jump_latest": False}
                self.current_session = {"id": "sess-1", "bubbles": [], "channel_id": "launcher"}
                self._pending_input_attachments_data = []
                self._active_turn_attachments_data = []
                self._current_turn_user_row = None
                self._busy = False
                self._abort_requested = False
                self._bridge_ready = True
                self._user_scrolled_up = True
                self.send_btn = DummyButton()
                self.stop_btn = DummyButton()
                self.sent = []
                self.rows = []
                self.scroll_calls = []
                self.follow_calls = []

            def _handle_local_slash_command(self, *_args, **_kwargs):
                return False

            def _is_channel_process_session(self):
                return False

            def _is_remote_session(self, _session=None):
                return False

            def _ensure_session(self, _text):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                row = DummyRow(role, text, finished, auto_scroll)
                self.rows.append(row)
                return row

            def _set_status(self, _text):
                return None

            def _refresh_composer_enabled(self):
                return None

            def _persist_session(self, _session):
                return None

            def _refresh_token_label(self):
                return None

            def _update_stream_row_tokens(self, *, live):
                return None

            def _current_llm_name(self):
                return "test-llm"

            def _send_cmd(self, obj):
                self.sent.append(dict(obj))

            def _scroll_row_to_top(self, row, preserve_scroll_state=False):
                self.scroll_calls.append((row.role, bool(preserve_scroll_state)))

            def _set_follow_latest_user(self, value):
                self.follow_calls.append(bool(value))

            def _refresh_input_attachment_bar(self):
                return None

            def _refresh_floating_chat_window(self):
                return None

            def _set_current_turn_user_row(self, row):
                self._current_turn_user_row = row

        dummy = DummyBridge()
        editor = DummyEditor("hello")

        result = dummy._submit_user_message("hello", attachments=[], source_editor=editor)

        self.assertTrue(result)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.sent, [{"cmd": "send", "text": "hello", "images": [], "session_id": "sess-1"}])
        self.assertEqual(dummy.scroll_calls, [])
        self.assertEqual(dummy.follow_calls, [False])
        self.assertTrue(dummy._user_scrolled_up)
        self.assertFalse(getattr(dummy, "_turn_auto_jump_latest", False))
        self.assertIs(dummy._current_turn_user_row, dummy.rows[0])

    def test_remote_exec_chat_turn_sends_uploaded_remote_image_paths(self):
        class DummyStdin:
            def __init__(self):
                self.writes = []

            def write(self, data):
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                self.writes.append(str(data))

            def flush(self):
                return None

        class DummyChannel:
            def __init__(self, owner):
                self._owner = owner

            def settimeout(self, _value):
                return None

            def exit_status_ready(self):
                return bool(getattr(self._owner, "done", False))

        class DummyStdout:
            def __init__(self, lines):
                self._lines = [str(line) + "\n" for line in lines]
                self.done = False
                self.channel = DummyChannel(self)

            def __iter__(self):
                for line in self._lines:
                    yield line
                self.done = True

        class DummyStderr:
            def __iter__(self):
                return iter(())

        class DummyClient:
            def __init__(self, stdin, stdout, stderr):
                self.stdin = stdin
                self.stdout = stdout
                self.stderr = stderr
                self.closed = False

            def exec_command(self, *_args, **_kwargs):
                return self.stdin, self.stdout, self.stderr

            def close(self):
                self.closed = True

        class DummyBridge(BridgeRuntimeMixin):
            _remote_exec_chat_turn = BridgeRuntimeMixin._remote_exec_chat_turn
            _remote_parse_bridge_event_text = BridgeRuntimeMixin._remote_parse_bridge_event_text

            def __init__(self, client):
                self.client = client
                self.cleaned = []
                self.emitted = []
                self.stage_calls = []

            def _remote_device_payload(self, _session):
                return {"agent_dir": "/srv/agant", "python_cmd": "python3", "username": "root"}, {}

            def _open_vps_ssh_client(self, _payload, timeout=12):
                return self.client, "", "", False

            def _remote_stage_bridge_runtime(self, _client, _remote_dir):
                return "/srv/agant/temp/bridge_runtime.py"

            def _remote_stage_chat_images(self, _client, _remote_dir, images):
                self.stage_calls.append(list(images))
                return ["/srv/agant/temp/launcher_runtime/chat_uploads/remote-1.png"]

            def _session_reasoning_effort_payload(self, session=None):
                return False, None

            def _remote_emit_bridge_event(self, ev, *, session_id=""):
                self.emitted.append((dict(ev), str(session_id or "")))

            def _remote_cleanup_files(self, _client, remote_paths, device=None):
                self.cleaned.append(list(remote_paths))

        stdin = DummyStdin()
        stdout = DummyStdout(
            [
                json.dumps({"event": "ready"}, ensure_ascii=False),
                json.dumps({"event": "state_loaded"}, ensure_ascii=False),
                json.dumps({"event": "done", "text": "ok"}, ensure_ascii=False),
            ]
        )
        client = DummyClient(stdin, stdout, DummyStderr())
        dummy = DummyBridge(client)

        result = dummy._remote_exec_chat_turn({"id": "sess-remote"}, "hello", ["C:/tmp/demo.png"])

        commands = [
            json.loads(line)
            for chunk in stdin.writes
            for line in str(chunk).splitlines()
            if str(line).strip()
        ]
        send_cmd = next(cmd for cmd in commands if cmd.get("cmd") == "send")

        self.assertEqual(result, "ok")
        self.assertEqual(dummy.stage_calls, [["C:/tmp/demo.png"]])
        self.assertEqual(send_cmd["text"], "hello")
        self.assertEqual(send_cmd["images"], ["/srv/agant/temp/launcher_runtime/chat_uploads/remote-1.png"])
        self.assertEqual(dummy.cleaned, [["/srv/agant/temp/launcher_runtime/chat_uploads/remote-1.png"]])
        self.assertTrue(client.closed)

    def test_local_slash_clear_input_clears_main_and_floating_drafts(self):
        class DummyEditor:
            def __init__(self, text=""):
                self.text = str(text)
                self.cleared = 0

            def clear(self):
                self.text = ""
                self.cleared += 1

            def toPlainText(self):
                return self.text

            def setPlainText(self, text):
                self.text = str(text)

        class DummyFloating:
            def __init__(self):
                self.input_box = DummyEditor("stale-floating-draft")

        class DummyBridge(BridgeRuntimeMixin):
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input

            def __init__(self):
                self.input_box = DummyEditor("/help")
                self._floating_chat_window = DummyFloating()
                self._pending_input_attachments_data = [{"path": "demo.png"}]
                self.attachment_refreshes = 0

            def _refresh_input_attachment_bar(self):
                self.attachment_refreshes += 1

            def _sync_draft_to_floating(self, *, force=False):
                if force:
                    self._floating_chat_window.input_box.setPlainText(self.input_box.toPlainText())

        dummy = DummyBridge()
        dummy._local_slash_clear_input(source_editor=dummy.input_box)

        self.assertEqual(dummy.input_box.text, "")
        self.assertEqual(dummy._floating_chat_window.input_box.text, "")
        self.assertEqual(dummy.input_box.cleared, 1)
        self.assertEqual(dummy._floating_chat_window.input_box.cleared, 1)
        self.assertEqual(dummy._pending_input_attachments_data, [])
        self.assertEqual(dummy.attachment_refreshes, 1)

    def test_handle_local_slash_continue_no_longer_intercepts_launcher_restore(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_continue_rows = BridgeRuntimeMixin._local_slash_continue_rows
            _local_slash_restore_session = BridgeRuntimeMixin._local_slash_restore_session

            def __init__(self):
                self.current_session = {"id": "sess-current"}
                self._pending_input_attachments_data = []
                self.loaded = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_from_floating(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _current_device_context(self):
                return "local", "local"

            def _active_sessions_for_channel(self, channel_id, device_scope=None, device_id=None):
                self.request = (str(channel_id), str(device_scope), str(device_id))
                return [
                    {"id": "sess-current", "pinned": False, "updated_at": 10},
                    {"id": "sess-older", "pinned": False, "updated_at": 20},
                    {"id": "sess-newer", "pinned": True, "updated_at": 15},
                ]

            def _load_session_by_id(self, sid):
                self.loaded.append(str(sid))

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "information") as info_box, mock.patch.object(
            bridge_runtime.QMessageBox, "warning"
        ) as warning_box:
            consumed = dummy._handle_local_slash_command("/continue", source_editor=editor)

        self.assertFalse(consumed)
        self.assertEqual(editor.cleared, 0)
        self.assertFalse(hasattr(dummy, "request"))
        self.assertEqual(dummy.loaded, [])
        self.assertEqual(dummy.statuses, [])
        info_box.assert_not_called()
        warning_box.assert_not_called()

    def test_handle_local_slash_continue_n_no_longer_intercepts_launcher_restore(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_continue_rows = BridgeRuntimeMixin._local_slash_continue_rows
            _local_slash_restore_session = BridgeRuntimeMixin._local_slash_restore_session

            def __init__(self):
                self.current_session = {"id": "sess-current"}
                self._pending_input_attachments_data = []
                self.loaded = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _current_device_context(self):
                return "local", "local"

            def _active_sessions_for_channel(self, channel_id, device_scope=None, device_id=None):
                return [
                    {"id": "sess-current", "pinned": False, "updated_at": 10},
                    {"id": "sess-most-recent", "pinned": False, "updated_at": 30},
                    {"id": "sess-second", "pinned": True, "updated_at": 20},
                    {"id": "sess-third", "pinned": False, "updated_at": 15},
                ]

            def _load_session_by_id(self, sid):
                self.loaded.append(str(sid))

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()

        consumed = dummy._handle_local_slash_command("/continue 2", source_editor=editor)

        self.assertFalse(consumed)
        self.assertEqual(editor.cleared, 0)
        self.assertEqual(dummy.loaded, [])
        self.assertEqual(dummy.statuses, [])

    def test_handle_local_slash_cost_formats_current_session_summary(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_cost_number = BridgeRuntimeMixin._local_slash_cost_number
            _local_slash_cost_time_label = BridgeRuntimeMixin._local_slash_cost_time_label
            _local_slash_cost_context_metrics = BridgeRuntimeMixin._local_slash_cost_context_metrics
            _local_slash_cost_section_lines = BridgeRuntimeMixin._local_slash_cost_section_lines
            _handle_local_slash_cost = BridgeRuntimeMixin._handle_local_slash_cost
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback

            def __init__(self):
                self.current_session = {
                    "id": "sess-cost",
                    "title": "Graphite",
                    "backend_history": [{"role": "user", "content": "hello"}],
                    "snapshot": {"context_window_chars": 1200, "current_input_chars": 300},
                    "token_usage": {
                        "events": [
                            {
                                "input_tokens": 100,
                                "output_tokens": 90,
                                "total_tokens": 190,
                                "cache_creation_input_tokens": 20,
                                "cache_read_input_tokens": 50,
                                "api_calls": 3,
                                "usage_source": "provider",
                            }
                        ],
                        "last_model": "gpt-5.4",
                    },
                }
                self._pending_input_attachments_data = []
                self.rows = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()

        consumed = dummy._handle_local_slash_command("/cost", source_editor=editor)

        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.statuses, ["已显示当前会话的 /cost 统计。"])
        self.assertIn("Token usage: 260 total (170 input + 90 output)", dummy.rows[-1][1])
        self.assertIn("Cache: 50 read · 20 created · 29.4% hit", dummy.rows[-1][1])
        self.assertIn("Context window: 75% left (300 chars used / 1.2K cap)", dummy.rows[-1][1])

    def test_handle_local_slash_llm_switches_by_llm_idx(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyCombo:
            def __init__(self):
                self.current_index = -1

            def setCurrentIndex(self, index):
                self.current_index = int(index)

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_switch_llm = BridgeRuntimeMixin._local_slash_switch_llm
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback

            def __init__(self):
                self.llms = [
                    {"idx": 0, "name": "First", "current": True},
                    {"idx": 7, "name": "Second", "current": False},
                ]
                self.llm_combo = DummyCombo()
                self._ignore_llm_change = False
                self._pending_input_attachments_data = []
                self.changed = []
                self.rows = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_from_floating(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _on_llm_changed(self, index):
                self.changed.append(int(index))

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()
        consumed = dummy._handle_local_slash_command("/llm 7", source_editor=editor)

        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.llm_combo.current_index, 1)
        self.assertEqual(dummy.changed, [1])
        self.assertEqual(dummy.statuses, ["已切换模型。"])

    def test_handle_local_slash_help_emits_chat_feedback_instead_of_dialog(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback
            _local_slash_help_text = BridgeRuntimeMixin._local_slash_help_text

            def __init__(self):
                self._pending_input_attachments_data = []
                self.rows = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "information") as info_box:
            consumed = dummy._handle_local_slash_command("/help", source_editor=editor)

        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertTrue(dummy.rows)
        self.assertIn("/help - 显示帮助", dummy.rows[-1][1])
        self.assertEqual(dummy.statuses, ["已显示启动器支持的斜杠命令。"])
        info_box.assert_not_called()

    def test_handle_local_slash_help_feedback_stays_transient_and_local_only(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(ChatViewMixin, BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback
            _local_slash_help_text = BridgeRuntimeMixin._local_slash_help_text
            _transient_chat_feedback_key = ChatViewMixin._transient_chat_feedback_key
            _transient_chat_feedback_rows = ChatViewMixin._transient_chat_feedback_rows
            _display_session_bubbles = ChatViewMixin._display_session_bubbles

            def __init__(self):
                self.current_session = {"id": "sess-1", "bubbles": [], "channel_id": "launcher"}
                self._pending_input_attachments_data = []
                self._transient_chat_feedback = []
                self.rows = []
                self.statuses = []
                self.persisted = []
                self.sent = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _persist_session(self, session):
                self.persisted.append(dict(session))

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "information") as info_box:
            consumed = dummy._handle_local_slash_command("/help", source_editor=editor)

        visible = dummy._display_session_bubbles(dummy.current_session)
        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.current_session["bubbles"], [])
        self.assertEqual(dummy.persisted, [])
        self.assertEqual(dummy.sent, [])
        self.assertEqual(len(dummy._transient_chat_feedback), 1)
        self.assertEqual(dummy._transient_chat_feedback[0]["key"], "session:sess-1")
        self.assertEqual(len(visible), 1)
        self.assertIn("/help - 显示帮助", visible[0]["text"])
        self.assertEqual(dummy.statuses, ["已显示启动器支持的斜杠命令。"])
        info_box.assert_not_called()

    def test_handle_local_slash_llm_respects_channel_process_read_only(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyCombo:
            def __init__(self):
                self.current_index = -1

            def setCurrentIndex(self, index):
                self.current_index = int(index)

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_switch_llm = BridgeRuntimeMixin._local_slash_switch_llm
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback

            def __init__(self):
                self.current_session = {"id": "snap-1", "session_kind": "channel_process"}
                self.llms = [
                    {"idx": 0, "name": "First", "current": True},
                    {"idx": 7, "name": "Second", "current": False},
                ]
                self.llm_combo = DummyCombo()
                self._ignore_llm_change = False
                self._pending_input_attachments_data = []
                self.changed = []
                self.rows = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _is_channel_process_session(self, session=None):
                return True

            def _on_llm_changed(self, index):
                self.changed.append(int(index))

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "information") as info_box:
            consumed = dummy._handle_local_slash_command("/llm 7", source_editor=editor)

        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertEqual(dummy.llm_combo.current_index, -1)
        self.assertEqual(dummy.changed, [])
        self.assertEqual(dummy.statuses, ["渠道进程会话仅支持查看日志，不能切换模型。"])
        self.assertIn("不能切换模型", dummy.rows[-1][1])
        info_box.assert_not_called()

    def test_handle_local_slash_llm_invalid_index_reports_actual_valid_ids(self):
        class DummyEditor:
            def __init__(self):
                self.cleared = 0

            def clear(self):
                self.cleared += 1

        class DummyBridge(BridgeRuntimeMixin):
            _handle_local_slash_command = BridgeRuntimeMixin._handle_local_slash_command
            _local_slash_clear_input = BridgeRuntimeMixin._local_slash_clear_input
            _local_slash_switch_llm = BridgeRuntimeMixin._local_slash_switch_llm
            _show_local_slash_feedback = BridgeRuntimeMixin._show_local_slash_feedback

            def __init__(self):
                self.llms = [
                    {"idx": 0, "name": "First", "current": True},
                    {"idx": 7, "name": "Second", "current": False},
                ]
                self.llm_combo = None
                self._pending_input_attachments_data = []
                self.rows = []
                self.statuses = []

            def _refresh_input_attachment_bar(self):
                return None

            def _sync_draft_to_floating(self, *, force=False):
                return None

            def _add_message_row(self, role, text, *, finished, auto_scroll):
                self.rows.append((str(role), str(text), bool(finished), bool(auto_scroll)))
                return object()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyBridge()
        editor = DummyEditor()
        with mock.patch.object(bridge_runtime.QMessageBox, "warning") as warning_box:
            consumed = dummy._handle_local_slash_command("/llm 1", source_editor=editor)

        self.assertTrue(consumed)
        self.assertEqual(editor.cleared, 1)
        self.assertIn("有效编号: 0, 7", dummy.rows[-1][1])
        self.assertEqual(dummy.statuses, ["斜杠命令 /llm 执行失败。"])
        warning_box.assert_not_called()

    def test_set_agent_dir_stops_lan_interface_when_switching_agent(self):
        class DummyList:
            def clear(self):
                return None

        class DummyNav(NavigationMixin):
            _set_agent_dir = NavigationMixin._set_agent_dir

            def __init__(self):
                self.agent_dir = "C:\\old-agent"
                self.cfg = {}
                self.current_session = None
                self._selected_session_id = None
                self._pending_state_session = None
                self._ignore_session_select = False
                self._last_session_list_signature = None
                self.session_list = DummyList()
                self.calls = []
                self.pages = None
                self._settings_page = None

            def _stop_bridge(self):
                self.calls.append("stop_bridge")

            def _stop_all_managed_channels(self, refresh=False):
                self.calls.append(("stop_channels", bool(refresh)))

            def _stop_scheduler_process(self, refresh=False):
                self.calls.append(("stop_scheduler", bool(refresh)))

            def _stop_lan_interface_process(self, refresh=False):
                self.calls.append(("stop_lan", bool(refresh)))

            def _refresh_welcome_state(self):
                self.calls.append("refresh_welcome")

            def _settings_reload(self, categories=None, force=False):
                self.calls.append(("settings_reload", list(categories or []), bool(force)))

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._set_agent_dir("C:\\new-agent", persist=False)

        self.assertIn(("stop_lan", False), dummy.calls)
        self.assertLess(dummy.calls.index(("stop_scheduler", False)), dummy.calls.index(("stop_lan", False)))

    def test_set_agent_dir_clears_scheduler_and_lan_exit_state(self):
        class DummyList:
            def clear(self):
                return None

        class DummyNav(NavigationMixin):
            _set_agent_dir = NavigationMixin._set_agent_dir

            def __init__(self):
                self.agent_dir = "C:\\old-agent"
                self.cfg = {}
                self.current_session = None
                self._selected_session_id = None
                self._pending_state_session = None
                self._ignore_session_select = False
                self._last_session_list_signature = None
                self._scheduler_last_exit_code = 23
                self._lan_interface_last_exit_code = 99
                self.session_list = DummyList()
                self.pages = None
                self._settings_page = None

            def _stop_bridge(self):
                return None

            def _stop_all_managed_channels(self, refresh=False):
                return None

            def _stop_scheduler_process(self, refresh=False):
                return None

            def _stop_lan_interface_process(self, refresh=False):
                return None

            def _refresh_welcome_state(self):
                return None

            def _settings_reload(self, categories=None, force=False):
                return None

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._set_agent_dir("C:\\new-agent", persist=False)

        self.assertIsNone(dummy._scheduler_last_exit_code)
        self.assertIsNone(dummy._lan_interface_last_exit_code)

    def test_set_agent_dir_invalidates_remote_settings_load_flags_and_generation(self):
        class DummyList:
            def clear(self):
                return None

        class DummyNav(NavigationMixin):
            _set_agent_dir = NavigationMixin._set_agent_dir

            def __init__(self):
                self.agent_dir = "C:\\old-agent"
                self.cfg = {}
                self.current_session = None
                self._selected_session_id = None
                self._pending_state_session = None
                self._ignore_session_select = False
                self._last_session_list_signature = None
                self._local_channel_autostart_scheduled = True
                self._chat_runtime_bootstrap_pending = True
                self._lan_interface_autostart_scheduled = True
                self._lan_interface_autostart_running = True
                self._qt_api_remote_loading = True
                self._qt_channel_remote_loading = True
                self._settings_personal_remote_sync_running = True
                self._settings_usage_remote_sync_running = True
                self._settings_personal_remote_sync_key = "old-personal"
                self._settings_personal_remote_synced_key = "old-personal-synced"
                self._settings_usage_remote_sync_key = "old-usage"
                self._settings_usage_remote_synced_key = "old-usage-synced"
                self._remote_channel_sync_running = True
                self._remote_launcher_sync_running = True
                self._remote_launcher_sync_pending_force = True
                self._remote_launcher_sync_pending_device_id = "box-1"
                self._remote_launcher_sync_pending_refresh = True
                self._next_remote_launcher_sync_at = 12.3
                self._next_remote_channel_sync_at = 45.6
                self._settings_schedule_remote_reload_token = 99
                self._settings_target_change_token = 7
                self._runtime_context_generation = 2
                self.session_list = DummyList()
                self.pages = None
                self._settings_page = None

            def _stop_bridge(self):
                return None

            def _stop_all_managed_channels(self, refresh=False):
                return None

            def _stop_scheduler_process(self, refresh=False):
                return None

            def _stop_lan_interface_process(self, refresh=False):
                return None

            def _bump_settings_target_generation(self):
                self._settings_target_change_token += 1
                return self._settings_target_change_token

            def _refresh_welcome_state(self):
                return None

            def _settings_reload(self, categories=None, force=False):
                return None

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._set_agent_dir("C:\\new-agent", persist=False)

        self.assertFalse(dummy._local_channel_autostart_scheduled)
        self.assertFalse(dummy._chat_runtime_bootstrap_pending)
        self.assertFalse(dummy._lan_interface_autostart_scheduled)
        self.assertFalse(dummy._lan_interface_autostart_running)
        self.assertFalse(dummy._qt_api_remote_loading)
        self.assertFalse(dummy._qt_channel_remote_loading)
        self.assertFalse(dummy._settings_personal_remote_sync_running)
        self.assertFalse(dummy._settings_usage_remote_sync_running)
        self.assertEqual(dummy._settings_personal_remote_sync_key, "")
        self.assertEqual(dummy._settings_personal_remote_synced_key, "")
        self.assertEqual(dummy._settings_usage_remote_sync_key, "")
        self.assertEqual(dummy._settings_usage_remote_synced_key, "")
        self.assertFalse(dummy._remote_channel_sync_running)
        self.assertFalse(dummy._remote_launcher_sync_running)
        self.assertFalse(dummy._remote_launcher_sync_pending_force)
        self.assertEqual(dummy._remote_launcher_sync_pending_device_id, "")
        self.assertFalse(dummy._remote_launcher_sync_pending_refresh)
        self.assertEqual(dummy._next_remote_launcher_sync_at, 0.0)
        self.assertEqual(dummy._next_remote_channel_sync_at, 0.0)
        self.assertEqual(dummy._settings_schedule_remote_reload_token, 0)
        self.assertEqual(dummy._settings_target_change_token, 8)
        self.assertEqual(dummy._runtime_context_generation, 3)

    def test_load_config_migrates_launcher_config_from_install_root(self):
        with tempfile.TemporaryDirectory() as td:
            install_root = os.path.join(td, "Programs", "GenericAgentLauncher")
            version_dir = os.path.join(install_root, "app", "versions", "1.2.3")
            data_root = os.path.join(td, "GenericAgentLauncherData")
            os.makedirs(version_dir, exist_ok=True)
            legacy_path = os.path.join(install_root, "launcher_config.json")
            expected = {"agent_dir": "agent", "remote_devices": [{"id": "srv-1", "host": "10.0.0.8"}]}
            with open(legacy_path, "w", encoding="utf-8") as f:
                json.dump(expected, f, ensure_ascii=False, indent=2)

            patched = {
                "APP_DIR": version_dir,
                "IS_WINDOWS": True,
                "PROGRAMS_ROOT": install_root,
                "DATA_ROOT": data_root,
                "CONFIG_PATH": os.path.join(data_root, "config", "launcher_config.json"),
                "LEGACY_CONFIG_PATH": os.path.join(version_dir, "launcher_config.json"),
                "STATE_DIR": os.path.join(data_root, "state"),
                "UPDATES_DIR": os.path.join(data_root, "updates"),
                "UPDATE_JOBS_DIR": os.path.join(data_root, "updates", "jobs"),
                "UPDATE_DOWNLOADS_DIR": os.path.join(data_root, "updates", "downloads"),
                "UPDATE_STAGING_DIR": os.path.join(data_root, "updates", "staging"),
            }
            originals = {name: getattr(runtime, name) for name in patched}
            try:
                for name, value in patched.items():
                    setattr(runtime, name, value)
                with mock.patch.dict(os.environ, {"GA_LAUNCHER_PROGRAMS_ROOT": ""}, clear=False):
                    loaded = runtime.load_config()
            finally:
                for name, value in originals.items():
                    setattr(runtime, name, value)

            self.assertEqual(loaded, expected)
            self.assertTrue(os.path.isfile(patched["CONFIG_PATH"]))
            with open(patched["CONFIG_PATH"], "r", encoding="utf-8") as f:
                persisted = json.load(f)
            self.assertEqual(persisted, expected)

    def test_refresh_settings_target_combo_auto_fallback_invalidates_target_cached_pages(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self._current_index = 0

            def count(self):
                return len(self.items)

            def currentIndex(self):
                return self._current_index

            def blockSignals(self, _blocked):
                return None

            def setCurrentIndex(self, index):
                self._current_index = int(index)

            def clear(self):
                self.items.clear()
                self._current_index = 0

            def addItem(self, label, data):
                self.items.append((label, data))

            def itemData(self, index):
                try:
                    return self.items[int(index)][1]
                except Exception:
                    return None

        class DummySettings(SettingsPanelMixin):
            _refresh_settings_target_combo = SettingsPanelMixin._refresh_settings_target_combo
            _apply_settings_target_selection = SettingsPanelMixin._apply_settings_target_selection

            def __init__(self):
                self.cfg = {}
                self.settings_target_combo = DummyCombo()
                self._settings_target_scope = "remote"
                self._settings_target_device_id = "missing-box"
                self._settings_target_combo_signature = (("远程设备", "remote", "missing-box", "10.0.0.8"),)
                self._settings_target_change_token = 4
                self._settings_loaded_categories = {"api", "channels", "schedule", "personal", "usage", "theme"}
                self._current_settings_category = "api"
                self.calls = []

            def _normalize_settings_target(self, raw):
                data = dict(raw or {})
                scope = str(data.get("scope") or "local").strip().lower()
                device_id = str(data.get("device_id") or "local").strip() or "local"
                if scope not in ("local", "remote"):
                    scope = "local"
                    device_id = "local"
                if scope == "local":
                    device_id = "local"
                return {"scope": scope, "device_id": device_id}

            def _settings_target_combo_entries(self):
                return [("本机", {"scope": "local", "device_id": "local"})]

            def _sync_personal_target_combo(self, entries, target_index, signature, force=False):
                self.calls.append(("sync_personal", int(target_index), tuple(signature), bool(force)))

            def _dismiss_combo_popup(self, combo):
                self.calls.append("dismiss_popup")

            def _bump_settings_target_generation(self):
                self._settings_target_change_token += 1
                return self._settings_target_change_token

            def _refresh_settings_target_notice(self):
                self.calls.append("refresh_notice")

            def _refresh_settings_target_visibility(self, key=None):
                self.calls.append(("refresh_visibility", key))

            def _settings_category_uses_target_switch(self, key):
                return str(key or "").strip().lower() in {"api", "channels", "schedule", "usage"}

            def _settings_reload(self, *, categories=None, force=False):
                self.calls.append(("settings_reload", list(categories or []), bool(force)))

        dummy = DummySettings()
        with mock.patch.object(lz, "save_config") as save_config:
            dummy._refresh_settings_target_combo(force=False)

        self.assertEqual(dummy._settings_target_scope, "local")
        self.assertEqual(dummy._settings_target_device_id, "local")
        self.assertEqual(dummy._settings_target_change_token, 5)
        self.assertEqual(dummy.cfg.get("settings_target"), {"scope": "local", "device_id": "local"})
        self.assertNotIn("api", dummy._settings_loaded_categories)
        self.assertNotIn("channels", dummy._settings_loaded_categories)
        self.assertNotIn("schedule", dummy._settings_loaded_categories)
        self.assertNotIn("personal", dummy._settings_loaded_categories)
        self.assertNotIn("usage", dummy._settings_loaded_categories)
        self.assertIn("theme", dummy._settings_loaded_categories)
        save_config.assert_called_once_with(dummy.cfg)

    def test_schedule_summary_status_reports_external_running_for_local_scheduler(self):
        class DummySchedule(ScheduleRuntimeMixin):
            def __init__(self):
                self._scheduler_proc = None
                self._scheduler_last_exit_code = None

            def _scheduler_cleanup_if_exited(self):
                return None

            def _schedule_last_data(self):
                return {"enabled_count": 1}

            def _scheduler_proc_alive(self):
                return False

            def _scheduler_external_running(self):
                return True

        dummy = DummySchedule()
        summary = dummy._schedule_summary_status()
        self.assertEqual(summary["text"], "外部运行中")
        self.assertEqual(summary["code"], "running")

    def test_reload_schedule_panel_clears_stale_snapshot_when_remote_target_missing(self):
        class DummySchedule(ScheduleRuntimeMixin):
            _reload_schedule_panel = ScheduleRuntimeMixin._reload_schedule_panel
            _schedule_reset_snapshot = ScheduleRuntimeMixin._schedule_reset_snapshot

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.settings_schedule_notice = mock.Mock()
                self.settings_schedule_list_layout = object()
                self._schedule_last_data_snapshot = {"is_remote": True, "tasks": [{"id": "old"}], "runtime_status": "运行中"}
                self._schedule_task_state_rows_data = [{"task_id": "old"}]

            def _clear_layout(self, _layout):
                return None

            def _schedule_target_context(self):
                return {"is_remote": True, "device_id": "missing-box", "label": "缺失设备"}

            def _schedule_target_device(self):
                return None

        dummy = DummySchedule()
        dummy._reload_schedule_panel()

        snapshot = dummy._schedule_last_data_snapshot
        self.assertTrue(snapshot.get("is_remote"))
        self.assertEqual(snapshot.get("device_id"), "missing-box")
        self.assertEqual(snapshot.get("runtime_detail"), "当前设置目标对应的远程设备不存在。")
        self.assertEqual(snapshot.get("tasks"), [])
        self.assertEqual(dummy._schedule_task_state_rows_data, [])

    def test_reload_schedule_panel_clears_stale_snapshot_when_agent_dir_invalid(self):
        class DummySchedule(ScheduleRuntimeMixin):
            _reload_schedule_panel = ScheduleRuntimeMixin._reload_schedule_panel
            _schedule_reset_snapshot = ScheduleRuntimeMixin._schedule_reset_snapshot

            def __init__(self):
                self.agent_dir = ""
                self.settings_schedule_notice = mock.Mock()
                self.settings_schedule_list_layout = object()
                self._schedule_last_data_snapshot = {"is_remote": False, "tasks": [{"id": "old"}], "runtime_status": "运行中"}
                self._schedule_task_state_rows_data = [{"task_id": "old"}]

            def _clear_layout(self, _layout):
                return None

            def _schedule_target_context(self):
                return {"is_remote": False, "device_id": "local", "label": "本机"}

        dummy = DummySchedule()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_schedule_panel()

        snapshot = dummy._schedule_last_data_snapshot
        self.assertFalse(snapshot.get("is_remote"))
        self.assertEqual(snapshot.get("runtime_detail"), "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(snapshot.get("tasks"), [])
        self.assertEqual(dummy._schedule_task_state_rows_data, [])

    def test_load_mykey_source_marks_read_failures_as_load_failed(self):
        class DummySettings(SettingsPanelMixin):
            _load_mykey_source = SettingsPanelMixin._load_mykey_source

            def _settings_target_read_mykey_text(self):
                return False, "", "/remote/mykey.py", "SSH 连接失败"

        dummy = DummySettings()
        py_path, parsed = dummy._load_mykey_source()

        self.assertEqual(py_path, "/remote/mykey.py")
        self.assertTrue(parsed.get("load_failed"))
        self.assertEqual(parsed.get("error"), "SSH 连接失败")
        self.assertEqual(parsed.get("configs"), [])

    def test_load_mykey_source_parses_json_when_reader_returns_mykey_json(self):
        class DummySettings(SettingsPanelMixin):
            _load_mykey_source = SettingsPanelMixin._load_mykey_source
            _settings_parse_mykey_text = SettingsPanelMixin._settings_parse_mykey_text

            def _settings_target_read_mykey_text(self):
                payload = {
                    "native_oai_config": {
                        "name": "primary",
                        "apikey": "sk-demo",
                        "apibase": "https://api.example/v1",
                        "model": "gpt-5.4",
                    }
                }
                return True, json.dumps(payload, ensure_ascii=False), "C:\\demo\\mykey.json", ""

        dummy = DummySettings()
        py_path, parsed = dummy._load_mykey_source()

        self.assertEqual(py_path, "C:\\demo\\mykey.json")
        self.assertEqual(parsed.get("error"), None)
        self.assertEqual(len(parsed.get("configs") or []), 1)
        self.assertEqual(parsed["configs"][0]["data"]["name"], "primary")

    def test_settings_target_list_sop_documents_filters_supported_local_files(self):
        class DummySettings(SettingsPanelMixin):
            _settings_sop_normalize_relpath = SettingsPanelMixin._settings_sop_normalize_relpath
            _settings_sop_label = SettingsPanelMixin._settings_sop_label
            _settings_target_list_sop_documents = SettingsPanelMixin._settings_target_list_sop_documents

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir

            def _settings_target_context(self):
                return {"is_remote": False, "label": "本机"}

        with tempfile.TemporaryDirectory() as td:
            memory_dir = os.path.join(td, "memory")
            os.makedirs(os.path.join(memory_dir, "skill_search"), exist_ok=True)
            os.makedirs(os.path.join(memory_dir, "autonomous_operation_sop"), exist_ok=True)
            with open(os.path.join(memory_dir, "scheduled_task_sop.md"), "w", encoding="utf-8") as f:
                f.write("# scheduled task sop\n")
            with open(os.path.join(memory_dir, "skill_search", "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("# skill search\n")
            with open(os.path.join(memory_dir, "subagent.md"), "w", encoding="utf-8") as f:
                f.write("# ignored\n")
            with open(os.path.join(memory_dir, "autonomous_operation_sop", "task_planning.md"), "w", encoding="utf-8") as f:
                f.write("# ignored nested planning\n")

            dummy = DummySettings(td)
            with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
                docs, err = dummy._settings_target_list_sop_documents()

        self.assertEqual(err, "")
        self.assertEqual(
            [item["relpath"] for item in docs],
            ["memory/scheduled_task_sop.md", "memory/skill_search/SKILL.md"],
        )
        self.assertEqual(
            [item["label"] for item in docs],
            ["scheduled_task_sop.md", "skill_search/SKILL.md"],
        )

    def test_settings_target_read_and_write_sop_text_round_trip_locally(self):
        class DummySettings(SettingsPanelMixin):
            _settings_sop_normalize_relpath = SettingsPanelMixin._settings_sop_normalize_relpath
            _settings_target_read_sop_text = SettingsPanelMixin._settings_target_read_sop_text
            _settings_target_write_sop_text = SettingsPanelMixin._settings_target_write_sop_text
            _settings_target_display_path = SettingsPanelMixin._settings_target_display_path

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir

            def _settings_target_context(self):
                return {"is_remote": False, "label": "本机"}

        with tempfile.TemporaryDirectory() as td:
            memory_dir = os.path.join(td, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            sop_path = os.path.join(memory_dir, "plan_sop.md")
            with open(sop_path, "w", encoding="utf-8") as f:
                f.write("before = 1\n")

            dummy = DummySettings(td)
            with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
                ok, text, display_path, err = dummy._settings_target_read_sop_text("memory/plan_sop.md")
                self.assertTrue(ok)
                self.assertEqual(text, "before = 1\n")
                self.assertEqual(display_path, sop_path)
                self.assertEqual(err, "")

                ok, display_path, err = dummy._settings_target_write_sop_text("memory/plan_sop.md", "after = 2\n")
                self.assertTrue(ok)
                self.assertEqual(display_path, sop_path)
                self.assertEqual(err, "")

            with open(sop_path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "after = 2\n")

    def test_settings_target_read_mykey_text_reads_legacy_docker_target_via_direct_ssh_path(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self._storage = storage

            def open(self, path, mode):
                return DummyRemoteFile(self._storage, path, mode)

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._storage = storage

            def open_sftp(self):
                return DummySFTP(self._storage)

            def close(self):
                return None

        class DummySettings(SettingsPanelMixin):
            _settings_target_read_mykey_text = SettingsPanelMixin._settings_target_read_mykey_text
            _settings_target_ensure_remote_mykey = SettingsPanelMixin._settings_target_ensure_remote_mykey
            _settings_target_display_path = SettingsPanelMixin._settings_target_display_path
            _settings_target_remote_agent_dir = SettingsPanelMixin._settings_target_remote_agent_dir
            _settings_target_remote_stage_dir = SettingsPanelMixin._settings_target_remote_stage_dir

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage
                self.commands = []
                self._device = {
                    "id": "box-1",
                    "name": "旧版远端",
                    "host": "10.0.0.8",
                    "username": "root",
                    "agent_mode": "docker",
                    "remote_mode": "docker_container",
                    "docker_container": "ga-prod",
                    "docker_agent_dir": "/opt/agant",
                    "agent_dir": "/opt/agant",
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device": self._device}

            def _settings_target_open_remote_client(self, device, timeout=10):
                return DummyClient(self._storage), ""

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, "", ""

        storage = {"/opt/agant/mykey.py": b"host_value = 1\n"}
        dummy = DummySettings(storage)

        ok, text, display_path, err = dummy._settings_target_read_mykey_text()

        self.assertTrue(ok)
        self.assertEqual(text, "host_value = 1\n")
        self.assertEqual(display_path, "/opt/agant/mykey.py")
        self.assertEqual(err, "")
        self.assertTrue(any("mkdir -p /opt/agant" in cmd for cmd in dummy.commands))
        self.assertFalse(any("docker " in cmd for cmd in dummy.commands))
        self.assertEqual(storage["/opt/agant/mykey.py"], b"host_value = 1\n")

    def test_settings_target_write_mykey_text_writes_legacy_docker_target_via_direct_ssh_path(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self._storage = storage

            def open(self, path, mode):
                return DummyRemoteFile(self._storage, path, mode)

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._storage = storage

            def open_sftp(self):
                return DummySFTP(self._storage)

            def close(self):
                return None

        class DummySettings(SettingsPanelMixin):
            _settings_target_write_mykey_text = SettingsPanelMixin._settings_target_write_mykey_text
            _settings_target_ensure_remote_mykey = SettingsPanelMixin._settings_target_ensure_remote_mykey
            _settings_target_display_path = SettingsPanelMixin._settings_target_display_path
            _settings_target_remote_agent_dir = SettingsPanelMixin._settings_target_remote_agent_dir
            _settings_target_remote_stage_dir = SettingsPanelMixin._settings_target_remote_stage_dir

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage
                self.commands = []
                self._device = {
                    "id": "box-1",
                    "name": "旧版远端",
                    "host": "10.0.0.8",
                    "username": "root",
                    "agent_mode": "docker",
                    "remote_mode": "docker_container",
                    "docker_container": "ga-prod",
                    "docker_agent_dir": "/opt/agant",
                    "agent_dir": "/opt/agant",
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device": self._device}

            def _settings_target_open_remote_client(self, device, timeout=10):
                return DummyClient(self._storage), ""

            def _vps_exec_remote(self, client, cmd, timeout=0):
                text = str(cmd)
                self.commands.append(text)
                if text.startswith("mv -f "):
                    for path in list(self._storage):
                        if path.startswith("/opt/agant/mykey.py.tmp."):
                            self._storage["/opt/agant/mykey.py"] = self._storage.pop(path)
                            break
                return 0, "", ""

        storage = {}
        dummy = DummySettings(storage)

        ok, display_path, err = dummy._settings_target_write_mykey_text("docker_write = 1\n")

        self.assertTrue(ok)
        self.assertEqual(display_path, "/opt/agant/mykey.py")
        self.assertEqual(err, "")
        staged_files = [path for path in storage if path.startswith("/opt/agant/mykey.py.tmp.")]
        self.assertEqual(staged_files, [])
        self.assertEqual(storage["/opt/agant/mykey.py"], b"docker_write = 1\n")
        self.assertTrue(any("mv -f " in cmd and "/opt/agant/mykey.py.tmp." in cmd and "/opt/agant/mykey.py" in cmd for cmd in dummy.commands))
        self.assertFalse(any("docker " in cmd for cmd in dummy.commands))

    def test_settings_target_write_mykey_text_surfaces_move_failure_for_legacy_docker_target_after_ssh_normalization(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self._storage = storage

            def open(self, path, mode):
                return DummyRemoteFile(self._storage, path, mode)

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._storage = storage

            def open_sftp(self):
                return DummySFTP(self._storage)

            def close(self):
                return None

        class DummySettings(SettingsPanelMixin):
            _settings_target_write_mykey_text = SettingsPanelMixin._settings_target_write_mykey_text
            _settings_target_ensure_remote_mykey = SettingsPanelMixin._settings_target_ensure_remote_mykey
            _settings_target_display_path = SettingsPanelMixin._settings_target_display_path
            _settings_target_remote_agent_dir = SettingsPanelMixin._settings_target_remote_agent_dir
            _settings_target_remote_stage_dir = SettingsPanelMixin._settings_target_remote_stage_dir

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage
                self.commands = []
                self._device = {
                    "id": "box-1",
                    "name": "旧版远端",
                    "host": "10.0.0.8",
                    "username": "root",
                    "agent_mode": "docker",
                    "remote_mode": "docker_container",
                    "docker_container": "ga-prod",
                    "docker_agent_dir": "/opt/agant",
                    "agent_dir": "/opt/agant",
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device": self._device}

            def _settings_target_open_remote_client(self, device, timeout=10):
                return DummyClient(self._storage), ""

            def _vps_exec_remote(self, client, cmd, timeout=0):
                text = str(cmd)
                self.commands.append(text)
                if text.startswith("mv -f "):
                    return 1, "", "remote mv failed"
                return 0, "", ""

        storage = {}
        dummy = DummySettings(storage)

        ok, display_path, err = dummy._settings_target_write_mykey_text("docker_write = 1\n")

        self.assertFalse(ok)
        self.assertEqual(display_path, "/opt/agant/mykey.py")
        self.assertEqual(err, "remote mv failed")
        self.assertTrue(any(path.startswith("/opt/agant/mykey.py.tmp.") for path in storage))
        self.assertTrue(any("mv -f " in cmd and "/opt/agant/mykey.py.tmp." in cmd for cmd in dummy.commands))
        self.assertFalse(any("docker " in cmd for cmd in dummy.commands))

    def test_settings_target_remote_mykey_host_mode_keeps_direct_sftp_behavior(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self._storage = storage
                self.opened = []

            def open(self, path, mode):
                self.opened.append((path, mode))
                return DummyRemoteFile(self._storage, path, mode)

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._sftp = DummySFTP(storage)

            def open_sftp(self):
                return self._sftp

            def close(self):
                return None

        class DummySettings(SettingsPanelMixin):
            _settings_target_read_mykey_text = SettingsPanelMixin._settings_target_read_mykey_text
            _settings_target_write_mykey_text = SettingsPanelMixin._settings_target_write_mykey_text
            _settings_target_ensure_remote_mykey = SettingsPanelMixin._settings_target_ensure_remote_mykey
            _settings_target_display_path = SettingsPanelMixin._settings_target_display_path
            _settings_target_remote_agent_dir = SettingsPanelMixin._settings_target_remote_agent_dir
            _settings_target_remote_stage_dir = SettingsPanelMixin._settings_target_remote_stage_dir

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage
                self.commands = []
                self.client = DummyClient(storage)
                self._device = {
                    "id": "box-ssh",
                    "name": "SSH 远端",
                    "host": "10.0.0.9",
                    "username": "root",
                    "remote_mode": "ssh",
                    "agent_dir": "/srv/agant",
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device": self._device}

            def _settings_target_open_remote_client(self, device, timeout=10):
                return self.client, ""

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, "", ""

        storage = {"/srv/agant/mykey.py": b"host_value = 1\n"}
        dummy = DummySettings(storage)

        ok, text, display_path, err = dummy._settings_target_read_mykey_text()
        self.assertTrue(ok)
        self.assertEqual(text, "host_value = 1\n")
        self.assertEqual(display_path, "/srv/agant/mykey.py")
        self.assertEqual(err, "")

        ok, display_path, err = dummy._settings_target_write_mykey_text("host_write = 2\n")
        self.assertTrue(ok)
        self.assertEqual(display_path, "/srv/agant/mykey.py")
        self.assertEqual(err, "")
        self.assertTrue(any(path == "/srv/agant/mykey.py" and mode == "rb" for path, mode in dummy.client._sftp.opened))
        self.assertTrue(any(path.startswith("/srv/agant/mykey.py.tmp.") and mode == "wb" for path, mode in dummy.client._sftp.opened))
        self.assertFalse(any("docker " in cmd for cmd in dummy.commands))

    def test_schedule_run_remote_job_drops_stale_context_before_success_callback(self):
        class DummyNotice:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummySchedule(ScheduleRuntimeMixin):
            _schedule_run_remote_job = ScheduleRuntimeMixin._schedule_run_remote_job

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self._settings_target_change_token = 5
                self.settings_schedule_notice = DummyNotice()
                self.calls = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _api_on_ui_thread(self, fn):
                fn()

        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                dummy._runtime_context_generation += 1
                if callable(self._target):
                    self._target()

        dummy = DummySchedule()
        with mock.patch.object(schedule_runtime.threading, "Thread", ImmediateThread):
            dummy._schedule_run_remote_job(
                title="同步目录",
                notice_text="正在同步…",
                worker=lambda: ("C:\\cache", 3),
                on_success=lambda result: dummy.calls.append(result),
            )

        self.assertEqual(dummy.settings_schedule_notice.text, "正在同步…")
        self.assertEqual(dummy.calls, [])

    def test_start_scheduler_process_delayed_check_drops_stale_context(self):
        class DummyProc:
            returncode = None

            def poll(self):
                return None

        class DummySchedule(ScheduleRuntimeMixin):
            _start_scheduler_process = ScheduleRuntimeMixin._start_scheduler_process
            _after_scheduler_launch_check = ScheduleRuntimeMixin._after_scheduler_launch_check

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir
                self.cfg = {"python_exe": sys.executable}
                self._runtime_context_generation = 3
                self._scheduler_proc = None
                self._scheduler_log_handle = None
                self._scheduler_last_exit_code = None
                self.reload_calls = 0

            def _schedule_target_context(self):
                return {"is_remote": False}

            def _scheduler_proc_alive(self):
                proc = getattr(self, "_scheduler_proc", None)
                return bool(proc and proc.poll() is None)

            def _scheduler_external_running(self):
                return False

            def _check_runtime_dependencies(self, purpose="", visual=True):
                return True

            def _reload_schedule_panel(self):
                self.reload_calls += 1

        with tempfile.TemporaryDirectory() as td:
            reflect_dir = os.path.join(td, "reflect")
            os.makedirs(reflect_dir, exist_ok=True)
            scheduler_py = os.path.join(reflect_dir, "scheduler.py")
            agentmain_py = os.path.join(td, "agentmain.py")
            with open(scheduler_py, "w", encoding="utf-8") as f:
                f.write("# scheduler")
            with open(agentmain_py, "w", encoding="utf-8") as f:
                f.write("# agentmain")

            dummy = DummySchedule(td)
            delayed = []
            with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
                lz, "upstream_scheduler_paths", return_value={"scheduler_py": scheduler_py}
            ), mock.patch.object(lz, "_resolve_config_path", return_value=sys.executable), mock.patch.object(
                lz, "_find_system_python", return_value=sys.executable
            ), mock.patch.object(
                lz, "_popen_external_subprocess", return_value=DummyProc()
            ), mock.patch.object(
                schedule_runtime.QTimer, "singleShot", side_effect=lambda _ms, cb: delayed.append(cb)
            ), mock.patch.object(schedule_runtime.QMessageBox, "warning") as warning_box:
                self.assertTrue(dummy._start_scheduler_process(show_errors=True))
                self.assertEqual(dummy.reload_calls, 1)
                self.assertEqual(len(delayed), 1)
                dummy._runtime_context_generation += 1
                delayed[0]()
                dummy._scheduler_close_log_handle()

            self.assertEqual(dummy.reload_calls, 1)
            warning_box.assert_not_called()

    def test_lan_interface_status_lines_report_external_running_explicitly(self):
        class DummyUsage(PersonalUsageMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {"lan_interface": {"enabled": True, "auto_start": True, "bind_all": False, "port": 8501, "frontend": "foo.py"}}

            def _lan_interface_cfg(self):
                return dict(self.cfg.get("lan_interface") or {})

            def _lan_interface_proc_alive(self):
                return False

            def _lan_interface_external_running(self, port=None):
                return True

            def _lan_interface_urls(self, cfg=None):
                return {"local": "http://127.0.0.1:8501", "lan": []}

            def _lan_interface_log_path(self):
                return "C:\\demo\\temp\\lan.log"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
            lines = dummy._lan_interface_status_lines()

        self.assertTrue(lines)
        self.assertIn("状态：外部运行中", lines[0])

    def test_schedule_button_state_helpers_explain_disabled_reasons(self):
        class DummySchedule(ScheduleRuntimeMixin):
            _schedule_runtime_start_disabled_reason = ScheduleRuntimeMixin._schedule_runtime_start_disabled_reason
            _schedule_runtime_stop_disabled_reason = ScheduleRuntimeMixin._schedule_runtime_stop_disabled_reason
            _schedule_report_disabled_reason = ScheduleRuntimeMixin._schedule_report_disabled_reason
            _schedule_tasks_dir_disabled_reason = ScheduleRuntimeMixin._schedule_tasks_dir_disabled_reason
            _schedule_log_disabled_reason = ScheduleRuntimeMixin._schedule_log_disabled_reason

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _scheduler_proc_alive(self):
                return False

            def _scheduler_external_running(self):
                return True

        dummy = DummySchedule()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
            self.assertEqual(
                dummy._schedule_runtime_start_disabled_reason(is_remote=False),
                "检测到外部调度器实例正在运行；请先关闭外部实例。",
            )
            self.assertEqual(
                dummy._schedule_runtime_stop_disabled_reason(is_remote=False),
                "当前是外部启动的调度器进程，启动器无法直接停止。",
            )
        self.assertEqual(
            dummy._schedule_runtime_start_disabled_reason(is_remote=True, runtime_code="running", scheduler_pid=123),
            "远端调度器已在运行；无需重复启动。",
        )
        self.assertEqual(
            dummy._schedule_runtime_stop_disabled_reason(is_remote=True, runtime_code="stopped", scheduler_pid=0),
            "当前未检测到远端调度器运行中进程。",
        )
        self.assertEqual(
            dummy._schedule_report_disabled_reason({"latest_report_path": ""}, is_remote=True),
            "当前远端任务还没有可同步的报告文件。",
        )
        self.assertEqual(dummy._schedule_tasks_dir_disabled_reason("", is_remote=False), "当前任务目录路径不可用。")
        self.assertEqual(
            dummy._schedule_log_disabled_reason("", is_remote=True, title="调度日志"),
            "当前远端调度日志路径不可用，暂时无法下载。",
        )

    def test_apply_schedule_button_state_sets_tooltips(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummySchedule(ScheduleRuntimeMixin):
            _apply_schedule_button_state = ScheduleRuntimeMixin._apply_schedule_button_state

        dummy = DummySchedule()
        btn = DummyButton()
        dummy._apply_schedule_button_state(btn, False, enabled_tooltip="enabled", disabled_tooltip="disabled")
        self.assertFalse(btn.enabled)
        self.assertEqual(btn.tooltip, "disabled")
        dummy._apply_schedule_button_state(btn, True, enabled_tooltip="enabled", disabled_tooltip="disabled")
        self.assertTrue(btn.enabled)
        self.assertEqual(btn.tooltip, "enabled")

    def test_reload_lan_interface_panel_sets_button_tooltips_for_external_running(self):
        class DummyToggle:
            def __init__(self):
                self.checked = None
                self.enabled = None

            def blockSignals(self, _blocked):
                return None

            def setChecked(self, value):
                self.checked = bool(value)

            def setEnabled(self, value):
                self.enabled = bool(value)

        class DummySpin:
            def __init__(self):
                self.value = None
                self.enabled = None

            def blockSignals(self, _blocked):
                return None

            def setValue(self, value):
                self.value = int(value)

            def setEnabled(self, value):
                self.enabled = bool(value)

        class DummyCombo:
            def __init__(self):
                self.enabled = None
                self.index = 0
                self.items = [("默认", "foo.py")]

            def blockSignals(self, _blocked):
                return None

            def count(self):
                return len(self.items)

            def itemData(self, index):
                return self.items[int(index)][1]

            def setCurrentIndex(self, index):
                self.index = int(index)

            def setEnabled(self, value):
                self.enabled = bool(value)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _reload_lan_interface_panel = PersonalUsageMixin._reload_lan_interface_panel
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _lan_interface_form_disabled_reason = PersonalUsageMixin._lan_interface_form_disabled_reason
            _lan_interface_toggle_disabled_reason = PersonalUsageMixin._lan_interface_toggle_disabled_reason

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {"lan_interface": {"enabled": True, "auto_start": True, "bind_all": False, "port": 8501, "frontend": "foo.py"}}
                self.settings_lan_status = DummyLabel()
                self.settings_lan_enabled = DummyToggle()
                self.settings_lan_bind_all = DummyToggle()
                self.settings_lan_autostart = DummyToggle()
                self.settings_lan_port_spin = DummySpin()
                self.settings_lan_frontend_combo = DummyCombo()
                self.settings_lan_save_btn = DummyButton()
                self.settings_lan_start_btn = DummyButton()
                self.settings_lan_stop_btn = DummyButton()
                self.settings_lan_open_btn = DummyButton()
                self.settings_lan_log_btn = DummyButton()

            def _lan_interface_cfg(self):
                return dict(self.cfg.get("lan_interface") or {})

            def _lan_interface_proc_alive(self):
                return False

            def _lan_interface_external_running(self, port=None):
                return True

            def _lan_interface_log_path(self):
                return ""

            def _lan_interface_status_lines(self):
                return ["状态：外部运行中"]

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
            dummy._reload_lan_interface_panel()

        self.assertFalse(dummy.settings_lan_start_btn.enabled)
        self.assertFalse(dummy.settings_lan_stop_btn.enabled)
        self.assertFalse(dummy.settings_lan_log_btn.enabled)
        self.assertIn("请先关闭外部进程", dummy.settings_lan_start_btn.tooltip)
        self.assertIn("启动器无法直接停止", dummy.settings_lan_stop_btn.tooltip)
        self.assertIn("当前还没有可用的局域网 Web 日志文件", dummy.settings_lan_log_btn.tooltip)
        self.assertEqual(dummy.settings_lan_status.text, "状态：外部运行中")

    def test_reload_lan_interface_panel_sets_invalid_dir_tooltips(self):
        class DummyToggle:
            def blockSignals(self, _blocked):
                return None

            def setChecked(self, value):
                return None

            def setEnabled(self, value):
                return None

        class DummySpin:
            def blockSignals(self, _blocked):
                return None

            def setValue(self, value):
                return None

            def setEnabled(self, value):
                return None

        class DummyCombo:
            def __init__(self):
                self.items = [("默认", "foo.py")]

            def blockSignals(self, _blocked):
                return None

            def count(self):
                return len(self.items)

            def itemData(self, index):
                return self.items[int(index)][1]

            def setCurrentIndex(self, index):
                return None

            def setEnabled(self, value):
                return None

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _reload_lan_interface_panel = PersonalUsageMixin._reload_lan_interface_panel
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _lan_interface_form_disabled_reason = PersonalUsageMixin._lan_interface_form_disabled_reason
            _lan_interface_toggle_disabled_reason = PersonalUsageMixin._lan_interface_toggle_disabled_reason

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {"lan_interface": {"enabled": True, "auto_start": False, "bind_all": False, "port": 8501, "frontend": "foo.py"}}
                self.settings_lan_status = DummyLabel()
                self.settings_lan_enabled = DummyToggle()
                self.settings_lan_bind_all = DummyToggle()
                self.settings_lan_autostart = DummyToggle()
                self.settings_lan_port_spin = DummySpin()
                self.settings_lan_frontend_combo = DummyCombo()
                self.settings_lan_save_btn = DummyButton()
                self.settings_lan_start_btn = DummyButton()
                self.settings_lan_stop_btn = DummyButton()
                self.settings_lan_open_btn = DummyButton()
                self.settings_lan_log_btn = DummyButton()

            def _lan_interface_cfg(self):
                return dict(self.cfg.get("lan_interface") or {})

            def _lan_interface_proc_alive(self):
                return False

            def _lan_interface_external_running(self, port=None):
                return False

            def _lan_interface_log_path(self):
                return ""

            def _lan_interface_status_lines(self):
                return ["请先选择有效的 GenericAgent 目录。"]

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_lan_interface_panel()

        self.assertFalse(dummy.settings_lan_save_btn.enabled)
        self.assertFalse(dummy.settings_lan_start_btn.enabled)
        self.assertFalse(dummy.settings_lan_open_btn.enabled)
        self.assertFalse(dummy.settings_lan_log_btn.enabled)
        self.assertEqual(dummy.settings_lan_save_btn.tooltip, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy.settings_lan_start_btn.tooltip, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy.settings_lan_open_btn.tooltip, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy.settings_lan_log_btn.tooltip, "请先选择有效的 GenericAgent 目录。")

    def test_personal_usage_helpers_explain_lan_toggle_and_langfuse_clear_states(self):
        class DummyUsage(PersonalUsageMixin):
            _lan_interface_form_disabled_reason = PersonalUsageMixin._lan_interface_form_disabled_reason
            _lan_interface_toggle_disabled_reason = PersonalUsageMixin._lan_interface_toggle_disabled_reason
            _langfuse_clear_disabled_reason = PersonalUsageMixin._langfuse_clear_disabled_reason

        dummy = DummyUsage()
        self.assertEqual(dummy._lan_interface_form_disabled_reason(valid_agent_dir=False), "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy._lan_interface_form_disabled_reason(valid_agent_dir=True), "")
        self.assertEqual(
            dummy._lan_interface_toggle_disabled_reason(valid_agent_dir=False, feature_enabled=True),
            "请先选择有效的 GenericAgent 目录。",
        )
        self.assertEqual(
            dummy._lan_interface_toggle_disabled_reason(valid_agent_dir=True, feature_enabled=False),
            "请先开启局域网 Web 接口，再调整这个选项。",
        )
        self.assertEqual(dummy._lan_interface_toggle_disabled_reason(valid_agent_dir=True, feature_enabled=True), "")
        self.assertEqual(
            dummy._langfuse_clear_disabled_reason(configured=False),
            "当前还没有已保存的 Langfuse 配置可清除。",
        )
        self.assertEqual(dummy._langfuse_clear_disabled_reason(configured=True), "")

    def test_start_lan_interface_process_delayed_check_drops_stale_context(self):
        class DummyProc:
            returncode = None

            def poll(self):
                return None

        class DummyUsage(PersonalUsageMixin):
            _start_lan_interface_process = PersonalUsageMixin._start_lan_interface_process
            _after_lan_interface_launch_check = PersonalUsageMixin._after_lan_interface_launch_check

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir
                self.cfg = {"python_exe": sys.executable, "lan_interface": {"enabled": True, "auto_start": True, "bind_all": False, "port": 8501, "frontend": "stapp.py"}}
                self._runtime_context_generation = 2
                self._lan_interface_proc = None
                self._lan_interface_log_handle = None
                self._lan_interface_last_exit_code = None
                self.reload_calls = 0
                self.statuses = []

            def _lan_interface_cfg(self):
                return dict(self.cfg.get("lan_interface") or {})

            def _lan_interface_proc_alive(self):
                proc = getattr(self, "_lan_interface_proc", None)
                return bool(proc and proc.poll() is None)

            def _lan_interface_external_running(self, port=None):
                return False

            def _lan_interface_frontend_path(self, frontend):
                return os.path.join(self.agent_dir, "frontends", str(frontend or ""))

            def _check_runtime_dependencies(self, purpose="", extra_packages=None, visual=True):
                return True

            def _lan_interface_port_in_use(self, port):
                return False

            def _lan_interface_health_ok(self, port):
                return False

            def _lan_interface_log_path(self):
                return os.path.join(self.agent_dir, "temp", "lan.log")

            def _lan_interface_command(self, py, cfg, script_path):
                return [py, script_path]

            def _lan_interface_urls(self, cfg=None):
                return {"local": "http://127.0.0.1:8501", "lan": []}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _reload_lan_interface_panel(self):
                self.reload_calls += 1

        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "frontends"), exist_ok=True)
            os.makedirs(os.path.join(td, "temp"), exist_ok=True)
            frontend = os.path.join(td, "frontends", "stapp.py")
            with open(frontend, "w", encoding="utf-8") as f:
                f.write("# streamlit")

            dummy = DummyUsage(td)
            delayed = []
            with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
                lz, "_resolve_config_path", return_value=sys.executable
            ), mock.patch.object(
                lz, "_find_system_python", return_value=sys.executable
            ), mock.patch.object(
                lz, "_popen_external_subprocess", return_value=DummyProc()
            ), mock.patch.object(
                personal_usage.QApplication, "instance", return_value=object()
            ), mock.patch.object(
                personal_usage.QTimer, "singleShot", side_effect=lambda _ms, cb: delayed.append(cb)
            ), mock.patch.object(personal_usage.QMessageBox, "warning") as warning_box:
                self.assertTrue(dummy._start_lan_interface_process(show_errors=True, skip_dependency_check=False, refresh=True))
                self.assertEqual(dummy.reload_calls, 1)
                self.assertEqual(len(delayed), 1)
                dummy._runtime_context_generation += 1
                delayed[0]()
            dummy._lan_interface_close_log_handle()

            self.assertEqual(dummy.reload_calls, 1)
            warning_box.assert_not_called()

    def test_trigger_settings_remote_session_sync_still_calls_done_without_blocking_syncers(self):
        class DummyUsage(PersonalUsageMixin):
            _trigger_settings_remote_session_sync = PersonalUsageMixin._trigger_settings_remote_session_sync

            def __init__(self):
                self.calls = []

        dummy = DummyUsage()

        def mark_done():
            dummy.calls.append("done")

        with mock.patch.object(personal_usage.QTimer, "singleShot", side_effect=lambda _ms, cb: cb()):
            dummy._trigger_settings_remote_session_sync(device_id="srv-1", on_done=mark_done, include_all_channels=True)

        self.assertEqual(dummy.calls, ["done"])

    def test_trigger_settings_remote_session_sync_passes_captured_agent_dir_and_context(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyUsage(PersonalUsageMixin):
            _trigger_settings_remote_session_sync = PersonalUsageMixin._trigger_settings_remote_session_sync

            def __init__(self):
                self.agent_dir = "C:\\demo" if os.name == "nt" else os.path.join(os.sep, "tmp", "demo")
                self._runtime_context_generation = 4
                self._settings_target_change_token = 9
                self.calls = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _sync_remote_device_launcher_sessions_blocking(self, **kwargs):
                self.calls.append(("launcher", dict(kwargs)))

            def _sync_remote_device_channel_process_sessions_blocking(self, **kwargs):
                self.calls.append(("channel", dict(kwargs)))

            def _api_on_ui_thread(self, fn):
                fn()

        dummy = DummyUsage()
        with mock.patch.object(personal_usage.threading, "Thread", ImmediateThread):
            dummy._trigger_settings_remote_session_sync(
                device_id="srv-1",
                on_done=lambda: dummy.calls.append(("done", {})),
                include_all_channels=True,
                include_usage=True,
            )

        launcher_call = next(payload for name, payload in dummy.calls if name == "launcher")
        channel_call = next(payload for name, payload in dummy.calls if name == "channel")
        expected_agent_dir = os.path.abspath(dummy.agent_dir)
        self.assertEqual(launcher_call["agent_dir"], expected_agent_dir)
        self.assertEqual(channel_call["agent_dir"], expected_agent_dir)
        self.assertEqual(launcher_call["runtime_context"]["agent_dir"], expected_agent_dir)
        self.assertEqual(launcher_call["runtime_context"]["runtime_generation"], 4)
        self.assertEqual(launcher_call["runtime_context"]["settings_target_generation"], 9)
        self.assertIn(("done", {}), dummy.calls)

    def test_terminate_process_tree_kills_spawned_child_process(self):
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=False if os.name == "nt" else True,
        )
        try:
            child_line = str(proc.stdout.readline() if proc.stdout is not None else "").strip()
            self.assertTrue(child_line.isdigit(), msg=f"unexpected child pid line: {child_line!r}")
            child_pid = int(child_line)
            self.assertTrue(runtime.terminate_process_tree(proc, terminate_timeout=0.3, kill_timeout=1.5))
            proc.wait(timeout=5)
            self.assertFalse(self._pid_exists(child_pid), msg=f"child process still alive: {child_pid}")
        finally:
            try:
                runtime.terminate_process_tree(proc, terminate_timeout=0.1, kill_timeout=0.2)
            except Exception:
                pass

    def test_popen_external_subprocess_uses_new_session_on_posix(self):
        popen_mock = mock.Mock(return_value=object())
        with mock.patch.object(runtime.os, "name", "posix"), mock.patch.object(runtime.subprocess, "Popen", popen_mock):
            runtime._popen_external_subprocess(["python", "-V"])

        _args, kwargs = popen_mock.call_args
        self.assertTrue(kwargs.get("start_new_session"))
        self.assertIn("env", kwargs)

    def test_launch_visible_terminal_script_uses_new_console_on_windows(self):
        popen_mock = mock.Mock(return_value=object())
        with mock.patch.object(runtime.os, "name", "nt"), mock.patch.object(
            runtime.subprocess, "Popen", popen_mock
        ), mock.patch.object(runtime, "_external_subprocess_env", return_value={"PYTHONUTF8": "1"}), mock.patch.object(
            runtime.tempfile, "mkstemp", return_value=(123, r"C:\temp\ga_tui_launch_test.cmd")
        ), mock.patch.object(
            runtime.os, "close", return_value=None
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ) as open_mock:
            self.assertTrue(
                runtime.launch_visible_terminal_script(
                    "C:\\Python\\python.exe",
                    "C:\\agent\\frontends\\tuiapp.py",
                    cwd="C:\\agent",
                    env={"KEEP": "1"},
                    title="终端 TUI",
                )
            )

        _args, kwargs = popen_mock.call_args
        self.assertEqual(_args[0][0:3], ["cmd.exe", "/d", "/k"])
        self.assertEqual(_args[0][3], r"C:\temp\ga_tui_launch_test.cmd")
        self.assertEqual(kwargs["cwd"], "C:\\agent")
        self.assertEqual(kwargs["env"], {"PYTHONUTF8": "1"})
        self.assertEqual(kwargs["creationflags"], getattr(runtime.subprocess, "CREATE_NEW_CONSOLE", 0))
        handle = open_mock()
        written = "".join(call.args[0] for call in handle.write.call_args_list)
        self.assertIn("@echo off", written)
        self.assertIn("title 终端 TUI", written)
        self.assertIn('cd /d "C:\\agent"', written)
        self.assertIn('"C:\\Python\\python.exe" "frontends\\tuiapp.py"', written)

    def test_launch_visible_terminal_script_uses_osascript_on_macos(self):
        run_mock = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        with mock.patch.object(runtime.os, "name", "posix"), mock.patch.object(runtime, "IS_MACOS", True), mock.patch.object(
            runtime.subprocess, "run", run_mock
        ), mock.patch.object(runtime, "_external_subprocess_env", return_value={"PYTHONUTF8": "1"}):
            self.assertTrue(
                runtime.launch_visible_terminal_script(
                    "/opt/homebrew/bin/python3",
                    "/Users/tester/agent/frontends/tuiapp.py",
                    cwd="/Users/tester/agent",
                    env={"KEEP": "1"},
                    title="终端 TUI",
                )
            )

        _args, kwargs = run_mock.call_args
        self.assertEqual(_args[0][0], "osascript")
        self.assertEqual(_args[0][1], "-e")
        applescript = _args[0][2]
        self.assertIn('tell application "Terminal"', applescript)
        self.assertIn("do script", applescript)
        self.assertIn("tuiapp.py", applescript)

    def test_terminate_process_tree_uses_process_group_on_posix(self):
        state = {"alive": True}

        class FakeProc:
            def __init__(self):
                self.pid = 999
                self.stdout = None
                self.stderr = None
                self.stdin = None

            def poll(self):
                return None if state["alive"] else 0

            def wait(self, timeout=None):
                if state["alive"]:
                    raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
                return 0

        proc = FakeProc()

        def fake_getpgid(value):
            return 4321 if int(value) == 999 else 1234

        def fake_kill(pid, sig):
            if int(sig) == 0:
                if state["alive"]:
                    return None
                raise ProcessLookupError()
            state["alive"] = False
            return None

        def fake_killpg(pgid, sig):
            self.assertEqual(int(pgid), 4321)
            expected_term = int(getattr(runtime.signal, "SIGTERM", 15))
            expected_kill = int(getattr(runtime.signal, "SIGKILL", expected_term))
            self.assertIn(int(sig), (expected_term, expected_kill))
            state["alive"] = False
            return None

        with mock.patch.object(runtime.os, "name", "posix"), mock.patch.object(
            runtime.os, "getpgid", side_effect=fake_getpgid, create=True
        ), mock.patch.object(runtime.os, "kill", side_effect=fake_kill) as kill_mock, mock.patch.object(
            runtime.os, "killpg", side_effect=fake_killpg, create=True
        ) as killpg_mock:
            ok = runtime.terminate_process_tree(proc, terminate_timeout=0.1, kill_timeout=0.1)

        self.assertTrue(ok)
        self.assertGreaterEqual(killpg_mock.call_count, 1)
        self.assertGreaterEqual(kill_mock.call_count, 1)

    def test_bridge_script_path_points_to_repo_root_bridge(self):
        bridge_path = lz._bridge_script_path()
        expected = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bridge.py")
        self.assertEqual(os.path.normpath(bridge_path), os.path.normpath(expected))
        self.assertTrue(os.path.isfile(bridge_path), msg=f"missing bridge.py: {bridge_path}")

    def test_launcher_bootstrap_avoids_launcher_core_facade_import(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_bootstrap.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from launcher_core_parts.constants import MAIN_EXE_NAME", src)
        self.assertIn("from launcher_core_parts.runtime import (", src)
        self.assertNotIn("from launcher_app import core as lz", src)

    def test_updater_avoids_launcher_core_facade_import(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "updater.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from launcher_core_parts.runtime import updater_log", src)
        self.assertIn("from launcher_core_parts.update_manager import apply_update_job", src)
        self.assertNotIn("from launcher_app import core as lz", src)

    def test_launcher_bootstrap_falls_back_to_dev_onedir_layout(self):
        with tempfile.TemporaryDirectory() as td:
            bootstrap_exe = os.path.join(td, "LauncherBootstrap.exe")
            app_dir = os.path.join(td, "GenericAgentLauncher")
            main_exe_name = "GenericAgentLauncher.exe"
            app_exe = os.path.join(app_dir, main_exe_name)
            os.makedirs(app_dir, exist_ok=True)
            with open(bootstrap_exe, "wb") as f:
                f.write(b"bootstrap")
            with open(app_exe, "wb") as f:
                f.write(b"main")

            with mock.patch.object(launcher_bootstrap, "MAIN_EXE_NAME", main_exe_name), mock.patch.object(
                launcher_bootstrap, "load_version_state", return_value={}
            ), mock.patch.object(
                launcher_bootstrap, "resolved_versions_dir", return_value=os.path.join(td, "missing_versions")
            ), mock.patch.object(launcher_bootstrap.sys, "frozen", True, create=True), mock.patch.object(
                launcher_bootstrap.sys, "executable", bootstrap_exe
            ):
                picked = launcher_bootstrap._pick_target_executable()

            self.assertEqual(os.path.normcase(os.path.normpath(picked)), os.path.normcase(os.path.normpath(app_exe)))

    def test_launcher_bootstrap_rejects_self_target(self):
        with tempfile.TemporaryDirectory() as td:
            bootstrap_exe = os.path.join(td, "LauncherBootstrap.exe")
            with open(bootstrap_exe, "wb") as f:
                f.write(b"bootstrap")

            with mock.patch.object(launcher_bootstrap, "MAIN_EXE_NAME", "LauncherBootstrap.exe"), mock.patch.object(
                launcher_bootstrap, "load_version_state", return_value={}
            ), mock.patch.object(
                launcher_bootstrap, "resolved_versions_dir", return_value=os.path.join(td, "missing_versions")
            ), mock.patch.object(
                launcher_bootstrap.sys, "frozen", True, create=True
            ), mock.patch.object(
                launcher_bootstrap.sys, "executable", bootstrap_exe
            ):
                picked = launcher_bootstrap._pick_target_executable()

            self.assertEqual(picked, "")

    def test_navigation_quick_enter_always_skips_dependency_check(self):
        class DummyNav(NavigationMixin):
            def __init__(self):
                self.calls = []

            def _enter_chat(self, *, skip_dependency_check=False):
                self.calls.append(bool(skip_dependency_check))

        dummy = DummyNav()
        dummy._quick_enter_chat()
        self.assertEqual(dummy.calls[-1], True)

    def test_navigation_regular_enter_still_checks_dependencies(self):
        class DummyNav(NavigationMixin):
            _enter_chat = NavigationMixin._enter_chat

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.current_session = None
                self.calls = []

            def _check_runtime_dependencies(self, purpose=""):
                self.calls.append(("check_dependencies", purpose))
                return False

            def _show_chat_page(self):
                self.calls.append("show_chat")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _render_session(self, session):
                self.calls.append(("render_session", session))

            def _reset_chat_area(self, text):
                self.calls.append(("reset_chat_area", text))

            def _defer_chat_runtime_bootstrap(self):
                self.calls.append("bootstrap")

            def _show_locate(self):
                self.calls.append("show_locate")

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "_ensure_mykey_file", return_value={"ok": True, "created": False}
        ):
            dummy._enter_chat()

        self.assertEqual(dummy.calls, [("check_dependencies", "载入内核")])

    def test_refresh_welcome_state_sets_enter_chat_button_tooltip(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyNav(NavigationMixin):
            _refresh_welcome_state = NavigationMixin._refresh_welcome_state
            _apply_navigation_widget_state = NavigationMixin._apply_navigation_widget_state

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self.enter_chat_btn = DummyButton()

            def _refresh_download_state(self):
                return None

        dummy = DummyNav()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._refresh_welcome_state()
        self.assertFalse(dummy.enter_chat_btn.enabled)
        self.assertEqual(dummy.enter_chat_btn.tooltip, "请先选择有效的 GenericAgent 目录。")

        dummy.agent_dir = "C:\\demo"
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True):
            dummy._refresh_welcome_state()
        self.assertTrue(dummy.enter_chat_btn.enabled)
        self.assertEqual(dummy.enter_chat_btn.tooltip, "进入聊天页并开始准备当前内核环境。")

    def test_refresh_official_gui_state_sets_launch_button_tooltips(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyNav(NavigationMixin):
            _apply_navigation_widget_state = NavigationMixin._apply_navigation_widget_state
            _refresh_official_gui_state = NavigationMixin._refresh_official_gui_state
            _refresh_official_frontend_entry = NavigationMixin._refresh_official_frontend_entry
            _refresh_official_desktop_release_state = NavigationMixin._refresh_official_desktop_release_state
            _official_frontend_extra_packages = NavigationMixin._official_frontend_extra_packages
            _official_gui_extra_packages = NavigationMixin._official_gui_extra_packages
            _official_frontend_script_path = NavigationMixin._official_frontend_script_path
            _official_desktop_release_page_url = NavigationMixin._official_desktop_release_page_url
            _official_desktop_release_candidates = NavigationMixin._official_desktop_release_candidates

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self.official_gui_launch_btn = DummyButton()
                self.official_desktop_launch_btn = DummyButton()
                self.official_gui_path_label = DummyLabel()
                self.official_gui_path_hint_label = DummyLabel()
                self.official_gui_status_label = DummyLabel()
                self.official_gui_entry_status_label = DummyLabel()
                self.official_gui_dependency_label = DummyLabel()
                self.official_desktop_status_label = DummyLabel()
                self.official_desktop_dependency_label = DummyLabel()

        dummy = DummyNav()
        groups = [
            {
                "id": "launch_web_ui",
                "items": [
                    {"package": "streamlit>=1.28"},
                    {"package": "pywebview>=4.0"},
                ],
            },
        ]
        with mock.patch.object(lz, "resolve_upstream_frontend_dependency_groups", return_value=groups), mock.patch.object(
            lz, "is_valid_agent_dir", return_value=False
        ), mock.patch.object(
            navigation.os, "name", "nt"
        ):
            dummy._refresh_official_gui_state()
        self.assertFalse(dummy.official_gui_launch_btn.enabled)
        self.assertFalse(dummy.official_desktop_launch_btn.enabled)
        self.assertEqual(dummy.official_gui_launch_btn.tooltip, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy.official_desktop_launch_btn.tooltip, "请先选择有效的 GenericAgent 目录，并把发布版 exe 放到 frontends/。")
        self.assertIn("streamlit>=1.28", dummy.official_gui_dependency_label.text)
        self.assertIn("pywebview>=4.0", dummy.official_gui_dependency_label.text)
        self.assertIn("GenericAgent-windows-x64.exe", dummy.official_desktop_dependency_label.text)
        self.assertIn("frontends/GenericAgent.exe", dummy.official_desktop_status_label.text)

        dummy.agent_dir = "C:\\demo"
        with mock.patch.object(lz, "resolve_upstream_frontend_dependency_groups", return_value=groups), mock.patch.object(
            lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            navigation.os, "name", "nt"
        ), mock.patch.object(
            navigation.os.path,
            "isfile",
            side_effect=lambda path: os.path.normcase(os.path.normpath(path))
            in {
                os.path.normcase(os.path.normpath(os.path.join("C:\\demo", "launch.pyw"))),
                os.path.normcase(os.path.normpath(os.path.join("C:\\demo", "frontends", "GenericAgent.exe"))),
            },
        ):
            dummy._refresh_official_gui_state()
        self.assertTrue(dummy.official_gui_launch_btn.enabled)
        self.assertTrue(dummy.official_desktop_launch_btn.enabled)
        self.assertEqual(dummy.official_gui_launch_btn.tooltip, "检查依赖后拉起上游 launch.pyw。")
        self.assertEqual(dummy.official_desktop_launch_btn.tooltip, "拉起当前目录 frontends/ 下的官方发布版桌面客户端。")
        self.assertIn("C:\\demo", dummy.official_gui_status_label.text)
        self.assertIn("GenericAgent.exe", dummy.official_desktop_status_label.text)

        with mock.patch.object(lz, "resolve_upstream_frontend_dependency_groups", return_value=groups), mock.patch.object(
            lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            navigation.os, "name", "nt"
        ), mock.patch.object(
            navigation.os.path,
            "isfile",
            side_effect=lambda path: os.path.normcase(os.path.normpath(path))
            == os.path.normcase(os.path.normpath(os.path.join("C:\\demo", "launch.pyw"))),
        ):
            dummy._refresh_official_gui_state()
        self.assertFalse(dummy.official_desktop_launch_btn.enabled)
        self.assertEqual(dummy.official_desktop_launch_btn.tooltip, "当前目录未找到官方发布版桌面客户端，可先打开 Release 页面下载。")

    def test_launch_official_gui_checks_dependencies_and_spawns_launch_pyw(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyProc:
            pid = 1234

        class DummyNav(NavigationMixin):
            _launch_official_gui = NavigationMixin._launch_official_gui
            _launch_official_frontend = NavigationMixin._launch_official_frontend
            _official_frontend_extra_packages = NavigationMixin._official_frontend_extra_packages
            _official_gui_extra_packages = NavigationMixin._official_gui_extra_packages
            _official_frontend_script_path = NavigationMixin._official_frontend_script_path

            def __init__(self, agent_dir, python_exe):
                self.agent_dir = agent_dir
                self.cfg = {}
                self.python_exe = python_exe
                self.notice = DummyLabel()
                self.official_gui_notice_label = self.notice
                self.check_calls = []
                self.statuses = []
                self.remembered = []

            def _check_runtime_dependencies(self, **kwargs):
                self.check_calls.append(dict(kwargs))
                return True

            def _resolve_bridge_python(self):
                return self.python_exe, None

            def _remember_bridge_python(self, py_path):
                self.remembered.append(str(py_path))

            def _set_status(self, text):
                self.statuses.append(str(text))

        with tempfile.TemporaryDirectory() as td:
            launch_path = os.path.join(td, "launch.pyw")
            python_exe = os.path.join(td, "python.exe")
            with open(launch_path, "w", encoding="utf-8") as f:
                f.write("# launch")
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("# python")

            dummy = DummyNav(td, python_exe)
            groups = [{"id": "launch_web_ui", "items": [{"package": "streamlit>=1.28"}, {"package": "pywebview>=4.0"}]}]
            with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
                lz, "_ensure_mykey_file", return_value={"ok": True, "created": False}
            ), mock.patch.object(
                lz, "resolve_upstream_frontend_dependency_groups", return_value=groups
            ), mock.patch.object(
                lz, "_external_subprocess_env", return_value={"GA_TEST": "1"}
            ), mock.patch.object(
                lz, "_popen_external_subprocess", return_value=DummyProc()
            ) as popen_mock, mock.patch.object(
                navigation.QMessageBox, "information"
            ) as info_box, mock.patch.object(
                navigation.QMessageBox, "warning"
            ) as warning_box, mock.patch.object(
                navigation.QMessageBox, "critical"
            ) as critical_box:
                dummy._launch_official_gui()

        self.assertEqual(len(dummy.check_calls), 1)
        self.assertEqual(dummy.check_calls[0]["purpose"], "启动官方 GUI")
        self.assertEqual(dummy.check_calls[0]["extra_packages"], ["streamlit>=1.28", "pywebview>=4.0"])
        self.assertEqual(dummy.remembered, [python_exe])
        popen_args, popen_kwargs = popen_mock.call_args
        self.assertEqual(popen_args[0], [python_exe, launch_path])
        self.assertEqual(popen_kwargs["cwd"], td)
        self.assertEqual(popen_kwargs["env"], {"GA_TEST": "1"})
        self.assertIs(popen_kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(popen_kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(popen_kwargs["stderr"], subprocess.DEVNULL)
        self.assertIn("已拉起官方 GUI。", dummy.statuses)
        self.assertIn("launch.pyw", dummy.notice.text)
        info_box.assert_not_called()
        warning_box.assert_not_called()
        critical_box.assert_not_called()

    def test_launch_official_desktop_app_spawns_windows_release_exe(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyProc:
            pid = 4321

        class DummyNav(NavigationMixin):
            _launch_official_desktop_app = NavigationMixin._launch_official_desktop_app
            _official_desktop_release_page_url = NavigationMixin._official_desktop_release_page_url
            _official_desktop_release_candidates = NavigationMixin._official_desktop_release_candidates

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir
                self.cfg = {}
                self.notice = DummyLabel()
                self.official_gui_notice_label = self.notice
                self.statuses = []

            def _set_status(self, text):
                self.statuses.append(str(text))

        with tempfile.TemporaryDirectory() as td:
            desktop_path = os.path.join(td, "frontends", "GenericAgent.exe")
            os.makedirs(os.path.dirname(desktop_path), exist_ok=True)
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write("# exe")

            dummy = DummyNav(td)
            with mock.patch.object(
                navigation.os, "name", "nt"
            ), mock.patch.object(
                lz, "is_valid_agent_dir", return_value=True
            ), mock.patch.object(
                lz, "_external_subprocess_env", return_value={"GA_TEST": "1"}
            ), mock.patch.object(
                lz, "_popen_external_subprocess", return_value=DummyProc()
            ) as popen_mock, mock.patch.object(
                navigation.QMessageBox, "critical"
            ) as critical_box:
                dummy._launch_official_desktop_app()

        popen_args, popen_kwargs = popen_mock.call_args
        self.assertEqual(popen_args[0], [desktop_path])
        self.assertEqual(popen_kwargs["cwd"], os.path.dirname(desktop_path))
        self.assertEqual(popen_kwargs["env"], {"GA_TEST": "1"})
        self.assertIs(popen_kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(popen_kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(popen_kwargs["stderr"], subprocess.DEVNULL)
        self.assertIn("已拉起官方桌面版。", dummy.statuses)
        self.assertIn("GenericAgent.exe", dummy.notice.text)
        critical_box.assert_not_called()

    def test_launch_official_desktop_app_spawns_macos_release_app(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyProc:
            pid = 8642

        class DummyNav(NavigationMixin):
            _launch_official_desktop_app = NavigationMixin._launch_official_desktop_app
            _official_desktop_release_page_url = NavigationMixin._official_desktop_release_page_url
            _official_desktop_release_candidates = NavigationMixin._official_desktop_release_candidates

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self.notice = DummyLabel()
                self.official_gui_notice_label = self.notice
                self.statuses = []

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyNav()
        app_path = os.path.join("/Applications", "GenericAgent.app")
        with mock.patch.object(
            navigation.os, "name", "posix"
        ), mock.patch.object(
            lz, "IS_MACOS", True
        ), mock.patch.object(
            navigation.os.path, "isdir", side_effect=lambda path: os.path.normpath(path) == os.path.normpath(app_path)
        ), mock.patch.object(
            lz, "_external_subprocess_env", return_value={"GA_TEST": "1"}
        ), mock.patch.object(
            lz, "_popen_external_subprocess", return_value=DummyProc()
        ) as popen_mock, mock.patch.object(
            navigation.QMessageBox, "critical"
        ) as critical_box:
            dummy._launch_official_desktop_app()

        popen_args, popen_kwargs = popen_mock.call_args
        self.assertEqual(popen_args[0], ["open", app_path])
        self.assertEqual(popen_kwargs["cwd"], os.path.dirname(app_path))
        self.assertEqual(popen_kwargs["env"], {"GA_TEST": "1"})
        self.assertIn("已拉起官方桌面版。", dummy.statuses)
        self.assertIn("GenericAgent.app", dummy.notice.text)
        critical_box.assert_not_called()

    def test_launch_official_desktop_app_opens_release_page_when_missing(self):
        class DummyNav(NavigationMixin):
            _launch_official_desktop_app = NavigationMixin._launch_official_desktop_app
            _official_desktop_release_page_url = NavigationMixin._official_desktop_release_page_url
            _official_desktop_release_candidates = NavigationMixin._official_desktop_release_candidates

            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self.opened = 0

            def _open_official_desktop_release_page(self):
                self.opened += 1
                return True

        dummy = DummyNav()
        with mock.patch.object(navigation.os, "name", "posix"), mock.patch.object(lz, "IS_MACOS", True), mock.patch.object(
            navigation.os.path, "isdir", return_value=False
        ):
            ok = dummy._launch_official_desktop_app()

        self.assertFalse(ok)
        self.assertEqual(dummy.opened, 1)

    def test_download_cleanup_removes_invalid_target_directory(self):
        class DummyDownload(DownloadMixin):
            pass

        dummy = DummyDownload()
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "GenericAgent")
            os.makedirs(os.path.join(target, "partial"), exist_ok=True)
            with open(os.path.join(target, "partial", "leftover.txt"), "w", encoding="utf-8") as f:
                f.write("stale")

            ok, detail = dummy._remove_download_target_path(target)

            self.assertTrue(ok, msg=detail)
            self.assertFalse(os.path.exists(target))

    def test_download_success_text_uses_system_python_guidance_without_private_installer(self):
        class DummyDownload(DownloadMixin):
            def _supports_private_python_installer(self):
                return False

        dummy = DummyDownload()
        self.assertTrue(dummy._uses_system_python_download_mode())
        self.assertEqual(dummy._download_managed_env_button_text(), "构建项目虚拟环境")
        self.assertEqual(dummy._download_managed_env_ready_text(), "下载完成，已构建项目虚拟环境并设置为当前 GenericAgent 目录。现在可以直接进入聊天。")
        self.assertEqual(
            dummy._download_existing_target_ready_text(),
            "已使用现有目录。请使用系统 Python 进入聊天；首次载入时会自动执行依赖检查。",
        )
        self.assertEqual(
            dummy._download_clone_ready_text(),
            "下载完成，已设置为当前 GenericAgent 目录。请使用系统 Python 进入聊天；首次载入时会自动执行依赖检查。",
        )
        self.assertEqual(dummy._download_git_missing_message(), "未检测到 Git。请先安装 Git：\nhttps://git-scm.com/downloads")

    def test_select_project_venv_seed_python_prefers_configured_python_when_compatible(self):
        class DummyDownload(DownloadMixin):
            _select_project_venv_seed_python = DownloadMixin._select_project_venv_seed_python
            _project_venv_seed_candidates = DownloadMixin._project_venv_seed_candidates
            _private_runtime_paths = DownloadMixin._private_runtime_paths
            _normalized_download_path = DownloadMixin._normalized_download_path
            _download_path_is_within = DownloadMixin._download_path_is_within

            def __init__(self, cfg):
                self.cfg = cfg

            def _supports_private_python_installer(self):
                return False

        with tempfile.TemporaryDirectory() as td:
            configured_python = os.path.join(td, "configured", "bin", "python3")
            system_python = os.path.join(td, "system", "bin", "python3")
            os.makedirs(os.path.dirname(configured_python), exist_ok=True)
            os.makedirs(os.path.dirname(system_python), exist_ok=True)
            for path in (configured_python, system_python):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("#!/usr/bin/env python3\n")

            dummy = DummyDownload({"python_exe": configured_python})
            with mock.patch.object(lz, "_resolve_configured_python_exe", return_value=configured_python), mock.patch.object(
                lz, "_probe_python_command", return_value={"path": configured_python, "version": "3.12.4"}
            ), mock.patch.object(
                lz, "_system_python_candidates", return_value=[{"path": system_python, "version": "3.11.9"}]
            ), mock.patch.object(
                lz, "resolve_upstream_dependency_manifest", return_value={"requires_python": ">=3.11,<3.13"}
            ):
                info, err = dummy._select_project_venv_seed_python(td)

        self.assertEqual(err, "")
        self.assertEqual(info["path"], configured_python)
        self.assertEqual(info["version"], "3.12.4")
        self.assertEqual(info["source"], "已配置 python_exe")

    def test_select_project_venv_seed_python_reports_macos_guidance_for_unsupported_versions(self):
        class DummyDownload(DownloadMixin):
            _select_project_venv_seed_python = DownloadMixin._select_project_venv_seed_python
            _project_venv_seed_candidates = DownloadMixin._project_venv_seed_candidates
            _private_runtime_paths = DownloadMixin._private_runtime_paths
            _normalized_download_path = DownloadMixin._normalized_download_path
            _download_path_is_within = DownloadMixin._download_path_is_within

            def __init__(self):
                self.cfg = {}

            def _supports_private_python_installer(self):
                return False

        with tempfile.TemporaryDirectory() as td:
            system_python = os.path.join(td, "system", "bin", "python3")
            os.makedirs(os.path.dirname(system_python), exist_ok=True)
            with open(system_python, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")

            dummy = DummyDownload()
            with mock.patch.object(lz, "_system_python_candidates", return_value=[{"path": system_python, "version": "3.10.14"}]), mock.patch.object(
                lz, "resolve_upstream_dependency_manifest", return_value={"requires_python": ">=3.11,<3.13"}
            ):
                info, err = dummy._select_project_venv_seed_python(td)

        self.assertEqual(info, {})
        self.assertIn("Homebrew Python 3.11 / 3.12", err)
        self.assertIn("launcher_config.json", err)
        self.assertIn(">=3.11,<3.13", err)
        self.assertIn("Python 3.10", err)

    def test_select_project_venv_seed_python_skips_launcher_runtime_interpreters(self):
        class DummyDownload(DownloadMixin):
            _select_project_venv_seed_python = DownloadMixin._select_project_venv_seed_python
            _project_venv_seed_candidates = DownloadMixin._project_venv_seed_candidates
            _private_runtime_paths = DownloadMixin._private_runtime_paths
            _normalized_download_path = DownloadMixin._normalized_download_path
            _download_path_is_within = DownloadMixin._download_path_is_within

            def __init__(self, cfg):
                self.cfg = cfg

            def _supports_private_python_installer(self):
                return False

        with tempfile.TemporaryDirectory() as td:
            runtime_python = os.path.join(td, ".launcher_runtime", "venv312", "bin", "python3")
            system_python = os.path.join(td, "system", "bin", "python3")
            os.makedirs(os.path.dirname(runtime_python), exist_ok=True)
            os.makedirs(os.path.dirname(system_python), exist_ok=True)
            for path in (runtime_python, system_python):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("#!/usr/bin/env python3\n")

            dummy = DummyDownload({"python_exe": runtime_python})
            with mock.patch.object(lz, "_resolve_configured_python_exe", return_value=runtime_python), mock.patch.object(
                lz, "_probe_python_command", return_value={"path": runtime_python, "version": "3.12.4"}
            ), mock.patch.object(
                lz, "_system_python_candidates", return_value=[{"path": system_python, "version": "3.11.9"}]
            ), mock.patch.object(
                lz, "resolve_upstream_dependency_manifest", return_value={"requires_python": ">=3.11,<3.13"}
            ):
                info, err = dummy._select_project_venv_seed_python(td)

        self.assertEqual(err, "")
        self.assertEqual(info["path"], system_python)
        self.assertEqual(info["source"], "系统候选")

    def test_ensure_private_python_env_builds_project_venv_without_touching_seed_python_packages(self):
        class DummyQueue:
            def __init__(self):
                self.events = []

            def put(self, ev):
                self.events.append(dict(ev))

        class DummyDownload(DownloadMixin):
            _ensure_private_python_env = DownloadMixin._ensure_private_python_env
            _build_runtime_venv = DownloadMixin._build_runtime_venv
            _private_runtime_paths = DownloadMixin._private_runtime_paths

            def __init__(self, target, seed_python):
                self.cfg = {}
                self.target = target
                self.seed_python = seed_python
                self._event_queue = DummyQueue()
                self.calls = []

            def _supports_private_python_installer(self):
                return False

            def _select_project_venv_seed_python(self, _target):
                return {"path": self.seed_python, "version": "3.12.4", "source": "系统候选"}, ""

            def _run_checked_command(self, args, **_kwargs):
                self.calls.append(list(args))
                runtime_paths = self._private_runtime_paths(self.target)
                if args[:3] == [self.seed_python, "-m", "venv"]:
                    os.makedirs(os.path.dirname(runtime_paths["venv_python"]), exist_ok=True)
                    with open(runtime_paths["venv_python"], "w", encoding="utf-8") as f:
                        f.write("#!/usr/bin/env python3\n")
                return True, ""

        with tempfile.TemporaryDirectory() as td:
            seed_python = os.path.join(td, "seed", "bin", "python3")
            os.makedirs(os.path.dirname(seed_python), exist_ok=True)
            with open(seed_python, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")

            dummy = DummyDownload(td, seed_python)
            with mock.patch.object(lz, "_probe_python_agent_compat", return_value=(True, "")):
                python_exe, err = dummy._ensure_private_python_env(td)

            runtime_paths = dummy._private_runtime_paths(td)

        self.assertEqual(err, None)
        self.assertEqual(python_exe, runtime_paths["venv_python"])
        self.assertEqual(dummy.calls[0], [seed_python, "-m", "venv", "--clear", runtime_paths["venv_root"]])
        self.assertEqual(dummy.calls[1], [runtime_paths["venv_python"], "-m", "ensurepip", "--upgrade"])
        self.assertEqual(
            dummy.calls[2],
            [runtime_paths["venv_python"], "-m", "pip", "install", "--upgrade", "requests", "simplejson"],
        )
        self.assertTrue(all(call[0] == runtime_paths["venv_python"] for call in dummy.calls[1:]))
        self.assertTrue(any("不会写入系统 Python" in str(ev.get("msg") or "") for ev in dummy._event_queue.events))

    def test_refresh_download_state_tolerates_missing_private_controls(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setText(self, text):
                self.text = str(text)

        class DummyProgress:
            def __init__(self):
                self.range = None
                self.value = None

            def setRange(self, left, right):
                self.range = (int(left), int(right))

            def setValue(self, value):
                self.value = int(value)

        class DummyDownload(DownloadMixin):
            _refresh_download_state = DownloadMixin._refresh_download_state

            def __init__(self):
                self.install_parent = "/tmp"
                self._download_running = False
                self._download_mode = ""
                self.download_parent_label = DummyLabel()
                self.download_parent_value = DummyLabel()
                self.download_btn = DummyButton()
                self.download_progress = DummyProgress()

        dummy = DummyDownload()
        dummy._refresh_download_state()
        self.assertIn("GenericAgent", dummy.download_parent_label.text)
        self.assertEqual(dummy.download_parent_value.text, "/tmp")
        self.assertTrue(dummy.download_btn.enabled)
        self.assertEqual(dummy.download_btn.text, "开始下载")
        self.assertEqual(dummy.download_progress.range, (0, 1))
        self.assertEqual(dummy.download_progress.value, 0)

    def test_refresh_download_state_keeps_project_venv_button_enabled_without_private_installer(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setText(self, text):
                self.text = str(text)

        class DummyDownload(DownloadMixin):
            _refresh_download_state = DownloadMixin._refresh_download_state

            def __init__(self):
                self.install_parent = "/tmp"
                self._download_running = False
                self._download_mode = ""
                self.download_private_btn = DummyButton()

            def _supports_private_python_installer(self):
                return False

        dummy = DummyDownload()
        dummy._refresh_download_state()
        self.assertTrue(dummy.download_private_btn.enabled)
        self.assertEqual(dummy.download_private_btn.text, "构建项目虚拟环境")

        dummy._download_running = True
        dummy._download_mode = "private_python"
        dummy._refresh_download_state()
        self.assertFalse(dummy.download_private_btn.enabled)
        self.assertEqual(dummy.download_private_btn.text, "构建中…")

    def test_channel_runtime_detects_local_wechat_processes_on_posix(self):
        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self, agent_dir):
                self.agent_dir = agent_dir
                self.cfg = {}
                self._channel_procs = {}

        dummy = DummyChannel("/tmp/GenericAgent")
        fake_output = "\n".join(
            [
                "101 /usr/bin/python3 /tmp/GenericAgent/frontends/wechatapp.py",
                "202 python3 frontends/wechatapp.py",
                "303 python3 /somewhere/else.py",
            ]
        )

        result = mock.Mock(returncode=0, stdout=fake_output, stderr="")
        realpath_map = {
            "/tmp/GenericAgent": "/private/tmp/GenericAgent",
            "/proc/101/cwd": "/other/place",
            "/proc/202/cwd": "/private/tmp/GenericAgent",
            "/proc/303/cwd": "/somewhere",
        }
        with mock.patch.object(channel_runtime.os, "name", "posix"), mock.patch.object(
            channel_runtime.subprocess, "run", return_value=result
        ), mock.patch.object(channel_runtime.os.path, "realpath", side_effect=lambda path: realpath_map.get(path, path)):
            pids = dummy._find_local_wechat_process_pids()

        self.assertEqual(pids, [101, 202])

    def test_iter_local_channel_processes_windows_falls_back_to_wmic_when_cim_has_no_cmdline(self):
        class DummyChannel(ChannelRuntimeMixin):
            _iter_local_channel_processes = ChannelRuntimeMixin._iter_local_channel_processes

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}

        dummy = DummyChannel()
        wmic_fallback_output = "\n".join(
            [
                "12345\tpython -u frontends/tgapp.py",
                "23456\tpython -m frontends.tgapp",
            ]
        )
        calls = []

        def fake_run(args, **kwargs):
            calls.append(list(args or []))
            return mock.Mock(returncode=0, stdout=wmic_fallback_output, stderr="")

        with mock.patch.object(channel_runtime.os, "name", "nt"), mock.patch.object(
            channel_runtime.subprocess,
            "run",
            side_effect=fake_run,
        ):
            rows = dummy._iter_local_channel_processes()

        self.assertEqual([row["pid"] for row in rows], [12345, 23456])
        self.assertTrue(any("tgapp.py" in str(row.get("norm_cmd") or "") for row in rows))
        ps_cmd = " ".join(calls[0]) if calls else ""
        self.assertIn("powershell", ps_cmd.lower())
        self.assertIn("wmic", ps_cmd.lower())

    def test_common_process_matcher_accepts_relative_script_when_realpaths_point_to_same_agent_dir(self):
        self.assertTrue(
            chat_common.process_cmdline_matches_agent_script(
                "python3 frontends/wechatapp.py",
                agent_dir="/tmp/GenericAgent",
                script_rel="frontends/wechatapp.py",
                cwd="/private/tmp/GenericAgent",
                agent_dir_real="/private/tmp/GenericAgent",
                cwd_real="/private/tmp/GenericAgent",
            )
        )
        self.assertFalse(
            chat_common.process_cmdline_matches_agent_script(
                "python3 frontends/wechatapp.py",
                agent_dir="/tmp/GenericAgent",
                script_rel="frontends/wechatapp.py",
                cwd="/srv/other-agent",
                agent_dir_real="/private/tmp/GenericAgent",
                cwd_real="/srv/other-agent",
            )
        )

    def test_common_process_matcher_accepts_python_module_invocation(self):
        self.assertTrue(
            chat_common.process_cmdline_matches_agent_script(
                "python3 -m frontends.wechatapp",
                agent_dir="/tmp/GenericAgent",
                script_rel="frontends/wechatapp.py",
                cwd="/private/tmp/GenericAgent",
                agent_dir_real="/private/tmp/GenericAgent",
                cwd_real="/private/tmp/GenericAgent",
            )
        )

    def test_channel_runtime_detects_local_external_processes_for_multiple_channels(self):
        class DummyChannel(ChannelRuntimeMixin):
            _local_channel_external_pids = ChannelRuntimeMixin._local_channel_external_pids

            def __init__(self):
                self.agent_dir = "/tmp/GenericAgent"
                self.cfg = {}
                self._channel_procs = {"wechat": {"proc": mock.Mock(pid=101)}}

            def _iter_local_channel_processes(self):
                return [
                    {
                        "pid": 101,
                        "cmdline": "python3 frontends/wechatapp.py",
                        "norm_cmd": "python3 frontends/wechatapp.py",
                        "cwd": "/private/tmp/GenericAgent",
                        "cwd_real": "/private/tmp/GenericAgent",
                    },
                    {
                        "pid": 202,
                        "cmdline": "python3 -m frontends.telegramapp",
                        "norm_cmd": "python3 -m frontends.telegramapp",
                        "cwd": "/private/tmp/GenericAgent",
                        "cwd_real": "/private/tmp/GenericAgent",
                    },
                    {
                        "pid": 303,
                        "cmdline": "python3 /srv/other/frontends/wechatapp.py",
                        "norm_cmd": "python3 /srv/other/frontends/wechatapp.py",
                        "cwd": "/srv/other",
                        "cwd_real": "/srv/other",
                    },
                ]

        dummy = DummyChannel()
        specs = [
            {"id": "wechat", "script": "wechatapp.py"},
            {"id": "telegram", "script": "telegramapp.py"},
        ]
        with mock.patch.object(channel_runtime.os, "name", "posix"), mock.patch.object(
            channel_runtime.os.path, "realpath", side_effect=lambda path: "/private/tmp/GenericAgent" if path == "/tmp/GenericAgent" else path
        ), mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", specs):
            detected = dummy._local_channel_external_pids()
        self.assertEqual(detected, {"telegram": [202]})

    def test_channel_runtime_refreshes_wechat_external_running_from_lock_or_process(self):
        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self._channel_procs = {}

        dummy = DummyChannel()
        with mock.patch.object(dummy, "_local_channel_external_pids", return_value={"wechat": [321]}), mock.patch.object(
            dummy, "_wechat_singleton_locked", return_value=False
        ):
            self.assertTrue(dummy._refresh_wechat_external_running())
            self.assertTrue(dummy._channel_external_running("wechat"))

    def test_channel_runtime_terminate_pid_force_uses_shared_tree_terminator_on_posix(self):
        class DummyChannel(ChannelRuntimeMixin):
            pass

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.os, "name", "posix"), mock.patch.object(
            channel_runtime.lz, "terminate_process_tree", return_value=True
        ) as terminator:
            self.assertTrue(dummy._terminate_pid_force(456))
        terminator.assert_called_once_with(456, terminate_timeout=0.8, kill_timeout=0.8)

    def test_local_channel_external_pids_matches_launcher_owned_conductor_runtime(self):
        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._channel_procs = {}

            def _iter_local_channel_processes(self, *, force=False):
                return [
                    {
                        "pid": 202,
                        "cmdline": 'python.exe C:\\launcher\\runtime\\conductor\\conductor.py',
                        "norm_cmd": 'python.exe c:/launcher/runtime/conductor/conductor.py',
                        "cwd": "",
                        "cwd_real": "",
                    }
                ]

        dummy = DummyChannel()
        spec = {"id": "conductor", "script": "conductor.py"}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [spec]), mock.patch.object(
            channel_runtime.lz, "channel_script_path", return_value="C:\\launcher\\runtime\\conductor\\conductor.py"
        ):
            detected = dummy._local_channel_external_pids()
        self.assertEqual(detected, {"conductor": [202]})

    def test_takeover_local_external_conductor_instances_terminates_matching_pids(self):
        class DummyChannel(ChannelRuntimeMixin):
            _takeover_local_external_channel_instances = ChannelRuntimeMixin._takeover_local_external_channel_instances
            _find_local_channel_external_pids = ChannelRuntimeMixin._find_local_channel_external_pids
            _channel_allows_local_external_takeover = ChannelRuntimeMixin._channel_allows_local_external_takeover
            _channel_set_external_running = ChannelRuntimeMixin._channel_set_external_running
            _channel_external_running = ChannelRuntimeMixin._channel_external_running
            _scan_local_channel_external_snapshot = ChannelRuntimeMixin._scan_local_channel_external_snapshot
            _apply_local_channel_external_snapshot = ChannelRuntimeMixin._apply_local_channel_external_snapshot

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {"communication_channels": {"conductor": {"external_running": True}}}
                self._channel_procs = {}
                self.killed = []
                self.scan_count = 0

            def _channel_runtime_cfg(self, channel_id):
                bucket = self.cfg.setdefault("communication_channels", {})
                return bucket.setdefault(str(channel_id), {})

            def _local_channel_external_pids(self, *, force=False):
                self.scan_count += 1
                if self.scan_count == 1:
                    return {"conductor": [32404]}
                return {}

            def _wechat_singleton_locked(self):
                return False

            def _terminate_pid_force(self, pid):
                self.killed.append(int(pid))
                return True

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "conductor"}]), mock.patch.object(
            channel_runtime.lz, "save_config", return_value=None
        ), mock.patch.object(channel_runtime.time, "sleep", return_value=None):
            ok, killed, failed = dummy._takeover_local_external_channel_instances("conductor")

        self.assertTrue(ok)
        self.assertEqual(killed, [32404])
        self.assertEqual(failed, [])
        self.assertEqual(dummy.killed, [32404])
        self.assertFalse(dummy._channel_external_running("conductor"))

    def test_start_wechat_health_watch_drops_stale_runtime_before_ui_callback(self):
        class DummyProc:
            pid = 321

            def poll(self):
                return None

        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_wechat_health_watch = ChannelRuntimeMixin._start_wechat_health_watch

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 5
                self._channel_procs = {"wechat": {"proc": DummyProc(), "log_start_pos": 0}}
                self.calls = []
                self.statuses = []

            def _channel_log_since(self, channel_id, start_pos=0, limit=16000):
                return "[getUpdates] err: -14 session timeout"

            def _wechat_session_timeout_log_hit(self, text):
                return True

            def _stop_channel_process(self, channel_id):
                self.calls.append(("stop", str(channel_id)))

            def _clear_wx_token_info(self):
                self.calls.append("clear_token")

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                self._runtime_context_generation += 1
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread), mock.patch.object(
            channel_runtime.time, "sleep", side_effect=lambda _secs: None
        ):
            dummy._start_wechat_health_watch(show_errors=True)

        self.assertEqual(dummy.calls, [])
        self.assertEqual(dummy.statuses, [])

    def test_start_wechat_health_watch_still_stops_and_clears_current_runtime(self):
        class DummyProc:
            pid = 654

            def poll(self):
                return None

        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_wechat_health_watch = ChannelRuntimeMixin._start_wechat_health_watch

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 3
                self._channel_procs = {"wechat": {"proc": DummyProc(), "log_start_pos": 0}}
                self.calls = []
                self.statuses = []

            def _channel_log_since(self, channel_id, start_pos=0, limit=16000):
                return "[getUpdates] err: -14 session timeout"

            def _wechat_session_timeout_log_hit(self, text):
                return True

            def _stop_channel_process(self, channel_id):
                self.calls.append(("stop", str(channel_id)))

            def _clear_wx_token_info(self):
                self.calls.append("clear_token")

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread), mock.patch.object(
            channel_runtime.time, "sleep", side_effect=lambda _secs: None
        ):
            dummy._start_wechat_health_watch(show_errors=True)

        self.assertEqual(dummy.calls[0], ("stop", "wechat"))
        self.assertIn("clear_token", dummy.calls)
        self.assertTrue(dummy.statuses)
        self.assertIn("本地微信绑定已失效", dummy.statuses[0])
        self.assertTrue(any(isinstance(item, tuple) and item[0] == "warning" for item in dummy.calls))

    def test_start_channel_process_autostart_explains_manual_bind_when_wechat_not_bound(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process_autostart = ChannelRuntimeMixin._start_channel_process_autostart

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._channel_procs = {}
                self.statuses = []
                self.done_values = []

            def _channel_proc_alive(self, channel_id):
                return False

            def _wx_token_info(self):
                return {}

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_INDEX", {"wechat": {"label": "微信", "id": "wechat"}}
        ):
            dummy._start_channel_process_autostart("wechat", done=lambda started: dummy.done_values.append(bool(started)))

        self.assertEqual(dummy.statuses, ["本地微信未绑定，自动启动已跳过；如需启动请先手动扫码绑定。"])
        self.assertEqual(dummy.done_values, [False])

    def test_start_channel_process_lazy_loads_channel_source_before_save(self):
        class DummyProc:
            pid = 1234

            def poll(self):
                return None

        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 1
                self._channel_procs = {}
                self._qt_channel_py_path = ""
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.calls = []
                self._remote_context = {"is_remote": False}

            def _channel_target_context(self):
                return False, None, dict(self._remote_context)

            def _load_channels_source(self):
                self.calls.append("load_source")
                return self.agent_dir, "C:\\demo\\channels\\mykey.py", {"error": "", "configs": [], "extras": {}, "passthrough": []}

            def _qt_channels_save(self, silent=False, apply_running=True):
                self.calls.append(("save", str(self._qt_channel_py_path)))
                return True

            def _channel_proc_alive(self, channel_id):
                return False

            def _channel_conflict_message(self, channel_id):
                return ""

            def _check_runtime_dependencies(self, **kwargs):
                self.calls.append("deps_ok")
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _channel_log_path(self, channel_id):
                return "C:\\demo\\temp\\launcher_channels\\telegram.log"

            def _create_channel_process_session(self, channel_id, proc, log_path):
                return "sess-1"

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                self.calls.append(("sync", str(channel_id), bool(final)))

            def _reload_channels_editor_state(self):
                self.calls.append("reload")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

        dummy = DummyChannel()
        dummy_proc = DummyProc()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"telegram": {"id": "telegram", "label": "Telegram", "script": "tgapp.py", "fields": []}}), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.os.path, "isfile", return_value=True
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_config_path", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "_popen_external_subprocess", return_value=dummy_proc
        ), mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            ok = dummy._start_channel_process("telegram", show_errors=False)

        self.assertTrue(ok)
        self.assertIn("load_source", dummy.calls)
        self.assertIn(("save", "C:\\demo\\channels\\mykey.py"), dummy.calls)

    def test_start_channel_process_telegram_does_not_open_wechat_qr_dialog(self):
        class DummyProc:
            pid = 2234

            def poll(self):
                return None

        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 1
                self._channel_procs = {}
                self._qt_channel_py_path = "C:\\demo\\channels\\mykey.py"
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.qr_calls = 0

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _qt_channels_save(self, silent=False, apply_running=True):
                return True

            def _channel_proc_alive(self, channel_id):
                return False

            def _stop_channel_process(self, channel_id):
                raise AssertionError("telegram first start should not attempt restart")

            def _open_wechat_qr_dialog(self, show_errors=True, remote_device=None):
                self.qr_calls += 1
                return True

            def _channel_conflict_message(self, channel_id):
                return ""

            def _check_runtime_dependencies(self, **kwargs):
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _channel_log_path(self, channel_id):
                return "C:\\demo\\temp\\launcher_channels\\telegram.log"

            def _create_channel_process_session(self, channel_id, proc, log_path):
                return "sess-telegram"

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                return None

            def _reload_channels_editor_state(self):
                return None

            def _refresh_sessions(self):
                return None

        dummy = DummyChannel()
        with mock.patch.object(
            channel_runtime.lz,
            "COMM_CHANNEL_INDEX",
            {"telegram": {"id": "telegram", "label": "Telegram", "script": "tgapp.py", "fields": []}},
        ), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.os.path, "isfile", return_value=True
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_config_path", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "_popen_external_subprocess", return_value=DummyProc()
        ), mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            ok = dummy._start_channel_process("telegram", show_errors=False)

        self.assertTrue(ok)
        self.assertEqual(dummy.qr_calls, 0)

    def test_start_channel_process_terminal_channel_uses_visible_terminal_launcher_and_skips_process_tracking(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 1
                self._channel_procs = {}
                self._qt_channel_py_path = "C:\\demo\\channels\\mykey.py"
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.statuses = []
                self.calls = []

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _qt_channels_save(self, silent=False, apply_running=True):
                self.calls.append(("save", bool(silent), bool(apply_running)))
                return True

            def _channel_proc_alive(self, channel_id):
                return False

            def _channel_conflict_message(self, channel_id):
                return ""

            def _check_runtime_dependencies(self, **kwargs):
                self.calls.append(("deps", dict(kwargs)))
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _channel_log_path(self, channel_id):
                raise AssertionError("terminal launch should not create a managed log file")

            def _create_channel_process_session(self, channel_id, proc, log_path):
                raise AssertionError("terminal launch should not create a managed session")

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                raise AssertionError("terminal launch should not sync a managed session")

            def _reload_channels_editor_state(self):
                self.calls.append("reload")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyChannel()
        terminal_spec = {
            "id": "tui",
            "label": "终端 TUI",
            "script": "tui_v3.py",
            "script_candidates": ["tui_v3.py", "tuiapp_v2.py", "tuiapp.py"],
            "pip": "prompt_toolkit rich Pillow textual",
            "fields": [],
            "launch_mode": "terminal",
        }
        with mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_INDEX", {"tui": terminal_spec}
        ), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.lz, "channel_script_path", return_value="C:\\demo\\frontends\\tuiapp.py"
        ), mock.patch.object(
            channel_runtime.os.path,
            "isfile",
            side_effect=lambda path: str(path) in ("python", "C:\\demo\\frontends\\tuiapp.py"),
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_configured_python_exe", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "launch_visible_terminal_script", return_value=True
        ) as launcher, mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ) as open_mock:
            ok = dummy._start_channel_process("tui", show_errors=False)

        self.assertTrue(ok)
        launcher.assert_called_once_with(
            "python",
            "C:\\demo\\frontends\\tuiapp.py",
            cwd="C:\\demo",
            env={},
            title="终端 TUI",
        )
        self.assertEqual(dummy._channel_procs, {})
        self.assertNotIn("reload", dummy.calls)
        self.assertNotIn("refresh_sessions", dummy.calls)
        open_mock.assert_not_called()
        self.assertTrue(any("新终端" in status for status in dummy.statuses))

    def test_open_channel_web_page_falls_back_to_browser_when_desktop_services_fails(self):
        class DummyChannel(ChannelRuntimeMixin):
            _open_channel_web_page = ChannelRuntimeMixin._open_channel_web_page

            def _remote_channel_label_text(self, channel_id):
                return "Conductor 总管台"

            def _channel_web_url(self, channel_id):
                return "http://127.0.0.1:8900/"

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.QDesktopServices, "openUrl", return_value=False) as desktop_open, mock.patch.object(
            channel_runtime.webbrowser, "open", return_value=True
        ) as browser_open:
            self.assertTrue(dummy._open_channel_web_page("conductor", show_errors=False))

        desktop_open.assert_called_once()
        browser_open.assert_called_once_with("http://127.0.0.1:8900/")

    def test_start_remote_channel_process_rejects_local_only_channel(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_extras = {}
                self.statuses = []
                self.warnings = []

            def _channel_is_local_only(self, channel_id):
                return True

            def _remote_channel_label_text(self, channel_id):
                return "Conductor 总管台"

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.warnings.append((str(title), str(text), str(detail)))

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"conductor": {"id": "conductor", "label": "Conductor 总管台"}}):
            self.assertFalse(dummy._start_remote_channel_process("conductor", show_errors=True))

        self.assertTrue(any("只支持在启动器本机运行" in text for text in dummy.statuses))
        self.assertTrue(any("只支持在启动器本机运行" in text for _title, text, _detail in dummy.warnings))

    def test_local_slash_command_items_include_optional_upstream_export_continue_rename_review_commands_when_available(self):
        class DummyBridge(BridgeRuntimeMixin):
            _local_slash_command_items = BridgeRuntimeMixin._local_slash_command_items
            _upstream_optional_slash_command_items = BridgeRuntimeMixin._upstream_optional_slash_command_items

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir

        with tempfile.TemporaryDirectory() as td:
            frontends = os.path.join(td, "frontends")
            os.makedirs(frontends, exist_ok=True)
            for filename in ("continue_cmd.py", "btw_cmd.py", "review_cmd.py", "export_cmd.py", "session_names.py"):
                with open(os.path.join(frontends, filename), "w", encoding="utf-8") as f:
                    f.write("# demo\n")

            with mock.patch.object(bridge_runtime.lz, "is_valid_agent_dir", return_value=True):
                items = DummyBridge(td)._local_slash_command_items()

        commands = [str(item.get("command") or "") for item in items]
        self.assertIn("/cost", commands)
        self.assertIn("/cost all", commands)
        self.assertIn("/continue", commands)
        self.assertIn("/continue N", commands)
        self.assertIn("/continue <name>", commands)
        self.assertIn("/rename <name>", commands)
        self.assertIn("/export", commands)
        self.assertIn("/export clip", commands)
        self.assertIn("/export all", commands)
        self.assertIn("/export <file>", commands)
        self.assertIn("/btw <q>", commands)
        self.assertIn("/review [scope]", commands)

    def test_install_optional_upstream_frontend_slash_patches_loads_continue_btw_review_modules(self):
        class DummyAgent:
            installs = []

        with tempfile.TemporaryDirectory() as td:
            frontends = os.path.join(td, "frontends")
            os.makedirs(frontends, exist_ok=True)
            file_specs = {
                "continue_cmd.py": "_ga_launcher_continue_cmd",
                "btw_cmd.py": "_ga_launcher_btw_cmd",
                "review_cmd.py": "_ga_launcher_review_cmd",
            }
            for filename, module_name in file_specs.items():
                with open(os.path.join(frontends, filename), "w", encoding="utf-8") as f:
                    f.write(
                        "def install(cls):\n"
                        "    cls.installs.append(__name__)\n"
                    )

            bridge._install_optional_upstream_frontend_slash_patches(td, DummyAgent)

        self.assertEqual(
            DummyAgent.installs,
            ["_ga_launcher_continue_cmd", "_ga_launcher_btw_cmd", "_ga_launcher_review_cmd"],
        )

    def test_install_optional_upstream_frontend_slash_patches_adds_export_rename_and_named_continue_handlers(self):
        queue_mod = __import__("queue")

        class DummyAgent:
            def __init__(self):
                self.log_path = os.path.join("C:\\demo", "temp", "model_responses", "model_responses_123.txt")
                self.llmclient = types.SimpleNamespace(log_path=self.log_path, backend=types.SimpleNamespace(history=[{"role": "user", "content": "hi"}]))

            def _handle_slash_cmd(self, raw_query, display_queue):
                return raw_query

        with tempfile.TemporaryDirectory() as td:
            frontends = os.path.join(td, "frontends")
            os.makedirs(frontends, exist_ok=True)
            with open(os.path.join(frontends, "continue_cmd.py"), "w", encoding="utf-8") as f:
                f.write(
                    "calls = []\n"
                    "def install(cls):\n"
                    "    return None\n"
                    "def list_sessions(exclude_pid=None):\n"
                    "    return [('C:/demo/temp/model_responses/model_responses_999.txt', 1710000000.0, 'preview text', 2)]\n"
                    "def reset_conversation(agent, message=None):\n"
                    "    calls.append(('reset', message))\n"
                    "def restore(agent, path):\n"
                    "    calls.append(('restore', path))\n"
                    "    return ('✅ restored', True)\n"
                    "def _rel_time(_mtime):\n"
                    "    return '1分钟前'\n"
                    "def _escape_md(text):\n"
                    "    return str(text)\n"
                )
            with open(os.path.join(frontends, "export_cmd.py"), "w", encoding="utf-8") as f:
                f.write(
                    "calls = []\n"
                    "def last_assistant_text(agent):\n"
                    "    calls.append(('last', getattr(agent, 'log_path', '')))\n"
                    "    return 'assistant body'\n"
                    "def wrap_for_clipboard(text, language='markdown'):\n"
                    "    return f'WRAPPED:{text}'\n"
                    "def export_to_temp(text, name):\n"
                    "    calls.append(('file', text, name))\n"
                    "    return f'C:/demo/temp/{name}'\n"
                )
            with open(os.path.join(frontends, "session_names.py"), "w", encoding="utf-8") as f:
                f.write(
                    "calls = []\n"
                    "def name_for(path):\n"
                    "    if path.endswith('model_responses_999.txt'):\n"
                    "        return 'demo-name'\n"
                    "    if path.endswith('model_responses_123.txt'):\n"
                    "        return 'current-name'\n"
                    "    return ''\n"
                    "def path_for(name, exclude_basename=None):\n"
                    "    calls.append(('path_for', name, exclude_basename))\n"
                    "    if str(name).lower() == 'demo-name':\n"
                    "        return 'C:/demo/temp/model_responses/model_responses_999.txt'\n"
                    "    return None\n"
                    "def has_name(name, exclude_basename=None):\n"
                    "    return False\n"
                    "def set_name(path, name):\n"
                    "    calls.append(('set_name', path, name))\n"
                    "def migrate(old_path, new_path):\n"
                    "    calls.append(('migrate', old_path, new_path))\n"
                )

            bridge._install_optional_upstream_frontend_slash_patches(td, DummyAgent)
            agent = DummyAgent()

            export_queue = queue_mod.Queue()
            continue_queue = queue_mod.Queue()
            rename_queue = queue_mod.Queue()

            export_result = agent._handle_slash_cmd("/export clip", export_queue)
            continue_result = agent._handle_slash_cmd("/continue demo-name", continue_queue)
            rename_result = agent._handle_slash_cmd("/rename graphite", rename_queue)

            export_item = export_queue.get_nowait()
            continue_item = continue_queue.get_nowait()
            rename_item = rename_queue.get_nowait()

        self.assertIsNone(export_result)
        self.assertEqual(export_item["done"], "📋 最后一轮回复:\n\nWRAPPED:assistant body")
        self.assertIsNone(continue_result)
        self.assertEqual(continue_item["done"], "✅ restored")
        self.assertIsNone(rename_result)
        self.assertEqual(rename_item["done"], "✅ 已重命名为 'graphite'")

    def test_start_channel_process_restarts_managed_local_channel_instead_of_reusing_proc(self):
        class DummyProc:
            def __init__(self, pid):
                self.pid = pid

            def poll(self):
                return None

        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 1
                self._qt_channel_py_path = "C:\\demo\\channels\\mykey.py"
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.stop_calls = []
                self.session_ids = []
                self._channel_procs = {
                    "telegram": {
                        "proc": DummyProc(111),
                        "log_handle": None,
                        "log_path": "C:\\demo\\temp\\launcher_channels\\telegram.log",
                    }
                }

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _qt_channels_save(self, silent=False, apply_running=True):
                return True

            def _channel_proc_alive(self, channel_id):
                info = self._channel_procs.get(channel_id) or {}
                proc = info.get("proc")
                return bool(proc) and proc.poll() is None

            def _stop_channel_process(self, channel_id):
                info = self._channel_procs.pop(channel_id, None) or {}
                proc = info.get("proc")
                self.stop_calls.append(int(getattr(proc, "pid", 0) or 0))
                return True

            def _channel_conflict_message(self, channel_id):
                return ""

            def _check_runtime_dependencies(self, **kwargs):
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _channel_log_path(self, channel_id):
                return "C:\\demo\\temp\\launcher_channels\\telegram.log"

            def _create_channel_process_session(self, channel_id, proc, log_path):
                self.session_ids.append(int(getattr(proc, "pid", 0) or 0))
                return f"sess-{proc.pid}"

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                return None

            def _reload_channels_editor_state(self):
                return None

            def _refresh_sessions(self):
                return None

        dummy = DummyChannel()
        new_proc = DummyProc(222)
        with mock.patch.object(
            channel_runtime.lz,
            "COMM_CHANNEL_INDEX",
            {"telegram": {"id": "telegram", "label": "Telegram", "script": "tgapp.py", "fields": []}},
        ), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.os.path, "isfile", return_value=True
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_config_path", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "_popen_external_subprocess", return_value=new_proc
        ) as popen_proc, mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ):
            ok = dummy._start_channel_process("telegram", show_errors=False)

        self.assertTrue(ok)
        self.assertEqual(dummy.stop_calls, [111])
        self.assertEqual(dummy.session_ids, [222])
        self.assertIs(dummy._channel_procs["telegram"]["proc"], new_proc)
        popen_proc.assert_called_once()

    def test_qt_channels_save_keeps_existing_extras_when_widgets_not_rendered(self):
        class DummyChannel(ChannelRuntimeMixin):
            _qt_channels_save = ChannelRuntimeMixin._qt_channels_save

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {
                    "tg_bot_token": "old_token",
                    "tg_allowed_users": [1001, 1002],
                }
                self._qt_channel_states = {}
                self.saved_text = ""

            def _settings_target_context(self):
                return {"is_remote": False}

            def _settings_target_write_mykey_text(self, text):
                self.saved_text = str(text or "")
                return True, self._qt_channel_py_path, ""

            def _restart_running_channels(self, show_errors=False):
                return 0

            def _reload_channels_editor_state(self):
                return None

        dummy = DummyChannel()
        specs = [
            {
                "id": "telegram",
                "fields": [
                    {"key": "tg_bot_token", "kind": "password"},
                    {"key": "tg_allowed_users", "kind": "list_int"},
                ],
            }
        ]
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", specs), mock.patch.object(channel_runtime.lz, "save_config", return_value=None):
            ok = dummy._qt_channels_save(silent=True, apply_running=False)

        self.assertTrue(ok)
        self.assertEqual(dummy._qt_channel_extras.get("tg_bot_token"), "old_token")
        self.assertEqual(dummy._qt_channel_extras.get("tg_allowed_users"), [1001, 1002])
        self.assertIn("tg_bot_token = 'old_token'", dummy.saved_text)
        self.assertIn("tg_allowed_users = [1001, 1002]", dummy.saved_text)

    def test_qt_channels_save_blocks_invalid_api_references_before_write(self):
        class DummyChannel(ChannelRuntimeMixin):
            _qt_channels_save = ChannelRuntimeMixin._qt_channels_save

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_configs = [
                    {
                        "var": "mixin_config",
                        "kind": "mixin",
                        "data": {"llm_nos": ["gpt-native"], "max_retries": 3},
                    },
                    {
                        "var": "native_oai_config",
                        "kind": "native_oai",
                        "data": {"name": "mimo", "apikey": "sk-demo", "apibase": "https://api.example/v1", "model": "demo"},
                    },
                ]
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self._qt_channel_states = {}
                self.calls = []

            def _settings_target_context(self):
                return {"is_remote": False}

            def _settings_target_write_mykey_text(self, _text):
                raise AssertionError("invalid channel API references must block before write")

            def _channel_critical(self, title, text, detail=""):
                self.calls.append(("critical", str(title), str(text), str(detail)))

        dummy = DummyChannel()
        ok = dummy._qt_channels_save(silent=False, apply_running=False)

        self.assertFalse(ok)
        self.assertEqual(dummy.calls[0][0], "critical")
        self.assertEqual(dummy.calls[0][1], "保存失败")
        self.assertIn("gpt-native", dummy.calls[0][2])

    def test_open_wechat_qr_dialog_drops_stale_local_status_callback(self):
        class DummySignal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

        class DummyButton:
            def __init__(self, text=""):
                self.text = str(text)
                self.clicked = DummySignal()

            def setStyleSheet(self, _style):
                return None

        class DummyLabel:
            instances = []

            def __init__(self, text=""):
                self.text = str(text)
                DummyLabel.instances.append(self)

            def setObjectName(self, _name):
                return None

            def setWordWrap(self, _enabled):
                return None

            def setAlignment(self, _alignment):
                return None

            def setPixmap(self, _pixmap):
                return None

            def setText(self, text):
                self.text = str(text)

        class DummyLayout:
            def __init__(self, _parent=None):
                return None

            def setContentsMargins(self, *_args):
                return None

            def setSpacing(self, _value):
                return None

            def addWidget(self, _widget, *_args):
                return None

            def addLayout(self, _layout, *_args):
                return None

            def addStretch(self, _value):
                return None

        class DummyDialog:
            def __init__(self, _parent=None):
                self.result_code = 0

            def setWindowTitle(self, _title):
                return None

            def setModal(self, _modal):
                return None

            def resize(self, _w, _h):
                return None

            def accept(self):
                self.result_code = 1

            def reject(self):
                self.result_code = 0

            def done(self, code):
                self.result_code = int(code)

            def exec(self):
                return self.result_code

        class DummyCard:
            pass

        class DummyPixmap:
            def loadFromData(self, _data, _fmt):
                return True

            def scaled(self, *_args):
                return self

        class DummyQrImage:
            def convert(self, _mode):
                return self

            def save(self, buf, format="PNG"):
                buf.write(b"png")
                return True

        class SelectiveThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target
                self._name = name

            def start(self):
                if self._name is None and callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _open_wechat_qr_dialog = ChannelRuntimeMixin._open_wechat_qr_dialog

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 4
                self.statuses = []
                self.warnings = []

            def _local_begin_wechat_qr_login(self, timeout=45):
                return True, {"qrcode": "qr-1", "qrcode_img_content": "content", "login_id": "login-1", "issued_at": 1.0}, ""

            def _local_wechat_qr_state(self, login_id):
                return True, {"status": "expired", "error": "", "login_id": str(login_id), "qrcode": "qr-1"}, ""

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                self._runtime_context_generation += 1
                if callable(callback):
                    callback()

            def _panel_card(self):
                return DummyCard()

            def _action_button_style(self, primary=False):
                return "primary" if primary else "default"

            def _channel_warning(self, title, text, detail=""):
                self.warnings.append((str(title), str(text), str(detail)))

        DummyLabel.instances = []
        dummy = DummyChannel()
        with mock.patch.object(channel_runtime, "QDialog", DummyDialog), mock.patch.object(
            channel_runtime, "QVBoxLayout", DummyLayout
        ), mock.patch.object(channel_runtime, "QHBoxLayout", DummyLayout), mock.patch.object(
            channel_runtime, "QLabel", DummyLabel
        ), mock.patch.object(channel_runtime, "QPushButton", DummyButton), mock.patch.object(
            channel_runtime, "QPixmap", DummyPixmap
        ), mock.patch.object(channel_runtime.threading, "Thread", SelectiveThread), mock.patch.object(
            channel_runtime.time, "sleep", side_effect=lambda _secs: None
        ), mock.patch.object(channel_runtime.lz.qrcode, "make", return_value=DummyQrImage()):
            ok = dummy._open_wechat_qr_dialog(show_errors=True)

        self.assertFalse(ok)
        label_texts = [item.text for item in DummyLabel.instances]
        self.assertIn("请使用微信扫码，确认后会自动完成绑定。", label_texts)
        self.assertNotIn("二维码已过期，请点“重新获取”。", label_texts)

    def test_open_wechat_qr_dialog_keeps_local_status_callback_for_current_runtime(self):
        class DummySignal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

        class DummyButton:
            def __init__(self, text=""):
                self.text = str(text)
                self.clicked = DummySignal()

            def setStyleSheet(self, _style):
                return None

        class DummyLabel:
            instances = []

            def __init__(self, text=""):
                self.text = str(text)
                DummyLabel.instances.append(self)

            def setObjectName(self, _name):
                return None

            def setWordWrap(self, _enabled):
                return None

            def setAlignment(self, _alignment):
                return None

            def setPixmap(self, _pixmap):
                return None

            def setText(self, text):
                self.text = str(text)

        class DummyLayout:
            def __init__(self, _parent=None):
                return None

            def setContentsMargins(self, *_args):
                return None

            def setSpacing(self, _value):
                return None

            def addWidget(self, _widget, *_args):
                return None

            def addLayout(self, _layout, *_args):
                return None

            def addStretch(self, _value):
                return None

        class DummyDialog:
            def __init__(self, _parent=None):
                self.result_code = 0

            def setWindowTitle(self, _title):
                return None

            def setModal(self, _modal):
                return None

            def resize(self, _w, _h):
                return None

            def accept(self):
                self.result_code = 1

            def reject(self):
                self.result_code = 0

            def done(self, code):
                self.result_code = int(code)

            def exec(self):
                return self.result_code

        class DummyCard:
            pass

        class DummyPixmap:
            def loadFromData(self, _data, _fmt):
                return True

            def scaled(self, *_args):
                return self

        class DummyQrImage:
            def convert(self, _mode):
                return self

            def save(self, buf, format="PNG"):
                buf.write(b"png")
                return True

        class SelectiveThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target
                self._name = name

            def start(self):
                if self._name is None and callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _open_wechat_qr_dialog = ChannelRuntimeMixin._open_wechat_qr_dialog

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 4
                self.statuses = []
                self.warnings = []

            def _local_begin_wechat_qr_login(self, timeout=45):
                return True, {"qrcode": "qr-1", "qrcode_img_content": "content", "login_id": "login-1", "issued_at": 1.0}, ""

            def _local_wechat_qr_state(self, login_id):
                return True, {"status": "expired", "error": "", "login_id": str(login_id), "qrcode": "qr-1"}, ""

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

            def _panel_card(self):
                return DummyCard()

            def _action_button_style(self, primary=False):
                return "primary" if primary else "default"

            def _channel_warning(self, title, text, detail=""):
                self.warnings.append((str(title), str(text), str(detail)))

        DummyLabel.instances = []
        dummy = DummyChannel()
        with mock.patch.object(channel_runtime, "QDialog", DummyDialog), mock.patch.object(
            channel_runtime, "QVBoxLayout", DummyLayout
        ), mock.patch.object(channel_runtime, "QHBoxLayout", DummyLayout), mock.patch.object(
            channel_runtime, "QLabel", DummyLabel
        ), mock.patch.object(channel_runtime, "QPushButton", DummyButton), mock.patch.object(
            channel_runtime, "QPixmap", DummyPixmap
        ), mock.patch.object(channel_runtime.threading, "Thread", SelectiveThread), mock.patch.object(
            channel_runtime.time, "sleep", side_effect=lambda _secs: None
        ), mock.patch.object(channel_runtime.lz.qrcode, "make", return_value=DummyQrImage()):
            ok = dummy._open_wechat_qr_dialog(show_errors=True)

        self.assertFalse(ok)
        label_texts = [item.text for item in DummyLabel.instances]
        self.assertIn("二维码已过期，请点“重新获取”。", label_texts)

    def test_channel_status_reports_external_running_for_local_channel(self):
        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = ""
                self.cfg = {}
                self._channel_procs = {}

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return str(channel_id) == "wechat"

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_missing_required(self, _channel_id, _values):
                return []

            def _channel_is_auto_start(self, _channel_id):
                return False

        dummy = DummyChannel()
        text, _color = dummy._channel_status("wechat", {})
        self.assertEqual(text, "外部运行中")

    def test_create_local_channel_process_session_does_not_reuse_remote_cached_session(self):
        class DummyProc:
            pid = 2468

        class DummyChannel(ChannelRuntimeMixin):
            _find_reusable_channel_process_session = ChannelRuntimeMixin._find_reusable_channel_process_session
            _create_channel_process_session = ChannelRuntimeMixin._create_channel_process_session

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _channel_session_title(self, channel_id, started_at):
                return f"{channel_id}-{int(started_at)}"

            def _ensure_session_usage_metadata(self, session):
                return session

        dummy = DummyChannel()
        saved = []

        class DummyUuid:
            hex = "1234567890abcdef1234567890abcdef"

        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            channel_runtime.lz,
            "list_sessions",
            return_value=[
                {
                    "id": "rdev_box-1_telegram_proc",
                    "session_kind": "channel_process",
                    "channel_id": "telegram",
                    "device_scope": "remote",
                    "device_id": "box-1",
                    "updated_at": 999.0,
                }
            ],
        ), mock.patch.object(channel_runtime.lz, "load_session", return_value={}), mock.patch.object(
            channel_runtime.lz, "save_session", side_effect=lambda root, payload, touch=False: saved.append(dict(payload))
        ), mock.patch.object(channel_runtime.uuid, "uuid4", return_value=DummyUuid()), mock.patch.object(
            channel_runtime.time, "time", return_value=1234.0
        ):
            session_id = dummy._create_channel_process_session("telegram", DummyProc(), "C:\\demo\\telegram.log")

        self.assertEqual(session_id, "1234567890ab")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], "1234567890ab")
        self.assertEqual(saved[0]["device_scope"], "local")
        self.assertEqual(saved[0]["device_id"], "local")
        self.assertEqual(saved[0]["channel_id"], "telegram")

    def test_autostart_channels_skips_channels_marked_external_running(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_autostart_channels = ChannelRuntimeMixin._start_autostart_channels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._autostart_channels_running = False
                self._autostart_channel_pending_ids = set()
                self._autostart_channel_current = ""
                self.calls = []

            def _channel_is_auto_start(self, channel_id):
                return str(channel_id) == "wechat"

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return str(channel_id) == "wechat"

            def _request_local_channel_external_running_refresh(self, **kwargs):
                self.calls.append(("request_refresh", bool(kwargs.get("force", False))))
                return False

            def _refresh_channels_runtime_status_labels(self):
                return None

        dummy = DummyChannel()
        specs = [{"id": "wechat"}, {"id": "telegram"}]
        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_SPECS", specs
        ):
            dummy._start_autostart_channels()

        self.assertEqual(dummy.calls, [("request_refresh", False)])
        self.assertEqual(dummy._autostart_channel_pending_ids, set())
        self.assertEqual(dummy._autostart_channel_current, "")

    def test_autostart_channels_waits_for_async_external_probe_before_building_queue(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_autostart_channels = ChannelRuntimeMixin._start_autostart_channels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._autostart_channels_running = False
                self._autostart_channel_pending_ids = set()
                self._autostart_channel_current = ""
                self.external_running = False
                self.started = []
                self.calls = []
                self.pending_callback = None

            def _channel_is_auto_start(self, channel_id):
                return str(channel_id) == "wechat"

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return bool(self.external_running) if str(channel_id) == "wechat" else False

            def _request_local_channel_external_running_refresh(self, **kwargs):
                self.calls.append(("request_refresh", bool(kwargs.get("force", False))))
                callback = kwargs.get("after")
                if self.pending_callback is None:
                    self.pending_callback = callback
                    return True
                return False

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _channel_post_ui(self, fn, action_name=""):
                fn()

            def _start_channel_process_autostart(self, channel_id, done=None):
                self.started.append(str(channel_id))
                if callable(done):
                    done(True)

        dummy = DummyChannel()
        specs = [{"id": "wechat"}]
        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_SPECS", specs
        ):
            dummy._start_autostart_channels()
            self.assertEqual(dummy.started, [])
            self.assertTrue(callable(dummy.pending_callback))
            dummy.external_running = True
            dummy.pending_callback({})

        self.assertEqual(dummy.calls[0], ("request_refresh", False))
        self.assertEqual(dummy.started, [])
        self.assertEqual(dummy._autostart_channel_pending_ids, set())
        self.assertEqual(dummy._autostart_channel_current, "")

    def test_stop_all_managed_channels_clears_autostart_queue_state(self):
        class DummyChannel(ChannelRuntimeMixin):
            _stop_all_managed_channels = ChannelRuntimeMixin._stop_all_managed_channels

            def __init__(self):
                self._autostart_channels_run_id = 4
                self._autostart_channels_running = True
                self._autostart_channel_pending_ids = {"wechat", "telegram"}
                self._autostart_channel_current = "wechat"
                self._channel_procs = {"wechat": object(), "telegram": object()}
                self.stopped = []

            def _stop_channel_process(self, channel_id):
                self.stopped.append(str(channel_id))
                return str(channel_id) == "wechat"

        dummy = DummyChannel()
        stopped = dummy._stop_all_managed_channels(refresh=False)

        self.assertEqual(stopped, 1)
        self.assertEqual(dummy.stopped, ["wechat", "telegram"])
        self.assertEqual(dummy._autostart_channels_run_id, 5)
        self.assertFalse(dummy._autostart_channels_running)
        self.assertEqual(dummy._autostart_channel_pending_ids, set())
        self.assertEqual(dummy._autostart_channel_current, "")

    def test_autostart_queue_does_not_continue_after_stop_all_managed_channels(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_autostart_channels = ChannelRuntimeMixin._start_autostart_channels
            _stop_all_managed_channels = ChannelRuntimeMixin._stop_all_managed_channels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._autostart_channels_run_id = 0
                self._autostart_channels_running = False
                self._autostart_channel_pending_ids = set()
                self._autostart_channel_current = ""
                self.started = []
                self.callbacks = {}
                self.status_refreshes = 0

            def _channel_is_auto_start(self, channel_id):
                return str(channel_id) in {"wechat", "telegram"}

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, _channel_id):
                return False

            def _request_local_channel_external_running_refresh(self, **kwargs):
                return False

            def _refresh_channels_runtime_status_labels(self):
                self.status_refreshes += 1

            def _channel_post_ui(self, fn, action_name=""):
                fn()

            def _start_channel_process_autostart(self, channel_id, done=None):
                self.started.append(str(channel_id))
                self.callbacks[str(channel_id)] = done

            def _stop_channel_process(self, _channel_id):
                return False

        dummy = DummyChannel()
        specs = [{"id": "wechat"}, {"id": "telegram"}]
        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_SPECS", specs
        ):
            dummy._start_autostart_channels()

        self.assertEqual(dummy.started, ["wechat"])
        self.assertTrue(dummy._autostart_channels_running)
        self.assertEqual(dummy._autostart_channel_pending_ids, {"wechat", "telegram"})
        self.assertEqual(dummy._autostart_channel_current, "wechat")

        dummy._stop_all_managed_channels(refresh=False)
        stale_done = dummy.callbacks["wechat"]
        stale_done(True)

        self.assertEqual(dummy.started, ["wechat"])
        self.assertFalse(dummy._autostart_channels_running)
        self.assertEqual(dummy._autostart_channel_pending_ids, set())
        self.assertEqual(dummy._autostart_channel_current, "")

    def test_autostart_queue_takes_over_external_conductor(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_autostart_channels = ChannelRuntimeMixin._start_autostart_channels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._autostart_channels_run_id = 0
                self._autostart_channels_running = False
                self._autostart_channel_pending_ids = set()
                self._autostart_channel_current = ""
                self.started = []
                self.callbacks = {}
                self.status_refreshes = 0

            def _channel_is_auto_start(self, channel_id):
                return str(channel_id) == "conductor"

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return str(channel_id) == "conductor"

            def _request_local_channel_external_running_refresh(self, **_kwargs):
                return False

            def _refresh_channels_runtime_status_labels(self):
                self.status_refreshes += 1

            def _channel_post_ui(self, fn, action_name=""):
                fn()

            def _start_channel_process_autostart(self, channel_id, done=None):
                self.started.append(str(channel_id))
                self.callbacks[str(channel_id)] = done

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "conductor"}]
        ):
            dummy._start_autostart_channels()

        self.assertEqual(dummy.started, ["conductor"])
        self.assertTrue(dummy._autostart_channels_running)
        self.assertEqual(dummy._autostart_channel_pending_ids, {"conductor"})
        self.assertEqual(dummy._autostart_channel_current, "conductor")

    def test_remote_channel_start_drops_stale_context_before_ui_refresh(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                dummy._runtime_context_generation += 1
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self._last_session_list_signature = "sig"
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                self.calls.append(("save", bool(silent), bool(apply_running)))
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                return True, "", {"ok": True}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                return True, "", {"pid": 321}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("wechat", show_errors=True))

        self.assertEqual(dummy.calls, [("save", True, False)])
        self.assertEqual(len(dummy.statuses), 1)
        self.assertIn("正在启动远端", dummy.statuses[0])

    def test_remote_channel_stop_drops_stale_context_before_ui_refresh(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                dummy._runtime_context_generation += 1
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _stop_remote_channel_process = ChannelRuntimeMixin._stop_remote_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 4
                self._settings_target_change_token = 7
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_stop_channel_process_blocking(self, device, channel_id):
                return True, "", {"was_running": True}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._stop_remote_channel_process("wechat"))

        self.assertEqual(dummy.calls, [])
        self.assertEqual(len(dummy.statuses), 1)
        self.assertIn("正在停止远端", dummy.statuses[0])

    def test_remote_channel_log_read_drops_stale_context_before_ui_refresh(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                dummy._runtime_context_generation += 1
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _show_remote_channel_log_tail = ChannelRuntimeMixin._show_remote_channel_log_tail

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 6
                self._settings_target_change_token = 8
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_tail_channel_log_blocking(self, device, channel_id):
                return True, "tail content", "运行中", ""

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text), str(detail)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._show_remote_channel_log_tail("wechat", "微信"))

        self.assertEqual(dummy.calls, [])
        self.assertEqual(len(dummy.statuses), 1)
        self.assertIn("正在读取远端", dummy.statuses[0])

    def test_remote_channel_start_reports_success_with_log_hint(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self._last_session_list_signature = "sig"
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                self.calls.append(("save", bool(silent), bool(apply_running)))
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                return True, "", {"ok": True}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                return True, "", {"pid": 321}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("wechat", show_errors=True))

        self.assertEqual(
            dummy.statuses,
            [
                "正在启动远端 微信 渠道…",
                "已启动远端 微信 渠道（PID 321）；如无新消息可再查看远端日志。",
            ],
        )
        self.assertIn(("info", "启动成功", "远端 微信 已启动；如无响应可继续查看远端日志。"), dummy.calls)

    def test_remote_channel_start_prepares_dependencies_before_launch(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                self.calls.append(("deps", str(channel_id), tuple(self._channel_extra_packages(spec))))
                return True, "", {"ok": True}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                self.calls.append(("start", str(channel_id)))
                return True, "", {"pid": 987}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("telegram", show_errors=True))

        self.assertEqual(dummy.calls[:2], [("deps", "telegram", ("python-telegram-bot",)), ("start", "telegram")])
        self.assertEqual(dummy.statuses[-1], "已启动远端 Telegram / 纸飞机 渠道（PID 987）；如无新消息可再查看远端日志。")

    def test_remote_channel_start_blocks_invalid_api_references_before_launch(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self._qt_channel_configs = [
                    {
                        "var": "mixin_config",
                        "kind": "mixin",
                        "data": {"llm_nos": ["gpt-native"], "max_retries": 3},
                    },
                    {
                        "var": "native_oai_config",
                        "kind": "native_oai",
                        "data": {"name": "mimo", "apikey": "sk-demo", "apibase": "https://api.example/v1", "model": "demo"},
                    },
                ]
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                raise AssertionError("invalid API references must block before save")

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

        dummy = DummyChannel()

        self.assertFalse(dummy._start_remote_channel_process("telegram", show_errors=True))
        self.assertEqual(dummy.statuses, ["当前 API 配置引用无效，请先到 API 页面修复后再启动渠道。"])
        self.assertEqual(dummy.calls[0][0], "warning")
        self.assertEqual(dummy.calls[0][1], "API 配置无效")
        self.assertIn("gpt-native", dummy.calls[0][3])

    def test_remote_channel_start_stops_on_dependency_install_failure(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                return False, "远端 Telegram / 纸飞机 依赖安装失败：缺少 pip。", {"report_text": "pip install python-telegram-bot 失败"}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                raise AssertionError("remote start should not run after dependency failure")

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text), str(detail)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("telegram", show_errors=True))

        self.assertEqual(dummy.statuses[-1], "远端 Telegram / 纸飞机 依赖安装失败：缺少 pip。")
        self.assertIn(
            ("warning", "依赖安装失败", "远端 Telegram / 纸飞机 依赖安装失败：缺少 pip。", "pip install python-telegram-bot 失败"),
            dummy.calls,
        )

    def test_remote_channel_start_reports_restart_for_managed_running_process(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                return True, "", {"ok": True}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                return True, "", {"restarted": True, "previous_pid": 321, "pid": 654}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("wechat", show_errors=True))

        self.assertEqual(
            dummy.statuses,
            [
                "正在启动远端 微信 渠道…",
                "已重启远端 微信 渠道（旧 PID 321 -> 新 PID 654）。",
            ],
        )
        self.assertIn(("info", "重启成功", "远端 微信 已重启；如无响应可继续查看远端日志。"), dummy.calls)

    def test_start_channel_process_blocks_invalid_api_references_before_launch(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_configs = [
                    {
                        "var": "mixin_config",
                        "kind": "mixin",
                        "data": {"llm_nos": ["gpt-native"], "max_retries": 3},
                    },
                    {
                        "var": "native_oai_config",
                        "kind": "native_oai",
                        "data": {"name": "mimo", "apikey": "sk-demo", "apibase": "https://api.example/v1", "model": "demo"},
                    },
                ]
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _qt_channels_save(self, silent=True, apply_running=False):
                raise AssertionError("invalid API references must block before save")

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"telegram": {"id": "telegram", "label": "Telegram", "script": "tgapp.py", "fields": []}}):
            self.assertFalse(dummy._start_channel_process("telegram", show_errors=True))

        self.assertEqual(dummy.statuses, ["当前 API 配置引用无效，请先到 API 页面修复后再启动渠道。"])
        self.assertEqual(dummy.calls[0][0], "warning")
        self.assertEqual(dummy.calls[0][1], "API 配置无效")
        self.assertIn("gpt-native", dummy.calls[0][3])

    def test_start_channel_process_blocks_conductor_without_runnable_llm_config(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _qt_channels_save(self, silent=True, apply_running=False):
                raise AssertionError("conductor readiness must block before save")

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

        dummy = DummyChannel()
        spec = {"id": "conductor", "label": "Conductor 总管台", "script": "conductor.py", "fields": []}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"conductor": spec}), mock.patch.object(
            channel_runtime.os.path, "isfile", side_effect=lambda path: str(path).replace("/", "\\") == "C:\\demo\\mykey.py"
        ), mock.patch.object(
            channel_runtime.lz, "parse_mykey_source", return_value={"configs": [], "extras": {}, "passthrough": [], "error": None}
        ):
            self.assertFalse(dummy._start_channel_process("conductor", show_errors=True))

        self.assertEqual(dummy.statuses, ["当前没有可直接运行的非 mixin API 会话。请先填写至少一条模型配置。"])
        self.assertEqual(dummy.calls[0][0], "warning")
        self.assertEqual(dummy.calls[0][1], "Conductor 未就绪")
        self.assertIn("至少一条模型配置", dummy.calls[0][2])

    def test_start_channel_process_seeds_conductor_from_loaded_api_state(self):
        class DummyChannel(ChannelRuntimeMixin, ApiEditorMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._qt_channel_py_path = ""
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self._qt_api_hidden_configs = []
                self._qt_api_state = [
                    {
                        "var": "native_oai_config",
                        "format": "oai_chat",
                        "tpl_key": "openai",
                        "apibase": "https://api.example/v1",
                        "apikey": "sk-demo",
                        "model": "gpt-5.4",
                        "advanced_values": {},
                        "advanced_expanded": False,
                        "raw_extra": {},
                        "model_choices": [],
                        "model_status": "",
                        "model_fetching": False,
                        "name": "primary",
                    }
                ]
                self._qt_api_extras = {}
                self._qt_api_passthrough = []
                self.saved_text = ""
                self.statuses = []

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _qt_channels_save(self, silent=True, apply_running=False):
                return True

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_extra_packages(self, _spec):
                return []

            def _check_runtime_dependencies(self, **_kwargs):
                return True

            def _settings_target_write_mykey_text(self, text):
                self.saved_text = str(text)
                return True, "C:\\demo\\mykey.py", ""

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _create_channel_process_session(self, channel_id, proc, log_path):
                return "sess-1"

            def _channel_set_external_running(self, channel_id, enabled, *, persist=False):
                return None

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                return None

            def _reload_channels_editor_state(self):
                return None

            def _refresh_sessions(self):
                return None

        class DummyProc:
            def __init__(self):
                self.returncode = None
                self.pid = 1234

            def poll(self):
                return None

        dummy = DummyChannel()
        spec = {"id": "conductor", "label": "Conductor 总管台", "script": "conductor.py", "fields": []}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"conductor": spec}), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_configured_python_exe", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "_popen_external_subprocess", return_value=DummyProc()
        ), mock.patch(
            "builtins.open", mock.mock_open()
        ), mock.patch.object(
            channel_runtime.lz, "channel_script_path", return_value="C:\\launcher\\runtime\\conductor.py"
        ), mock.patch.object(
            channel_runtime.os.path, "isfile", side_effect=lambda path: str(path).replace("/", "\\") in {"C:\\launcher\\runtime\\conductor.py", "python"}
        ):
            ok = dummy._start_channel_process("conductor", show_errors=False)

        self.assertTrue(ok)
        self.assertIn("'apikey': 'sk-demo'", dummy.saved_text)
        self.assertIn("'apibase': 'https://api.example/v1'", dummy.saved_text)
        self.assertEqual(dummy._qt_channel_py_path, "C:\\demo\\mykey.py")
        self.assertEqual(dummy._qt_channel_configs[0]["data"]["name"], "primary")

    def test_start_channel_process_takes_over_external_conductor_before_launch(self):
        class DummyChannel(ChannelRuntimeMixin):
            _start_channel_process = ChannelRuntimeMixin._start_channel_process

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_configs = []
                self._qt_channel_passthrough = []
                self._qt_channel_extras = {}
                self.statuses = []
                self.takeover_calls = []
                self.external_running = True

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _channel_api_reference_errors(self):
                return []

            def _channel_prepare_launch_configs(self, _channel_id):
                return True, []

            def _channel_launch_config_errors(self, _channel_id):
                return []

            def _qt_channels_save(self, silent=True, apply_running=False):
                return True

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, _channel_id):
                return bool(self.external_running)

            def _channel_set_external_running(self, channel_id, enabled, *, persist=False):
                self.external_running = bool(enabled)

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_extra_packages(self, _spec):
                return []

            def _check_runtime_dependencies(self, **_kwargs):
                return True

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _takeover_local_external_channel_instances(self, channel_id):
                self.takeover_calls.append(str(channel_id))
                self.external_running = False
                return True, [32404], []

            def _create_channel_process_session(self, channel_id, proc, log_path):
                return "sess-1"

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                return None

            def _reload_channels_editor_state(self):
                return None

            def _refresh_sessions(self):
                return None

        class DummyProc:
            def __init__(self):
                self.returncode = None
                self.pid = 4321

            def poll(self):
                return None

        dummy = DummyChannel()
        spec = {"id": "conductor", "label": "Conductor 总管台", "script": "conductor.py", "fields": []}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"conductor": spec}), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            channel_runtime.QTimer, "singleShot", side_effect=lambda *_args, **_kwargs: None
        ), mock.patch.object(
            channel_runtime.lz, "_resolve_configured_python_exe", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_find_system_python", return_value="python"
        ), mock.patch.object(
            channel_runtime.lz, "_external_subprocess_env", return_value={}
        ), mock.patch.object(
            channel_runtime.lz, "_popen_external_subprocess", return_value=DummyProc()
        ) as popen_mock, mock.patch(
            "builtins.open", mock.mock_open()
        ), mock.patch.object(
            channel_runtime.lz, "channel_script_path", return_value="C:\\launcher\\runtime\\conductor.py"
        ), mock.patch.object(
            channel_runtime.os.path, "isfile", side_effect=lambda path: str(path).replace("/", "\\") in {"C:\\launcher\\runtime\\conductor.py", "python"}
        ):
            ok = dummy._start_channel_process("conductor", show_errors=False)

        self.assertTrue(ok)
        self.assertEqual(dummy.takeover_calls, ["conductor"])
        self.assertIn("已关闭 1 个外部 Conductor 总管台 进程", dummy.statuses[0])
        popen_mock.assert_called_once()
        env = dict(popen_mock.call_args.kwargs.get("env") or {})
        self.assertEqual(env.get("GA_LAUNCHER_AGENT_DIR"), "C:\\demo")

    def test_stop_channel_process_stops_external_conductor_when_unmanaged(self):
        class DummyChannel(ChannelRuntimeMixin):
            _stop_channel_process = ChannelRuntimeMixin._stop_channel_process

            def __init__(self):
                self.cfg = {}
                self._channel_procs = {}
                self.external_running = True
                self.calls = []

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _channel_external_running(self, _channel_id):
                return bool(self.external_running)

            def _channel_set_external_running(self, channel_id, enabled, *, persist=False):
                self.external_running = bool(enabled)

            def _takeover_local_external_channel_instances(self, channel_id):
                self.calls.append(("takeover", str(channel_id)))
                self.external_running = False
                return True, [32404], []

            def _reload_channels_editor_state(self):
                self.calls.append("reload")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

        dummy = DummyChannel()
        self.assertTrue(dummy._stop_channel_process("conductor"))
        self.assertEqual(dummy.calls[0], ("takeover", "conductor"))
        self.assertIn("reload", dummy.calls)
        self.assertIn("refresh_sessions", dummy.calls)

    def test_channel_start_disabled_reason_reports_conductor_missing_mykey(self):
        class DummyChannel(ChannelRuntimeMixin):
            _channel_start_disabled_reason = ChannelRuntimeMixin._channel_start_disabled_reason

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_configs = []
                self._qt_channel_extras = {}

            def _channel_target_context(self):
                return False, {}, {"is_remote": False}

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, _channel_id):
                return False

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_missing_required(self, _channel_id, _values):
                return []

        dummy = DummyChannel()
        spec = {"id": "conductor", "label": "Conductor 总管台", "script": "conductor.py", "fields": []}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_INDEX", {"conductor": spec}), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(channel_runtime.os.path, "isfile", return_value=False):
            reason = dummy._channel_start_disabled_reason("conductor", {})

        self.assertIn("mykey.py / mykey.json", reason)
        self.assertIn("可复用的 API 配置", reason)

    def test_remote_start_channel_process_blocking_uses_shared_matcher_for_wechat_probe(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_start_channel_process_blocking = ChannelRuntimeMixin._remote_start_channel_process_blocking

            def __init__(self):
                self.scripts = []

            def _remote_exec_json_script(self, device, script, timeout=120):
                self.scripts.append(str(script))
                return True, {"ok": True, "restarted": True, "previous_pid": 321, "pid": 654}, ""

        dummy = DummyChannel()
        ok, msg, payload = dummy._remote_start_channel_process_blocking(
            {"id": "box-1"},
            "wechat",
            {"id": "wechat", "label": "微信", "script": "wechatapp.py", "conflicts_with": []},
        )

        self.assertTrue(ok, msg=msg)
        self.assertTrue(payload.get("restarted"))
        self.assertEqual(payload.get("previous_pid"), 321)
        self.assertEqual(len(dummy.scripts), 1)
        script = dummy.scripts[0]
        self.assertIn("process_cmdline_matches_agent_script", script)
        self.assertIn("def read_pid_cwd(pid):", script)
        self.assertIn("find_wechat_pids", script)
        self.assertIn("if not terminate_pid_force(existing_pid):", script)
        self.assertIn("'restarted': bool(restarted)", script)

    def test_remote_prepare_runtime_dependencies_blocking_uses_self_contained_remote_installer(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_prepare_runtime_dependencies_blocking = ChannelRuntimeMixin._remote_prepare_runtime_dependencies_blocking

            def __init__(self):
                self.scripts = []
                self.cfg = {
                    "vps_profiles": [
                        {
                            "id": "box-1",
                            "dep_install_mode": "mirror",
                            "pip_mirror_url": "https://mirror.example/simple",
                        }
                    ]
                }

            def _remote_exec_json_script(self, device, script, timeout=120):
                self.scripts.append((str(script), int(timeout)))
                return True, {"ok": True, "python": "/usr/bin/python3", "report_text": ""}, ""

            def _vps_dep_install_source(self, dep_install_mode, pip_mirror_url=""):
                return {
                    "mode": str(dep_install_mode),
                    "index_url": str(pip_mirror_url or ""),
                    "trusted_host": "mirror.example",
                }

        dummy = DummyChannel()
        ok, msg, payload = dummy._remote_prepare_runtime_dependencies_blocking(
            {"id": "box-1"},
            "telegram",
            {"id": "telegram", "label": "Telegram / 纸飞机", "script": "tgapp.py", "pip": "python-telegram-bot"},
        )

        self.assertTrue(ok, msg=msg)
        self.assertEqual(payload.get("python"), "/usr/bin/python3")
        self.assertEqual(len(dummy.scripts), 1)
        script, timeout = dummy.scripts[0]
        self.assertEqual(timeout, 600)
        self.assertIn("py_bin = str(os.environ.get('GA_PY_BIN') or sys.executable or '').strip()", script)
        self.assertIn("remote_channel_dependency_state.json", script)
        self.assertIn("def install_with_fallback(args, *, label, timeout=1200):", script)
        self.assertIn("同步远端 requirements.txt", script)
        self.assertIn("安装远端渠道依赖", script)
        self.assertIn("mirror.example", script)
        self.assertIn("python-telegram-bot", script)
        self.assertNotIn("from launcher_core_parts import python_env as ga_python_env", script)

    def test_remote_channel_start_wechat_unbound_reopens_qr_before_retry(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _start_remote_channel_process = ChannelRuntimeMixin._start_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 2
                self._qt_channel_extras = {}
                self.calls = []
                self.statuses = []
                self.start_attempts = 0

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _qt_channels_save(self, silent=True, apply_running=False):
                self.calls.append(("save", bool(silent), bool(apply_running)))
                return True

            def _channel_missing_required(self, channel_id, values):
                return []

            def _remote_channel_conflict_message(self, did, channel_id):
                return ""

            def _remote_prepare_runtime_dependencies_blocking(self, device, channel_id, spec):
                return True, "", {"ok": True}

            def _remote_start_channel_process_blocking(self, device, channel_id, spec):
                self.start_attempts += 1
                if self.start_attempts == 1:
                    return False, "远端微信未绑定。", {}
                return True, "", {"pid": 456}

            def _open_wechat_qr_dialog(self, show_errors=True, remote_device=None):
                self.calls.append(("open_qr", bool(show_errors), dict(remote_device or {})))
                return True

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._start_remote_channel_process("wechat", show_errors=True))

        self.assertEqual(dummy.start_attempts, 2)
        self.assertIn("远端微信未绑定，已转入远端扫码绑定；完成后会继续尝试启动。", dummy.statuses)
        self.assertIn(("open_qr", True, {"id": "box-1"}), dummy.calls)
        self.assertEqual(dummy.statuses[-1], "已启动远端 微信 渠道（PID 456）；如无新消息可再查看远端日志。")

    def test_remote_stop_blocking_reports_failure_when_process_still_running(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_stop_channel_process_blocking = ChannelRuntimeMixin._remote_stop_channel_process_blocking

            def _remote_exec_json_script(self, device, script, timeout=80):
                return True, {"ok": True, "was_running": True, "stopped": False, "status": "停止失败"}, ""

        dummy = DummyChannel()
        ok, msg, payload = dummy._remote_stop_channel_process_blocking({"id": "box-1"}, "wechat")

        self.assertFalse(ok)
        self.assertEqual(msg, "远端停止失败，进程可能仍在运行。")
        self.assertEqual(payload["status"], "停止失败")

    def test_remote_stop_blocking_embeds_fallback_pid_for_external_process(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_stop_channel_process_blocking = ChannelRuntimeMixin._remote_stop_channel_process_blocking

            def __init__(self):
                self.script = ""

            def _remote_exec_json_script(self, device, script, timeout=80):
                self.script = str(script)
                return True, {"ok": True, "was_running": True, "stopped": True, "status": "已退出"}, ""

        dummy = DummyChannel()
        ok, msg, payload = dummy._remote_stop_channel_process_blocking({"id": "box-1"}, "wechat", fallback_pid=4321)

        self.assertTrue(ok, msg=msg)
        self.assertEqual(payload["status"], "已退出")
        self.assertIn("fallback_pid = 4321", dummy.script)
        self.assertIn("if pid <= 0 and fallback_pid > 0:", dummy.script)

    def test_remote_channel_stop_uses_cached_pid_for_external_process(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _stop_remote_channel_process = ChannelRuntimeMixin._stop_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 4
                self._settings_target_change_token = 7
                self.calls = []
                self.statuses = []
                self.received_fallback_pid = None

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_channel_cached_session(self, did, cid):
                return {"process_status": "外部运行中", "process_pid": 4321, "managed_by_launcher": False}

            def _remote_stop_channel_process_blocking(self, device, channel_id, fallback_pid=0):
                self.received_fallback_pid = int(fallback_pid or 0)
                return True, "", {"was_running": True}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._stop_remote_channel_process("wechat"))

        self.assertEqual(dummy.received_fallback_pid, 4321)
        self.assertIn(("info", "已停止", "远端 微信 已停止。"), dummy.calls)

    def test_remote_channel_stop_reports_not_running_without_redundant_stop(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _stop_remote_channel_process = ChannelRuntimeMixin._stop_remote_channel_process
            _remote_channel_label_text = ChannelRuntimeMixin._remote_channel_label_text

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 4
                self._settings_target_change_token = 7
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_stop_channel_process_blocking(self, device, channel_id, fallback_pid=0):
                return True, "", {"was_running": False}

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _force_remote_channel_sync(self):
                self.calls.append("sync")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_labels")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_editor")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._stop_remote_channel_process("wechat"))

        self.assertEqual(
            dummy.statuses,
            [
                "正在停止远端 微信 渠道…",
                "远端 微信 当前未运行；无需重复停止。",
            ],
        )
        self.assertIn(("info", "未运行", "远端 微信 当前未运行，无需重复停止。"), dummy.calls)

    def test_remote_channel_log_read_reports_tail_hint(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _show_remote_channel_log_tail = ChannelRuntimeMixin._show_remote_channel_log_tail
            _remote_channel_log_loaded_status = ChannelRuntimeMixin._remote_channel_log_loaded_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 6
                self._settings_target_change_token = 8
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_tail_channel_log_blocking(self, device, channel_id):
                return True, "tail content", "运行中", ""

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text), str(detail)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._show_remote_channel_log_tail("wechat", "微信"))

        self.assertEqual(
            dummy.statuses,
            [
                "正在读取远端 微信 日志…",
                "已读取远端 微信 日志；可直接查看末尾输出继续排查。",
            ],
        )
        self.assertIn(("info", "微信 远端日志尾部", "状态：运行中；以下为远端日志末尾输出。", "tail content"), dummy.calls)

    def test_remote_channel_log_read_failure_explains_retry(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyChannel(ChannelRuntimeMixin):
            _show_remote_channel_log_tail = ChannelRuntimeMixin._show_remote_channel_log_tail

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 6
                self._settings_target_change_token = 8
                self.calls = []
                self.statuses = []

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _channel_target_context(self):
                return True, {"id": "box-1"}, {"is_remote": True, "device_id": "box-1"}

            def _remote_tail_channel_log_blocking(self, device, channel_id):
                return False, "", "", "SSH 超时"

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _channel_info(self, title, text, detail=""):
                self.calls.append(("info", str(title), str(text), str(detail)))

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

            def _channel_post_ui(self, callback, action_name="界面刷新"):
                if callable(callback):
                    callback()

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.threading, "Thread", ImmediateThread):
            self.assertTrue(dummy._show_remote_channel_log_tail("wechat", "微信"))

        self.assertEqual(
            dummy.statuses,
            [
                "正在读取远端 微信 日志…",
                "读取远端 微信 日志失败；请检查 SSH 连接后重试：SSH 超时",
            ],
        )
        self.assertIn(("warning", "读取失败", "读取远端 微信 日志失败；请检查 SSH 连接后重试：SSH 超时", ""), dummy.calls)

    def test_remote_channel_log_loaded_status_distinguishes_empty_tail(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_channel_log_loaded_status = ChannelRuntimeMixin._remote_channel_log_loaded_status

        dummy = DummyChannel()
        self.assertEqual(dummy._remote_channel_log_loaded_status("微信", "tail"), "已读取远端 微信 日志；可直接查看末尾输出继续排查。")
        self.assertEqual(dummy._remote_channel_log_loaded_status("微信", ""), "已读取远端 微信 日志；当前还没有新的日志输出。")

    def test_after_channel_launch_check_drops_stale_context(self):
        class DummyProc:
            returncode = 9

            def poll(self):
                return self.returncode

        class DummyChannel(ChannelRuntimeMixin):
            _after_channel_launch_check = ChannelRuntimeMixin._after_channel_launch_check

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 5
                self._channel_procs = {"wechat": {"proc": DummyProc()}}
                self._last_session_list_signature = "sig"
                self.calls = []

            def _sync_channel_process_session(self, channel_id, final=False, exit_code=None):
                self.calls.append(("sync", channel_id, bool(final), exit_code))

            def _close_channel_log_handle(self, channel_id):
                self.calls.append(("close_log", channel_id))

            def _channel_tail_log(self, channel_id):
                return "tail"

            def _channel_set_external_running(self, channel_id, enabled, persist=False):
                self.calls.append(("external", channel_id, bool(enabled), bool(persist)))

            def _reload_channels_editor_state(self):
                self.calls.append("reload")

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _channel_warning(self, title, text, detail=""):
                self.calls.append(("warning", str(title), str(text), str(detail)))

        dummy = DummyChannel()
        stale_context = {"agent_dir": "C:\\demo", "runtime_generation": 4, "settings_target_generation": 0}
        dummy._after_channel_launch_check("wechat", show_errors=True, context=stale_context)

        self.assertEqual(dummy.calls, [])
        self.assertIn("wechat", dummy._channel_procs)

    def test_refresh_channel_runtime_status_disables_start_for_external_local_channel(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                    }
                }

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _refresh_local_channel_external_running(self, *, persist=False):
                return {"wechat": [321]}

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                return ("外部运行中", "#999999")

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return str(channel_id) == "wechat"

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_missing_required(self, _channel_id, _values):
                return []

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "fields": []}]), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["wechat"]
        self.assertFalse(state["start_btn"].enabled)
        self.assertFalse(state["stop_btn"].enabled)
        self.assertEqual(state["status_label"].text, "外部运行中")
        self.assertIn("外部 微信 进程正在运行", state["start_btn"].tooltip)
        self.assertIn("启动器无法直接停止", state["stop_btn"].tooltip)

    def test_refresh_channel_runtime_status_allows_conductor_takeover_for_external_local_channel(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "conductor": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                    }
                }

            def _channel_target_context(self):
                return False, None, {"is_remote": False}

            def _refresh_channel_source_actions(self):
                return None

            def _request_local_channel_external_running_refresh(self, **_kwargs):
                return False

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                return ("外部运行中", "#999999")

            def _channel_proc_alive(self, _channel_id):
                return False

            def _channel_external_running(self, channel_id):
                return str(channel_id) == "conductor"

            def _channel_conflict_message(self, _channel_id):
                return ""

            def _channel_missing_required(self, _channel_id, _values):
                return []

            def _channel_launch_mode(self, _channel_id):
                return "web"

            def _channel_is_local_only(self, _channel_id):
                return False

        dummy = DummyChannel()
        specs = [{"id": "conductor", "label": "Conductor 总管台", "fields": []}]
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", specs), mock.patch.object(
            channel_runtime.lz, "is_valid_agent_dir", return_value=True
        ):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["conductor"]
        self.assertTrue(state["start_btn"].enabled)
        self.assertTrue(state["stop_btn"].enabled)
        self.assertEqual(state["status_label"].text, "外部运行中")
        self.assertIn("接管并重启", state["start_btn"].tooltip)

    def test_refresh_channel_runtime_status_sets_remote_button_tooltips_when_device_missing(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "bind_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "missing-box", "device": None}

            def _remote_channel_device_sync_state(self, _did):
                return {}

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                return ("正在校验远端状态", "#999999")

            def _remote_channel_check_hint(self, did, cid):
                return "等待首次校验服务器状态。"

            def _remote_channel_is_running(self, did, cid):
                return False

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "label": "微信", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["wechat"]
        self.assertFalse(state["start_btn"].enabled)
        self.assertFalse(state["stop_btn"].enabled)
        self.assertFalse(state["bind_btn"].enabled)
        self.assertFalse(state["log_btn"].enabled)
        self.assertFalse(state["detail_btn"].enabled)
        self.assertIn("远端设备信息不可用", state["start_btn"].tooltip)
        self.assertIn("远端设备信息不可用", state["stop_btn"].tooltip)
        self.assertIn("无法为 微信 打开远端扫码", state["bind_btn"].tooltip)
        self.assertIn("无法读取 微信 的远端状态或日志", state["log_btn"].tooltip)
        self.assertIn("无法读取 微信 的远端状态或日志", state["detail_btn"].tooltip)

    def test_refresh_channel_runtime_status_treats_claimed_remote_process_with_pid_as_managed(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _channel_status = ChannelRuntimeMixin._channel_status
            _channel_start_disabled_reason = ChannelRuntimeMixin._channel_start_disabled_reason
            _channel_stop_disabled_reason = ChannelRuntimeMixin._channel_stop_disabled_reason
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels
            _apply_channel_button_state = ChannelRuntimeMixin._apply_channel_button_state
            _remote_channel_is_running = ChannelRuntimeMixin._remote_channel_is_running
            _remote_channel_is_launcher_managed = ChannelRuntimeMixin._remote_channel_is_launcher_managed
            _remote_channel_is_external_running = ChannelRuntimeMixin._remote_channel_is_external_running
            _remote_channel_process_pid = ChannelRuntimeMixin._remote_channel_process_pid
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1", "name": "Docker Box"}}

            def _refresh_channel_source_actions(self):
                return None

            def _remote_channel_cached_session(self, did, cid):
                return {
                    "process_status": "运行中",
                    "process_pid": 4321,
                    "managed_by_launcher": True,
                }

            def _remote_channel_status_check_age(self, did, cid):
                return 0.0, 0.0

            def _remote_channel_device_sync_state(self, did):
                return {}

            def _remote_channel_check_hint(self, did, cid):
                return "最近校验：2026-05-06 12:00:00"

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "label": "微信", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["wechat"]
        self.assertEqual(state["status_label"].text, "运行中 (PID 4321)")
        self.assertTrue(state["start_btn"].enabled)
        self.assertTrue(state["stop_btn"].enabled)
        self.assertIn("重启远端 微信 渠道", state["start_btn"].tooltip)
        self.assertIn("停止远端 微信 渠道", state["stop_btn"].tooltip)

    def test_refresh_channel_runtime_status_disables_remote_actions_for_external_process_without_pid(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _channel_status = ChannelRuntimeMixin._channel_status
            _channel_start_disabled_reason = ChannelRuntimeMixin._channel_start_disabled_reason
            _channel_stop_disabled_reason = ChannelRuntimeMixin._channel_stop_disabled_reason
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels
            _apply_channel_button_state = ChannelRuntimeMixin._apply_channel_button_state
            _remote_channel_is_running = ChannelRuntimeMixin._remote_channel_is_running
            _remote_channel_is_launcher_managed = ChannelRuntimeMixin._remote_channel_is_launcher_managed
            _remote_channel_is_external_running = ChannelRuntimeMixin._remote_channel_is_external_running
            _remote_channel_process_pid = ChannelRuntimeMixin._remote_channel_process_pid
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1", "name": "Personal VPS"}}

            def _refresh_channel_source_actions(self):
                return None

            def _remote_channel_cached_session(self, did, cid):
                return {
                    "process_status": "外部运行中",
                    "process_pid": 0,
                    "managed_by_launcher": False,
                }

            def _remote_channel_status_check_age(self, did, cid):
                return 0.0, 0.0

            def _remote_channel_device_sync_state(self, did):
                return {}

            def _remote_channel_check_hint(self, did, cid):
                return "最近校验：2026-05-06 12:00:00"

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "label": "微信", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["wechat"]
        self.assertEqual(state["status_label"].text, "外部运行中")
        self.assertFalse(state["start_btn"].enabled)
        self.assertFalse(state["stop_btn"].enabled)
        self.assertIn("不会重复启动", state["start_btn"].tooltip)
        self.assertIn("暂未获取到 PID", state["stop_btn"].tooltip)

    def test_refresh_channel_runtime_status_keeps_external_label_for_unclaimed_remote_pid(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _channel_status = ChannelRuntimeMixin._channel_status
            _channel_start_disabled_reason = ChannelRuntimeMixin._channel_start_disabled_reason
            _channel_stop_disabled_reason = ChannelRuntimeMixin._channel_stop_disabled_reason
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels
            _apply_channel_button_state = ChannelRuntimeMixin._apply_channel_button_state
            _remote_channel_is_running = ChannelRuntimeMixin._remote_channel_is_running
            _remote_channel_is_launcher_managed = ChannelRuntimeMixin._remote_channel_is_launcher_managed
            _remote_channel_is_external_running = ChannelRuntimeMixin._remote_channel_is_external_running
            _remote_channel_process_pid = ChannelRuntimeMixin._remote_channel_process_pid
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1", "name": "Personal VPS"}}

            def _refresh_channel_source_actions(self):
                return None

            def _remote_channel_cached_session(self, did, cid):
                return {
                    "process_status": "外部运行中",
                    "process_pid": 4321,
                    "managed_by_launcher": False,
                }

            def _remote_channel_status_check_age(self, did, cid):
                return 0.0, 0.0

            def _remote_channel_device_sync_state(self, did):
                return {}

            def _remote_channel_check_hint(self, did, cid):
                return "最近校验：2026-05-06 12:00:00"

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "label": "微信", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["wechat"]
        self.assertEqual(state["status_label"].text, "外部运行中 (PID 4321)")
        self.assertFalse(state["start_btn"].enabled)
        self.assertTrue(state["stop_btn"].enabled)
        self.assertIn("不会重复启动", state["start_btn"].tooltip)
        self.assertIn("停止远端 微信 渠道", state["stop_btn"].tooltip)

    def test_remote_channel_status_keeps_cached_running_state_during_sync_failures(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _remote_channel_cached_session(self, did, cid):
                return {
                    "process_status": "运行中",
                    "process_pid": 4321,
                    "managed_by_launcher": True,
                }

            def _remote_channel_status_check_age(self, did, cid):
                return 99.0, 99.0

            def _remote_channel_device_sync_state(self, did):
                return {"fail_count": 3, "last_error": "SSH reset"}

            def _request_remote_channel_status_refresh(self):
                raise AssertionError("cached running state should not be replaced by VPS error flow")

            def _remote_channel_is_external_running(self, did, cid):
                return False

        dummy = DummyChannel()
        text, color = dummy._remote_channel_status("box-1", "wechat")

        self.assertEqual(text, "运行中 (PID 4321)")
        self.assertEqual(color, channel_runtime.C["accent"])

    def test_remote_channel_status_returns_no_process_when_sync_stuck_too_long(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._remote_channel_sync_running = True
                self._REMOTE_CHANNEL_PROBE_TIMEOUT_SECONDS = 120.0

            def _remote_channel_cached_session(self, did, cid):
                return {}

            def _remote_channel_status_check_age(self, did, cid):
                return float("inf"), float("inf")

            def _remote_channel_device_sync_state(self, did):
                return {"last_attempt_at": 1.0, "fail_count": 0, "last_error": ""}

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.time, "time", return_value=1000.0):
            text, color = dummy._remote_channel_status("box-1", "wechat")
        self.assertEqual(text, "未检测到远端进程")
        self.assertEqual(color, channel_runtime.C["muted"])
        self.assertTrue(dummy._remote_channel_sync_running)

    def test_remote_channel_status_shows_checking_within_probe_timeout(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_channel_status = ChannelRuntimeMixin._remote_channel_status

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._remote_channel_sync_running = True
                self._REMOTE_CHANNEL_PROBE_TIMEOUT_SECONDS = 120.0

            def _remote_channel_cached_session(self, did, cid):
                return {}

            def _remote_channel_status_check_age(self, did, cid):
                return float("inf"), float("inf")

            def _remote_channel_device_sync_state(self, did):
                return {"last_attempt_at": 950.0, "fail_count": 0, "last_error": ""}

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.time, "time", return_value=1000.0):
            text, color = dummy._remote_channel_status("box-1", "wechat")
        self.assertEqual(text, "正在校验远端状态")
        self.assertEqual(color, channel_runtime.C["text_soft"])

    def test_request_channel_status_refresh_uses_manual_remote_sync_only(self):
        class DummyChannel(ChannelRuntimeMixin):
            _request_channel_status_refresh = ChannelRuntimeMixin._request_channel_status_refresh

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.calls = []

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1"}}

            def _request_remote_channel_status_refresh(self):
                self.calls.append("remote_refresh")

            def _sync_all_channel_process_sessions(self):
                self.calls.append("local_sync")

            def _refresh_channels_runtime_status_labels(self, *, force=False):
                self.calls.append(("refresh_labels", bool(force)))

        dummy = DummyChannel()
        dummy._request_channel_status_refresh()
        self.assertEqual(dummy.calls, ["remote_refresh"])

    def test_remote_channel_check_hint_reports_timeout_when_sync_stuck(self):
        class DummyChannel(ChannelRuntimeMixin):
            _remote_channel_check_hint = ChannelRuntimeMixin._remote_channel_check_hint

            def __init__(self):
                self._remote_channel_sync_running = True
                self._REMOTE_CHANNEL_PROBE_TIMEOUT_SECONDS = 30.0

            def _remote_device_auto_ssh_enabled(self, _device_id):
                return True

            def _remote_channel_last_checked_at(self, _device_id, _channel_id):
                return 0.0

            def _remote_channel_device_sync_state(self, _device_id):
                return {"last_attempt_at": 100.0, "fail_count": 0, "last_error": ""}

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.time, "time", return_value=200.0):
            hint = dummy._remote_channel_check_hint("box-1", "wechat")
        self.assertIn("已超时", hint)

    def test_request_remote_channel_status_refresh_resets_stuck_sync_and_schedules_timeout_refresh(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _request_remote_channel_status_refresh = ChannelRuntimeMixin._request_remote_channel_status_refresh

            def __init__(self):
                self._last_remote_channel_status_refresh_at = 0.0
                self._remote_channel_sync_running = True
                self._next_remote_channel_sync_at = 99.0
                self.settings_channels_notice = DummyLabel()
                self.calls = []

            def _auto_ssh_remote_devices(self):
                return [{"id": "box-1"}]

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1"}}

            def _remote_channel_sync_is_stuck(self, did):
                return True

            def _force_remote_channel_sync(self):
                self.calls.append("force_sync")

            def _schedule_remote_probe_timeout_refresh(self, did):
                self.calls.append(("schedule_timeout", str(did)))

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.time, "time", return_value=1000.0):
            dummy._request_remote_channel_status_refresh()
        self.assertFalse(dummy._remote_channel_sync_running)
        self.assertEqual(dummy._next_remote_channel_sync_at, 0.0)
        self.assertEqual(dummy.calls, ["force_sync", ("schedule_timeout", "box-1")])

    def test_sync_remote_device_channel_process_sessions_recovers_from_stuck_running_flag(self):
        class DummySidebar(SidebarSessionsMixin):
            _sync_remote_device_channel_process_sessions = SidebarSessionsMixin._sync_remote_device_channel_process_sessions

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._remote_channel_sync_running = True
                self._next_remote_channel_sync_at = 0.0
                self._remote_channel_device_sync_errors = {"box-1": {"last_attempt_at": 100.0}}
                self._runtime_context_generation = 1
                self.started = False

            def _auto_ssh_remote_devices(self):
                return [{"id": "box-1"}]

            def _remote_channel_probe_timeout_seconds(self):
                return 30.0

            def _sync_remote_device_channel_process_sessions_blocking(self, *, agent_dir="", runtime_context=None):
                self.started = True
                return False

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _should_refresh_remote_sync_ui(self):
                return False

        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.time, "time", return_value=200.0), mock.patch.object(
            sidebar_sessions.threading, "Thread", ImmediateThread
        ):
            dummy._sync_remote_device_channel_process_sessions()
        self.assertTrue(dummy.started)
        self.assertFalse(dummy._remote_channel_sync_running)

    def test_request_channel_status_refresh_requests_async_local_external_before_sync(self):
        class DummyChannel(ChannelRuntimeMixin):
            _request_channel_status_refresh = ChannelRuntimeMixin._request_channel_status_refresh

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.calls = []

            def _settings_target_context(self):
                return {"is_remote": False}

            def _request_local_channel_external_running_refresh(self, **kwargs):
                self.calls.append(("request_refresh", bool(kwargs.get("persist", False)), bool(kwargs.get("force", False))))
                callback = kwargs.get("after")
                if callable(callback):
                    callback({"wechat": [321]})
                return True

            def _sync_all_channel_process_sessions(self):
                self.calls.append("local_sync")

            def _refresh_channels_runtime_status_labels(self, *, force=False):
                self.calls.append(("refresh_labels", bool(force)))

        dummy = DummyChannel()
        dummy._request_channel_status_refresh()
        self.assertEqual(dummy.calls, [("request_refresh", True, True), "local_sync", ("refresh_labels", True)])

    def test_refresh_channel_runtime_status_notice_uses_cached_result_wording_after_failures(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self.settings_channels_notice = DummyLabel()
                self._qt_channel_states = {
                    "wechat": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1", "name": "Personal VPS"}}

            def _refresh_channel_source_actions(self):
                return None

            def _remote_channel_device_sync_state(self, did):
                return {"fail_count": 3, "last_error": "SSH reset"}

            def _remote_channel_device_has_cached_sessions(self, did):
                return True

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                return ("运行中 (PID 4321)", "#00aa00")

            def _remote_channel_check_hint(self, did, cid):
                return "最近校验：2026-05-06 12:00:00"

            def _remote_channel_is_running(self, did, cid):
                return True

            def _remote_channel_is_launcher_managed(self, did, cid):
                return True

            def _remote_channel_process_pid(self, did, cid):
                return 4321

            def _channel_start_disabled_reason(self, channel_id, values, *, target_ctx=None):
                return "disabled"

            def _channel_stop_disabled_reason(self, channel_id, values, *, target_ctx=None):
                return ""

            def _channel_bind_disabled_reason(self, channel_id, *, target_ctx=None):
                return ""

            def _channel_remote_aux_disabled_reason(self, channel_id, *, target_ctx=None):
                return ""

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [{"id": "wechat", "label": "微信", "fields": []}]):
            dummy._refresh_channels_runtime_status_labels()

        self.assertIn("当前展示最近一次缓存结果", dummy.settings_channels_notice.text)
        self.assertIn("SSH reset", dummy.settings_channels_notice.text)

    def test_refresh_channel_runtime_status_disables_local_only_web_channel_for_remote_target(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""
                self.style = ""
                self.visible = None

            def setText(self, text):
                self.text = str(text)

            def setStyleSheet(self, style):
                self.style = str(style)

            def setVisible(self, visible):
                self.visible = bool(visible)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _refresh_channels_runtime_status_labels = ChannelRuntimeMixin._refresh_channels_runtime_status_labels

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._qt_channel_extras = {}
                self.settings_channels_notice = DummyLabel()
                self._qt_channel_states = {
                    "conductor": {
                        "status_label": DummyLabel(),
                        "status_hint_label": DummyLabel(),
                        "start_btn": DummyButton(),
                        "stop_btn": DummyButton(),
                        "log_btn": DummyButton(),
                        "detail_btn": DummyButton(),
                        "open_btn": DummyButton(),
                    }
                }

            def _settings_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "device": {"id": "box-1"}}

            def _refresh_channel_source_actions(self):
                return None

            def _channel_is_local_only(self, channel_id):
                return True

            def _channel_status(self, channel_id, values, *, target_ctx=None):
                return ("仅本机", "#999999")

            def _channel_start_disabled_reason(self, channel_id, values, *, target_ctx=None):
                return "Conductor 总管台 当前只支持在启动器本机运行，不支持远端托管。"

            def _channel_stop_disabled_reason(self, channel_id, values, *, target_ctx=None):
                return "Conductor 总管台 当前只支持在启动器本机运行，不支持远端托管。"

            def _channel_remote_aux_disabled_reason(self, channel_id, *, target_ctx=None):
                return "Conductor 总管台 当前仅支持启动器本机使用。"

            def _channel_open_disabled_reason(self, channel_id, *, target_ctx=None):
                return "Conductor 总管台 当前仅支持在启动器本机打开网页。"

        dummy = DummyChannel()
        spec = {"id": "conductor", "label": "Conductor 总管台", "fields": []}
        with mock.patch.object(channel_runtime.lz, "COMM_CHANNEL_SPECS", [spec]):
            dummy._refresh_channels_runtime_status_labels()

        state = dummy._qt_channel_states["conductor"]
        self.assertFalse(state["start_btn"].enabled)
        self.assertFalse(state["stop_btn"].enabled)
        self.assertFalse(state["log_btn"].enabled)
        self.assertFalse(state["detail_btn"].enabled)
        self.assertFalse(state["open_btn"].enabled)
        self.assertIn("只支持启动器本机托管", state["status_hint_label"].text)

    def test_reload_channels_editor_state_clears_stale_source_when_agent_dir_invalid(self):
        class DummyChannel(ChannelRuntimeMixin):
            _reload_channels_editor_state = ChannelRuntimeMixin._reload_channels_editor_state
            _reset_channels_source_state = ChannelRuntimeMixin._reset_channels_source_state

            def __init__(self):
                self.agent_dir = ""
                self.settings_channels_notice = mock.Mock()
                self.settings_channels_list_layout = object()
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = [{"id": "wechat"}]
                self._qt_channel_passthrough = ["old"]
                self._qt_channel_extras = {"bot_token": "abc"}
                self._qt_channel_states = {"wechat": {"start_btn": object()}}

            def _clear_layout(self, _layout):
                return None

            def _settings_target_context(self):
                return {"is_remote": False}

        dummy = DummyChannel()
        with mock.patch.object(channel_runtime.lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_channels_editor_state()

        self.assertEqual(dummy._qt_channel_py_path, "")
        self.assertEqual(dummy._qt_channel_parse_error, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy._qt_channel_configs, [])
        self.assertEqual(dummy._qt_channel_passthrough, [])
        self.assertEqual(dummy._qt_channel_extras, {})
        self.assertEqual(dummy._qt_channel_states, {})

    def test_reload_api_editor_state_clears_stale_source_when_agent_dir_invalid(self):
        class DummyApi(ApiEditorMixin):
            _reload_api_editor_state = ApiEditorMixin._reload_api_editor_state
            _reset_api_source_state = ApiEditorMixin._reset_api_source_state

            def __init__(self):
                self.agent_dir = ""
                self.settings_api_notice = mock.Mock()
                self.settings_api_list_layout = object()
                self._qt_api_py_path = "C:\\demo\\mykey.py"
                self._qt_api_parse_error = ""
                self._qt_api_hidden_configs = [{"var": "legacy", "kind": "custom", "data": {"x": 1}}]
                self._qt_api_state = [{"var": "claude", "kind": "native_claude"}]
                self._qt_api_extras = {"bot_token": "abc"}
                self._qt_api_passthrough = ["old"]

            def _clear_layout(self, _layout):
                return None

            def _settings_target_context(self):
                return {"is_remote": False}

        dummy = DummyApi()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_api_editor_state()

        self.assertEqual(dummy._qt_api_py_path, "")
        self.assertEqual(dummy._qt_api_parse_error, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(dummy._qt_api_hidden_configs, [])
        self.assertEqual(dummy._qt_api_state, [])
        self.assertEqual(dummy._qt_api_extras, {})
        self.assertEqual(dummy._qt_api_passthrough, [])

    def test_apply_loaded_api_source_keeps_page_read_only_when_mykey_read_failed(self):
        class DummyApi(ApiEditorMixin):
            _apply_loaded_api_source = ApiEditorMixin._apply_loaded_api_source
            _reset_api_source_state = ApiEditorMixin._reset_api_source_state

            def __init__(self):
                self.settings_api_notice = mock.Mock()
                self._qt_api_py_path = "C:\\demo\\mykey.py"
                self._qt_api_parse_error = ""
                self._qt_api_hidden_configs = [{"var": "legacy", "kind": "custom", "data": {"x": 1}}]
                self._qt_api_state = [{"var": "claude", "kind": "native_claude"}]
                self._qt_api_extras = {"bot_token": "abc"}
                self._qt_api_passthrough = ["old"]
                self.render_calls = 0

            def _render_api_cards(self):
                self.render_calls += 1

        dummy = DummyApi()
        dummy._apply_loaded_api_source(
            "/remote/mykey.py",
            {"error": "SSH 连接失败", "configs": [], "extras": {}, "passthrough": [], "load_failed": True},
        )

        self.assertEqual(dummy._qt_api_py_path, "")
        self.assertEqual(dummy._qt_api_parse_error, "SSH 连接失败")
        self.assertEqual(dummy._qt_api_hidden_configs, [])
        self.assertEqual(dummy._qt_api_state, [])
        self.assertEqual(dummy._qt_api_extras, {})
        self.assertEqual(dummy._qt_api_passthrough, [])
        self.assertEqual(dummy.render_calls, 0)
        notice_text = dummy.settings_api_notice.setText.call_args[0][0]
        self.assertIn("/remote/mykey.py", notice_text)
        self.assertIn("当前读取失败：SSH 连接失败", notice_text)

    def test_qt_api_save_blocks_invalid_mixin_llm_reference_before_write(self):
        class DummyApi(ApiEditorMixin):
            _qt_api_save = ApiEditorMixin._qt_api_save

            def __init__(self):
                self._qt_api_py_path = "C:\\demo\\mykey.py"
                self._qt_api_extras = {}
                self._qt_api_passthrough = []
                self._reload_api_editor_state_calls = 0
                self._reload_channels_editor_state_calls = 0

            def _api_build_save_configs(self):
                return [
                    {
                        "var": "native_oai_config",
                        "kind": "native_oai",
                        "data": {"name": "mimo", "apikey": "sk", "apibase": "https://api.openai.com/v1", "model": "gpt-5.4"},
                    },
                    {
                        "var": "mixin_config",
                        "kind": "mixin",
                        "data": {"llm_nos": ["gpt-native"], "max_retries": 3},
                    },
                ]

            def _reload_api_editor_state(self):
                self._reload_api_editor_state_calls += 1

            def _reload_channels_editor_state(self):
                self._reload_channels_editor_state_calls += 1

        dummy = DummyApi()
        with mock.patch.object(api_editor.QMessageBox, "critical") as critical_box, mock.patch.object(
            api_editor.lz, "serialize_mykey_py"
        ) as serializer, mock.patch("builtins.open", mock.mock_open()) as open_mock:
            dummy._qt_api_save(restart=False)

        serializer.assert_not_called()
        open_mock.assert_not_called()
        critical_box.assert_called_once()
        self.assertEqual(critical_box.call_args.args[1], "保存失败")
        self.assertIn("gpt-native", critical_box.call_args.args[2])
        self.assertEqual(dummy._reload_api_editor_state_calls, 0)
        self.assertEqual(dummy._reload_channels_editor_state_calls, 0)

    def test_apply_loaded_api_source_restores_auto_generated_names_to_blank_fields(self):
        class DummyApi(ApiEditorMixin):
            _apply_loaded_api_source = ApiEditorMixin._apply_loaded_api_source
            _api_make_simple_state = ApiEditorMixin._api_make_simple_state

            def __init__(self):
                self.settings_api_notice = mock.Mock()
                self._qt_api_hidden_configs = []
                self._qt_api_state = []
                self._qt_api_extras = {}
                self._qt_api_passthrough = []
                self._qt_api_parse_error = ""
                self._qt_api_py_path = ""
                self.render_calls = 0

            def _render_api_cards(self):
                self.render_calls += 1

            def _refresh_api_source_actions(self):
                return None

        dummy = DummyApi()
        dummy._apply_loaded_api_source(
            "C:\\demo\\mykey.py",
            {
                "error": "",
                "configs": [
                    {
                        "var": "native_oai_config",
                        "kind": "native_oai",
                        "data": {
                            "name": "api.openai.com",
                            "apibase": "https://api.openai.com/v1",
                            "apikey": "sk-demo",
                            "model": "gpt-5.4",
                        },
                    },
                    {
                        "var": "native_oai_config2",
                        "kind": "native_oai",
                        "data": {
                            "name": "api.openai.com-2",
                            "apibase": "https://api.openai.com/v1",
                            "apikey": "sk-demo-2",
                            "model": "gpt-5.4-mini",
                        },
                    },
                    {
                        "var": "native_oai_config3",
                        "kind": "native_oai",
                        "data": {
                            "name": "Prod OpenAI",
                            "apibase": "https://api.third.example/v1",
                            "apikey": "sk-demo-3",
                            "model": "gpt-5.4",
                        },
                    }
                ],
                "extras": {},
                "passthrough": [],
            },
        )

        self.assertEqual(dummy.render_calls, 1)
        self.assertEqual(dummy._qt_api_state[0]["name"], "")
        self.assertEqual(dummy._qt_api_state[0]["persisted_name"], "api.openai.com")
        self.assertTrue(dummy._qt_api_state[0]["auto_name_locked"])
        self.assertEqual(dummy._qt_api_state[1]["name"], "")
        self.assertEqual(dummy._qt_api_state[1]["persisted_name"], "api.openai.com-2")
        self.assertTrue(dummy._qt_api_state[1]["auto_name_locked"])
        self.assertEqual(dummy._qt_api_state[2]["name"], "Prod OpenAI")
        self.assertEqual(dummy._qt_api_state[2]["persisted_name"], "Prod OpenAI")
        self.assertFalse(dummy._qt_api_state[2]["auto_name_locked"])

    def test_api_build_save_configs_prefers_custom_name_and_keeps_blank_fallback(self):
        class DummyApi(ApiEditorMixin):
            _api_build_save_configs = ApiEditorMixin._api_build_save_configs
            _api_format_meta = ApiEditorMixin._api_format_meta
            _api_sync_state_var_kind = ApiEditorMixin._api_sync_state_var_kind
            _api_advanced_field_keys = ApiEditorMixin._api_advanced_field_keys
            _api_normalize_advanced_value = ApiEditorMixin._api_normalize_advanced_value
            _api_effective_name = ApiEditorMixin._api_effective_name
            _api_base_name = ApiEditorMixin._api_base_name
            _api_state_kind = ApiEditorMixin._api_state_kind

            def __init__(self):
                self._qt_api_hidden_configs = []
                self._qt_api_state = []

        dummy = DummyApi()
        dummy._qt_api_state = [
            {
                "var": "native_oai_config",
                "name": "Prod OpenAI",
                "format": "oai_chat",
                "tpl_key": "openai",
                "apibase": "https://api.openai.com/v1",
                "apikey": "sk-live",
                "model": "gpt-5.4",
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            },
            {
                "var": "native_oai_config2",
                "name": "",
                "format": "oai_chat",
                "tpl_key": "openai",
                "apibase": "https://api.second.example/v1",
                "apikey": "sk-second",
                "model": "gpt-5.4-mini",
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            },
        ]

        configs = dummy._api_build_save_configs()

        self.assertEqual(configs[0]["data"]["name"], "Prod OpenAI")
        self.assertEqual(configs[1]["data"]["name"], "api.second.example")

    def test_qt_api_move_reorders_visible_state_and_save_order(self):
        class DummyApi(ApiEditorMixin):
            _qt_api_move = ApiEditorMixin._qt_api_move
            _qt_api_move_disabled_reason = ApiEditorMixin._qt_api_move_disabled_reason
            _api_build_save_configs = ApiEditorMixin._api_build_save_configs
            _api_format_meta = ApiEditorMixin._api_format_meta
            _api_sync_state_var_kind = ApiEditorMixin._api_sync_state_var_kind
            _api_advanced_field_keys = ApiEditorMixin._api_advanced_field_keys
            _api_normalize_advanced_value = ApiEditorMixin._api_normalize_advanced_value
            _api_effective_name = ApiEditorMixin._api_effective_name
            _api_base_name = ApiEditorMixin._api_base_name
            _api_state_kind = ApiEditorMixin._api_state_kind

            def __init__(self):
                self._qt_api_hidden_configs = [{"var": "legacy_hidden", "kind": "custom", "data": {"x": 1}}]
                self._qt_api_state = []
                self.render_calls = 0

            def _render_api_cards(self):
                self.render_calls += 1

        dummy = DummyApi()
        dummy._qt_api_state = [
            {
                "var": "native_oai_config",
                "name": "First",
                "format": "oai_chat",
                "tpl_key": "openai",
                "apibase": "https://first.example/v1",
                "apikey": "sk-first",
                "model": "gpt-5.4",
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            },
            {
                "var": "native_oai_config2",
                "name": "Second",
                "format": "oai_chat",
                "tpl_key": "openai",
                "apibase": "https://second.example/v1",
                "apikey": "sk-second",
                "model": "gpt-5.4-mini",
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            },
            {
                "var": "native_oai_config3",
                "name": "Third",
                "format": "oai_chat",
                "tpl_key": "openai",
                "apibase": "https://third.example/v1",
                "apikey": "sk-third",
                "model": "gpt-5.4-nano",
                "advanced_values": {},
                "advanced_expanded": False,
                "user_agent": "",
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            },
        ]

        self.assertTrue(dummy._qt_api_move(2, -1))
        self.assertEqual(dummy.render_calls, 1)
        self.assertEqual(
            [state["var"] for state in dummy._qt_api_state],
            ["native_oai_config", "native_oai_config3", "native_oai_config2"],
        )

        configs = dummy._api_build_save_configs()

        self.assertEqual(
            [cfg["var"] for cfg in configs],
            ["native_oai_config", "native_oai_config3", "native_oai_config2", "legacy_hidden"],
        )

    def test_api_round_trip_keeps_hidden_slots_and_avoids_hidden_name_collisions(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyApi(ApiEditorMixin):
            _apply_loaded_api_source = ApiEditorMixin._apply_loaded_api_source
            _qt_api_move = ApiEditorMixin._qt_api_move
            _qt_api_move_disabled_reason = ApiEditorMixin._qt_api_move_disabled_reason
            _api_build_save_configs = ApiEditorMixin._api_build_save_configs
            _api_make_simple_state = ApiEditorMixin._api_make_simple_state
            _api_format_meta = ApiEditorMixin._api_format_meta
            _api_template_choices = ApiEditorMixin._api_template_choices
            _api_infer_template_key = ApiEditorMixin._api_infer_template_key
            _api_infer_format_key = ApiEditorMixin._api_infer_format_key
            _api_known_advanced_keys_for_kind = ApiEditorMixin._api_known_advanced_keys_for_kind
            _api_default_model_for_state = ApiEditorMixin._api_default_model_for_state
            _api_auto_generated_name = ApiEditorMixin._api_auto_generated_name
            _api_effective_name = ApiEditorMixin._api_effective_name
            _api_base_name = ApiEditorMixin._api_base_name
            _api_unique_name = ApiEditorMixin._api_unique_name
            _api_persisted_name = ApiEditorMixin._api_persisted_name
            _api_state_kind = ApiEditorMixin._api_state_kind
            _api_sync_state_var_kind = ApiEditorMixin._api_sync_state_var_kind
            _api_advanced_field_keys = ApiEditorMixin._api_advanced_field_keys
            _api_normalize_advanced_value = ApiEditorMixin._api_normalize_advanced_value

            def __init__(self):
                self.settings_api_notice = DummyLabel()
                self._qt_api_hidden_configs = []
                self._qt_api_state = []
                self._qt_api_order_slots = []
                self._qt_api_extras = {}
                self._qt_api_passthrough = []
                self._qt_api_parse_error = ""
                self._qt_api_py_path = ""
                self.render_calls = 0

            def _set_api_source_status(self, status, *, py_path=None, error_text=None):
                self.api_status = str(status)
                if py_path is not None:
                    self._qt_api_py_path = str(py_path)
                if error_text is not None:
                    self._qt_api_parse_error = str(error_text)

            def _render_api_cards(self):
                self.render_calls += 1

            def _refresh_api_source_actions(self):
                self.refresh_calls = getattr(self, "refresh_calls", 0) + 1

            def _qt_api_add_channel(self, format_key, *, render=True):
                raise AssertionError("unexpected auto add")

        src = """
native_oai_config = {
    'name': 'First',
    'apikey': 'sk-first',
    'apibase': 'https://first.example/v1',
    'model': 'gpt-5.4',
}
legacy_hidden = {
    'name': 'api.second.example',
    'llm_nos': [0, 1],
}
native_oai_config2 = {
    'name': 'api.second.example',
    'apikey': 'sk-second',
    'apibase': 'https://api.second.example/v1',
    'model': 'gpt-5.4-mini',
}
"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(src)
            parsed = lz.parse_mykey_py(fp)

        dummy = DummyApi()
        dummy._apply_loaded_api_source("C:\\demo\\mykey.py", parsed)
        self.assertTrue(dummy._qt_api_move(1, -1))

        configs = dummy._api_build_save_configs()

        self.assertEqual(
            [cfg["var"] for cfg in configs],
            ["native_oai_config2", "legacy_hidden", "native_oai_config"],
        )
        self.assertEqual(configs[0]["data"]["name"], "api.second.example-2")
        self.assertEqual(configs[1]["data"]["name"], "api.second.example")

    def test_api_reorder_keeps_mixin_refs_stable_when_visible_mixin_card_moves(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyApi(ApiEditorMixin):
            _apply_loaded_api_source = ApiEditorMixin._apply_loaded_api_source
            _qt_api_move = ApiEditorMixin._qt_api_move
            _qt_api_move_disabled_reason = ApiEditorMixin._qt_api_move_disabled_reason
            _api_build_save_configs = ApiEditorMixin._api_build_save_configs
            _api_make_simple_state = ApiEditorMixin._api_make_simple_state
            _api_format_meta = ApiEditorMixin._api_format_meta
            _api_template_choices = ApiEditorMixin._api_template_choices
            _api_infer_template_key = ApiEditorMixin._api_infer_template_key
            _api_infer_format_key = ApiEditorMixin._api_infer_format_key
            _api_known_advanced_keys_for_kind = ApiEditorMixin._api_known_advanced_keys_for_kind
            _api_default_model_for_state = ApiEditorMixin._api_default_model_for_state
            _api_auto_generated_name = ApiEditorMixin._api_auto_generated_name
            _api_effective_name = ApiEditorMixin._api_effective_name
            _api_base_name = ApiEditorMixin._api_base_name
            _api_unique_name = ApiEditorMixin._api_unique_name
            _api_persisted_name = ApiEditorMixin._api_persisted_name
            _api_state_kind = ApiEditorMixin._api_state_kind
            _api_sync_state_var_kind = ApiEditorMixin._api_sync_state_var_kind
            _api_advanced_field_keys = ApiEditorMixin._api_advanced_field_keys
            _api_normalize_advanced_value = ApiEditorMixin._api_normalize_advanced_value

            def __init__(self):
                self.settings_api_notice = DummyLabel()
                self._qt_api_hidden_configs = []
                self._qt_api_state = []
                self._qt_api_order_slots = []
                self._qt_api_extras = {}
                self._qt_api_passthrough = []
                self._qt_api_parse_error = ""
                self._qt_api_py_path = ""

            def _set_api_source_status(self, status, *, py_path=None, error_text=None):
                self.api_status = str(status)
                if py_path is not None:
                    self._qt_api_py_path = str(py_path)
                if error_text is not None:
                    self._qt_api_parse_error = str(error_text)

            def _render_api_cards(self):
                return None

            def _refresh_api_source_actions(self):
                return None

            def _qt_api_add_channel(self, format_key, *, render=True):
                raise AssertionError("unexpected auto add")

        src = """
native_oai_config = {
    'name': 'api.openai.com',
    'apikey': 'sk-first',
    'apibase': 'https://api.openai.com/v1',
    'model': 'gpt-5.4',
}
mixin_config = {
    'name': 'Primary Mixin',
    'llm_nos': ['api.openai.com-2'],
}
native_oai_config2 = {
    'name': 'api.openai.com-2',
    'apikey': 'sk-second',
    'apibase': 'https://api.openai.com/v1',
    'model': 'gpt-5.4-mini',
}
"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(src)
            parsed = lz.parse_mykey_py(fp)

        dummy = DummyApi()
        dummy._apply_loaded_api_source("C:\\demo\\mykey.py", parsed)
        self.assertTrue(dummy._qt_api_move(1, -1))

        configs = dummy._api_build_save_configs()

        self.assertEqual(
            [cfg["var"] for cfg in configs],
            ["mixin_config", "native_oai_config", "native_oai_config2"],
        )
        self.assertEqual(configs[0]["data"]["llm_nos"], ["api.openai.com-2"])
        self.assertEqual(configs[1]["data"]["name"], "api.openai.com")
        self.assertEqual(configs[2]["data"]["name"], "api.openai.com-2")

    def test_qt_api_add_channel_supports_mixin_cards(self):
        class DummyApi(ApiEditorMixin):
            _qt_api_add_channel = ApiEditorMixin._qt_api_add_channel
            _api_format_meta = ApiEditorMixin._api_format_meta

            def __init__(self):
                self._qt_api_state = []
                self._qt_api_hidden_configs = []
                self.render_calls = 0

            def _render_api_cards(self):
                self.render_calls += 1

        dummy = DummyApi()
        dummy._qt_api_add_channel("mixin", render=False)

        self.assertEqual(dummy.render_calls, 0)
        self.assertEqual(len(dummy._qt_api_state), 1)
        self.assertEqual(dummy._qt_api_state[0]["var"], "mixin_config")
        self.assertEqual(dummy._qt_api_state[0]["format"], "mixin")
        self.assertEqual(dummy._qt_api_state[0]["tpl_key"], "mixin")
        self.assertEqual(dummy._qt_api_state[0]["llm_nos"], [])

    def test_bind_api_add_button_menu_exposes_mixin_action(self):
        class DummySignal:
            def __init__(self):
                self._callbacks = []

            def connect(self, callback):
                self._callbacks.append(callback)

            def emit(self):
                for callback in list(self._callbacks):
                    callback()

        class DummyAction:
            def __init__(self, text):
                self.text = str(text)
                self.triggered = DummySignal()

        class DummyMenu:
            def __init__(self, parent=None):
                self.parent = parent
                self.actions = []

            def addAction(self, text):
                action = DummyAction(text)
                self.actions.append(action)
                return action

        class DummyButton:
            def __init__(self):
                self.menu = None

            def setMenu(self, menu):
                self.menu = menu

        class DummySettings(SettingsPanelMixin):
            _api_add_menu_specs = SettingsPanelMixin._api_add_menu_specs
            _bind_api_add_button_menu = SettingsPanelMixin._bind_api_add_button_menu

            def __init__(self):
                self.add_calls = []

            def _qt_api_add_channel(self, format_key, *, render=True):
                self.add_calls.append((str(format_key), bool(render)))

        dummy = DummySettings()
        button = DummyButton()
        with mock.patch.object(settings_panel, "QMenu", DummyMenu):
            menu = dummy._bind_api_add_button_menu(button)

        self.assertIs(button.menu, menu)
        self.assertEqual(
            [action.text for action in menu.actions],
            ["添加 Claude 原生", "添加 Chat Completions", "添加 Responses", "添加 Mixin 故障转移"],
        )
        menu.actions[-1].triggered.emit()
        self.assertEqual(dummy.add_calls, [("mixin", True)])

    def test_qt_api_move_ignores_edge_and_single_card_requests(self):
        class DummyApi(ApiEditorMixin):
            _qt_api_move = ApiEditorMixin._qt_api_move
            _qt_api_move_disabled_reason = ApiEditorMixin._qt_api_move_disabled_reason

            def __init__(self):
                self._qt_api_state = [
                    {
                        "var": "native_oai_config",
                        "name": "",
                        "format": "oai_chat",
                        "tpl_key": "openai",
                    }
                ]
                self.render_calls = 0

            def _render_api_cards(self):
                self.render_calls += 1

        dummy = DummyApi()

        self.assertFalse(dummy._qt_api_move(0, -1))
        self.assertFalse(dummy._qt_api_move(0, 1))
        self.assertFalse(dummy._qt_api_move(3, -1))
        self.assertEqual(dummy._qt_api_move_disabled_reason(0, -1), "当前只有一张 API 卡片，无需调整顺序。")
        self.assertEqual(dummy.render_calls, 0)
        self.assertEqual(dummy._qt_api_state[0]["var"], "native_oai_config")

    def test_refresh_api_source_actions_disables_remote_restart_and_load_failed_save(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyApi(ApiEditorMixin):
            _refresh_api_source_actions = ApiEditorMixin._refresh_api_source_actions
            _api_source_status = ApiEditorMixin._api_source_status
            _api_source_disabled_reason = ApiEditorMixin._api_source_disabled_reason
            _apply_api_button_state = ApiEditorMixin._apply_api_button_state

            def __init__(self, *, is_remote, status):
                self._qt_api_source_status = str(status)
                self.settings_api_add_btn = DummyButton()
                self.settings_api_save_btn = DummyButton()
                self.settings_api_restart_btn = DummyButton()
                self.settings_api_raw_btn = DummyButton()
                self._is_remote = bool(is_remote)

            def _settings_target_context(self):
                return {"is_remote": self._is_remote}

        remote_dummy = DummyApi(is_remote=True, status="ready")
        remote_dummy._refresh_api_source_actions()
        self.assertTrue(remote_dummy.settings_api_save_btn.enabled)
        self.assertFalse(remote_dummy.settings_api_restart_btn.enabled)
        self.assertIn("服务器侧重启对应进程", remote_dummy.settings_api_restart_btn.tooltip)
        self.assertEqual(remote_dummy.settings_api_add_btn.tooltip, "新增一张 API 配置卡片。")
        self.assertEqual(remote_dummy.settings_api_save_btn.tooltip, "把当前 API 配置写回 mykey.py。")
        self.assertEqual(remote_dummy.settings_api_raw_btn.tooltip, "直接编辑当前目标的 mykey.py 原文。")

        failed_dummy = DummyApi(is_remote=False, status="load_failed")
        failed_dummy._refresh_api_source_actions()
        self.assertFalse(failed_dummy.settings_api_add_btn.enabled)
        self.assertFalse(failed_dummy.settings_api_save_btn.enabled)
        self.assertFalse(failed_dummy.settings_api_restart_btn.enabled)
        self.assertTrue(failed_dummy.settings_api_raw_btn.enabled)
        self.assertIn("请先用“直接编辑文件”处理原文", failed_dummy.settings_api_add_btn.tooltip)
        self.assertIn("请先用“直接编辑文件”处理原文", failed_dummy.settings_api_save_btn.tooltip)
        self.assertIn("请先用“直接编辑文件”处理原文", failed_dummy.settings_api_restart_btn.tooltip)

    def test_refresh_api_source_actions_explains_invalid_dir_and_loading_states(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyApi(ApiEditorMixin):
            _refresh_api_source_actions = ApiEditorMixin._refresh_api_source_actions
            _api_source_status = ApiEditorMixin._api_source_status
            _api_source_disabled_reason = ApiEditorMixin._api_source_disabled_reason
            _apply_api_button_state = ApiEditorMixin._apply_api_button_state

            def __init__(self, *, status):
                self._qt_api_source_status = str(status)
                self.settings_api_add_btn = DummyButton()
                self.settings_api_save_btn = DummyButton()
                self.settings_api_restart_btn = DummyButton()
                self.settings_api_raw_btn = DummyButton()

            def _settings_target_context(self):
                return {"is_remote": False}

        invalid_dummy = DummyApi(status="invalid_dir")
        invalid_dummy._refresh_api_source_actions()
        self.assertFalse(invalid_dummy.settings_api_add_btn.enabled)
        self.assertFalse(invalid_dummy.settings_api_save_btn.enabled)
        self.assertFalse(invalid_dummy.settings_api_restart_btn.enabled)
        self.assertFalse(invalid_dummy.settings_api_raw_btn.enabled)
        self.assertEqual(invalid_dummy.settings_api_save_btn.tooltip, "请先选择有效的 GenericAgent 目录。")
        self.assertEqual(invalid_dummy.settings_api_raw_btn.tooltip, "请先选择有效的 GenericAgent 目录。")

        loading_dummy = DummyApi(status="loading")
        loading_dummy._refresh_api_source_actions()
        self.assertFalse(loading_dummy.settings_api_add_btn.enabled)
        self.assertFalse(loading_dummy.settings_api_save_btn.enabled)
        self.assertFalse(loading_dummy.settings_api_restart_btn.enabled)
        self.assertFalse(loading_dummy.settings_api_raw_btn.enabled)
        self.assertEqual(loading_dummy.settings_api_add_btn.tooltip, "正在读取当前目标的 mykey.py，请稍候。")
        self.assertEqual(loading_dummy.settings_api_raw_btn.tooltip, "正在读取当前目标的 mykey.py，请稍候。")

    def test_api_model_fetch_disabled_reason_explains_fetching_state(self):
        class DummyApi(ApiEditorMixin):
            _api_model_fetch_disabled_reason = ApiEditorMixin._api_model_fetch_disabled_reason

        dummy = DummyApi()
        self.assertEqual(dummy._api_model_fetch_disabled_reason({"model_fetching": True}), "当前正在拉取该配置的模型列表，请稍候。")
        self.assertEqual(dummy._api_model_fetch_disabled_reason({"model_fetching": False}), "")

    def test_qt_api_fetch_models_drops_result_when_state_is_no_longer_active(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummyApi(ApiEditorMixin):
            _qt_api_fetch_models = ApiEditorMixin._qt_api_fetch_models

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 1
                self._settings_target_change_token = 3
                self.render_calls = 0
                self.state = {
                    "format": "oai_chat",
                    "apibase": "https://api.example.com/v1",
                    "apikey": "k",
                    "model": "",
                    "model_choices": [],
                    "model_status": "",
                    "model_fetching": False,
                }
                self._qt_api_state = [self.state]

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _render_api_cards(self):
                self.render_calls += 1

            def _api_on_ui_thread(self, fn):
                self._qt_api_state = []
                fn()

        dummy = DummyApi()
        with mock.patch.object(api_editor.threading, "Thread", ImmediateThread), mock.patch.object(
            api_editor.lz, "_fetch_remote_models", return_value=["gpt-5"]
        ):
            dummy._qt_api_fetch_models(dummy.state)

        self.assertEqual(dummy.render_calls, 1)
        self.assertEqual(dummy._qt_api_state, [])
        self.assertEqual(dummy.state["model_status"], "正在拉取模型列表…")
        self.assertTrue(dummy.state["model_fetching"])

    def test_apply_loaded_channels_source_renders_before_async_local_channel_external_refresh(self):
        class DummyChannel(ChannelRuntimeMixin):
            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._channel_procs = {}
                self.settings_channels_notice = mock.Mock()
                self.calls = []

            def _render_channel_cards(self):
                self.calls.append("render_cards")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_runtime_labels")

        dummy = DummyChannel()
        dummy._apply_loaded_channels_source("C:\\demo\\mykey.py", {"error": "", "configs": [], "passthrough": [], "extras": {}})
        self.assertEqual(dummy.calls, ["render_cards", "refresh_runtime_labels"])

    def test_apply_loaded_channels_source_keeps_page_read_only_when_mykey_read_failed(self):
        class DummyChannel(ChannelRuntimeMixin):
            _apply_loaded_channels_source = ChannelRuntimeMixin._apply_loaded_channels_source
            _reset_channels_source_state = ChannelRuntimeMixin._reset_channels_source_state

            def __init__(self):
                self.settings_channels_notice = mock.Mock()
                self._qt_channel_py_path = "C:\\demo\\mykey.py"
                self._qt_channel_parse_error = ""
                self._qt_channel_configs = [{"id": "wechat"}]
                self._qt_channel_passthrough = ["old"]
                self._qt_channel_extras = {"bot_token": "abc"}
                self._qt_channel_states = {"wechat": {"start_btn": object()}}
                self.calls = []

            def _render_channel_cards(self):
                self.calls.append("render_cards")

            def _refresh_channels_runtime_status_labels(self):
                self.calls.append("refresh_runtime_labels")

        dummy = DummyChannel()
        dummy._apply_loaded_channels_source(
            "/remote/mykey.py",
            {"error": "SSH 连接失败", "configs": [], "extras": {}, "passthrough": [], "load_failed": True},
        )

        self.assertEqual(dummy._qt_channel_py_path, "")
        self.assertEqual(dummy._qt_channel_parse_error, "SSH 连接失败")
        self.assertEqual(dummy._qt_channel_configs, [])
        self.assertEqual(dummy._qt_channel_passthrough, [])
        self.assertEqual(dummy._qt_channel_extras, {})
        self.assertEqual(dummy._qt_channel_states, {})
        self.assertEqual(dummy.calls, [])
        notice_text = dummy.settings_channels_notice.setText.call_args[0][0]
        self.assertIn("/remote/mykey.py", notice_text)
        self.assertIn("当前读取失败：SSH 连接失败", notice_text)

    def test_refresh_channel_source_actions_disables_remote_stop_all_and_load_failed_save(self):
        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyChannel(ChannelRuntimeMixin):
            _refresh_channel_source_actions = ChannelRuntimeMixin._refresh_channel_source_actions
            _channel_source_status = ChannelRuntimeMixin._channel_source_status
            _channel_source_action_disabled_reason = ChannelRuntimeMixin._channel_source_action_disabled_reason
            _apply_channel_button_state = ChannelRuntimeMixin._apply_channel_button_state

            def __init__(self, *, is_remote, status):
                self._qt_channel_source_status = str(status)
                self.settings_channels_save_btn = DummyButton()
                self.settings_channels_refresh_btn = DummyButton()
                self.settings_channels_stop_all_btn = DummyButton()
                self._is_remote = bool(is_remote)

            def _settings_target_context(self):
                return {"is_remote": self._is_remote}

        remote_dummy = DummyChannel(is_remote=True, status="ready")
        remote_dummy._refresh_channel_source_actions()
        self.assertTrue(remote_dummy.settings_channels_save_btn.enabled)
        self.assertFalse(remote_dummy.settings_channels_stop_all_btn.enabled)
        self.assertIn("远端目标", remote_dummy.settings_channels_stop_all_btn.tooltip)

        failed_dummy = DummyChannel(is_remote=False, status="load_failed")
        failed_dummy._refresh_channel_source_actions()
        self.assertFalse(failed_dummy.settings_channels_save_btn.enabled)
        self.assertTrue(failed_dummy.settings_channels_refresh_btn.enabled)
        self.assertTrue(failed_dummy.settings_channels_stop_all_btn.enabled)
        self.assertEqual(failed_dummy.settings_channels_save_btn.tooltip, "当前状态不可保存通讯配置。")
        self.assertEqual(failed_dummy.settings_channels_refresh_btn.tooltip, "手动刷新当前目标的渠道运行状态。")

    def test_show_settings_category_reloads_dynamic_or_target_pages_and_skips_cached_local_pages(self):
        class DummyStack:
            def __init__(self):
                self.current = None

            def setCurrentWidget(self, widget):
                self.current = widget

        class DummyButton:
            def __init__(self):
                self.styles = []

            def setStyleSheet(self, style):
                self.styles.append(style)

        class DummySettings(SettingsPanelMixin):
            _show_settings_category = SettingsPanelMixin._show_settings_category
            _settings_category_needs_live_reload = SettingsPanelMixin._settings_category_needs_live_reload
            _settings_category_refreshes_target_combo = SettingsPanelMixin._settings_category_refreshes_target_combo
            _settings_should_reload_on_switch = SettingsPanelMixin._settings_should_reload_on_switch
            _settings_category_scope_mode = SettingsPanelMixin._settings_category_scope_mode
            _settings_category_uses_target_switch = SettingsPanelMixin._settings_category_uses_target_switch

            def __init__(self):
                self.settings_stack = DummyStack()
                self._settings_pages = {
                    "channels": {"widget": object()},
                    "sop": {"widget": object()},
                    "vps": {"widget": object()},
                    "api": {"widget": object()},
                    "theme": {"widget": object()},
                    "about": {"widget": object()},
                }
                self._settings_nav_buttons = {
                    "channels": DummyButton(),
                    "sop": DummyButton(),
                    "vps": DummyButton(),
                    "api": DummyButton(),
                    "theme": DummyButton(),
                    "about": DummyButton(),
                }
                self._settings_loaded_categories = {"vps", "theme", "about"}
                self.calls = []

            def _refresh_settings_target_visibility(self, key):
                self.calls.append(("visibility", key))

            def _sidebar_button_style(self, *, selected=False, subtle=False):
                if selected:
                    return "selected"
                if subtle:
                    return "subtle"
                return "default"

            def _settings_reload(self, *, categories=None, force=False):
                self.calls.append(("reload", list(categories or []), bool(force)))

            def _refresh_settings_status_label(self, key=None):
                self.calls.append(("status", key))

        dummy = DummySettings()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("channels", reload=True)
        self.assertIn(("reload", ["channels"], False), dummy.calls)

        dummy.calls.clear()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("api", reload=True)
        self.assertIn(("reload", ["api"], False), dummy.calls)

        dummy.calls.clear()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("sop", reload=True)
        self.assertIn(("reload", ["sop"], False), dummy.calls)

        dummy.calls.clear()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("vps", reload=True)
        self.assertNotIn(("reload", ["vps"], False), dummy.calls)
        self.assertIn(("status", "vps"), dummy.calls)

        dummy.calls.clear()
        with mock.patch("qt_chat_parts.settings_panel.QTimer.singleShot", side_effect=lambda *_args: _args[-1]()):
            dummy._show_settings_category("theme", reload=True)
        self.assertNotIn(("reload", ["theme"], False), dummy.calls)
        self.assertIn(("status", "theme"), dummy.calls)

    def test_show_settings_switches_page_before_forced_reload(self):
        class DummyPages:
            def __init__(self):
                self.current = None

            def setCurrentWidget(self, widget):
                self.current = widget

        class DummyBtn:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

            def clicked(self):
                return None

        class DummyNav(NavigationMixin):
            _show_settings = NavigationMixin._show_settings

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.pages = DummyPages()
                self._settings_page = object()
                self._current_settings_category = "api"
                self._settings_top_back_btn = None
                self.events = []

            def _ensure_settings_page_built(self):
                self.events.append("ensure")

            def setWindowTitle(self, text):
                self.events.append(("title", str(text)))

            def _refresh_welcome_state(self):
                self.events.append("refresh_welcome")

            def _settings_reload(self, *, categories=None, force=False):
                self.events.append(("reload", list(categories or []), bool(force), self.pages.current is self._settings_page))

        dummy = DummyNav()
        with mock.patch.object(navigation.lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            navigation.QTimer, "singleShot", side_effect=lambda *_args: _args[-1]()
        ):
            dummy._show_settings()
        self.assertEqual(dummy.pages.current, dummy._settings_page)
        self.assertIn(("reload", ["api"], True, True), dummy.events)

    def test_settings_reload_skips_target_combo_refresh_for_local_only_categories(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = str(value or "")

        class DummySettings(SettingsPanelMixin):
            _settings_reload = SettingsPanelMixin._settings_reload
            _refresh_settings_status_label = SettingsPanelMixin._refresh_settings_status_label
            _settings_category_refreshes_target_combo = SettingsPanelMixin._settings_category_refreshes_target_combo
            _settings_category_scope_mode = SettingsPanelMixin._settings_category_scope_mode
            _settings_category_uses_target_switch = SettingsPanelMixin._settings_category_uses_target_switch

            def __init__(self):
                self.cfg = {}
                self.agent_dir = ""
                self._current_settings_category = "theme"
                self._settings_loaded_categories = set()
                self.settings_status_label = DummyLabel()
                self.calls = []

            def _normalize_settings_target(self, raw):
                data = dict(raw or {})
                scope = str(data.get("scope") or "local").strip().lower()
                if scope != "remote":
                    scope = "local"
                device_id = str(data.get("device_id") or "local").strip() or "local"
                if scope == "local":
                    device_id = "local"
                return {"scope": scope, "device_id": device_id}

            def _settings_target_context(self):
                return {"is_remote": False, "label": "本机目录"}

            def _refresh_settings_target_combo(self, *, force=False):
                self.calls.append(("refresh_target_combo", bool(force)))

            def _reload_api_editor_state(self):
                self.calls.append("reload_api")

            def _reload_channels_editor_state(self):
                self.calls.append("reload_channels")

            def _reload_vps_panel(self):
                self.calls.append("reload_vps")

            def _reload_schedule_panel(self):
                self.calls.append("reload_schedule")

            def _reload_personal_panel(self):
                self.calls.append("reload_personal")

            def _reload_theme_panel(self):
                self.calls.append("reload_theme")

            def _reload_usage_panel(self):
                self.calls.append("reload_usage")

            def _reload_about_panel(self):
                self.calls.append("reload_about")

        dummy = DummySettings()
        dummy._settings_reload(categories=["theme"], force=False)

        self.assertNotIn(("refresh_target_combo", False), dummy.calls)
        self.assertIn("reload_theme", dummy.calls)
        self.assertEqual(dummy.settings_status_label.text, "当前页为启动器本机设置，不需要切换目标设备。")

        dummy.calls.clear()
        dummy._current_settings_category = "api"
        dummy._settings_reload(categories=["api"], force=False)
        self.assertIn(("refresh_target_combo", False), dummy.calls)

    def test_complete_orb_drag_release_skips_snap_and_save_for_clicks(self):
        class DummyOrb:
            _complete_orb_drag_release = launcher_window.FloatingOrbWindow._complete_orb_drag_release

            def __init__(self, moved):
                self.calls = []
                self._drag_moved = bool(moved)
                self._orb_pressed = True
                self._host = types.SimpleNamespace(
                    _save_floating_orb_position=lambda pos: self.calls.append(("save", pos.x(), pos.y()))
                )

            def _snap_to_edge(self):
                self.calls.append("snap")

            def pos(self):
                return launcher_window.QPoint(40, 60)

            def update(self):
                self.calls.append("update")

            def toggle_panel(self):
                self.calls.append("toggle")

        dummy = DummyOrb(moved=False)
        was_click = dummy._complete_orb_drag_release(toggle_on_click=True, refresh_orb=True)

        self.assertTrue(was_click)
        self.assertEqual(dummy.calls, ["update", "toggle"])
        self.assertFalse(dummy._orb_pressed)

    def test_complete_orb_drag_release_persists_only_after_real_drag(self):
        class DummyOrb:
            _complete_orb_drag_release = launcher_window.FloatingOrbWindow._complete_orb_drag_release

            def __init__(self, moved):
                self.calls = []
                self._drag_moved = bool(moved)
                self._orb_pressed = True
                self._host = types.SimpleNamespace(
                    _save_floating_orb_position=lambda pos: self.calls.append(("save", pos.x(), pos.y()))
                )

            def _snap_to_edge(self):
                self.calls.append("snap")

            def pos(self):
                return launcher_window.QPoint(40, 60)

            def update(self):
                self.calls.append("update")

            def toggle_panel(self):
                self.calls.append("toggle")

        dummy = DummyOrb(moved=True)
        was_click = dummy._complete_orb_drag_release(toggle_on_click=True, refresh_orb=True)

        self.assertFalse(was_click)
        self.assertEqual(dummy.calls, ["snap", ("save", 40, 60), "update"])
        self.assertFalse(dummy._orb_pressed)

    def test_vps_disabled_reason_helpers_cover_connection_and_deploy_prerequisites(self):
        class DummySettings(SettingsPanelMixin):
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _vps_connection_incomplete_reason = SettingsPanelMixin._vps_connection_incomplete_reason
            _vps_auth_missing_reason = SettingsPanelMixin._vps_auth_missing_reason
            _vps_runtime_connection_disabled_reason = SettingsPanelMixin._vps_runtime_connection_disabled_reason
            _vps_deploy_validation_error = SettingsPanelMixin._vps_deploy_validation_error
            _vps_takeover_validation_error = SettingsPanelMixin._vps_takeover_validation_error

            def __init__(self):
                self.cfg = {}

            def _collect_vps_form_data(self):
                return {}

            def _collect_vps_deploy_form_data(self):
                return {}

        dummy = DummySettings()
        self.assertEqual(dummy._vps_connection_incomplete_reason({}), "请先填写服务器地址和用户名。")
        self.assertEqual(
            dummy._vps_connection_incomplete_reason({"host": "10.0.0.8"}),
            "服务器地址和用户名需要同时填写。",
        )
        self.assertEqual(
            dummy._vps_runtime_connection_disabled_reason({"host": "10.0.0.8", "username": "root"}),
            "请至少提供 SSH 私钥路径或密码。",
        )
        with mock.patch.object(settings_panel.os.path, "isfile", return_value=False):
            self.assertEqual(
                dummy._vps_auth_missing_reason(
                    {"host": "10.0.0.8", "username": "root", "ssh_key_path": "keys/missing.pem"}
                ),
                "SSH 私钥路径不存在，请检查后重试。",
            )
        with mock.patch.object(settings_panel.lz, "REPO_URL", ""):
            self.assertEqual(
                dummy._vps_deploy_validation_error(
                    {
                        "source": "git",
                        "remote_dir": "/srv/genericagent",
                        "repo_url": "",
                    }
                ),
                "拉取模式下，仓库地址不能为空。",
            )
        self.assertEqual(
            dummy._vps_deploy_validation_error(
                {
                    "source": "git",
                    "repo_url": "https://example.com/repo.git",
                }
            ),
            "",
        )
        with mock.patch.object(settings_panel.os.path, "isdir", return_value=False):
            self.assertEqual(
                dummy._vps_deploy_validation_error(
                    {
                        "source": "upload",
                        "remote_dir": "/srv/genericagent",
                        "local_agent_dir": "missing-agent",
                    }
                ),
                "上传模式下，本地 agant 目录不存在。",
            )
        self.assertEqual(
            dummy._vps_deploy_validation_error(
                {
                    "source": "git",
                    "remote_dir": "/srv/genericagent",
                    "repo_url": "https://example.com/repo.git",
                    "dep_install_mode": "mirror",
                    "pip_mirror_url": "ftp://mirror.example.com/simple",
                }
            ),
            "pip 镜像地址格式无效，请填写 http(s) URL。",
        )
        self.assertEqual(
            dummy._vps_takeover_validation_error({"takeover_agent_dir": ""}),
            "请先填写要接管的 agant 路径。",
        )
        self.assertEqual(
            dummy._normalize_vps_takeover_cfg({"takeover_agent_dir": "/srv/agant"}),
            {
                "remote_mode": "ssh",
                "takeover_mode": "path",
                "takeover_agent_dir": "/srv/agant",
                "takeover_python_cmd": "python3",
                "docker_takeover_container": "",
                "docker_takeover_agent_dir": "",
                "docker_takeover_python_cmd": "python3",
            },
        )
        self.assertEqual(
            dummy._normalize_vps_takeover_cfg({"docker_takeover_container": "ga-prod", "docker_takeover_agent_dir": "/app"}),
            {
                "remote_mode": "ssh",
                "takeover_mode": "path",
                "takeover_agent_dir": "/app",
                "takeover_python_cmd": "python3",
                "docker_takeover_container": "",
                "docker_takeover_agent_dir": "",
                "docker_takeover_python_cmd": "python3",
            },
        )

    def test_vps_profile_to_remote_device_uses_path_takeover_context(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _vps_profile_to_remote_device = SettingsPanelMixin._vps_profile_to_remote_device
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value

            def __init__(self):
                self.cfg = {}

        dummy = DummySettings()
        device = dummy._vps_profile_to_remote_device(
            {
                "id": "srv-1",
                "name": "生产环境",
                "host": "10.0.0.8",
                "username": "root",
                "password": "pw",
                "remote_mode": "ssh",
                "remote_dir": "/srv/other",
                "takeover_mode": "path",
                "takeover_agent_dir": "/opt/agant",
                "takeover_python_cmd": "python",
            }
        )
        self.assertEqual(device["agent_dir"], "/opt/agant")
        self.assertEqual(device["python_cmd"], "python")
        self.assertEqual(device["docker_container"], "")
        self.assertEqual(device["remote_mode"], "ssh")
        self.assertEqual(device["takeover_mode"], "path")
        self.assertEqual(device["takeover_agent_dir"], "/opt/agant")
        self.assertEqual(device["name"], "生产环境")

    def test_collect_current_vps_profile_form_data_normalizes_legacy_docker_takeover_to_path_on_save(self):
        class DummyLineEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _collect_vps_form_data = SettingsPanelMixin._collect_vps_form_data
            _collect_vps_takeover_form_data = SettingsPanelMixin._collect_vps_takeover_form_data
            _collect_current_vps_profile_form_data = SettingsPanelMixin._collect_current_vps_profile_form_data
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value

            def __init__(self):
                self.cfg = {}
                self.current = {
                    "id": "srv-1",
                    "name": "生产环境",
                    "host": "10.0.0.8",
                    "username": "root",
                    "password": "pw",
                    "remote_mode": "docker_container",
                    "docker_takeover_container": "ga-prod",
                    "docker_takeover_agent_dir": "/opt/agant",
                    "docker_takeover_python_cmd": "python",
                }
                self.settings_vps_host_edit = DummyLineEdit("10.0.0.8")
                self.settings_vps_username_edit = DummyLineEdit("root")
                self.settings_vps_key_path_edit = DummyLineEdit("")
                self.settings_vps_password_edit = DummyLineEdit("pw")
                self.settings_vps_takeover_agent_dir_edit = DummyLineEdit("/opt/agant")

            def _current_vps_profile(self):
                return dict(self.current)

            def _collect_vps_deploy_form_data(self):
                return {}

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

        class DummySpin:
            def value(self):
                return 22

        dummy = DummySettings()
        dummy.settings_vps_port_spin = DummySpin()
        payload = dummy._collect_current_vps_profile_form_data()
        self.assertEqual(payload["remote_mode"], "ssh")
        self.assertEqual(payload["takeover_mode"], "path")
        self.assertEqual(payload["takeover_agent_dir"], "/opt/agant")
        self.assertEqual(payload["takeover_python_cmd"], "python")
        self.assertEqual(payload["docker_takeover_container"], "")
        self.assertEqual(payload["docker_takeover_agent_dir"], "")
        self.assertEqual(payload["docker_takeover_python_cmd"], "python3")

    def test_normalize_vps_profile_strips_repeated_legacy_auto_added_docker_suffixes(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value

            def __init__(self):
                self.cfg = {}

        dummy = DummySettings()
        profile = dummy._normalize_vps_profile(
            {
                "id": "srv-1",
                "name": "我的服务器（Docker）（Docker）（Docker）",
                "host": "10.0.0.8",
                "username": "root",
                "password": "pw",
                "remote_mode": "docker_container",
                "docker_takeover_container": "ga-prod",
            }
        )
        self.assertEqual(profile["name"], "我的服务器")

    def test_normalize_vps_profile_preserves_user_name_without_docker_takeover_context(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value

            def __init__(self):
                self.cfg = {}

        dummy = DummySettings()
        profile = dummy._normalize_vps_profile(
            {
                "id": "srv-1",
                "name": "我的服务器（Docker）",
                "host": "10.0.0.8",
                "username": "root",
                "password": "pw",
            }
        )
        self.assertEqual(profile["name"], "我的服务器（Docker）")

    def test_normalize_vps_profile_preserves_user_name_with_deploy_docker_container_only(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value

            def __init__(self):
                self.cfg = {}

        dummy = DummySettings()
        raw = {
            "id": "srv-1",
            "name": "实验环境（Docker）",
            "host": "10.0.0.8",
            "username": "root",
            "password": "pw",
            "docker_container": "genericagent",
        }
        profile = dummy._normalize_vps_profile(raw)
        self.assertFalse(dummy._vps_profile_name_needs_cleanup(raw))
        self.assertEqual(profile["name"], "实验环境（Docker）")

    def test_vps_profiles_write_back_cleaned_legacy_docker_names(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value
            _vps_profiles = SettingsPanelMixin._vps_profiles

            def __init__(self):
                self.cfg = {
                    "vps_current_profile_id": "srv-1",
                    "vps_profiles": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器（Docker）（Docker）",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "remote_mode": "docker_container",
                            "docker_takeover_container": "ga-prod",
                            "docker_takeover_agent_dir": "/opt/agant",
                        }
                    ],
                }
                self.saved_rows = None

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

            def _save_vps_profiles(self, rows, *, selected_id=""):
                self.saved_rows = [dict(item) for item in rows]
                self.cfg["vps_profiles"] = [dict(item) for item in rows]
                self.cfg["vps_current_profile_id"] = str(selected_id or "")
                return rows

        dummy = DummySettings()
        rows = dummy._vps_profiles()
        self.assertIsNotNone(dummy.saved_rows)
        self.assertEqual(rows[0]["name"], "我的服务器")
        self.assertEqual(dummy.saved_rows[0]["name"], "我的服务器")
        self.assertEqual(dummy.cfg["vps_profiles"][0]["name"], "我的服务器")

    def test_vps_profiles_reconstruct_legacy_docker_remote_device_rows_as_path_takeover(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value
            _vps_profiles = SettingsPanelMixin._vps_profiles

            def __init__(self):
                self.cfg = {
                    "remote_devices": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "agent_mode": "docker",
                            "remote_mode": "docker_container",
                            "agent_dir": "/opt/agant",
                            "docker_container": "ga-prod",
                            "docker_agent_dir": "/opt/agant",
                            "python_cmd": "python",
                            "auto_ssh": True,
                        }
                    ]
                }

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

        dummy = DummySettings()
        with mock.patch.object(settings_panel.lz, "save_config"):
            rows = dummy._vps_profiles()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remote_mode"], "ssh")
        self.assertEqual(rows[0]["takeover_mode"], "path")
        self.assertEqual(rows[0]["takeover_agent_dir"], "/opt/agant")
        self.assertEqual(rows[0]["takeover_python_cmd"], "python")
        self.assertEqual(rows[0]["docker_takeover_container"], "")
        self.assertEqual(rows[0]["docker_takeover_agent_dir"], "")

    def test_vps_profiles_reconstruct_path_takeover_from_host_remote_device_rows(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value
            _vps_profiles = SettingsPanelMixin._vps_profiles

            def __init__(self):
                self.cfg = {
                    "remote_devices": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "agent_mode": "host",
                            "remote_mode": "ssh",
                            "agent_dir": "/srv/agant",
                            "python_cmd": "python",
                            "takeover_mode": "path",
                            "takeover_agent_dir": "/srv/agant",
                            "takeover_python_cmd": "python",
                            "auto_ssh": True,
                        }
                    ]
                }

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

        dummy = DummySettings()
        rows = dummy._vps_profiles()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remote_mode"], "ssh")
        self.assertEqual(rows[0]["takeover_mode"], "path")
        self.assertEqual(rows[0]["takeover_agent_dir"], "/srv/agant")
        self.assertEqual(rows[0]["takeover_python_cmd"], "python")
        self.assertEqual(rows[0]["docker_takeover_container"], "")

    def test_vps_profiles_self_heal_remote_devices_missing_remote_mode(self):
        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value
            _vps_profiles = SettingsPanelMixin._vps_profiles
            _vps_profile_to_remote_device = SettingsPanelMixin._vps_profile_to_remote_device
            _save_vps_profiles = SettingsPanelMixin._save_vps_profiles
            _set_current_vps_profile_id = SettingsPanelMixin._set_current_vps_profile_id

            def __init__(self):
                self.cfg = {
                    "remote_devices": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "agent_mode": "docker",
                            "agent_dir": "/opt/agant",
                            "docker_container": "ga-prod",
                            "docker_agent_dir": "/opt/agant",
                            "python_cmd": "python",
                            "auto_ssh": True,
                        }
                    ]
                }

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

        dummy = DummySettings()
        with mock.patch.object(settings_panel.lz, "save_config"):
            rows = dummy._vps_profiles()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remote_mode"], "ssh")
        self.assertEqual(rows[0]["takeover_mode"], "path")
        self.assertEqual(rows[0]["takeover_agent_dir"], "/opt/agant")
        self.assertEqual(rows[0]["docker_takeover_container"], "")
        self.assertEqual(rows[0]["docker_takeover_agent_dir"], "")
        self.assertEqual(dummy.cfg["remote_devices"][0]["remote_mode"], "ssh")
        self.assertEqual(dummy.cfg["remote_devices"][0]["agent_mode"], "host")
        self.assertEqual(dummy.cfg["remote_devices"][0]["docker_container"], "")
        self.assertEqual(dummy.cfg["remote_devices"][0]["docker_agent_dir"], "")

    def test_refresh_vps_action_buttons_explains_missing_profiles_and_busy_states(self):
        class DummyWidget:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummySettings(SettingsPanelMixin):
            _refresh_vps_action_buttons = SettingsPanelMixin._refresh_vps_action_buttons
            _apply_vps_button_state = SettingsPanelMixin._apply_vps_button_state
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _vps_busy_reason = SettingsPanelMixin._vps_busy_reason
            _vps_connection_incomplete_reason = SettingsPanelMixin._vps_connection_incomplete_reason
            _vps_auth_missing_reason = SettingsPanelMixin._vps_auth_missing_reason
            _vps_runtime_connection_disabled_reason = SettingsPanelMixin._vps_runtime_connection_disabled_reason
            _vps_terminal_connect_disabled_reason = SettingsPanelMixin._vps_terminal_connect_disabled_reason
            _vps_terminal_disconnect_disabled_reason = SettingsPanelMixin._vps_terminal_disconnect_disabled_reason
            _vps_terminal_send_disabled_reason = SettingsPanelMixin._vps_terminal_send_disabled_reason
            _vps_deploy_validation_error = SettingsPanelMixin._vps_deploy_validation_error
            _vps_deploy_disabled_reason = SettingsPanelMixin._vps_deploy_disabled_reason
            _vps_profile_action_disabled_reason = SettingsPanelMixin._vps_profile_action_disabled_reason

            def __init__(self):
                self.cfg = {}
                self._profiles = []
                self._form_data = {}
                self._deploy_data = {}
                self._vps_form_profile_id = ""
                self._vps_connect_running = False
                self._vps_dep_install_running = False
                self._vps_terminal_connecting = False
                self._vps_terminal_connected = False
                self._vps_deploy_running = False
                self._vps_terminal_profile_id = ""
                self._vps_terminal_channel = None
                self.settings_vps_save_btn = DummyWidget()
                self.settings_vps_install_dep_btn = DummyWidget()
                self.settings_vps_test_btn = DummyWidget()
                self.settings_vps_terminal_connect_btn = DummyWidget()
                self.settings_vps_terminal_disconnect_btn = DummyWidget()
                self.settings_vps_terminal_send_btn = DummyWidget()
                self.settings_vps_terminal_input = DummyWidget()
                self.settings_vps_deploy_btn = DummyWidget()
                self.settings_vps_profile_combo = DummyWidget()
                self.settings_vps_profile_new_btn = DummyWidget()
                self.settings_vps_profile_rename_btn = DummyWidget()
                self.settings_vps_profile_delete_btn = DummyWidget()

            def _vps_profiles(self):
                return list(self._profiles)

            def _collect_vps_form_data(self):
                return dict(self._form_data)

            def _collect_vps_deploy_form_data(self):
                return dict(self._deploy_data)

            def _current_vps_profile_id(self):
                return str(self._vps_form_profile_id or "")

        empty_dummy = DummySettings()
        empty_dummy._refresh_vps_action_buttons()
        self.assertFalse(empty_dummy.settings_vps_save_btn.enabled)
        self.assertEqual(empty_dummy.settings_vps_save_btn.tooltip, "请先新建至少一个服务器配置。")
        self.assertFalse(empty_dummy.settings_vps_profile_combo.enabled)
        self.assertEqual(empty_dummy.settings_vps_profile_combo.tooltip, "当前还没有服务器配置可切换。")
        self.assertTrue(empty_dummy.settings_vps_profile_new_btn.enabled)
        self.assertFalse(empty_dummy.settings_vps_terminal_disconnect_btn.enabled)
        self.assertEqual(empty_dummy.settings_vps_terminal_disconnect_btn.tooltip, "当前没有已连接的远程终端。")
        self.assertFalse(empty_dummy.settings_vps_terminal_send_btn.enabled)
        self.assertEqual(empty_dummy.settings_vps_terminal_send_btn.tooltip, "请先连接远程终端。")

        busy_dummy = DummySettings()
        busy_dummy._profiles = [{"id": "srv-1"}]
        busy_dummy._form_data = {"host": "10.0.0.8", "username": "root", "password": "pw"}
        busy_dummy._deploy_data = {
            "source": "git",
            "repo_url": "https://example.com/repo.git",
            "remote_dir": "/srv/genericagent",
        }
        busy_dummy._vps_dep_install_running = True
        busy_dummy._refresh_vps_action_buttons()
        self.assertEqual(busy_dummy.settings_vps_install_dep_btn.text, "安装中…")
        self.assertFalse(busy_dummy.settings_vps_save_btn.enabled)
        self.assertEqual(busy_dummy.settings_vps_save_btn.tooltip, "正在安装 SSH 依赖，请等待当前任务完成。")
        self.assertFalse(busy_dummy.settings_vps_profile_new_btn.enabled)
        self.assertEqual(busy_dummy.settings_vps_profile_new_btn.tooltip, "正在安装 SSH 依赖，请等待当前任务完成。")
        self.assertFalse(busy_dummy.settings_vps_terminal_send_btn.enabled)
        self.assertEqual(busy_dummy.settings_vps_terminal_send_btn.tooltip, "正在安装 SSH 依赖，请等待当前任务完成。")

    def test_refresh_vps_action_buttons_handles_cross_server_terminal_and_deploy_validation(self):
        class DummyWidget:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummySettings(SettingsPanelMixin):
            _refresh_vps_action_buttons = SettingsPanelMixin._refresh_vps_action_buttons
            _apply_vps_button_state = SettingsPanelMixin._apply_vps_button_state
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _vps_busy_reason = SettingsPanelMixin._vps_busy_reason
            _vps_connection_incomplete_reason = SettingsPanelMixin._vps_connection_incomplete_reason
            _vps_auth_missing_reason = SettingsPanelMixin._vps_auth_missing_reason
            _vps_runtime_connection_disabled_reason = SettingsPanelMixin._vps_runtime_connection_disabled_reason
            _vps_terminal_connect_disabled_reason = SettingsPanelMixin._vps_terminal_connect_disabled_reason
            _vps_terminal_disconnect_disabled_reason = SettingsPanelMixin._vps_terminal_disconnect_disabled_reason
            _vps_terminal_send_disabled_reason = SettingsPanelMixin._vps_terminal_send_disabled_reason
            _vps_deploy_validation_error = SettingsPanelMixin._vps_deploy_validation_error
            _vps_deploy_disabled_reason = SettingsPanelMixin._vps_deploy_disabled_reason
            _vps_profile_action_disabled_reason = SettingsPanelMixin._vps_profile_action_disabled_reason

            def __init__(self):
                self.cfg = {}
                self._profiles = [{"id": "srv-2"}]
                self._form_data = {"host": "10.0.0.9", "username": "root", "password": "pw"}
                self._deploy_data = {
                    "source": "upload",
                    "local_agent_dir": "missing-agent",
                    "remote_dir": "/srv/genericagent",
                }
                self._vps_form_profile_id = "srv-2"
                self._vps_connect_running = False
                self._vps_dep_install_running = False
                self._vps_terminal_connecting = False
                self._vps_terminal_connected = True
                self._vps_deploy_running = False
                self._vps_terminal_profile_id = "srv-1"
                self._vps_terminal_channel = object()
                self.settings_vps_save_btn = DummyWidget()
                self.settings_vps_install_dep_btn = DummyWidget()
                self.settings_vps_test_btn = DummyWidget()
                self.settings_vps_terminal_connect_btn = DummyWidget()
                self.settings_vps_terminal_disconnect_btn = DummyWidget()
                self.settings_vps_terminal_send_btn = DummyWidget()
                self.settings_vps_terminal_input = DummyWidget()
                self.settings_vps_deploy_btn = DummyWidget()
                self.settings_vps_profile_combo = DummyWidget()
                self.settings_vps_profile_new_btn = DummyWidget()
                self.settings_vps_profile_rename_btn = DummyWidget()
                self.settings_vps_profile_delete_btn = DummyWidget()

            def _vps_profiles(self):
                return list(self._profiles)

            def _collect_vps_form_data(self):
                return dict(self._form_data)

            def _collect_vps_deploy_form_data(self):
                return dict(self._deploy_data)

            def _current_vps_profile_id(self):
                return str(self._vps_form_profile_id or "")

        dummy = DummySettings()
        with mock.patch.object(settings_panel.os.path, "isdir", return_value=False):
            dummy._refresh_vps_action_buttons()
        self.assertFalse(dummy.settings_vps_terminal_connect_btn.enabled)
        self.assertIn("另一台服务器", dummy.settings_vps_terminal_connect_btn.tooltip)
        self.assertTrue(dummy.settings_vps_terminal_disconnect_btn.enabled)
        self.assertTrue(dummy.settings_vps_terminal_send_btn.enabled)
        self.assertTrue(dummy.settings_vps_terminal_input.enabled)
        self.assertFalse(dummy.settings_vps_deploy_btn.enabled)
        self.assertEqual(dummy.settings_vps_deploy_btn.tooltip, "上传模式下，本地 agant 目录不存在。")

    def test_collect_vps_deploy_form_data_falls_back_to_combo_item_data(self):
        class DummyLineEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

        class BrokenCombo:
            def __init__(self, index, items):
                self._index = int(index)
                self._items = dict(items)

            def currentData(self):
                return None

            def currentIndex(self):
                return self._index

            def itemData(self, index):
                return self._items.get(int(index))

        class DummySettings(SettingsPanelMixin):
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _collect_vps_deploy_form_data = SettingsPanelMixin._collect_vps_deploy_form_data
            _combo_current_data_value = SettingsPanelMixin._combo_current_data_value

            def __init__(self):
                self.settings_vps_deploy_source_combo = BrokenCombo(1, {0: "upload", 1: "git"})
                self.settings_vps_dep_install_mode_combo = BrokenCombo(2, {0: "offline", 1: "global", 2: "mirror"})
                self.settings_vps_local_agent_dir_edit = DummyLineEdit("")
                self.settings_vps_repo_url_edit = DummyLineEdit("https://example.com/repo.git")
                self.settings_vps_remote_dir_edit = DummyLineEdit("/srv/genericagent")
                self.settings_vps_pip_mirror_edit = DummyLineEdit("https://mirror.example.com/simple")
                self.settings_vps_upload_excludes_edit = DummyLineEdit("")
                self.settings_vps_username_edit = DummyLineEdit("root")

        dummy = DummySettings()
        payload = dummy._collect_vps_deploy_form_data()
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["dep_install_mode"], "mirror")
        self.assertEqual(payload["repo_url"], "https://example.com/repo.git")

    def test_save_vps_deploy_preferences_falls_back_to_form_when_no_profile_exists(self):
        class DummyLineEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

        class BrokenCombo:
            def __init__(self, index, items):
                self._index = int(index)
                self._items = dict(items)

            def currentData(self):
                return None

            def currentIndex(self):
                return self._index

            def itemData(self, index):
                return self._items.get(int(index))

        class DummySettings(SettingsPanelMixin):
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _collect_vps_deploy_form_data = SettingsPanelMixin._collect_vps_deploy_form_data
            _combo_current_data_value = SettingsPanelMixin._combo_current_data_value
            _save_vps_deploy_preferences = SettingsPanelMixin._save_vps_deploy_preferences

            def __init__(self):
                self.settings_vps_deploy_source_combo = BrokenCombo(1, {0: "upload", 1: "git"})
                self.settings_vps_dep_install_mode_combo = BrokenCombo(1, {0: "offline", 1: "global"})
                self.settings_vps_local_agent_dir_edit = DummyLineEdit("")
                self.settings_vps_repo_url_edit = DummyLineEdit("https://example.com/repo.git")
                self.settings_vps_remote_dir_edit = DummyLineEdit("/srv/genericagent")
                self.settings_vps_pip_mirror_edit = DummyLineEdit("")
                self.settings_vps_upload_excludes_edit = DummyLineEdit("")
                self.settings_vps_username_edit = DummyLineEdit("root")

            def _persist_current_vps_profile_from_form(self, *, validate_pair=False, silent=True):
                return None

            def _current_vps_profile(self):
                return None

        dummy = DummySettings()
        payload = dummy._save_vps_deploy_preferences()
        self.assertEqual(payload["source"], "git")
        self.assertEqual(payload["repo_url"], "https://example.com/repo.git")
        self.assertEqual(payload["remote_dir"], "/srv/genericagent")

    def test_theme_target_size_prefers_screen_available_geometry_over_window_size(self):
        class DummyRect:
            def width(self):
                return 1920

            def height(self):
                return 1080

        class DummyScreen:
            def availableGeometry(self):
                return DummyRect()

        class DummySettings(SettingsPanelMixin):
            _theme_target_size = SettingsPanelMixin._theme_target_size

            def width(self):
                return 1200

            def height(self):
                return 700

            def screen(self):
                return DummyScreen()

        dummy = DummySettings()
        size = dummy._theme_target_size()
        self.assertEqual((size.width(), size.height()), (1920, 1080))

    def test_theme_target_size_falls_back_to_window_size_without_screen(self):
        class DummySettings(SettingsPanelMixin):
            _theme_target_size = SettingsPanelMixin._theme_target_size

            def width(self):
                return 1280

            def height(self):
                return 760

            def screen(self):
                return None

        dummy = DummySettings()
        with mock.patch.object(launcher_window.QApplication, "instance", return_value=None):
            size = dummy._theme_target_size()
        self.assertEqual((size.width(), size.height()), (1280, 760))

    def test_save_theme_preferences_respects_cleared_background_images(self):
        class DummyLineEdit:
            def __init__(self, text=""):
                self._text = str(text)

            def clear(self):
                self._text = ""

            def text(self):
                return self._text

            def setText(self, value):
                self._text = str(value)

        class DummyLabel:
            def __init__(self):
                self.text_value = ""

            def setText(self, value):
                self.text_value = str(value)

        class DummyCheckBox:
            def __init__(self, checked):
                self._checked = bool(checked)

            def isChecked(self):
                return self._checked

        class DummyCombo:
            def __init__(self, value, label=""):
                self._value = value
                self._label = label or str(value)

            def currentIndex(self):
                return 0

            def itemData(self, _index):
                return self._value

            def currentText(self):
                return self._label

        class DummySlider:
            def __init__(self, value):
                self._value = int(value)

            def value(self):
                return self._value

        class DummySettings(SettingsPanelMixin):
            _clear_theme_background_image = SettingsPanelMixin._clear_theme_background_image
            _clear_theme_floating_background_image = SettingsPanelMixin._clear_theme_floating_background_image
            _save_theme_preferences = SettingsPanelMixin._save_theme_preferences
            _normalize_theme_crop_data = SettingsPanelMixin._normalize_theme_crop_data

            def __init__(self):
                self.cfg = {
                    "appearance_mode": "dark",
                    "theme_bg_preset": "image",
                    "theme_bg_image": "assets/old-main.png",
                    "theme_bg_source": "assets/old-main-source.png",
                    "theme_bg_crop": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
                    "theme_floating_bg_preset": "image",
                    "theme_floating_bg_image": "assets/old-floating.png",
                    "theme_floating_bg_source": "assets/old-floating-source.png",
                    "theme_floating_bg_crop": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6},
                }
                self.settings_theme_font_combo = DummyCombo("", "默认")
                self.settings_theme_weight_combo = DummyCombo("400", "400")
                self.settings_theme_size_combo = DummyCombo("14", "14")
                self.settings_theme_visual_combo = DummyCombo("graphite", "石墨")
                self.settings_theme_bg_combo = DummyCombo("image", "图片背景")
                self.settings_theme_bg_mode_combo = DummyCombo("center", "居中裁切")
                self.settings_theme_fade_slider = DummySlider(18)
                self.settings_theme_floating_bg_combo = DummyCombo("image", "图片背景")
                self.settings_theme_floating_bg_mode_combo = DummyCombo("center", "居中裁切")
                self.settings_theme_floating_fade_slider = DummySlider(18)
                self.settings_theme_auto_jump_latest = DummyCheckBox(False)
                self.settings_theme_bg_image_path = DummyLineEdit("D:/demo/main.png")
                self.settings_theme_floating_bg_image_path = DummyLineEdit("D:/demo/floating.png")
                self.settings_theme_notice = DummyLabel()
                self._theme_bg_source_selected_path = "D:/demo/main.png"
                self._theme_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
                self._theme_bg_force_clear = False
                self._theme_floating_bg_source_selected_path = "D:/demo/floating.png"
                self._theme_floating_bg_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
                self._theme_floating_bg_force_clear = False
                self._theme_user_avatar_source_selected_path = ""
                self._theme_user_avatar_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
                self._theme_user_avatar_force_clear = False
                self._theme_ai_avatar_source_selected_path = ""
                self._theme_ai_avatar_crop_selected = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
                self._theme_ai_avatar_force_clear = False
                self.saved_modes = []
                self.reload_calls = 0
                self.statuses = []

            def _normalize_appearance_mode(self, mode):
                return "light" if str(mode or "").strip().lower() == "light" else "dark"

            def _apply_theme(self, mode):
                self.saved_modes.append(str(mode))

            def _reload_theme_panel(self):
                self.reload_calls += 1

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySettings()
        dummy._clear_theme_background_image()
        dummy._clear_theme_floating_background_image()

        saved = []
        with mock.patch.object(lz, "save_config", side_effect=lambda cfg: saved.append(dict(cfg))), mock.patch.object(
            settings_panel.QMessageBox, "warning"
        ) as warning_box:
            dummy._save_theme_preferences()

        self.assertEqual(dummy.cfg["theme_bg_image"], "")
        self.assertEqual(dummy.cfg["theme_bg_source"], "")
        self.assertEqual(dummy.cfg["theme_bg_crop"], {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        self.assertEqual(dummy.cfg["theme_floating_bg_image"], "")
        self.assertEqual(dummy.cfg["theme_floating_bg_source"], "")
        self.assertEqual(dummy.cfg["theme_floating_bg_crop"], {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        self.assertFalse(dummy.cfg["theme_chat_auto_jump_latest"])
        self.assertEqual(dummy.saved_modes, ["dark"])
        self.assertEqual(dummy.reload_calls, 1)
        self.assertEqual(dummy.statuses, ["主题设置已保存。"])
        self.assertTrue(saved)
        self.assertIn("主题已保存", dummy.settings_theme_notice.text_value)
        warning_box.assert_not_called()

    def test_reload_theme_panel_defaults_auto_jump_checkbox_on(self):
        class DummyCheckBox:
            def __init__(self):
                self.checked = None

            def setChecked(self, value):
                self.checked = bool(value)

        class DummyCombo:
            def __init__(self):
                self.index = 0
                self.style = ""

            def objectName(self):
                return ""

            def setStyleSheet(self, value):
                self.style = str(value)

            def hidePopup(self):
                return None

            def count(self):
                return 0

            def itemData(self, _index):
                return ""

            def setCurrentIndex(self, index):
                self.index = int(index)

            def blockSignals(self, _blocked):
                return None

            def findData(self, _value):
                return -1

            def itemText(self, _index):
                return ""

        class DummySlider:
            def __init__(self):
                self.value = None

            def blockSignals(self, _blocked):
                return None

            def setValue(self, value):
                self.value = int(value)

        class DummyLineEdit:
            def __init__(self):
                self.text_value = ""

            def setText(self, value):
                self.text_value = str(value)

        class DummyLabel:
            def __init__(self):
                self.text_value = ""

            def setText(self, value):
                self.text_value = str(value)

        class DummySettings(SettingsPanelMixin):
            _reload_theme_panel = SettingsPanelMixin._reload_theme_panel
            _normalize_theme_crop_data = SettingsPanelMixin._normalize_theme_crop_data
            _select_combo_data = SettingsPanelMixin._select_combo_data
            _apply_theme_combo_style = SettingsPanelMixin._apply_theme_combo_style

            def __init__(self):
                self.cfg = {}
                self.settings_theme_notice = DummyLabel()
                self.settings_theme_auto_jump_latest = DummyCheckBox()
                self.settings_theme_font_combo = DummyCombo()
                self.settings_theme_weight_combo = DummyCombo()
                self.settings_theme_size_combo = DummyCombo()
                self.settings_theme_visual_combo = DummyCombo()
                self.settings_theme_bg_combo = DummyCombo()
                self.settings_theme_bg_mode_combo = DummyCombo()
                self.settings_theme_fade_slider = DummySlider()
                self.settings_theme_floating_bg_combo = DummyCombo()
                self.settings_theme_floating_bg_mode_combo = DummyCombo()
                self.settings_theme_floating_fade_slider = DummySlider()
                self.settings_theme_bg_image_path = DummyLineEdit()
                self.settings_theme_floating_bg_image_path = DummyLineEdit()
                self.settings_theme_user_avatar_path = DummyLineEdit()
                self.settings_theme_ai_avatar_path = DummyLineEdit()

            def _ensure_theme_font_options(self):
                return None

            def _theme_combo_style(self):
                return ""

            def _dismiss_combo_popup(self, _combo):
                return None

            def _on_theme_fade_changed(self, _value):
                return None

            def _on_theme_floating_fade_changed(self, _value):
                return None

        dummy = DummySettings()
        dummy._reload_theme_panel()

        self.assertTrue(dummy.settings_theme_auto_jump_latest.checked)

    def test_on_vps_deploy_source_changed_refreshes_buttons_and_honors_item_data_fallback(self):
        class DummyWidget:
            def __init__(self):
                self.enabled = None

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

        class BrokenCombo:
            def __init__(self, index, items):
                self._index = int(index)
                self._items = dict(items)

            def currentData(self):
                return None

            def currentIndex(self):
                return self._index

            def itemData(self, index):
                return self._items.get(int(index))

        class DummySettings(SettingsPanelMixin):
            _on_vps_deploy_source_changed = SettingsPanelMixin._on_vps_deploy_source_changed
            _combo_current_data_value = SettingsPanelMixin._combo_current_data_value

            def __init__(self):
                self.settings_vps_deploy_source_combo = BrokenCombo(1, {0: "upload", 1: "git"})
                self.settings_vps_local_agent_dir_edit = DummyWidget()
                self.settings_vps_local_agent_browse_btn = DummyWidget()
                self.settings_vps_repo_url_edit = DummyWidget()
                self.refresh_calls = 0

            def _refresh_vps_action_buttons(self):
                self.refresh_calls += 1

        dummy = DummySettings()
        dummy._on_vps_deploy_source_changed()
        self.assertFalse(dummy.settings_vps_local_agent_dir_edit.enabled)
        self.assertFalse(dummy.settings_vps_local_agent_browse_btn.enabled)
        self.assertTrue(dummy.settings_vps_repo_url_edit.enabled)
        self.assertEqual(dummy.refresh_calls, 1)

    def test_normalize_remote_device_strips_repeated_auto_added_docker_suffixes_from_name(self):
        class DummySidebar(SidebarSessionsMixin):
            _normalize_remote_device = SidebarSessionsMixin._normalize_remote_device
            _normalize_remote_auto_ssh_value = SidebarSessionsMixin._normalize_remote_auto_ssh_value

        dummy = DummySidebar()
        device = dummy._normalize_remote_device(
            {
                "id": "srv-1",
                "name": "我的服务器（Docker）（Docker）（Docker）",
                "host": "10.0.0.8",
                "username": "root",
                "password": "pw",
                "docker_container": "ga-prod",
                "docker_agent_dir": "/opt/agant",
            }
        )
        self.assertEqual(device["name"], "我的服务器")
        self.assertEqual(device["agent_mode"], "docker")

    def test_remote_devices_write_back_cleaned_docker_names_and_sidebar_rows_use_clean_name(self):
        class DummySidebar(SidebarSessionsMixin):
            _normalize_remote_device = SidebarSessionsMixin._normalize_remote_device
            _normalize_remote_auto_ssh_value = SidebarSessionsMixin._normalize_remote_auto_ssh_value
            _remote_device_name_needs_cleanup = SidebarSessionsMixin._remote_device_name_needs_cleanup
            _remote_devices = SidebarSessionsMixin._remote_devices
            _remote_device_auto_ssh_enabled = SidebarSessionsMixin._remote_device_auto_ssh_enabled
            _sidebar_device_rows = SidebarSessionsMixin._sidebar_device_rows

            def __init__(self):
                self.cfg = {
                    "remote_devices": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器（Docker）（Docker）",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "docker_container": "ga-prod",
                            "docker_agent_dir": "/opt/agant",
                            "auto_ssh": True,
                        }
                    ]
                }
                self.saved_rows = None

            def _save_remote_devices(self, rows):
                self.saved_rows = [dict(item) for item in rows]
                self.cfg["remote_devices"] = [dict(item) for item in rows]

            def _fallback_remote_device_from_vps(self):
                return None

        dummy = DummySidebar()
        devices = dummy._remote_devices()
        rows = dummy._sidebar_device_rows()
        self.assertIsNotNone(dummy.saved_rows)
        self.assertEqual(devices[0]["name"], "我的服务器")
        self.assertEqual(dummy.saved_rows[0]["name"], "我的服务器")
        self.assertEqual(rows[0]["device_name"], "我的服务器")

    def test_remote_devices_cleanup_writeback_normalizes_legacy_docker_takeover_to_ssh_path(self):
        class DummySidebar(SidebarSessionsMixin):
            _normalize_remote_device = SidebarSessionsMixin._normalize_remote_device
            _normalize_remote_auto_ssh_value = SidebarSessionsMixin._normalize_remote_auto_ssh_value
            _remote_device_name_needs_cleanup = SidebarSessionsMixin._remote_device_name_needs_cleanup
            _remote_devices = SidebarSessionsMixin._remote_devices

            def __init__(self):
                self.cfg = {
                    "remote_devices": [
                        {
                            "id": "srv-1",
                            "name": "我的服务器（Docker）（Docker）",
                            "host": "10.0.0.8",
                            "username": "root",
                            "password": "pw",
                            "agent_mode": "docker",
                            "remote_mode": "docker_container",
                            "docker_container": "ga-prod",
                            "docker_agent_dir": "/opt/agant",
                            "python_cmd": "python",
                            "auto_ssh": True,
                        }
                    ]
                }

            def _save_remote_devices(self, rows):
                self.cfg["remote_devices"] = [dict(item) for item in rows]

            def _fallback_remote_device_from_vps(self):
                return None

        class DummySettings(SettingsPanelMixin):
            _strip_auto_docker_name_suffix = SettingsPanelMixin._strip_auto_docker_name_suffix
            _vps_profile_uses_docker_takeover = SettingsPanelMixin._vps_profile_uses_docker_takeover
            _vps_profile_name_needs_cleanup = SettingsPanelMixin._vps_profile_name_needs_cleanup
            _normalize_vps_connection_cfg = SettingsPanelMixin._normalize_vps_connection_cfg
            _normalize_vps_deploy_cfg = SettingsPanelMixin._normalize_vps_deploy_cfg
            _normalize_vps_takeover_cfg = SettingsPanelMixin._normalize_vps_takeover_cfg
            _normalize_vps_profile = SettingsPanelMixin._normalize_vps_profile
            _settings_normalize_remote_auto_ssh_value = SettingsPanelMixin._settings_normalize_remote_auto_ssh_value
            _vps_profiles = SettingsPanelMixin._vps_profiles

            def __init__(self, cfg):
                self.cfg = cfg

            def _make_vps_profile_id(self, seed=""):
                return str(seed or "generated")

        sidebar_dummy = DummySidebar()
        devices = sidebar_dummy._remote_devices()
        settings_dummy = DummySettings(sidebar_dummy.cfg)
        with mock.patch.object(settings_panel.lz, "save_config"):
            profiles = settings_dummy._vps_profiles()

        self.assertEqual(devices[0]["remote_mode"], "docker_container")
        self.assertEqual(sidebar_dummy.cfg["remote_devices"][0]["remote_mode"], "ssh")
        self.assertEqual(profiles[0]["remote_mode"], "ssh")
        self.assertEqual(profiles[0]["takeover_mode"], "path")
        self.assertEqual(profiles[0]["takeover_agent_dir"], "/opt/agant")
        self.assertEqual(profiles[0]["docker_takeover_container"], "")
        self.assertEqual(profiles[0]["docker_takeover_agent_dir"], "")

    def test_sync_draft_from_floating_propagates_empty_text_back_to_main_editor(self):
        class DummyEditor:
            def __init__(self, text=""):
                self._text = str(text)

            def toPlainText(self):
                return self._text

            def setPlainText(self, text):
                self._text = str(text)

        class DummyFloating:
            def __init__(self, text=""):
                self.input_box = DummyEditor(text)

        class DummyHost:
            def __init__(self):
                self._floating_chat_window = DummyFloating("")
                self.input_box = DummyEditor("stale draft")

        dummy = DummyHost()
        launcher_window.QtChatWindow._sync_draft_from_floating(dummy)
        self.assertEqual(dummy.input_box.toPlainText(), "")

    def test_refresh_composer_enabled_explains_channel_remote_and_busy_states(self):
        class DummyInput:
            def __init__(self):
                self.read_only = None
                self.placeholder = ""
                self.tooltip = ""

            def setReadOnly(self, value):
                self.read_only = bool(value)

            def setPlaceholderText(self, text):
                self.placeholder = str(text)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummyWidget:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""

            def setEnabled(self, enabled):
                self.enabled = bool(enabled)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummySession(SessionShellMixin):
            _refresh_composer_enabled = SessionShellMixin._refresh_composer_enabled
            _apply_composer_widget_state = SessionShellMixin._apply_composer_widget_state
            _composer_send_disabled_reason = SessionShellMixin._composer_send_disabled_reason
            _composer_stop_disabled_reason = SessionShellMixin._composer_stop_disabled_reason
            _composer_llm_disabled_reason = SessionShellMixin._composer_llm_disabled_reason
            _composer_reasoning_effort_disabled_reason = SessionShellMixin._composer_reasoning_effort_disabled_reason

            def __init__(self, *, channel_process=False, remote=False, busy=False, abort_requested=False, llms=None):
                self._channel_process = bool(channel_process)
                self._remote = bool(remote)
                self._busy = bool(busy)
                self._abort_requested = bool(abort_requested)
                self.llms = list(llms or [])
                self.input_box = DummyInput()
                self.send_btn = DummyWidget()
                self.stop_btn = DummyWidget()
                self.llm_combo = DummyWidget()
                self.reasoning_effort_combo = DummyWidget()
                self.sync_calls = 0
                self.reasoning_sync_calls = 0
                self.refresh_calls = 0

            def _is_channel_process_session(self, session=None):
                return self._channel_process

            def _is_remote_session(self):
                return self._remote

            def _sync_floating_llm_combo(self):
                self.sync_calls += 1

            def _sync_floating_reasoning_effort_combo(self):
                self.reasoning_sync_calls += 1

            def _refresh_floating_chat_window(self):
                self.refresh_calls += 1

        channel_dummy = DummySession(channel_process=True, llms=[{"idx": 0}])
        channel_dummy._refresh_composer_enabled()
        self.assertTrue(channel_dummy.input_box.read_only)
        self.assertIn("不能在这里继续发送消息", channel_dummy.input_box.placeholder)
        self.assertFalse(channel_dummy.send_btn.enabled)
        self.assertEqual(channel_dummy.send_btn.tooltip, "渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息。")
        self.assertFalse(channel_dummy.stop_btn.enabled)
        self.assertEqual(channel_dummy.stop_btn.tooltip, "渠道进程会话仅用于回顾日志与快照，不能在这里停止任务。")
        self.assertFalse(channel_dummy.llm_combo.enabled)
        self.assertEqual(channel_dummy.llm_combo.tooltip, "渠道进程会话仅支持查看日志，不能切换模型。")
        self.assertFalse(channel_dummy.reasoning_effort_combo.enabled)
        self.assertEqual(channel_dummy.reasoning_effort_combo.tooltip, "渠道进程会话仅支持查看日志，不能切换思考强度。")

        remote_busy_dummy = DummySession(remote=True, busy=True, llms=[{"idx": 0}])
        remote_busy_dummy._refresh_composer_enabled()
        self.assertFalse(remote_busy_dummy.send_btn.enabled)
        self.assertEqual(remote_busy_dummy.send_btn.tooltip, "当前正在等待模型回复，请稍候或先停止当前任务。")
        self.assertFalse(remote_busy_dummy.stop_btn.enabled)
        self.assertEqual(remote_busy_dummy.stop_btn.tooltip, "当前会话在远程设备执行，这里不支持直接停止远端任务。")
        self.assertTrue(remote_busy_dummy.llm_combo.enabled)
        self.assertEqual(remote_busy_dummy.llm_combo.tooltip, "切换当前会话使用的模型。")
        self.assertTrue(remote_busy_dummy.reasoning_effort_combo.enabled)
        self.assertEqual(remote_busy_dummy.reasoning_effort_combo.tooltip, "切换当前会话使用的思考强度。")
        self.assertIn("远程设备执行", remote_busy_dummy.input_box.placeholder)

        local_idle_dummy = DummySession(remote=False, busy=False, llms=[])
        local_idle_dummy._refresh_composer_enabled()
        self.assertTrue(local_idle_dummy.send_btn.enabled)
        self.assertEqual(local_idle_dummy.send_btn.tooltip, "发送当前输入内容。")
        self.assertFalse(local_idle_dummy.stop_btn.enabled)
        self.assertEqual(local_idle_dummy.stop_btn.tooltip, "当前没有正在执行的本地回复任务。")
        self.assertFalse(local_idle_dummy.llm_combo.enabled)
        self.assertEqual(local_idle_dummy.llm_combo.tooltip, "当前还没有可用的 LLM 配置。")
        self.assertFalse(local_idle_dummy.reasoning_effort_combo.enabled)
        self.assertEqual(local_idle_dummy.reasoning_effort_combo.tooltip, "当前还没有可用的 LLM 配置。")

    def test_session_info_tooltip_includes_running_subagent_count(self):
        class DummyLabel:
            def __init__(self, text=""):
                self._text = str(text)

            def text(self):
                return self._text

        class DummySession(SessionShellMixin):
            _info_tooltip_text = SessionShellMixin._info_tooltip_text
            _refresh_subagent_runtime_state = SessionShellMixin._refresh_subagent_runtime_state
            _count_running_subagents = SessionShellMixin._count_running_subagents
            _iter_local_subagent_processes = SessionShellMixin._iter_local_subagent_processes
            _subagent_runtime_summary_text = SessionShellMixin._subagent_runtime_summary_text

            def __init__(self):
                self.agent_dir = r"E:\\GenericAgent"
                self.session_mode_label = DummyLabel("当前会话：本地")
                self.status_label = DummyLabel("状态：空闲")
                self.session_token_tree_label = DummyLabel("Tokens：12 / 34")
                self._subagent_runtime_count = 0
                self._subagent_runtime_scan_ts = 0.0
                self.tooltip_refresh_calls = 0
                self.icon_refresh_calls = 0

            def _iter_local_channel_processes(self):
                return [
                    {"pid": 1001, "cmdline": r'"python" "E:\\GenericAgent\\agentmain.py" --task child-1', "cwd": r"E:\\GenericAgent", "cwd_real": ""},
                    {"pid": 1004, "cmdline": r'"python" "E:\\GenericAgent\\agentmain.py" --task=child-2 --verbose --nobg', "cwd": r"E:\\GenericAgent", "cwd_real": ""},
                    {"pid": 1002, "cmdline": r'"python" "E:\\GenericAgent\\agentmain.py" --serve', "cwd": r"E:\\GenericAgent", "cwd_real": ""},
                    {"pid": 1003, "cmdline": r'"python" "E:\\Elsewhere\\agentmain.py" --task child-2', "cwd": r"E:\\Elsewhere", "cwd_real": ""},
                ]

            def _refresh_info_tooltip(self):
                self.tooltip_refresh_calls += 1

            def _refresh_info_button_icon(self):
                self.icon_refresh_calls += 1

        dummy = DummySession()
        with mock.patch.object(chat_common, "process_cmdline_matches_agent_script", autospec=True) as matcher:
            matcher.side_effect = (
                lambda cmdline, *, agent_dir, script_rel, cwd="", agent_dir_real="", cwd_real="": "GenericAgent" in str(cmdline)
            )
            with mock.patch("qt_chat_parts.session_shell.process_cmdline_matches_agent_script", side_effect=matcher.side_effect), mock.patch(
                "qt_chat_parts.session_shell.os.getpid", return_value=9999
            ):
                dummy._refresh_subagent_runtime_state()

        self.assertEqual(dummy._subagent_runtime_count, 2)
        self.assertEqual(dummy.tooltip_refresh_calls, 2)
        self.assertEqual(dummy.icon_refresh_calls, 2)
        self.assertIn("后台子代理：2", dummy._info_tooltip_text())

    def test_remote_subagent_count_uses_current_device_agent_dir(self):
        class DummyClient:
            def close(self):
                return None

        class DummySession(SessionShellMixin):
            _count_remote_running_subagents = SessionShellMixin._count_remote_running_subagents
            _parse_subagent_process_rows = SessionShellMixin._parse_subagent_process_rows

            def __init__(self):
                self.current_session = {"device_scope": "remote", "device_id": "box-1"}
                self.commands = []

            def _remote_device_payload(self, _session):
                return (
                    {
                        "id": "box-1",
                        "username": "root",
                        "agent_mode": "host",
                        "agent_dir": "/srv/agant",
                    },
                    {"host": "10.0.0.8", "username": "root", "port": 22},
                )

            def _open_vps_ssh_client(self, _payload, timeout=8):
                self.commands.append(("open", int(timeout)))
                return DummyClient(), "", "", False

            def _vps_exec_remote(self, _client, cmd, timeout=20):
                self.commands.append((str(cmd), int(timeout)))
                return (
                    0,
                    "\n".join(
                        [
                            "101\t/srv/agant\tpython agentmain.py --task child-a --nobg",
                            "102\t/elsewhere\tpython agentmain.py --task child-b",
                            "103\t/srv/agant\tpython agentmain.py --serve",
                            "104\t/srv/agant\tpython agentmain.py --task=child-c --verbose",
                        ]
                    ),
                    "",
                )

        dummy = DummySession()
        count = dummy._count_remote_running_subagents()
        self.assertEqual(count, 2)
        self.assertEqual(dummy.commands[0], ("open", 8))
        self.assertIn("ps -eo pid=,args=", dummy.commands[1][0])

    def test_subagent_runtime_event_ignores_stale_device_target(self):
        class DummyBridge(BridgeRuntimeMixin):
            _handle_event = BridgeRuntimeMixin._handle_event

            def __init__(self):
                self._subagent_runtime_refresh_inflight_key = "remote:box-1"
                self.applied = []

            def _apply_subagent_runtime_count(self, count, *, target_key="", scanned_at=None):
                current_key = self._subagent_runtime_target_key()
                if target_key and current_key and target_key != current_key:
                    return
                self.applied.append((int(count or 0), str(target_key), float(scanned_at or 0.0)))

            def _subagent_runtime_target_key(self):
                return "remote:box-2"

        dummy = DummyBridge()
        dummy._handle_event({"event": "subagent_runtime_count", "target_key": "remote:box-1", "count": 3, "scanned_at": 12.5})
        self.assertEqual(dummy.applied, [])
        self.assertEqual(dummy._subagent_runtime_refresh_inflight_key, "")

        dummy._subagent_runtime_refresh_inflight_key = "remote:box-2"
        dummy._handle_event({"event": "subagent_runtime_count", "target_key": "remote:box-2", "count": 4, "scanned_at": 18.0})
        self.assertEqual(dummy.applied, [(4, "remote:box-2", 18.0)])
        self.assertEqual(dummy._subagent_runtime_refresh_inflight_key, "")

    def test_info_button_icon_switches_to_spinner_when_subagents_are_running(self):
        class DummyButton:
            def __init__(self):
                self.icon = None
                self.icon_size = None

            def setIcon(self, icon):
                self.icon = icon

            def setIconSize(self, size):
                self.icon_size = size

        dummy = types.SimpleNamespace(info_btn=DummyButton(), _subagent_runtime_count=2, _subagent_spinner_phase=3)
        with mock.patch.object(launcher_window, "_rotated_svg_icon", return_value="spinner"), mock.patch.object(
            launcher_window, "_svg_icon", return_value="info"
        ):
            launcher_window.QtChatWindow._refresh_info_button_icon(dummy)
        self.assertEqual(dummy.info_btn.icon, "spinner")
        self.assertEqual(dummy.info_btn.icon_size, launcher_window.QSize(14, 14))

        dummy._subagent_runtime_count = 0
        with mock.patch.object(launcher_window, "_rotated_svg_icon", return_value="spinner"), mock.patch.object(
            launcher_window, "_svg_icon", return_value="info"
        ):
            launcher_window.QtChatWindow._refresh_info_button_icon(dummy)
        self.assertEqual(dummy.info_btn.icon, "info")

    def test_sync_draft_to_floating_force_uses_main_editor_as_source_of_truth(self):
        class DummyEditor:
            def __init__(self, text=""):
                self._text = str(text)

            def toPlainText(self):
                return self._text

            def setPlainText(self, text):
                self._text = str(text)

        class DummyFloating:
            def __init__(self, text=""):
                self.input_box = DummyEditor(text)

        class DummyHost:
            def __init__(self):
                self._floating_chat_window = DummyFloating("stale floating draft")
                self.input_box = DummyEditor("")

        dummy = DummyHost()
        launcher_window.QtChatWindow._sync_draft_to_floating(dummy, force=True)
        self.assertEqual(dummy._floating_chat_window.input_box.toPlainText(), "")

    def test_floating_window_clamp_uses_target_screen_geometry_for_saved_position(self):
        class DummyScreen:
            def __init__(self, rect):
                self._rect = rect

            def availableGeometry(self):
                return self._rect

        class DummyFloating:
            _available_geometry_for_target = launcher_window.FloatingOrbWindow._available_geometry_for_target
            _clamp_pos = launcher_window.FloatingOrbWindow._clamp_pos

            def __init__(self, fallback_screen):
                self._fallback_screen = fallback_screen

            def _best_screen_for_window(self):
                return self._fallback_screen

            def geometry(self):
                return launcher_window.QRect(0, 0, 300, 300)

        primary = DummyScreen(launcher_window.QRect(0, 0, 1920, 1080))
        secondary = DummyScreen(launcher_window.QRect(1920, 0, 1440, 900))
        dummy = DummyFloating(primary)

        def fake_screen_at(point):
            return secondary if int(point.x()) >= 1920 else primary

        with mock.patch.object(launcher_window.QGuiApplication, "screenAt", side_effect=fake_screen_at):
            clamped = dummy._clamp_pos(launcher_window.QPoint(2500, 120), launcher_window.QSize(56, 56))

        self.assertGreaterEqual(clamped.x(), 1920 + 12)
        self.assertLessEqual(clamped.x(), (1920 + 1440) - 56 - 12)

    def test_floating_expand_panel_focuses_input_when_editable(self):
        class DummyPanel:
            def show(self):
                return None

        class DummyBall:
            def show(self):
                return None

        class DummyEditor:
            def __init__(self):
                self.focus_calls = []

            def isReadOnly(self):
                return False

            def setFocus(self, reason):
                self.focus_calls.append(reason)

        class DummyFloating:
            expand_panel = launcher_window.FloatingOrbWindow.expand_panel

            def __init__(self):
                self._expanded = False
                self.panel = DummyPanel()
                self.ball_btn = DummyBall()
                self._expanded_size = launcher_window.QSize(480, 760)
                self.input_box = DummyEditor()
                self.calls = []

            def _apply_window_size(self, size):
                self.calls.append(("size", size.width(), size.height()))

            def _place_ball(self):
                self.calls.append("place_ball")

            def _apply_native_window_style(self):
                self.calls.append("native_style")

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

            def _scroll_to_bottom(self):
                self.calls.append("scroll_bottom")

            def update(self):
                self.calls.append("update")

        dummy = DummyFloating()
        with mock.patch.object(launcher_window.QTimer, "singleShot", side_effect=lambda _ms, cb: cb()):
            dummy.expand_panel()

        self.assertTrue(dummy._expanded)
        self.assertTrue(dummy.input_box.focus_calls)

    def test_restore_from_tray_mode_focuses_main_input_when_editable(self):
        class DummyEditor:
            def __init__(self):
                self.focus_calls = []

            def setFocus(self, reason):
                self.focus_calls.append(reason)

        class DummyFloating:
            def hide(self):
                return None

        class DummyHost:
            def __init__(self):
                self._tray_mode_active = True
                self._tray_restore_to_fullscreen = False
                self._floating_chat_window = DummyFloating()
                self.input_box = DummyEditor()
                self.calls = []

            def isVisible(self):
                return False

            def _sync_draft_from_floating(self):
                self.calls.append("sync_from_floating")

            def showNormal(self):
                self.calls.append("show_normal")

            def showFullScreen(self):
                self.calls.append("show_fullscreen")

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

            def _show_chat_page(self):
                self.calls.append("show_chat_page")

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

            def _is_channel_process_session(self):
                return False

        dummy = DummyHost()
        with mock.patch.object(launcher_window.QTimer, "singleShot", side_effect=lambda _ms, cb: cb()):
            launcher_window.QtChatWindow._restore_from_tray_mode(dummy)

        self.assertFalse(dummy._tray_mode_active)
        self.assertEqual(dummy.input_box.focus_calls, [launcher_window.Qt.OtherFocusReason])

    def test_show_floating_chat_window_only_preserves_maximized_restore_state(self):
        class DummyHost:
            _show_floating_chat_window_only = launcher_window.QtChatWindow._show_floating_chat_window_only

            def __init__(self):
                self._tray_restore_to_fullscreen = False
                self._tray_restore_to_maximized = False
                self._tray_mode_active = False
                self.calls = []
                self._visible = True

            def isFullScreen(self):
                return False

            def isMaximized(self):
                return True

            def isVisible(self):
                return self._visible

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

            def hide(self):
                self._visible = False
                self.calls.append("hide")

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

        dummy = DummyHost()
        launcher_window.QtChatWindow._show_floating_chat_window_only(dummy)

        self.assertFalse(dummy._tray_restore_to_fullscreen)
        self.assertTrue(dummy._tray_restore_to_maximized)
        self.assertTrue(dummy._tray_mode_active)
        self.assertEqual(dummy.calls, ["show_floating", "hide", "refresh_floating"])

    def test_show_floating_chat_window_falls_back_without_tray_on_macos(self):
        class DummyFloating:
            def __init__(self):
                self.calls = []

            def show(self):
                self.calls.append("show")

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

        class DummyHost:
            _show_floating_chat_window = launcher_window.QtChatWindow._show_floating_chat_window

            def __init__(self):
                self._tray_mode_active = True
                self.calls = []
                self.statuses = []
                self.win = DummyFloating()

            def _ensure_launcher_tray_icon(self):
                return None

            def _ensure_floating_default_session(self):
                self.calls.append("ensure_default_session")

            def _ensure_floating_chat_window(self):
                self.calls.append("ensure_floating_window")
                return self.win

            def _sync_draft_to_floating(self, *, force=False):
                self.calls.append(("sync_draft", bool(force)))

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _focus_floating_input_if_possible(self):
                self.calls.append("focus_floating")

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", True), mock.patch.object(
            launcher_window.QMessageBox, "warning"
        ) as warning_box:
            launcher_window.QtChatWindow._show_floating_chat_window(dummy)

        self.assertFalse(dummy._tray_mode_active)
        self.assertEqual(
            dummy.calls,
            [
                "ensure_default_session",
                "ensure_floating_window",
                ("sync_draft", True),
                "refresh_floating",
                "focus_floating",
                "refresh_tray",
            ],
        )
        self.assertEqual(dummy.win.calls, ["show", "raise", "activate"])
        self.assertEqual(dummy.statuses, ["当前系统未提供托盘图标，已直接打开悬浮窗。"])
        warning_box.assert_not_called()

    def test_show_floating_chat_window_keeps_tray_mode_inactive_when_main_window_visible(self):
        class DummyTray:
            def __init__(self):
                self.calls = []

            def show(self):
                self.calls.append("show")

        class DummyFloating:
            def __init__(self):
                self.calls = []

            def show(self):
                self.calls.append("show")

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

        class DummyHost:
            _show_floating_chat_window = launcher_window.QtChatWindow._show_floating_chat_window

            def __init__(self):
                self._tray_mode_active = False
                self.calls = []
                self.tray = DummyTray()
                self.win = DummyFloating()

            def isVisible(self):
                return True

            def _ensure_launcher_tray_icon(self):
                return self.tray

            def _ensure_floating_default_session(self):
                self.calls.append("ensure_default_session")

            def _ensure_floating_chat_window(self):
                self.calls.append("ensure_floating_window")
                return self.win

            def _sync_draft_to_floating(self, *, force=False):
                self.calls.append(("sync_draft", bool(force)))

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _focus_floating_input_if_possible(self):
                self.calls.append("focus_floating")

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        launcher_window.QtChatWindow._show_floating_chat_window(dummy)

        self.assertFalse(dummy._tray_mode_active)
        self.assertEqual(dummy.tray.calls, ["show"])
        self.assertEqual(dummy.win.calls, ["show", "raise", "activate"])

    def test_show_floating_chat_window_only_refreshes_status_and_tooltip_after_hiding_main_window(self):
        class DummyStatus:
            def text(self):
                return ""

        class DummyFloating:
            def __init__(self, host):
                self._host = host
                self.tooltip = ""
                self.kwargs = None

            def refresh_action_texts(self):
                self.tooltip = self._host._floating_hide_action_tooltip()

            def sync_view(self, **kwargs):
                self.kwargs = dict(kwargs)

        class DummyHost:
            _show_floating_chat_window_only = launcher_window.QtChatWindow._show_floating_chat_window_only
            _refresh_floating_chat_window = launcher_window.QtChatWindow._refresh_floating_chat_window
            _floating_default_status_text = launcher_window.QtChatWindow._floating_default_status_text
            _floating_hide_action_tooltip = launcher_window.QtChatWindow._floating_hide_action_tooltip

            def __init__(self):
                self._tray_restore_to_fullscreen = False
                self._tray_restore_to_maximized = False
                self._tray_mode_active = False
                self._visible = True
                self.current_session = {}
                self.status_label = DummyStatus()
                self._busy = False
                self._abort_requested = False
                self.calls = []
                self._floating_chat_window = DummyFloating(self)

            def isFullScreen(self):
                return False

            def isMaximized(self):
                return False

            def isVisible(self):
                return self._visible

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

            def hide(self):
                self._visible = False
                self.calls.append("hide")

            def _system_tray_available(self):
                return True

            def _is_channel_process_session(self, session=None):
                return False

            def _floating_chat_title(self):
                return "title"

            def _floating_chat_subtitle(self):
                return "subtitle"

            def _floating_chat_transcript(self):
                return "transcript"

            def _floating_chat_meta(self):
                return "meta"

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        dummy._show_floating_chat_window_only()

        self.assertTrue(dummy._tray_mode_active)
        self.assertEqual(dummy._floating_chat_window.kwargs["status"], "已隐藏主窗口，悬浮窗可继续对话。")
        self.assertEqual(dummy._floating_chat_window.tooltip, "隐藏完整界面，仅保留托盘或悬浮窗入口。")
        self.assertEqual(dummy.calls, ["show_floating", "hide", "refresh_tray"])

    def test_enter_tray_floating_mode_keeps_windows_warning_when_tray_missing(self):
        class DummyHost:
            _enter_tray_floating_mode = launcher_window.QtChatWindow._enter_tray_floating_mode

            def _ensure_launcher_tray_icon(self):
                return None

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", False), mock.patch.object(
            launcher_window.QMessageBox, "warning"
        ) as warning_box:
            launcher_window.QtChatWindow._enter_tray_floating_mode(dummy)

        warning_box.assert_called_once()

    def test_enter_tray_floating_mode_falls_back_to_floating_on_macos_without_tray(self):
        class DummyHost:
            _enter_tray_floating_mode = launcher_window.QtChatWindow._enter_tray_floating_mode

            def __init__(self):
                self.calls = []

            def _ensure_launcher_tray_icon(self):
                return None

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", True), mock.patch.object(
            launcher_window.QMessageBox, "information"
        ) as info_box, mock.patch.object(launcher_window.QMessageBox, "warning") as warning_box:
            launcher_window.QtChatWindow._enter_tray_floating_mode(dummy)

        self.assertEqual(dummy.calls, ["show_floating"])
        info_box.assert_called_once()
        warning_box.assert_not_called()

    def test_hide_floating_chat_window_keeps_tray_mode_inactive_when_main_window_visible(self):
        class DummyFloating:
            def __init__(self):
                self.calls = []

            def hide(self):
                self.calls.append("hide")

        class DummyHost:
            _hide_floating_chat_window = launcher_window.QtChatWindow._hide_floating_chat_window

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._tray_mode_active = True
                self.calls = []

            def isVisible(self):
                return True

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        launcher_window.QtChatWindow._hide_floating_chat_window(dummy)

        self.assertEqual(dummy._floating_chat_window.calls, ["hide"])
        self.assertFalse(dummy._tray_mode_active)
        self.assertEqual(dummy.calls, ["refresh_tray"])

    def test_hide_floating_chat_window_keeps_tray_mode_active_when_main_window_hidden(self):
        class DummyFloating:
            def __init__(self):
                self.calls = []

            def hide(self):
                self.calls.append("hide")

        class DummyHost:
            _hide_floating_chat_window = launcher_window.QtChatWindow._hide_floating_chat_window

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._tray_mode_active = False
                self.calls = []

            def isVisible(self):
                return False

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

        dummy = DummyHost()
        launcher_window.QtChatWindow._hide_floating_chat_window(dummy)

        self.assertEqual(dummy._floating_chat_window.calls, ["hide"])
        self.assertTrue(dummy._tray_mode_active)
        self.assertEqual(dummy.calls, ["refresh_tray"])

    def test_close_tray_helpers_clears_reply_notify_tray_and_hides_stale_reply_tray(self):
        class DummyFloating:
            def __init__(self):
                self.calls = []

            def hide(self):
                self.calls.append("hide")

            def deleteLater(self):
                self.calls.append("delete")

        class DummyTray:
            def __init__(self):
                self.calls = []

            def hide(self):
                self.calls.append("hide")

        class DummyPopup:
            def __init__(self):
                self.calls = []

            def hide(self):
                self.calls.append("hide")

            def close(self):
                self.calls.append("close")

            def deleteLater(self):
                self.calls.append("delete")

        class DummyHost:
            _close_tray_helpers = launcher_window.QtChatWindow._close_tray_helpers
            _close_reply_done_popups = launcher_window.QtChatWindow._close_reply_done_popups

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._launcher_tray_icon = DummyTray()
                self._reply_notify_tray = DummyTray()
                self._reply_done_popups = [DummyPopup()]
                self._launcher_tray_menu = object()
                self._tray_restore_main_action = object()
                self._tray_show_floating_action = object()
                self._tray_hide_floating_action = object()
                self._tray_exit_action = object()
                self._launcher_tray_signal_owner = object()
                self._tray_restore_to_fullscreen = True
                self._tray_restore_to_maximized = True

        dummy = DummyHost()
        floating = dummy._floating_chat_window
        launcher_tray = dummy._launcher_tray_icon
        reply_tray = dummy._reply_notify_tray
        popup = dummy._reply_done_popups[0]

        dummy._close_tray_helpers()

        self.assertEqual(floating.calls, ["hide", "delete"])
        self.assertEqual(launcher_tray.calls, ["hide"])
        self.assertEqual(reply_tray.calls, ["hide"])
        self.assertEqual(popup.calls, ["hide", "close", "delete"])
        self.assertIsNone(dummy._floating_chat_window)
        self.assertIsNone(dummy._launcher_tray_icon)
        self.assertIsNone(dummy._reply_notify_tray)
        self.assertEqual(dummy._reply_done_popups, [])
        self.assertIsNone(dummy._launcher_tray_menu)
        self.assertIsNone(dummy._tray_restore_main_action)
        self.assertIsNone(dummy._tray_show_floating_action)
        self.assertIsNone(dummy._tray_hide_floating_action)
        self.assertIsNone(dummy._tray_exit_action)
        self.assertIsNone(dummy._launcher_tray_signal_owner)
        self.assertFalse(dummy._tray_restore_to_fullscreen)
        self.assertFalse(dummy._tray_restore_to_maximized)

    def test_floating_hide_action_text_uses_hide_label_without_tray_on_macos(self):
        class DummyHost:
            _floating_hide_action_text = launcher_window.QtChatWindow._floating_hide_action_text
            _floating_hide_action_tooltip = launcher_window.QtChatWindow._floating_hide_action_tooltip

            def isVisible(self):
                return False

            def _system_tray_available(self):
                return False

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", True):
            self.assertEqual(dummy._floating_hide_action_text(), "隐藏悬浮窗")
            self.assertIn("主窗口会继续保留", dummy._floating_hide_action_tooltip())

    def test_floating_hide_action_text_uses_hide_label_when_main_window_visible(self):
        class DummyHost:
            _floating_hide_action_text = launcher_window.QtChatWindow._floating_hide_action_text
            _floating_hide_action_tooltip = launcher_window.QtChatWindow._floating_hide_action_tooltip

            def isVisible(self):
                return True

            def _system_tray_available(self):
                return True

        dummy = DummyHost()
        self.assertEqual(dummy._floating_hide_action_text(), "隐藏悬浮窗")
        self.assertEqual(dummy._floating_hide_action_tooltip(), "隐藏当前悬浮窗，主窗口会继续保留。")

    def test_refresh_floating_chat_window_uses_visible_main_window_status_text(self):
        class DummyStatus:
            def text(self):
                return ""

        class DummyFloating:
            def __init__(self):
                self.kwargs = None
                self.refreshed = 0

            def refresh_action_texts(self):
                self.refreshed += 1

            def sync_view(self, **kwargs):
                self.kwargs = dict(kwargs)

        class DummyHost:
            _refresh_floating_chat_window = launcher_window.QtChatWindow._refresh_floating_chat_window
            _floating_default_status_text = launcher_window.QtChatWindow._floating_default_status_text

            def __init__(self):
                self._floating_chat_window = DummyFloating()
                self._tray_mode_active = False
                self.status_label = DummyStatus()
                self.current_session = {}
                self._busy = False
                self._abort_requested = False

            def isVisible(self):
                return True

            def _is_channel_process_session(self, session=None):
                return False

            def _floating_chat_title(self):
                return "title"

            def _floating_chat_subtitle(self):
                return "subtitle"

            def _floating_chat_transcript(self):
                return "transcript"

            def _floating_chat_meta(self):
                return "meta"

            def _refresh_launcher_tray_menu(self):
                return None

        dummy = DummyHost()
        launcher_window.QtChatWindow._refresh_floating_chat_window(dummy)

        self.assertEqual(dummy._floating_chat_window.refreshed, 1)
        self.assertEqual(dummy._floating_chat_window.kwargs["status"], "主窗口仍在显示，可继续使用悬浮窗对话。")

    def test_floating_default_status_text_prefers_visible_main_window_over_tray_flag(self):
        class DummyHost:
            _floating_default_status_text = launcher_window.QtChatWindow._floating_default_status_text

            def __init__(self):
                self._tray_mode_active = True

            def isVisible(self):
                return True

        dummy = DummyHost()
        self.assertEqual(dummy._floating_default_status_text(), "主窗口仍在显示，可继续使用悬浮窗对话。")

    def test_functions_menu_floating_action_text_uses_non_tray_label_on_macos_without_tray(self):
        class DummyHost:
            _functions_menu_floating_action_text = launcher_window.QtChatWindow._functions_menu_floating_action_text
            _floating_window_visible = launcher_window.QtChatWindow._floating_window_visible

            def __init__(self):
                self._floating_chat_window = None

            def _system_tray_available(self):
                return False

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", True):
            self.assertEqual(dummy._functions_menu_floating_action_text(), "打开悬浮窗，主窗口继续保留")

    def test_functions_menu_floating_action_text_uses_focus_label_when_floating_visible_without_tray(self):
        class DummyFloating:
            def isVisible(self):
                return True

        class DummyHost:
            _functions_menu_floating_action_text = launcher_window.QtChatWindow._functions_menu_floating_action_text
            _floating_window_visible = launcher_window.QtChatWindow._floating_window_visible

            def __init__(self):
                self._floating_chat_window = DummyFloating()

            def _system_tray_available(self):
                return False

        dummy = DummyHost()
        with mock.patch.object(launcher_window.lz, "IS_MACOS", True):
            self.assertEqual(dummy._functions_menu_floating_action_text(), "聚焦悬浮窗，主窗口继续保留")

    def test_handle_functions_menu_floating_action_uses_tray_mode_when_tray_available(self):
        class DummyHost:
            _handle_functions_menu_floating_action = launcher_window.QtChatWindow._handle_functions_menu_floating_action

            def __init__(self):
                self.calls = []

            def _system_tray_available(self):
                return True

            def _enter_tray_floating_mode(self):
                self.calls.append("enter_tray")

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

        dummy = DummyHost()
        dummy._handle_functions_menu_floating_action()

        self.assertEqual(dummy.calls, ["enter_tray"])

    def test_handle_functions_menu_floating_action_opens_floating_without_tray(self):
        class DummyHost:
            _handle_functions_menu_floating_action = launcher_window.QtChatWindow._handle_functions_menu_floating_action

            def __init__(self):
                self.calls = []

            def _system_tray_available(self):
                return False

            def _enter_tray_floating_mode(self):
                self.calls.append("enter_tray")

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

        dummy = DummyHost()
        dummy._handle_functions_menu_floating_action()

        self.assertEqual(dummy.calls, ["show_floating"])

    def test_handle_functions_menu_floating_action_focuses_visible_floating_without_tray(self):
        class DummyHost:
            _handle_functions_menu_floating_action = launcher_window.QtChatWindow._handle_functions_menu_floating_action

            def __init__(self):
                self.calls = []

            def _system_tray_available(self):
                return False

            def _focus_visible_floating_chat_window(self):
                self.calls.append("focus_floating")
                return True

            def _enter_tray_floating_mode(self):
                self.calls.append("enter_tray")

            def _show_floating_chat_window(self):
                self.calls.append("show_floating")

        dummy = DummyHost()
        dummy._handle_functions_menu_floating_action()

        self.assertEqual(dummy.calls, ["focus_floating"])

    def test_new_session_from_floating_refocuses_expanded_floating_input(self):
        class DummyHost:
            def __init__(self):
                self.calls = []

            def _new_session(self):
                self.calls.append("new_session")

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _focus_floating_input_if_possible(self):
                self.calls.append("focus_floating")

        dummy = DummyHost()
        launcher_window.QtChatWindow._new_session_from_floating(dummy)

        self.assertEqual(dummy.calls, ["new_session", "refresh_floating", "focus_floating"])

    def test_floating_session_change_refocuses_expanded_input_after_switch(self):
        class DummyCombo:
            def itemData(self, index):
                return "target-session" if index == 1 else ""

        class DummyFloating:
            def __init__(self):
                self.session_combo = DummyCombo()

        class DummyHost:
            def __init__(self):
                self._busy = False
                self._floating_chat_window = DummyFloating()
                self.current_session = {"id": "current-session"}
                self._last_session_list_signature = "cached"
                self.calls = []

            def _load_session_by_id(self, sid):
                self.calls.append(("load", sid))

            def _refresh_sessions(self):
                self.calls.append("refresh_sessions")

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _focus_floating_input_if_possible(self):
                self.calls.append("focus_floating")

        dummy = DummyHost()
        launcher_window.QtChatWindow._on_floating_session_changed(dummy, 1)

        self.assertIsNone(dummy._last_session_list_signature)
        self.assertEqual(
            dummy.calls,
            [("load", "target-session"), "refresh_sessions", "refresh_floating", "focus_floating"],
        )

    def test_load_session_by_id_aligns_sidebar_context_to_loaded_session(self):
        class DummySidebar(SidebarSessionsMixin):
            _load_session_by_id = SidebarSessionsMixin._load_session_by_id
            _align_sidebar_to_session = SidebarSessionsMixin._align_sidebar_to_session

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._busy = False
                self.current_session = None
                self._selected_session_id = None
                self._sidebar_device_scope = "local"
                self._sidebar_device_id = "local"
                self._sidebar_channel_id = "launcher"
                self._sidebar_view_mode = "roots"
                self._last_session_list_signature = "cached"
                self.rendered = None

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _remote_device_by_id(self, device_id):
                return {"id": device_id, "name": "Mac Mini"}

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                return None

            def _is_channel_process_session(self, session=None):
                return False

            def _bind_session_to_current_bridge(self, session, *, preserve_session_state=False):
                return None

            def _refresh_remote_session_cache_async(self, session):
                self.remote_refresh = dict(session)

            def _set_status(self, text):
                self.status_text = str(text)

            @property
            def _bridge_ready(self):
                return False

        dummy = DummySidebar()
        payload = {"id": "sess-1", "title": "Remote session", "channel_id": "wechat"}
        with mock.patch.object(lz, "load_session", return_value=dict(payload)):
            dummy._load_session_by_id("sess-1")

        self.assertEqual(dummy._selected_session_id, "sess-1")
        self.assertEqual(dummy._sidebar_device_scope, "remote")
        self.assertEqual(dummy._sidebar_device_id, "box-1")
        self.assertEqual(dummy._sidebar_channel_id, "wechat")
        self.assertEqual(dummy._sidebar_view_mode, "sessions")
        self.assertIsNone(dummy._last_session_list_signature)
        self.assertEqual(dummy.status_text, "已载入远程会话缓存，正在后台同步；可继续发送，新内容会尝试写回远端。")

    def test_load_session_by_id_restores_snapshot_reasoning_effort_for_local_bridge(self):
        class DummySidebar(SidebarSessionsMixin, BridgeRuntimeMixin):
            _load_session_by_id = SidebarSessionsMixin._load_session_by_id
            _session_reasoning_effort_payload = BridgeRuntimeMixin._session_reasoning_effort_payload
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._busy = False
                self.current_session = None
                self._selected_session_id = None
                self.sent = []
                self.requested = []
                self.rendered = None
                self.reasoning_sync_calls = 0

            def _session_device_scope_id(self, session):
                return ("local", "local")

            def _align_sidebar_to_session(self, session):
                return None

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                return None

            def _is_channel_process_session(self, session=None):
                return False

            def _bind_session_to_current_bridge(self, session, *, preserve_session_state=False):
                self.bound = dict(session)

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

            def _request_backend_state(self, sid):
                self.requested.append(str(sid))

            def _set_status(self, text):
                self.status_text = str(text)

            def _sync_reasoning_effort_combo(self):
                self.reasoning_sync_calls += 1

            @property
            def _bridge_ready(self):
                return True

        dummy = DummySidebar()
        payload = {
            "id": "sess-1",
            "title": "Local session",
            "backend_history": [],
            "agent_history": [],
            "reasoning_effort": "high",
            "snapshot": {"llm_idx": 2, "reasoning_effort": "high", "reasoning_effort_source": "override"},
        }
        with mock.patch.object(lz, "load_session", return_value=dict(payload)):
            dummy._load_session_by_id("sess-1")

        self.assertEqual(dummy.sent[0]["reasoning_effort"], "high")
        self.assertEqual(dummy.sent[0]["llm_idx"], 2)
        self.assertEqual(dummy.requested, ["sess-1"])
        self.assertEqual(dummy.reasoning_sync_calls, 1)
        self.assertEqual(dummy.status_text, "已载入本地会话。")

    def test_load_session_by_id_does_not_promote_runtime_reasoning_snapshot_into_override_payload(self):
        class DummySidebar(SidebarSessionsMixin, BridgeRuntimeMixin):
            _load_session_by_id = SidebarSessionsMixin._load_session_by_id
            _session_reasoning_effort_payload = BridgeRuntimeMixin._session_reasoning_effort_payload
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._busy = False
                self.current_session = None
                self._selected_session_id = None
                self.sent = []

            def _session_device_scope_id(self, session):
                return ("local", "local")

            def _align_sidebar_to_session(self, session):
                return None

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                return None

            def _is_channel_process_session(self, session=None):
                return False

            def _bind_session_to_current_bridge(self, session, *, preserve_session_state=False):
                self.bound = dict(session)

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

            def _request_backend_state(self, sid):
                self.requested = str(sid)

            def _set_status(self, text):
                self.status_text = str(text)

            def _sync_reasoning_effort_combo(self):
                return None

            @property
            def _bridge_ready(self):
                return True

        dummy = DummySidebar()
        payload = {
            "id": "sess-1",
            "title": "Follow config session",
            "backend_history": [],
            "agent_history": [],
            "snapshot": {"llm_idx": 2, "reasoning_effort": "high", "reasoning_effort_source": "runtime"},
        }
        with mock.patch.object(lz, "load_session", return_value=dict(payload)):
            dummy._load_session_by_id("sess-1")

        self.assertNotIn("reasoning_effort", dummy.sent[0])
        self.assertEqual(dummy.sent[0]["llm_idx"], 2)
        self.assertEqual(dummy.status_text, "已载入本地会话。")

    def test_handle_event_ready_restores_pending_snapshot_reasoning_effort(self):
        class DummyBridge(BridgeRuntimeMixin):
            _handle_event = BridgeRuntimeMixin._handle_event
            _session_reasoning_effort_payload = BridgeRuntimeMixin._session_reasoning_effort_payload
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value

            def __init__(self):
                self._bridge_ready = False
                self.llms = []
                self._bridge_reasoning_effort = ""
                self._pending_state_session = {
                    "id": "sess-1",
                    "backend_history": [{"role": "user", "content": "hi"}],
                    "agent_history": [{"role": "assistant", "content": "hello"}],
                    "reasoning_effort": "medium",
                    "snapshot": {"llm_idx": 3, "reasoning_effort": "medium", "reasoning_effort_source": "override"},
                }
                self.current_session = None
                self.sent = []
                self.requested = []
                self.reasoning_sync_calls = 0
                self.llm_sync_calls = 0
                self.status_text = ""

            def _stream_done(self, *_args, **_kwargs):
                raise AssertionError("unexpected stream_done")

            def _stream_update(self, *_args, **_kwargs):
                raise AssertionError("unexpected stream_update")

            def _handle_download_event(self, ev):
                return False

            def _sync_llm_combo(self):
                self.llm_sync_calls += 1

            def _sync_reasoning_effort_combo(self):
                self.reasoning_sync_calls += 1

            def _render_session(self, session):
                self.rendered = dict(session)

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

            def _request_backend_state(self, sid):
                self.requested.append(str(sid))

            def _refresh_composer_enabled(self):
                self.refreshed = True

            def _set_status(self, text):
                self.status_text = str(text)

        dummy = DummyBridge()
        dummy._handle_event({"event": "ready", "llms": [{"idx": 3, "name": "GPT", "current": True}]})

        self.assertTrue(dummy._bridge_ready)
        self.assertEqual(dummy.sent[0]["reasoning_effort"], "medium")
        self.assertEqual(dummy.sent[0]["llm_idx"], 3)
        self.assertEqual(dummy.requested, ["sess-1"])
        self.assertIsNone(dummy._pending_state_session)
        self.assertEqual(dummy.status_text, "桥接进程已就绪。")

    def test_set_status_keeps_recent_done_message_from_ready_override(self):
        class DummyLabel:
            def __init__(self, text=""):
                self._text = str(text or "")

            def setText(self, text):
                self._text = str(text or "")

            def text(self):
                return str(self._text)

        class DummyBridge(BridgeRuntimeMixin):
            _set_status = BridgeRuntimeMixin._set_status

            def __init__(self):
                self.status_label = DummyLabel("已完成。")
                self._last_task_complete_status_at = 1000.0

            def _refresh_info_tooltip(self):
                return None

        dummy = DummyBridge()
        with mock.patch.object(bridge_runtime.time, "time", return_value=1003.0):
            dummy._set_status("桥接进程已就绪。")
        self.assertEqual(dummy.status_label.text(), "已完成。")

    def test_load_session_by_id_preserves_saved_local_state_with_real_bind_mixin(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = None
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items = []
                self.current_index = None

            def addItem(self, label, value):
                self.items.append((label, value))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def itemData(self, index):
                return self.items[index][1]

        class DummySidebar(SidebarSessionsMixin, SessionShellMixin, BridgeRuntimeMixin):
            _load_session_by_id = SidebarSessionsMixin._load_session_by_id
            _bind_session_to_current_bridge = SessionShellMixin._bind_session_to_current_bridge
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_reasoning_effort_combo_disabled_reason = BridgeRuntimeMixin._bridge_reasoning_effort_combo_disabled_reason
            _current_session_reasoning_effort_override = BridgeRuntimeMixin._current_session_reasoning_effort_override
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _current_reasoning_effort_selection = BridgeRuntimeMixin._current_reasoning_effort_selection
            _session_reasoning_effort_payload = BridgeRuntimeMixin._session_reasoning_effort_payload
            _sync_reasoning_effort_combo = BridgeRuntimeMixin._sync_reasoning_effort_combo
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _is_remote_session = BridgeRuntimeMixin._is_remote_session

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._busy = False
                self.current_session = None
                self._selected_session_id = None
                self._pending_reasoning_effort_override = "xhigh"
                self._bridge_reasoning_effort = "low"
                self._ignore_reasoning_effort_change = False
                self.bridge_proc = mock.Mock(pid=4321)
                self.llms = [{"idx": 0, "name": "GPT", "current": True}]
                self.reasoning_effort_combo = DummyCombo()
                self.sent = []
                self.requested = []
                self.status_text = ""
                self.rendered = None

            def _current_llm_index(self):
                return 0

            def _session_device_scope_id(self, session):
                return ("local", "local")

            def _align_sidebar_to_session(self, session):
                return None

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                return None

            def _is_channel_process_session(self, session=None):
                return False

            def _send_cmd(self, payload):
                self.sent.append(dict(payload))

            def _request_backend_state(self, sid):
                self.requested.append(str(sid))

            def _set_status(self, text):
                self.status_text = str(text)

            def _sync_floating_reasoning_effort_combo(self):
                return None

            @property
            def _bridge_ready(self):
                return True

        dummy = DummySidebar()
        payload = {
            "id": "sess-1",
            "title": "Local session",
            "backend_history": [{"role": "user", "content": "hi"}],
            "agent_history": [{"role": "assistant", "content": "hello"}],
            "reasoning_effort": "high",
            "llm_idx": 2,
            "snapshot": {"llm_idx": 2, "reasoning_effort": "high", "reasoning_effort_source": "override"},
        }
        with mock.patch.object(lz, "load_session", return_value=dict(payload)):
            dummy._load_session_by_id("sess-1")

        self.assertIsNone(dummy._pending_reasoning_effort_override)
        self.assertEqual(dummy.current_session["llm_idx"], 2)
        self.assertEqual(dummy.current_session["process_pid"], 4321)
        self.assertEqual(dummy.current_session["snapshot"]["llm_idx"], 2)
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort"], "high")
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 5)
        self.assertEqual(dummy.sent[0]["llm_idx"], 2)
        self.assertEqual(dummy.sent[0]["reasoning_effort"], "high")
        self.assertEqual(dummy.requested, ["sess-1"])
        self.assertEqual(dummy.status_text, "已载入本地会话。")

    def test_bridge_set_state_without_reasoning_effort_resets_backend_to_model_default(self):
        class DummyBackend:
            def __init__(self, reasoning_effort):
                self.reasoning_effort = reasoning_effort
                self.history = []

        class DummyLLMClient:
            def __init__(self, backend):
                self.backend = backend

        created = {}

        class DummyAgent:
            def __init__(self):
                self.backends = [DummyBackend("low"), DummyBackend(None)]
                self.llmclients = [DummyLLMClient(item) for item in self.backends]
                self.llm_no = 0
                self.llmclient = self.llmclients[0]
                self.history = []
                self.handler = None
                self.inc_out = False
                created["agent"] = self

            def run(self):
                return None

            def list_llms(self):
                return [
                    (0, "launcher/default", self.llm_no == 0),
                    (1, "launcher/secondary", self.llm_no == 1),
                ]

            def next_llm(self, idx):
                self.llm_no = int(idx)
                self.llmclient = self.llmclients[self.llm_no]

            def abort(self):
                return None

        agentmain = types.ModuleType("agentmain")
        agentmain.GeneraticAgent = DummyAgent
        commands = "\n".join(
            [
                json.dumps({"cmd": "switch_reasoning_effort", "reasoning_effort": "high"}, ensure_ascii=False),
                json.dumps({"cmd": "set_state", "backend_history": [], "agent_history": [], "llm_idx": 0}, ensure_ascii=False),
                json.dumps({"cmd": "get_state", "session_id": "sess-1", "request_id": 1}, ensure_ascii=False),
                json.dumps({"cmd": "quit"}, ensure_ascii=False),
            ]
        ) + "\n"
        stdout = io.StringIO()
        stderr = io.StringIO()
        prev_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            sys.modules, {"agentmain": agentmain}, clear=False
        ), mock.patch.object(
            bridge, "_patch_llm_usage_capture", return_value=None
        ), mock.patch.object(
            bridge, "_patch_code_run_stdin", return_value=None
        ), mock.patch.object(
            sys, "argv", ["bridge.py", td]
        ), mock.patch.object(
            sys, "stdin", io.StringIO(commands)
        ), mock.patch.object(
            sys, "stdout", stdout
        ), mock.patch.object(
            sys, "stderr", stderr
        ):
            try:
                bridge.main()
            finally:
                os.chdir(prev_cwd)

        events = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        state_event = next(ev for ev in reversed(events) if ev.get("event") == "state")

        self.assertEqual(state_event["reasoning_effort"], "low")
        self.assertEqual(created["agent"].backends[0].reasoning_effort, "low")

    def test_bridge_switch_llm_without_reasoning_override_resets_backend_to_model_default(self):
        class DummyBackend:
            def __init__(self, reasoning_effort):
                self.reasoning_effort = reasoning_effort
                self.history = []

        class DummyLLMClient:
            def __init__(self, backend):
                self.backend = backend

        created = {}

        class DummyAgent:
            def __init__(self):
                self.backends = [DummyBackend("low"), DummyBackend("medium")]
                self.llmclients = [DummyLLMClient(item) for item in self.backends]
                self.llm_no = 0
                self.llmclient = self.llmclients[0]
                self.history = []
                self.handler = None
                self.inc_out = False
                created["agent"] = self

            def run(self):
                return None

            def list_llms(self):
                return [
                    (0, "launcher/default", self.llm_no == 0),
                    (1, "launcher/secondary", self.llm_no == 1),
                ]

            def next_llm(self, idx):
                self.llm_no = int(idx)
                self.llmclient = self.llmclients[self.llm_no]

            def abort(self):
                return None

        agentmain = types.ModuleType("agentmain")
        agentmain.GeneraticAgent = DummyAgent
        commands = "\n".join(
            [
                json.dumps({"cmd": "switch_reasoning_effort", "reasoning_effort": "high"}, ensure_ascii=False),
                json.dumps({"cmd": "switch_llm", "idx": 1}, ensure_ascii=False),
                json.dumps({"cmd": "switch_llm", "idx": 0}, ensure_ascii=False),
                json.dumps({"cmd": "get_state", "session_id": "sess-1", "request_id": 1}, ensure_ascii=False),
                json.dumps({"cmd": "quit"}, ensure_ascii=False),
            ]
        ) + "\n"
        stdout = io.StringIO()
        stderr = io.StringIO()
        prev_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(
            sys.modules, {"agentmain": agentmain}, clear=False
        ), mock.patch.object(
            bridge, "_patch_llm_usage_capture", return_value=None
        ), mock.patch.object(
            bridge, "_patch_code_run_stdin", return_value=None
        ), mock.patch.object(
            sys, "argv", ["bridge.py", td]
        ), mock.patch.object(
            sys, "stdin", io.StringIO(commands)
        ), mock.patch.object(
            sys, "stdout", stdout
        ), mock.patch.object(
            sys, "stderr", stderr
        ):
            try:
                bridge.main()
            finally:
                os.chdir(prev_cwd)

        events = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
        state_event = next(ev for ev in reversed(events) if ev.get("event") == "state")

        self.assertEqual(state_event["reasoning_effort"], "low")
        self.assertEqual(created["agent"].backends[0].reasoning_effort, "low")
        self.assertEqual(created["agent"].backends[1].reasoning_effort, "medium")

    def test_patch_agent_launcher_multimodal_skips_agent_without_task_queue_contract(self):
        class DummyAgent:
            def run(self):
                return None

        agent = DummyAgent()
        patched = bridge._patch_agent_launcher_multimodal(agent, types.SimpleNamespace())

        self.assertIs(patched, agent)
        self.assertFalse(getattr(agent, "_ga_launcher_multimodal_patched", False))
        self.assertNotIn("run", agent.__dict__)
        self.assertNotIn("put_task", agent.__dict__)

    def test_patch_agent_launcher_multimodal_tracks_new_upstream_turn_metadata_when_supported(self):
        queue_mod = __import__("queue")
        threading_mod = __import__("threading")
        captured = {}

        class DummyBackend:
            def __init__(self):
                self.history = []
                self.extra_sys_prompt = ""

        class DummyLLMClient:
            def __init__(self):
                self.backend = DummyBackend()
                self.log_path = ""

        class DummyHandler:
            def __init__(self, parent, history, temp_dir):
                self.parent = parent
                self.history_info = ["history-updated"]
                self.working = {}

        class DummyAgent:
            def __init__(self):
                self.task_queue = queue_mod.Queue()
                self.task_dir = None
                self.history = []
                self.handler = None
                self.stop_sig = False
                self.is_running = False
                self.inc_out = False
                self.verbose = True
                self.llmclient = DummyLLMClient()
                self.log_path = os.path.join("C:\\demo", "temp", "model_responses", "demo.txt")

            def abort(self):
                self.stop_sig = True

            def _handle_slash_cmd(self, raw_query, display_queue):
                return raw_query

        def fake_agent_runner_loop(llmclient, system_prompt, user_input, handler, tools_schema, max_turns=0, verbose=False, initial_user_content=None, yield_info=False):
            captured["max_turns"] = int(max_turns)
            captured["yield_info"] = bool(yield_info)
            captured["user_input"] = str(user_input)
            captured["initial_user_content"] = initial_user_content
            yield {"turn": 1}
            yield "**LLM Running (Turn 1) ...**"
            yield "turn1-body"
            yield {"turn": 2}
            yield "**LLM Running (Turn 2) ...**"
            yield "turn2-body"

        agentmain = types.SimpleNamespace(
            smart_format=lambda text, max_str_len=200: text,
            get_system_prompt=lambda: "system",
            GenericAgentHandler=DummyHandler,
            TOOLS_SCHEMA=[],
            agent_runner_loop=fake_agent_runner_loop,
            consume_file=lambda *_args, **_kwargs: False,
            format_error=lambda e: f"ERR:{e}",
            __file__=os.path.join("C:\\demo", "agentmain.py"),
        )
        agent = DummyAgent()
        bridge._patch_agent_launcher_multimodal(agent, agentmain)

        worker = threading_mod.Thread(target=agent.run, daemon=True)
        worker.start()
        dq = agent.put_task("hello")
        items = []
        while True:
            item = dq.get(timeout=2)
            items.append(item)
            if "done" in item:
                break

        done_item = items[-1]
        self.assertEqual(captured["max_turns"], 80)
        self.assertTrue(captured["yield_info"])
        self.assertEqual(agent.llmclient.log_path, agent.log_path)
        self.assertEqual(done_item["turn"], 2)
        self.assertEqual(len(done_item["outputs"]), 2)
        self.assertIn("turn1-body", done_item["outputs"][0])
        self.assertIn("turn2-body", done_item["outputs"][1])
        self.assertEqual(agent.history, ["history-updated"])

    def test_patch_agent_launcher_multimodal_falls_back_for_legacy_agent_loop_signature(self):
        queue_mod = __import__("queue")
        threading_mod = __import__("threading")
        captured = {}

        class DummyBackend:
            def __init__(self):
                self.history = []
                self.extra_sys_prompt = ""

        class DummyLLMClient:
            def __init__(self):
                self.backend = DummyBackend()
                self.log_path = ""

        class DummyHandler:
            def __init__(self, parent, history, temp_dir):
                self.parent = parent
                self.history_info = ["legacy-history"]
                self.working = {}

        class DummyAgent:
            def __init__(self):
                self.task_queue = queue_mod.Queue()
                self.task_dir = None
                self.history = []
                self.handler = None
                self.stop_sig = False
                self.is_running = False
                self.inc_out = False
                self.verbose = True
                self.llmclient = DummyLLMClient()
                self.log_path = os.path.join("C:\\demo", "temp", "model_responses", "legacy.txt")

            def abort(self):
                self.stop_sig = True

            def _handle_slash_cmd(self, raw_query, display_queue):
                return raw_query

        def fake_agent_runner_loop(llmclient, system_prompt, user_input, handler, tools_schema, max_turns=0, verbose=False, initial_user_content=None):
            captured["max_turns"] = int(max_turns)
            captured["user_input"] = str(user_input)
            captured["initial_user_content"] = initial_user_content
            yield "**LLM Running (Turn 1) ...**"
            yield "legacy-body"

        agentmain = types.SimpleNamespace(
            smart_format=lambda text, max_str_len=200: text,
            get_system_prompt=lambda: "system",
            GenericAgentHandler=DummyHandler,
            TOOLS_SCHEMA=[],
            agent_runner_loop=fake_agent_runner_loop,
            consume_file=lambda *_args, **_kwargs: False,
            format_error=lambda e: f"ERR:{e}",
            __file__=os.path.join("C:\\demo", "agentmain.py"),
        )
        agent = DummyAgent()
        bridge._patch_agent_launcher_multimodal(agent, agentmain)

        worker = threading_mod.Thread(target=agent.run, daemon=True)
        worker.start()
        dq = agent.put_task("hello")
        while True:
            item = dq.get(timeout=2)
            if "done" in item:
                break

        self.assertEqual(captured["max_turns"], 70)
        self.assertEqual(item["turn"], 1)
        self.assertEqual(item["outputs"], ["**LLM Running (Turn 1) ...**legacy-body"])
        self.assertEqual(agent.history, ["legacy-history"])

    def test_load_remote_session_without_saved_reasoning_keeps_combo_on_follow_config(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = None
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items = []
                self.current_index = None

            def addItem(self, label, value):
                self.items.append((label, value))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummySidebar(SidebarSessionsMixin, BridgeRuntimeMixin):
            _load_session_by_id = SidebarSessionsMixin._load_session_by_id
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_reasoning_effort_combo_disabled_reason = BridgeRuntimeMixin._bridge_reasoning_effort_combo_disabled_reason
            _current_session_reasoning_effort_override = BridgeRuntimeMixin._current_session_reasoning_effort_override
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _current_reasoning_effort_selection = BridgeRuntimeMixin._current_reasoning_effort_selection
            _sync_reasoning_effort_combo = BridgeRuntimeMixin._sync_reasoning_effort_combo
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _is_remote_session = BridgeRuntimeMixin._is_remote_session

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._busy = False
                self.current_session = None
                self._selected_session_id = None
                self._pending_reasoning_effort_override = "medium"
                self._bridge_reasoning_effort = "high"
                self._ignore_reasoning_effort_change = False
                self.llms = [{"idx": 0, "name": "GPT", "current": True}]
                self.reasoning_effort_combo = DummyCombo()
                self.status_text = ""
                self.remote_refresh = None

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _remote_device_by_id(self, device_id):
                return {"id": device_id, "name": "Mac Mini"}

            def _align_sidebar_to_session(self, session):
                return None

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                return None

            def _is_channel_process_session(self, session=None):
                return False

            def _refresh_remote_session_cache_async(self, session):
                self.remote_refresh = dict(session)

            def _set_status(self, text):
                self.status_text = str(text)

            def _sync_floating_reasoning_effort_combo(self):
                return None

            @property
            def _bridge_ready(self):
                return False

        dummy = DummySidebar()
        payload = {
            "id": "sess-remote",
            "title": "Remote session",
            "channel_id": "launcher",
            "snapshot": {"llm_idx": 0},
        }
        with mock.patch.object(lz, "load_session", return_value=dict(payload)):
            dummy._load_session_by_id("sess-remote")

        self.assertIsNone(dummy._pending_reasoning_effort_override)
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 0)
        self.assertTrue(dummy.reasoning_effort_combo.enabled)
        self.assertEqual(dummy.reasoning_effort_combo.tooltip, "切换当前会话使用的思考强度。")
        self.assertEqual(dummy.status_text, "已载入远程会话缓存，正在后台同步；可继续发送，新内容会尝试写回远端。")

    def test_new_blank_session_clears_stale_pending_reasoning_but_keeps_manual_first_send_override(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = None
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items = []
                self.current_index = None

            def addItem(self, label, value):
                self.items.append((label, value))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def itemData(self, index):
                return self.items[index][1]

        class DummySidebar(SidebarSessionsMixin, BridgeRuntimeMixin):
            _new_session = SidebarSessionsMixin._new_session
            _ensure_session = SidebarSessionsMixin._ensure_session
            _on_reasoning_effort_changed = BridgeRuntimeMixin._on_reasoning_effort_changed
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_reasoning_effort_combo_disabled_reason = BridgeRuntimeMixin._bridge_reasoning_effort_combo_disabled_reason
            _current_session_reasoning_effort_override = BridgeRuntimeMixin._current_session_reasoning_effort_override
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _current_reasoning_effort_selection = BridgeRuntimeMixin._current_reasoning_effort_selection
            _sync_reasoning_effort_combo = BridgeRuntimeMixin._sync_reasoning_effort_combo
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value
            _is_remote_session = BridgeRuntimeMixin._is_remote_session

            def __init__(self):
                self._busy = False
                self.agent_dir = "C:\\demo"
                self.current_session = {"id": "old-session", "device_scope": "local", "device_id": "local"}
                self._selected_session_id = "old-session"
                self._pending_state_session = {"id": "pending"}
                self._pending_reasoning_effort_override = "high"
                self._bridge_reasoning_effort = "low"
                self._ignore_reasoning_effort_change = False
                self._bridge_ready = False
                self.bridge_proc = mock.Mock(pid=99)
                self.llms = [{"idx": 0, "name": "GPT", "current": True}]
                self.reasoning_effort_combo = DummyCombo()
                self._sidebar_view_mode = "roots"
                self._sidebar_device_scope = "local"
                self._sidebar_device_id = "local"
                self._sidebar_channel_id = "launcher"
                self.status_text = ""
                self.reset_text = ""
                self.restart_calls = 0
                self.refresh_calls = 0
                self.header_updates = 0

            def _can_create_session_for_channel(self, channel_id, show_message=True, device_scope=None, device_id=None):
                return True

            def _set_status(self, text):
                self.status_text = str(text)

            def _reset_chat_area(self, text):
                self.reset_text = str(text)

            def _restart_bridge(self):
                self.restart_calls += 1

            def _refresh_composer_enabled(self):
                return None

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _current_llm_index(self):
                return 0

            def _ensure_session_usage_metadata(self, session):
                return None

            def _update_header_labels(self):
                self.header_updates += 1

            def _sync_floating_reasoning_effort_combo(self):
                return None

        dummy = DummySidebar()
        dummy._new_session(scope="local", device_id="local", prompt_device=False)

        self.assertIsNone(dummy.current_session)
        self.assertIsNone(dummy._pending_reasoning_effort_override)
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 3)
        self.assertEqual(dummy.restart_calls, 1)

        dummy._on_reasoning_effort_changed(5)
        self.assertEqual(dummy._pending_reasoning_effort_override, "high")

        dummy._ensure_session("hello world")

        self.assertEqual(dummy.current_session["reasoning_effort"], "high")
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort"], "high")
        self.assertEqual(dummy.current_session["snapshot"]["reasoning_effort_source"], "override")
        self.assertEqual(dummy.header_updates, 1)

    def test_clear_current_context_after_session_removed_clears_pending_reasoning_and_prevents_leak(self):
        class DummyCombo:
            def __init__(self):
                self.items = []
                self.current_index = None
                self.enabled = None
                self.tooltip = ""

            def clear(self):
                self.items = []
                self.current_index = None

            def addItem(self, label, value):
                self.items.append((label, value))

            def setCurrentIndex(self, index):
                self.current_index = int(index)

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

        class DummySidebar(SidebarSessionsMixin, BridgeRuntimeMixin):
            _clear_current_context_after_session_removed = SidebarSessionsMixin._clear_current_context_after_session_removed
            _ensure_session = SidebarSessionsMixin._ensure_session
            _apply_bridge_widget_state = BridgeRuntimeMixin._apply_bridge_widget_state
            _bridge_reasoning_effort_combo_disabled_reason = BridgeRuntimeMixin._bridge_reasoning_effort_combo_disabled_reason
            _current_session_reasoning_effort_override = BridgeRuntimeMixin._current_session_reasoning_effort_override
            _session_snapshot_reasoning_effort = BridgeRuntimeMixin._session_snapshot_reasoning_effort
            _session_snapshot_reasoning_effort_source = BridgeRuntimeMixin._session_snapshot_reasoning_effort_source
            _current_reasoning_effort_selection = BridgeRuntimeMixin._current_reasoning_effort_selection
            _sync_reasoning_effort_combo = BridgeRuntimeMixin._sync_reasoning_effort_combo
            _normalize_reasoning_effort_value = BridgeRuntimeMixin._normalize_reasoning_effort_value

            def __init__(self):
                self._pending_state_session = {"id": "pending"}
                self.current_session = {
                    "id": "old-session",
                    "reasoning_effort": "high",
                    "snapshot": {"reasoning_effort": "high", "reasoning_effort_source": "override"},
                }
                self._selected_session_id = "old-session"
                self._pending_reasoning_effort_override = "high"
                self._bridge_reasoning_effort = "low"
                self._ignore_reasoning_effort_change = False
                self.llms = [{"idx": 0, "name": "GPT", "current": True}]
                self.reasoning_effort_combo = DummyCombo()
                self.bridge_proc = mock.Mock(pid=99)
                self.status_text = ""
                self.reset_text = ""
                self.restart_calls = 0
                self.composer_refreshes = 0
                self.header_updates = 0

            def _set_status(self, text):
                self.status_text = str(text)

            def _reset_chat_area(self, text):
                self.reset_text = str(text)

            def _restart_bridge(self):
                self.restart_calls += 1

            def _refresh_composer_enabled(self):
                self.composer_refreshes += 1

            def _sync_floating_reasoning_effort_combo(self):
                return None

            def _current_device_context(self):
                return "local", "local"

            def _current_llm_index(self):
                return 0

            def _ensure_session_usage_metadata(self, session):
                return None

            def _update_header_labels(self):
                self.header_updates += 1

        dummy = DummySidebar()
        dummy._sync_reasoning_effort_combo()
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 5)

        dummy._clear_current_context_after_session_removed("当前会话已删除。", restart_bridge=True)

        self.assertIsNone(dummy._pending_state_session)
        self.assertIsNone(dummy.current_session)
        self.assertIsNone(dummy._selected_session_id)
        self.assertIsNone(dummy._pending_reasoning_effort_override)
        self.assertEqual(dummy.reasoning_effort_combo.current_index, 3)
        self.assertTrue(dummy.reasoning_effort_combo.enabled)
        self.assertEqual(dummy.reasoning_effort_combo.tooltip, "切换当前会话使用的思考强度。")
        self.assertEqual(dummy.status_text, "当前会话已删除。")
        self.assertEqual(dummy.reset_text, "选择一个会话，或新建会话开始聊天。")
        self.assertEqual(dummy.restart_calls, 1)
        self.assertEqual(dummy.composer_refreshes, 1)

        dummy._ensure_session("hello world")

        self.assertNotIn("reasoning_effort", dummy.current_session)
        self.assertNotIn("reasoning_effort", dummy.current_session["snapshot"])
        self.assertEqual(dummy.header_updates, 1)

    def test_rename_sidebar_session_updates_title_and_refreshes_list(self):
        class DummySidebar(SidebarSessionsMixin):
            _rename_sidebar_session = SidebarSessionsMixin._rename_sidebar_session

            def __init__(self):
                self.current_session = {"id": "sess-1", "title": "Old Title", "channel_id": "launcher"}
                self._last_session_list_signature = "cached"
                self.saved = []
                self.refresh_calls = 0
                self.status_text = ""
                self.header_updates = 0

            def _load_sidebar_session_row(self, row):
                return {"id": "sess-1", "title": "Old Title", "channel_id": "launcher"}

            def _save_sidebar_session_row(self, row, data, *, touch=True):
                self.saved.append((dict(row), dict(data), bool(touch)))
                return True, ""

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_text = str(text)

            def _update_header_labels(self):
                self.header_updates += 1

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.QInputDialog, "getText", return_value=("New Title", True)):
            dummy._rename_sidebar_session({"id": "sess-1"})

        self.assertEqual(len(dummy.saved), 1)
        self.assertEqual(dummy.saved[0][1]["title"], "New Title")
        self.assertTrue(dummy.saved[0][2])
        self.assertEqual(dummy.current_session["title"], "New Title")
        self.assertEqual(dummy.header_updates, 1)
        self.assertIsNone(dummy._last_session_list_signature)
        self.assertEqual(dummy.refresh_calls, 1)
        self.assertEqual(dummy.status_text, "已重命名会话：New Title")

    def test_rename_sidebar_session_ignores_cancel_blank_and_unchanged_titles(self):
        class DummySidebar(SidebarSessionsMixin):
            _rename_sidebar_session = SidebarSessionsMixin._rename_sidebar_session

            def __init__(self):
                self.current_session = {"id": "sess-1", "title": "Old Title"}
                self._last_session_list_signature = "cached"
                self.save_calls = 0
                self.refresh_calls = 0
                self.status_text = ""

            def _load_sidebar_session_row(self, row):
                return {"id": "sess-1", "title": "Old Title"}

            def _save_sidebar_session_row(self, row, data, *, touch=True):
                self.save_calls += 1
                return True, ""

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_text = str(text)

        for result in ((None, False), ("   ", True), ("Old Title", True)):
            dummy = DummySidebar()
            with mock.patch.object(sidebar_sessions.QInputDialog, "getText", return_value=result):
                dummy._rename_sidebar_session({"id": "sess-1"})

            self.assertEqual(dummy.save_calls, 0)
            self.assertEqual(dummy.refresh_calls, 0)
            self.assertEqual(dummy.current_session["title"], "Old Title")
            self.assertEqual(dummy._last_session_list_signature, "cached")
            self.assertEqual(dummy.status_text, "")

    def test_save_sidebar_session_row_local_failure_returns_structured_error(self):
        class DummySidebar(SidebarSessionsMixin):
            _save_sidebar_session_row = SidebarSessionsMixin._save_sidebar_session_row

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _session_device_scope_id(self, session):
                return ("local", "local")

        dummy = DummySidebar()
        with mock.patch.object(lz, "save_session", side_effect=OSError("disk full")) as save_session:
            ok, err = dummy._save_sidebar_session_row({"id": "sess-1"}, {"id": "sess-1", "title": "Demo"}, touch=True)

        self.assertFalse(ok)
        self.assertEqual(err, "写入本地会话失败：disk full")
        save_session.assert_called_once()

    def test_rename_local_sidebar_session_failure_warns_with_generic_save_title(self):
        class DummySidebar(SidebarSessionsMixin):
            _rename_sidebar_session = SidebarSessionsMixin._rename_sidebar_session
            _save_sidebar_session_row = SidebarSessionsMixin._save_sidebar_session_row

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.current_session = {"id": "sess-1", "title": "Old Title", "channel_id": "launcher"}
                self._last_session_list_signature = "cached"
                self.refresh_calls = 0
                self.status_text = ""

            def _load_sidebar_session_row(self, row):
                return {"id": "sess-1", "title": "Old Title", "channel_id": "launcher"}

            def _session_device_scope_id(self, session):
                return ("local", "local")

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_text = str(text)

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.QInputDialog, "getText", return_value=("New Title", True)), mock.patch.object(
            sidebar_sessions.QMessageBox, "warning"
        ) as warning_box, mock.patch.object(lz, "save_session", side_effect=OSError("disk full")) as save_session:
            dummy._rename_sidebar_session({"id": "sess-1"})

        save_session.assert_called_once()
        self.assertEqual(dummy.current_session["title"], "Old Title")
        self.assertEqual(dummy.refresh_calls, 0)
        self.assertEqual(dummy._last_session_list_signature, "cached")
        self.assertEqual(dummy.status_text, "")
        warning_box.assert_called_once()
        self.assertEqual(warning_box.call_args.args[1], "保存失败")
        self.assertEqual(warning_box.call_args.args[2], "写入本地会话失败：disk full")

    def test_rename_remote_sidebar_session_failure_keeps_local_title_and_skips_local_save(self):
        class DummySidebar(SidebarSessionsMixin):
            _rename_sidebar_session = SidebarSessionsMixin._rename_sidebar_session
            _save_sidebar_session_row = SidebarSessionsMixin._save_sidebar_session_row

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.current_session = {
                    "id": "sess-1",
                    "title": "Old Title",
                    "device_scope": "remote",
                    "device_id": "box-1",
                }
                self._last_session_list_signature = "cached"
                self.refresh_calls = 0
                self.status_text = ""

            def _load_sidebar_session_row(self, row):
                return {
                    "id": "sess-1",
                    "title": "Old Title",
                    "device_scope": "remote",
                    "device_id": "box-1",
                }

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _save_remote_session_source(self, data):
                self.remote_attempt = dict(data)
                return False, "SSH 超时"

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_text = str(text)

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.QInputDialog, "getText", return_value=("New Title", True)), mock.patch.object(
            sidebar_sessions.QMessageBox, "warning"
        ) as warning_box, mock.patch.object(lz, "save_session") as save_session:
            dummy._rename_sidebar_session({"id": "sess-1"})

        save_session.assert_not_called()
        self.assertEqual(dummy.remote_attempt["title"], "New Title")
        self.assertEqual(dummy.current_session["title"], "Old Title")
        self.assertEqual(dummy.refresh_calls, 0)
        self.assertEqual(dummy._last_session_list_signature, "cached")
        self.assertEqual(dummy.status_text, "")
        warning_box.assert_called_once()
        self.assertEqual(warning_box.call_args.args[1], "保存失败")
        self.assertIn("SSH 超时", warning_box.call_args.args[2])

    def test_set_sidebar_sessions_pinned_collects_local_save_failures(self):
        class DummySidebar(SidebarSessionsMixin):
            _set_sidebar_sessions_pinned = SidebarSessionsMixin._set_sidebar_sessions_pinned
            _save_sidebar_session_row = SidebarSessionsMixin._save_sidebar_session_row

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._last_session_list_signature = "cached"
                self.refresh_calls = 0

            def _load_sidebar_session_row(self, row):
                return {"id": row["id"], "title": row["title"], "channel_id": "launcher"}

            def _session_device_scope_id(self, session):
                return ("local", "local")

            def _refresh_sessions(self):
                self.refresh_calls += 1

        rows = [
            {"id": "sess-1", "title": "One", "pinned": False},
            {"id": "sess-2", "title": "Two", "pinned": False},
        ]
        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.QMessageBox, "warning") as warning_box, mock.patch.object(
            lz, "save_session", side_effect=OSError("disk full")
        ) as save_session:
            dummy._set_sidebar_sessions_pinned(rows, True)

        self.assertEqual(save_session.call_count, 2)
        self.assertIsNone(dummy._last_session_list_signature)
        self.assertEqual(dummy.refresh_calls, 1)
        warning_box.assert_called_once()
        self.assertEqual(warning_box.call_args.args[1], "保存失败")
        self.assertEqual(warning_box.call_args.args[2], "写入本地会话失败：disk full")

    def test_save_remote_session_source_rolls_back_remote_when_local_cache_save_fails(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    if path not in storage:
                        raise FileNotFoundError(path)
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self.storage = storage

            def open(self, path, mode):
                return DummyRemoteFile(self.storage, path, mode)

            def remove(self, path):
                if path not in self.storage:
                    raise FileNotFoundError(path)
                del self.storage[path]

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._sftp = DummySFTP(storage)

            def open_sftp(self):
                return self._sftp

            def close(self):
                return None

        class DummySidebar(SidebarSessionsMixin):
            _save_remote_session_source = SidebarSessionsMixin._save_remote_session_source

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _is_channel_process_session(self, session):
                return False

            def _remote_device_by_id(self, device_id):
                return {"id": "box-1", "name": "远程设备"}

            def _remote_source_session_id(self, data):
                return "remote-1"

            def _remote_launcher_sessions_dir(self, device):
                return "/remote/sessions"

            def _open_remote_device_client(self, dev, timeout=12):
                return DummyClient(self._storage), "", None

            def _ensure_remote_launcher_sessions_dir(self, client, dev):
                return True, ""

        remote_fp = "/remote/sessions/remote-1.json"
        original_remote = json.dumps({"id": "remote-1", "title": "Old Title"}, ensure_ascii=False, indent=2).encode("utf-8")
        storage = {remote_fp: original_remote}
        dummy = DummySidebar(storage)
        session = {
            "id": "cache-1",
            "title": "New Title",
            "device_scope": "remote",
            "device_id": "box-1",
            "remote_session_id": "remote-1",
        }
        with mock.patch.object(sidebar_sessions, "runtime_context_matches", return_value=True), mock.patch.object(
            lz, "save_session", side_effect=OSError("disk full")
        ) as save_session:
            ok, err = dummy._save_remote_session_source(session)

        self.assertFalse(ok)
        self.assertIn("disk full", err)
        self.assertIn("已回滚远端改动", err)
        save_session.assert_called_once()
        self.assertEqual(storage[remote_fp], original_remote)

    def test_save_remote_session_source_still_updates_local_cache_after_runtime_context_changes(self):
        class DummyRemoteFile:
            def __init__(self, storage, path, mode):
                self._storage = storage
                self._path = path
                self._mode = mode
                if "r" in mode:
                    if path not in storage:
                        raise FileNotFoundError(path)
                    self._buffer = io.BytesIO(storage[path])
                else:
                    self._buffer = io.BytesIO()

            def read(self):
                return self._buffer.read()

            def write(self, data):
                return self._buffer.write(data)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if "w" in self._mode and exc_type is None:
                    self._storage[self._path] = self._buffer.getvalue()
                self._buffer.close()
                return False

        class DummySFTP:
            def __init__(self, storage):
                self.storage = storage

            def open(self, path, mode):
                return DummyRemoteFile(self.storage, path, mode)

            def remove(self, path):
                if path not in self.storage:
                    raise FileNotFoundError(path)
                del self.storage[path]

            def close(self):
                return None

        class DummyClient:
            def __init__(self, storage):
                self._sftp = DummySFTP(storage)

            def open_sftp(self):
                return self._sftp

            def close(self):
                return None

        class DummySidebar(SidebarSessionsMixin):
            _save_remote_session_source = SidebarSessionsMixin._save_remote_session_source

            def __init__(self, storage):
                self.agent_dir = "C:\\demo"
                self._storage = storage

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _is_channel_process_session(self, session):
                return False

            def _remote_device_by_id(self, device_id):
                return {"id": "box-1", "name": "远程设备"}

            def _remote_source_session_id(self, data):
                return "remote-1"

            def _remote_launcher_sessions_dir(self, device):
                return "/remote/sessions"

            def _open_remote_device_client(self, dev, timeout=12):
                return DummyClient(self._storage), "", None

            def _ensure_remote_launcher_sessions_dir(self, client, dev):
                return True, ""

        remote_fp = "/remote/sessions/remote-1.json"
        original_remote = json.dumps({"id": "remote-1", "title": "Old Title"}, ensure_ascii=False, indent=2).encode("utf-8")
        storage = {remote_fp: original_remote}
        dummy = DummySidebar(storage)
        session = {
            "id": "cache-1",
            "title": "New Title",
            "device_scope": "remote",
            "device_id": "box-1",
            "remote_session_id": "remote-1",
        }
        with mock.patch.object(sidebar_sessions, "runtime_context_matches", return_value=False), mock.patch.object(
            lz, "save_session"
        ) as save_session:
            ok, err = dummy._save_remote_session_source(session, runtime_context={"agent_dir": "C:\\other"})

        self.assertTrue(ok)
        self.assertEqual(err, "")
        save_session.assert_called_once()
        saved_payload = save_session.call_args.args[1]
        self.assertEqual(saved_payload["id"], "cache-1")
        self.assertEqual(saved_payload["title"], "New Title")

    def test_refresh_remote_session_cache_async_updates_status_when_sync_succeeds(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummySidebar(SidebarSessionsMixin):
            _refresh_remote_session_cache_async = SidebarSessionsMixin._refresh_remote_session_cache_async

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self.current_session = {"id": "sess-1", "device_scope": "remote", "device_id": "box-1"}
                self._remote_session_refresh_inflight = set()
                self._last_session_list_signature = "cached"
                self.statuses = []
                self.rendered = None
                self.refresh_calls = 0
                self.composer_refreshes = 0

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _refresh_remote_session_cache(self, session, *, agent_dir="", runtime_context=None):
                return {"id": "sess-1", "title": "Fresh", "device_scope": "remote", "device_id": "box-1"}, ""

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _render_session(self, session):
                self.rendered = dict(session)

            def _refresh_composer_enabled(self):
                self.composer_refreshes += 1

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.threading, "Thread", ImmediateThread):
            dummy._refresh_remote_session_cache_async({"id": "sess-1", "device_scope": "remote", "device_id": "box-1"})

        self.assertEqual(dummy.current_session["title"], "Fresh")
        self.assertEqual(dummy.rendered["title"], "Fresh")
        self.assertEqual(dummy.statuses, ["已同步远程会话；后续发送会继续写回远端。"])
        self.assertEqual(dummy.refresh_calls, 1)
        self.assertEqual(dummy.composer_refreshes, 1)
        self.assertEqual(dummy._remote_session_refresh_inflight, set())

    def test_refresh_remote_session_cache_skips_stale_remote_overwrite_when_local_state_is_newer(self):
        class DummySidebar(SidebarSessionsMixin):
            _refresh_remote_session_cache = SidebarSessionsMixin._refresh_remote_session_cache
            _remote_session_has_newer_local_state = SidebarSessionsMixin._remote_session_has_newer_local_state

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _is_channel_process_session(self, session):
                return False

            def _remote_device_by_id(self, device_id):
                return {"id": "box-1", "name": "远程设备"}

            def _remote_source_session_id(self, data):
                return "remote-1"

            def _fetch_remote_session_payload(self, device, remote_session_id):
                return (
                    {
                        "id": "remote-1",
                        "title": "Remote Older",
                        "updated_at": 10.0,
                        "channel_id": "launcher",
                        "bubbles": [{"role": "assistant", "text": "old"}],
                    },
                    "",
                )

        session = {
            "id": "rchat_box-1_remote-1",
            "remote_session_id": "remote-1",
            "title": "Local Pending",
            "updated_at": 20.0,
            "remote_updated_at": 10.0,
            "device_scope": "remote",
            "device_id": "box-1",
            "channel_id": "launcher",
            "bubbles": [{"role": "user", "text": "new"}],
        }
        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions, "runtime_context_matches", return_value=True), mock.patch.object(
            lz, "save_session"
        ) as save_session:
            fresh, err = dummy._refresh_remote_session_cache(session, agent_dir="C:\\demo", runtime_context={"agent_dir": "C:\\demo"})

        self.assertEqual(err, "")
        self.assertEqual(fresh["title"], "Local Pending")
        self.assertEqual(fresh["bubbles"], [{"role": "user", "text": "new"}])
        save_session.assert_not_called()

    def test_refresh_remote_session_cache_async_does_not_rerender_busy_current_session(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummySidebar(SidebarSessionsMixin):
            _refresh_remote_session_cache_async = SidebarSessionsMixin._refresh_remote_session_cache_async

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self.current_session = {"id": "sess-1", "title": "Sending", "device_scope": "remote", "device_id": "box-1"}
                self._busy = True
                self._remote_session_refresh_inflight = set()
                self._last_session_list_signature = "cached"
                self.render_calls = 0
                self.refresh_calls = 0
                self.statuses = []

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _refresh_remote_session_cache(self, session, *, agent_dir="", runtime_context=None):
                return {"id": "sess-1", "title": "Fresh", "device_scope": "remote", "device_id": "box-1"}, ""

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _render_session(self, session):
                self.render_calls += 1

            def _refresh_composer_enabled(self):
                return None

            def _refresh_sessions(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.threading, "Thread", ImmediateThread):
            dummy._refresh_remote_session_cache_async({"id": "sess-1", "device_scope": "remote", "device_id": "box-1"})

        self.assertEqual(dummy.current_session["title"], "Sending")
        self.assertEqual(dummy.render_calls, 0)
        self.assertEqual(dummy.refresh_calls, 1)
        self.assertEqual(dummy.statuses, [])
        self.assertEqual(dummy._remote_session_refresh_inflight, set())

    def test_refresh_remote_session_cache_async_updates_status_when_sync_fails(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummySidebar(SidebarSessionsMixin):
            _refresh_remote_session_cache_async = SidebarSessionsMixin._refresh_remote_session_cache_async

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self.current_session = {"id": "sess-1", "device_scope": "remote", "device_id": "box-1"}
                self._remote_session_refresh_inflight = set()
                self.statuses = []

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _refresh_remote_session_cache(self, session, *, agent_dir="", runtime_context=None):
                return None, "SSH 超时"

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.threading, "Thread", ImmediateThread):
            dummy._refresh_remote_session_cache_async({"id": "sess-1", "device_scope": "remote", "device_id": "box-1"})

        self.assertEqual(dummy.statuses, ["远端同步失败，当前仍使用本地缓存：SSH 超时；可稍后重试或先检查 SSH。"])
        self.assertEqual(dummy._remote_session_refresh_inflight, set())

    def test_handle_remote_error_ignores_stale_session_id(self):
        class DummyBridge(BridgeRuntimeMixin):
            _handle_event = BridgeRuntimeMixin._handle_event

            def __init__(self):
                self.current_session = {"id": "sess-current"}
                self._stream_row = object()
                self._busy = True
                self._abort_requested = False
                self._current_stream_text = "partial"
                self._pending_stream_text = "partial"
                self._active_token_event_ts = 1.0
                self.statuses = []
                self.send_btn = types.SimpleNamespace(setEnabled=lambda *_args, **_kwargs: None)
                self.stop_btn = types.SimpleNamespace(setEnabled=lambda *_args, **_kwargs: None)

            def _clear_active_turn_attachments(self):
                self.attachments_cleared = True

            def _discard_stream_row(self, row=None):
                self.discard_called = True

            def _set_follow_latest_user(self, value):
                self.follow_latest = bool(value)

            def _clear_current_turn_user_row(self, row=None):
                self.anchor_cleared = True

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _refresh_composer_enabled(self):
                self.composer_refreshed = True

            def _refresh_token_label(self):
                self.tokens_refreshed = True

            def _refresh_floating_chat_window(self):
                self.floating_refreshed = True

        dummy = DummyBridge()
        with mock.patch.object(bridge_runtime.QMessageBox, "warning") as warning_box:
            dummy._handle_event({"event": "remote_error", "session_id": "sess-old", "msg": "stale error"})

        self.assertEqual(dummy.statuses, [])
        self.assertTrue(dummy._busy)
        self.assertFalse(getattr(dummy, "discard_called", False))
        warning_box.assert_not_called()

    def test_save_remote_session_source_async_reports_local_cache_preserved_on_failure(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummySidebar(SidebarSessionsMixin):
            _save_remote_session_source_async = SidebarSessionsMixin._save_remote_session_source_async

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self.statuses = []

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _save_remote_session_source(self, session, *, agent_dir="", runtime_context=None):
                return False, "SSH 超时"

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.threading, "Thread", ImmediateThread):
            dummy._save_remote_session_source_async({"id": "sess-1", "device_scope": "remote", "device_id": "box-1"})

        self.assertEqual(dummy.statuses, ["远端会话写回失败，当前内容仍保留在本地缓存：SSH 超时；可稍后重试同步或检查 SSH。"])

    def test_save_remote_session_source_async_reports_remote_rollback_status(self):
        class ImmediateThread:
            def __init__(self, target=None, name=None, daemon=None):
                self._target = target

            def start(self):
                if callable(self._target):
                    self._target()

        class DummySidebar(SidebarSessionsMixin):
            _save_remote_session_source_async = SidebarSessionsMixin._save_remote_session_source_async

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._runtime_context_generation = 2
                self.statuses = []

            def _session_device_scope_id(self, session):
                return ("remote", "box-1")

            def _save_remote_session_source(self, session, *, agent_dir="", runtime_context=None):
                return False, "写入本地缓存失败：disk full；已回滚远端改动。"

            def _sidebar_post_ui(self, callback):
                if callable(callback):
                    callback()

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummySidebar()
        with mock.patch.object(sidebar_sessions.threading, "Thread", ImmediateThread):
            dummy._save_remote_session_source_async({"id": "sess-1", "device_scope": "remote", "device_id": "box-1"})

        self.assertEqual(
            dummy.statuses,
            ["远端会话写回失败，已回滚远端改动，本地缓存保持不变：写入本地缓存失败：disk full；已回滚远端改动。；可稍后重试同步或检查本地磁盘。"],
        )

    def test_reload_personal_panel_remote_sync_notice_and_completion_status(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _reload_personal_panel = PersonalUsageMixin._reload_personal_panel

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 2
                self._settings_target_change_token = 4
                self._settings_personal_remote_sync_running = False
                self._settings_personal_remote_sync_key = ""
                self._settings_personal_remote_synced_key = ""
                self.settings_personal_notice = DummyLabel()
                self.settings_personal_scope_hint = DummyLabel()
                self.settings_personal_list_layout = object()
                self.statuses = []
                self.trigger_calls = []
                self.pending_done = None

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _reload_personal_preferences(self):
                return None

            def _reload_lan_interface_panel(self):
                return None

            def _clear_layout(self, _layout):
                return None

            def _settings_data_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "scope": "remote", "label": "Mac Mini"}

            def _settings_remote_sync_key(self, target, *, kind="personal"):
                return f"{kind}:{target['device_id']}"

            def _trigger_settings_remote_session_sync(self, *, device_id="", on_done=None, include_all_channels=False, include_usage=False):
                self.trigger_calls.append((device_id, bool(include_all_channels), bool(include_usage)))
                self.pending_done = on_done

            def _set_status(self, text):
                self.statuses.append(str(text))

            def _collect_archive_stats(self, scope, device_id):
                return {"active": {}}

            def _archive_known_channel_ids(self, scope, device_id):
                return []

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_personal_panel()
            self.assertEqual(dummy.settings_personal_notice.text, "正在同步 Mac Mini 的会话缓存；完成后会自动刷新，随后可继续调整会话上限。")
            self.assertTrue(callable(dummy.pending_done))
            dummy.pending_done()

        self.assertEqual(dummy.trigger_calls, [("box-1", True, False)])
        self.assertEqual(dummy.statuses, ["已同步 Mac Mini 的远端会话缓存；当前页面已刷新，可继续调整会话上限。"])
        self.assertEqual(dummy._settings_personal_remote_synced_key, "personal:box-1")

    def test_reload_usage_panel_remote_sync_notice_and_completion_status(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _reload_usage_panel = PersonalUsageMixin._reload_usage_panel

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self._runtime_context_generation = 2
                self._settings_target_change_token = 4
                self._settings_usage_remote_sync_running = False
                self._settings_usage_remote_sync_key = ""
                self._settings_usage_remote_synced_key = ""
                self.settings_usage_notice = DummyLabel()
                self.settings_usage_list_layout = object()
                self.statuses = []
                self.trigger_calls = []
                self.pending_done = None

            def _settings_target_generation(self):
                return self._settings_target_change_token

            def _clear_layout(self, _layout):
                return None

            def _settings_data_target_context(self):
                return {"is_remote": True, "device_id": "box-1", "scope": "remote", "label": "Mac Mini"}

            def _settings_remote_sync_key(self, target, *, kind="usage"):
                return f"{kind}:{target['device_id']}"

            def _trigger_settings_remote_session_sync(self, *, device_id="", on_done=None, include_all_channels=False, include_usage=False):
                self.trigger_calls.append((device_id, bool(include_all_channels), bool(include_usage)))
                self.pending_done = on_done

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False):
            dummy._reload_usage_panel()
            self.assertEqual(dummy.settings_usage_notice.text, "正在同步 Mac Mini 的远端使用日志、会话与渠道快照；完成后会自动刷新，可能需要数秒。")
            self.assertTrue(callable(dummy.pending_done))
            dummy.pending_done()

        self.assertEqual(dummy.trigger_calls, [("box-1", True, True)])
        self.assertEqual(dummy.statuses, ["已同步 Mac Mini 的远端使用日志、会话与渠道快照；当前页面已刷新。"])
        self.assertEqual(dummy._settings_usage_remote_synced_key, "usage:box-1")

    def test_clear_usage_logs_for_target_marks_sessions_cleared_and_skips_remote_resync(self):
        class DummyUsage(PersonalUsageMixin):
            _clear_usage_logs_for_target = PersonalUsageMixin._clear_usage_logs_for_target
            _settings_session_matches_target = PersonalUsageMixin._settings_session_matches_target

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self.cfg = {}
                self.current_session = {
                    "id": "sess-1",
                    "device_scope": "remote",
                    "device_id": "box-1",
                    "channel_id": "launcher",
                    "token_usage": {"events": [{"input_tokens": 10, "output_tokens": 20}]},
                }
                self._settings_usage_remote_sync_running = True
                self._settings_usage_remote_sync_key = "stale"
                self._settings_usage_remote_synced_key = ""
                self.reload_calls = 0
                self.token_refreshes = 0
                self.statuses = []

            def _settings_remote_sync_key(self, target, *, kind="usage"):
                return f"{kind}:{target['device_id']}"

            def _reload_usage_panel(self):
                self.reload_calls += 1

            def _refresh_token_label(self):
                self.token_refreshes += 1

            def _set_status(self, text):
                self.statuses.append(str(text))

        stats = {"activity": {"session_count": 1, "event_count": 3}}
        target = {"is_remote": True, "scope": "remote", "device_id": "box-1", "label": "Mac Mini"}
        payload = {
            "id": "sess-1",
            "device_scope": "remote",
            "device_id": "box-1",
            "channel_id": "launcher",
            "bubbles": [{"role": "user", "text": "hello"}],
            "token_usage": {"events": [{"input_tokens": 10, "output_tokens": 20}], "last_model": "gpt-5.4"},
        }
        saved = []

        def save_session(_agent_dir, session, *, touch=True):
            saved.append((dict(session), bool(touch)))

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "list_sessions", return_value=[{"id": "sess-1", "device_scope": "remote", "device_id": "box-1"}]
        ), mock.patch.object(
            lz, "load_session", return_value=dict(payload)
        ), mock.patch.object(
            lz, "save_session", side_effect=save_session
        ), mock.patch.object(
            personal_usage.QMessageBox, "question", return_value=personal_usage.QMessageBox.Yes
        ), mock.patch.object(
            personal_usage.QMessageBox, "information"
        ) as info_box:
            ok = dummy._clear_usage_logs_for_target(stats, target)

        self.assertTrue(ok)
        self.assertEqual(len(saved), 1)
        self.assertFalse(saved[0][1])
        saved_usage = saved[0][0]["token_usage"]
        self.assertEqual(saved_usage.get("events"), [])
        self.assertTrue(saved_usage.get("launcher_usage_cleared"))
        self.assertEqual(dummy.current_session["token_usage"].get("events"), [])
        self.assertTrue(dummy.current_session["token_usage"].get("launcher_usage_cleared"))
        self.assertEqual(dummy._settings_usage_remote_synced_key, "usage:box-1")
        self.assertEqual(dummy._settings_usage_remote_sync_key, "")
        self.assertFalse(dummy._settings_usage_remote_sync_running)
        self.assertEqual(dummy.reload_calls, 1)
        self.assertEqual(dummy.token_refreshes, 1)
        self.assertIn("Mac Mini", dummy.statuses[0])
        info_box.assert_called_once()

    def test_remote_launcher_sync_blocking_drops_stale_context_before_local_cache_write(self):
        class DummySidebar(SidebarSessionsMixin):
            _sync_remote_device_launcher_sessions_blocking = SidebarSessionsMixin._sync_remote_device_launcher_sessions_blocking

            def __init__(self):
                self.agent_dir = "C:\\new-agent"
                self._runtime_context_generation = 8
                self.current_session = None

            def _current_device_context(self):
                return ("remote", "box-1")

            def _session_device_scope_id(self, _session):
                return ("remote", "box-1")

            def _auto_ssh_remote_devices(self, _target_id=""):
                return [{"id": "box-1", "name": "Mac Mini"}]

            def _fetch_remote_launcher_session_metas(self, _dev, **_kwargs):
                return True, [{"id": "sess-1", "remote_session_id": "sess-1", "updated_at": 1.0, "channel_id": "launcher"}], ""

            def _normalize_remote_session_id(self, value, fallback=""):
                return str(value or fallback)

            def _remote_cache_session_id(self, did, remote_sid):
                return f"rchat_{did}_{remote_sid}"

            def _remote_session_cache_payload(self, _dev, row, _old):
                return {
                    "id": "rchat_box-1_sess-1",
                    "title": "Remote Session",
                    "updated_at": float(row.get("updated_at", 0) or 0),
                    "pinned": False,
                    "remote_session_id": "sess-1",
                    "device_id": "box-1",
                    "channel_id": "launcher",
                }

        dummy = DummySidebar()
        stale_context = {"agent_dir": "C:\\old-agent", "runtime_generation": 7, "settings_target_generation": 0}
        with mock.patch.object(lz, "load_session") as load_session, mock.patch.object(lz, "save_session") as save_session:
            changed = dummy._sync_remote_device_launcher_sessions_blocking(
                force=True,
                device_id="box-1",
                agent_dir="C:\\old-agent",
                runtime_context=stale_context,
            )

        self.assertFalse(changed)
        load_session.assert_not_called()
        save_session.assert_not_called()

    def test_fetch_remote_channel_snapshots_uses_docker_exec_and_parses_external_rows(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummySidebar(SidebarSessionsMixin):
            _fetch_remote_channel_snapshots = SidebarSessionsMixin._fetch_remote_channel_snapshots

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _remote_device_auto_ssh_enabled(self, device):
                return True

            def _remote_device_ssh_payload(self, device):
                return {"host": "10.0.0.8"}

            def _open_vps_ssh_client(self, payload, timeout=8):
                return self.client, "", "", False

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                payload = {
                    "rows": [
                        {
                            "channel_id": "telegram",
                            "channel_label": "Telegram / 纸飞机",
                            "title": "Telegram 进程",
                            "updated_at": 123.0,
                            "process_status": "外部运行中",
                            "process_pid": 4321,
                            "process_started_at": 0.0,
                            "process_ended_at": 0.0,
                            "managed_by_launcher": False,
                            "bubble_text": "external snapshot",
                        }
                    ]
                }
                return 0, json.dumps(payload, ensure_ascii=False), ""

        dummy = DummySidebar()
        device = {
            "id": "box-1",
            "host": "10.0.0.8",
            "username": "root",
            "agent_mode": "docker",
            "remote_mode": "docker_container",
            "docker_container": "ga-prod",
            "docker_agent_dir": "/opt/agant",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        specs = [{"id": "telegram", "label": "Telegram / 纸飞机", "script": "tgapp.py"}]
        with mock.patch.object(sidebar_sessions.lz, "COMM_CHANNEL_SPECS", specs):
            ok, rows, err = dummy._fetch_remote_channel_snapshots(device)

        self.assertTrue(ok, msg=err)
        self.assertEqual(rows[0]["process_status"], "外部运行中")
        self.assertFalse(rows[0]["managed_by_launcher"])
        self.assertEqual(rows[0]["process_pid"], 4321)
        self.assertTrue(dummy.client.closed)
        self.assertTrue(any("docker exec -i" in cmd and "ga-prod" in cmd for cmd in dummy.commands))
        self.assertTrue(any("ps -eo pid=,args=" in cmd for cmd in dummy.commands))
        self.assertTrue(any("frontends/tgapp.py" in cmd for cmd in dummy.commands))
        self.assertTrue(any("process_cmdline_matches_agent_script" in cmd for cmd in dummy.commands))
        self.assertTrue(any("/proc/" in cmd and "/cwd" in cmd for cmd in dummy.commands))
        self.assertFalse(any("    })\nfor cid, proc_info in scan_external_processes()" in cmd for cmd in dummy.commands))

    def test_fetch_remote_channel_snapshots_skips_local_only_channels(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummySidebar(SidebarSessionsMixin):
            _fetch_remote_channel_snapshots = SidebarSessionsMixin._fetch_remote_channel_snapshots

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _remote_device_auto_ssh_enabled(self, device):
                return True

            def _remote_device_ssh_payload(self, device):
                return {"host": "10.0.0.12"}

            def _open_vps_ssh_client(self, payload, timeout=8):
                return self.client, "", "", False

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, json.dumps({"rows": []}, ensure_ascii=False), ""

        dummy = DummySidebar()
        device = {
            "id": "box-5",
            "host": "10.0.0.12",
            "username": "root",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        specs = [
            {"id": "telegram", "label": "Telegram / 纸飞机", "script": "tgapp.py"},
            {"id": "conductor", "label": "Conductor 总管台", "script": "conductor.py", "local_only": True},
        ]
        with mock.patch.object(sidebar_sessions.lz, "COMM_CHANNEL_SPECS", specs):
            ok, _rows, err = dummy._fetch_remote_channel_snapshots(device)

        self.assertTrue(ok, msg=err)
        self.assertTrue(dummy.client.closed)
        self.assertTrue(any("frontends/tgapp.py" in cmd for cmd in dummy.commands))
        self.assertFalse(any("frontends/conductor.py" in cmd for cmd in dummy.commands))

    def test_fetch_remote_channel_snapshots_includes_wechat_lock_probe_for_unmatched_instances(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummySidebar(SidebarSessionsMixin):
            _fetch_remote_channel_snapshots = SidebarSessionsMixin._fetch_remote_channel_snapshots

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _remote_device_auto_ssh_enabled(self, device):
                return True

            def _remote_device_ssh_payload(self, device):
                return {"host": "10.0.0.9"}

            def _open_vps_ssh_client(self, payload, timeout=8):
                return self.client, "", "", False

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, json.dumps({"rows": []}, ensure_ascii=False), ""

        dummy = DummySidebar()
        device = {
            "id": "box-2",
            "host": "10.0.0.9",
            "username": "root",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        specs = [{"id": "wechat", "label": "微信", "script": "wechatapp.py"}]
        with mock.patch.object(sidebar_sessions.lz, "COMM_CHANNEL_SPECS", specs):
            ok, _rows, err = dummy._fetch_remote_channel_snapshots(device)

        self.assertTrue(ok, msg=err)
        self.assertTrue(dummy.client.closed)
        self.assertTrue(any("def wechat_lock_occupied" in cmd for cmd in dummy.commands))
        self.assertTrue(any("def wechat_lock_pid" in cmd for cmd in dummy.commands))
        self.assertTrue(any("matched_process_info('wechat', wechat_pid)" in cmd for cmd in dummy.commands))
        self.assertTrue(any("claim_channel_process('wechat'" in cmd for cmd in dummy.commands))
        self.assertTrue(any("ss -ltnp" in cmd for cmd in dummy.commands))
        self.assertTrue(any("WeChat 单实例锁检测（未匹配到进程命令）" in cmd for cmd in dummy.commands))
        self.assertTrue(any("rows_by_channel['wechat']" in cmd for cmd in dummy.commands))

    def test_fetch_remote_channel_snapshots_revalidates_managed_pid_and_prefilters_candidates(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummySidebar(SidebarSessionsMixin):
            _fetch_remote_channel_snapshots = SidebarSessionsMixin._fetch_remote_channel_snapshots

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _remote_device_auto_ssh_enabled(self, device):
                return True

            def _remote_device_ssh_payload(self, device):
                return {"host": "10.0.0.10"}

            def _open_vps_ssh_client(self, payload, timeout=8):
                return self.client, "", "", False

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, json.dumps({"rows": []}, ensure_ascii=False), ""

        dummy = DummySidebar()
        device = {
            "id": "box-3",
            "host": "10.0.0.10",
            "username": "root",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        specs = [
            {"id": "wechat", "label": "微信", "script": "wechatapp.py"},
            {"id": "telegram", "label": "Telegram / 纸飞机", "script": "tgapp.py"},
        ]
        with mock.patch.object(sidebar_sessions.lz, "COMM_CHANNEL_SPECS", specs):
            ok, _rows, err = dummy._fetch_remote_channel_snapshots(device)

        self.assertTrue(ok, msg=err)
        self.assertTrue(dummy.client.closed)
        self.assertTrue(any("matched = matched_process_info(cid, pid) if alive and pid > 0 else None" in cmd for cmd in dummy.commands))
        self.assertTrue(any("candidate_specs = []" in cmd for cmd in dummy.commands))
        self.assertTrue(
            any("any(process_cmdline_has_script(cmd, rel) for rel in script_rel_candidates)" in cmd for cmd in dummy.commands)
        )

    def test_generated_remote_channel_snapshot_script_ignores_stale_managed_session_rows(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummySidebar(SidebarSessionsMixin):
            _fetch_remote_channel_snapshots = SidebarSessionsMixin._fetch_remote_channel_snapshots

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _remote_device_auto_ssh_enabled(self, device):
                return True

            def _remote_device_ssh_payload(self, device):
                return {"host": "10.0.0.11"}

            def _open_vps_ssh_client(self, payload, timeout=8):
                return self.client, "", "", False

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, json.dumps({"rows": []}, ensure_ascii=False), ""

        dummy = DummySidebar()
        device = {
            "id": "box-4",
            "host": "10.0.0.11",
            "username": "root",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        specs = [{"id": "telegram", "label": "Telegram / 纸飞机", "script": "tgapp.py"}]
        with mock.patch.object(sidebar_sessions.lz, "COMM_CHANNEL_SPECS", specs):
            ok, _rows, err = dummy._fetch_remote_channel_snapshots(device)

        self.assertTrue(ok, msg=err)
        self.assertTrue(dummy.client.closed)
        cmd = dummy.commands[0]
        marker_pos = cmd.find("GA_SNAPSHOT_PY'\r\n")
        marker_len = len("GA_SNAPSHOT_PY'\r\n")
        if marker_pos < 0:
            marker_pos = cmd.find("GA_SNAPSHOT_PY'\n")
            marker_len = len("GA_SNAPSHOT_PY'\n")
        self.assertGreaterEqual(marker_pos, 0, msg=cmd)
        start = marker_pos + marker_len
        end_marker = "\r\nGA_SNAPSHOT_PY"
        end = cmd.rfind(end_marker)
        if end < 0:
            end_marker = "\nGA_SNAPSHOT_PY"
            end = cmd.rfind(end_marker)
        self.assertGreaterEqual(end, 0, msg=cmd)
        script = cmd[start:end]

        with tempfile.TemporaryDirectory() as td:
            sess_dir = os.path.join(td, "temp", "launcher_sessions")
            os.makedirs(sess_dir, exist_ok=True)
            stale_path = os.path.join(sess_dir, "launcher_remote_channel_telegram.json")
            with open(stale_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": "launcher_remote_channel_telegram",
                        "title": "Telegram 进程",
                        "session_kind": "channel_process",
                        "channel_id": "telegram",
                        "channel_label": "Telegram / 纸飞机",
                        "process_pid": 999999,
                        "process_status": "运行中",
                        "process_started_at": 1.0,
                        "process_ended_at": 0.0,
                        "updated_at": 1.0,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            # This legacy file should be ignored because the scan now narrows to launcher_remote_channel_*.json.
            with open(os.path.join(sess_dir, "other_channel_process.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": "other_channel_process",
                        "title": "Other 进程",
                        "session_kind": "channel_process",
                        "channel_id": "other",
                        "channel_label": "Other",
                        "process_pid": 999998,
                        "process_status": "运行中",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=td,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = None
            for line in reversed(str(result.stdout or "").splitlines()):
                text = str(line or "").strip()
                if not text.startswith("{"):
                    continue
                payload = json.loads(text)
                break
            self.assertIsInstance(payload, dict, msg=result.stdout)
            self.assertEqual(payload.get("rows"), [])

            with open(stale_path, "r", encoding="utf-8") as f:
                stale_data = json.load(f)
            self.assertEqual(stale_data.get("process_status"), "已退出")

    def test_sync_remote_channel_process_sessions_claims_remote_process_with_pid(self):
        class DummySidebar(SidebarSessionsMixin):
            _sync_remote_device_channel_process_sessions_blocking = SidebarSessionsMixin._sync_remote_device_channel_process_sessions_blocking

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _auto_ssh_remote_devices(self):
                return [{"id": "box-1", "name": "Docker Box"}]

            def _fetch_remote_channel_snapshots(self, device):
                return True, [
                    {
                        "channel_id": "telegram",
                        "channel_label": "Telegram / 纸飞机",
                        "title": "Telegram 进程",
                        "updated_at": 321.0,
                        "process_status": "运行中",
                        "process_pid": 4321,
                        "process_started_at": 0.0,
                        "process_ended_at": 0.0,
                        "managed_by_launcher": True,
                        "bubble_text": "claimed snapshot",
                    }
                ], ""

        dummy = DummySidebar()
        saved = []
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "load_session", return_value={}
        ), mock.patch.object(lz, "save_session", side_effect=lambda root, payload, touch=False: saved.append(dict(payload))), mock.patch.object(
            lz, "list_sessions", return_value=[]
        ):
            changed = dummy._sync_remote_device_channel_process_sessions_blocking(agent_dir="C:\\demo")

        self.assertTrue(changed)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["id"], "rdev_box-1_telegram_proc")
        self.assertEqual(saved[0]["process_status"], "运行中")
        self.assertTrue(saved[0]["managed_by_launcher"])

    def test_sync_remote_channel_process_sessions_keeps_external_fallback_without_pid(self):
        class DummySidebar(SidebarSessionsMixin):
            _sync_remote_device_channel_process_sessions_blocking = SidebarSessionsMixin._sync_remote_device_channel_process_sessions_blocking

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _auto_ssh_remote_devices(self):
                return [{"id": "box-1", "name": "Docker Box"}]

            def _fetch_remote_channel_snapshots(self, device):
                return True, [
                    {
                        "channel_id": "wechat",
                        "channel_label": "微信",
                        "title": "微信 进程",
                        "updated_at": 322.0,
                        "process_status": "外部运行中",
                        "process_pid": 0,
                        "process_started_at": 0.0,
                        "process_ended_at": 0.0,
                        "managed_by_launcher": False,
                        "bubble_text": "external fallback snapshot",
                    }
                ], ""

        dummy = DummySidebar()
        saved = []
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "load_session", return_value={}
        ), mock.patch.object(lz, "save_session", side_effect=lambda root, payload, touch=False: saved.append(dict(payload))), mock.patch.object(
            lz, "list_sessions", return_value=[]
        ):
            changed = dummy._sync_remote_device_channel_process_sessions_blocking(agent_dir="C:\\demo")

        self.assertTrue(changed)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["process_status"], "外部运行中")
        self.assertFalse(saved[0]["managed_by_launcher"])
        self.assertEqual(saved[0]["process_pid"], 0)

    def test_sync_remote_channel_process_sessions_preserves_launcher_managed_rows(self):
        class DummySidebar(SidebarSessionsMixin):
            _sync_remote_device_channel_process_sessions_blocking = SidebarSessionsMixin._sync_remote_device_channel_process_sessions_blocking

            def __init__(self):
                self.agent_dir = "C:\\demo"

            def _auto_ssh_remote_devices(self):
                return [{"id": "box-1", "name": "Mac Mini"}]

            def _fetch_remote_channel_snapshots(self, device):
                return True, [
                    {
                        "channel_id": "wechat",
                        "channel_label": "微信",
                        "title": "微信 进程",
                        "updated_at": 654.0,
                        "process_status": "运行中",
                        "process_pid": 9876,
                        "process_started_at": 600.0,
                        "process_ended_at": 0.0,
                        "managed_by_launcher": True,
                        "bubble_text": "managed snapshot",
                    }
                ], ""

        dummy = DummySidebar()
        saved = []
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            lz, "load_session", return_value={}
        ), mock.patch.object(lz, "save_session", side_effect=lambda root, payload, touch=False: saved.append(dict(payload))), mock.patch.object(
            lz, "list_sessions", return_value=[]
        ):
            changed = dummy._sync_remote_device_channel_process_sessions_blocking(agent_dir="C:\\demo")

        self.assertTrue(changed)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["process_status"], "运行中")
        self.assertTrue(saved[0]["managed_by_launcher"])

    def test_remote_exec_json_script_uses_docker_exec_for_container_targets(self):
        class DummyClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class DummyChannel(ChannelRuntimeMixin):
            _extract_json_payload = ChannelRuntimeMixin._extract_json_payload
            _remote_exec_json_script = ChannelRuntimeMixin._remote_exec_json_script

            def __init__(self):
                self.commands = []
                self.client = DummyClient()

            def _settings_target_open_remote_client(self, device, timeout=10):
                return self.client, ""

            def _vps_exec_remote(self, client, cmd, timeout=0):
                self.commands.append(str(cmd))
                return 0, json.dumps({"ok": True, "mode": "docker"}, ensure_ascii=False), ""

        dummy = DummyChannel()
        device = {
            "id": "box-1",
            "host": "10.0.0.8",
            "username": "root",
            "agent_mode": "docker",
            "remote_mode": "docker_container",
            "docker_container": "ga-prod",
            "docker_agent_dir": "/opt/agant",
            "agent_dir": "/opt/agant",
            "python_cmd": "python3",
        }
        ok, payload, err = dummy._remote_exec_json_script(device, "print('ignored')", timeout=20)

        self.assertTrue(ok, msg=err)
        self.assertEqual(payload["mode"], "docker")
        self.assertTrue(dummy.client.closed)
        self.assertTrue(any("docker exec -i" in cmd and "ga-prod" in cmd for cmd in dummy.commands))
        self.assertTrue(any("/opt/agant" in cmd and "GA_REMOTE_PY" in cmd for cmd in dummy.commands))

    def test_restore_from_tray_mode_restores_maximized_window_state(self):
        class DummyEditor:
            def __init__(self):
                self.focus_calls = []

            def setFocus(self, reason):
                self.focus_calls.append(reason)

        class DummyFloating:
            def hide(self):
                return None

        class DummyHost:
            def __init__(self):
                self._tray_mode_active = True
                self._tray_restore_to_fullscreen = False
                self._tray_restore_to_maximized = True
                self._floating_chat_window = DummyFloating()
                self.input_box = DummyEditor()
                self.calls = []

            def isVisible(self):
                return False

            def _sync_draft_from_floating(self):
                self.calls.append("sync_from_floating")

            def showNormal(self):
                self.calls.append("show_normal")

            def showMaximized(self):
                self.calls.append("show_maximized")

            def showFullScreen(self):
                self.calls.append("show_fullscreen")

            def raise_(self):
                self.calls.append("raise")

            def activateWindow(self):
                self.calls.append("activate")

            def _show_chat_page(self):
                self.calls.append("show_chat_page")

            def _refresh_floating_chat_window(self):
                self.calls.append("refresh_floating")

            def _refresh_launcher_tray_menu(self):
                self.calls.append("refresh_tray")

            def _is_channel_process_session(self):
                return False

        dummy = DummyHost()
        with mock.patch.object(launcher_window.QTimer, "singleShot", side_effect=lambda _ms, cb: cb()):
            launcher_window.QtChatWindow._restore_from_tray_mode(dummy)

        self.assertFalse(dummy._tray_mode_active)
        self.assertFalse(dummy._tray_restore_to_maximized)
        self.assertIn("show_maximized", dummy.calls)
        self.assertNotIn("show_normal", dummy.calls)

    def test_lan_interface_external_running_requires_health_without_managed_proc(self):
        class DummyUsage(PersonalUsageMixin):
            def __init__(self):
                self._lan_interface_proc = None
                self._lan_interface_log_handle = None
                self._lan_interface_last_exit_code = None
                self.cfg = {"lan_interface": {"enabled": True, "auto_start": True, "bind_all": True, "port": 8501, "frontend": "foo.py"}}

            def _lan_interface_cfg(self):
                return dict(self.cfg.get("lan_interface") or {})

        dummy = DummyUsage()
        with mock.patch.object(dummy, "_lan_interface_proc_alive", return_value=False), mock.patch.object(
            dummy, "_lan_interface_health_ok", return_value=True
        ):
            self.assertTrue(dummy._lan_interface_external_running(8501))
        with mock.patch.object(dummy, "_lan_interface_proc_alive", return_value=True), mock.patch.object(
            dummy, "_lan_interface_health_ok", return_value=True
        ):
            self.assertFalse(dummy._lan_interface_external_running(8501))

    def test_format_launcher_installation_text_includes_manual_macos_install_contract(self):
        class DummyUsage(PersonalUsageMixin):
            _format_launcher_installation_text = PersonalUsageMixin._format_launcher_installation_text

        dummy = DummyUsage()
        status = {
            "summary": "当前仍在 dmg 挂载目录中运行，建议先拖到 /Applications；如果只想安装到当前用户，也可以改放 ~/Applications，然后重新打开。",
            "app_bundle_path": f"/Volumes/GenericAgentLauncher/{runtime.APP_DISPLAY_NAME}.app",
            "executable_path": f"/Volumes/GenericAgentLauncher/{runtime.APP_DISPLAY_NAME}.app/Contents/MacOS/GenericAgentLauncher",
            "recommended_install_target": f"/Applications/{runtime.APP_DISPLAY_NAME}.app",
            "user_applications_target": f"/Users/tester/Applications/{runtime.APP_DISPLAY_NAME}.app",
            "data_root": "/Users/tester/Library/Application Support/GenericAgentLauncher",
            "running_from_disk_image": True,
            "running_from_translocation": True,
            "needs_relocation": True,
        }
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(
            personal_usage.lz, "DATA_ROOT", status["data_root"]
        ), mock.patch.object(
            personal_usage.lz, "macos_installation_status", return_value=status
        ):
            text = dummy._format_launcher_installation_text()

        self.assertIn("安装方式：未做 Apple Developer 签名 / 未 notarize 的 dmg 手动安装 / 手动替换 .app 升级", text)
        self.assertIn("推荐安装位置：/Applications", text)
        self.assertIn("`/Volumes/...`", text)
        self.assertIn("App Translocation", text)
        self.assertIn("拖到 `/Applications`", text)
        self.assertIn("`~/Applications`", text)

    def test_schedule_startup_install_hint_posts_warn_summary_to_status_bar(self):
        class DummyUsage(PersonalUsageMixin):
            _schedule_startup_install_hint = PersonalUsageMixin._schedule_startup_install_hint

            def __init__(self):
                self._startup_install_hint_scheduled = False
                self._closing_in_progress = False
                self.statuses = []

            def _set_status(self, text):
                self.statuses.append(str(text))

        dummy = DummyUsage()
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(
            personal_usage.lz,
            "macos_installation_status",
            return_value={"status": "warn", "summary": "请先把 app 移动到 /Applications。"},
        ), mock.patch.object(personal_usage.QTimer, "singleShot", side_effect=lambda _ms, cb: cb()):
            dummy._schedule_startup_install_hint()

        self.assertEqual(dummy.statuses, ["请先把 app 移动到 /Applications。"])
        self.assertFalse(dummy._startup_install_hint_scheduled)

    def test_launcher_manual_update_payload_describes_manual_macos_install_contract(self):
        class DummyUsage(PersonalUsageMixin):
            _display_local_user_path = PersonalUsageMixin._display_local_user_path
            _launcher_manual_update_payload = PersonalUsageMixin._launcher_manual_update_payload

        dummy = DummyUsage()
        install_state = {
            "recommended_install_target": "/Applications/GenericAgent Launcher.app",
            "data_root": "/Users/tester/Library/Application Support/GenericAgentLauncher",
        }
        info = {
            "target_version": "1.2.4",
            "external_url": "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.dmg",
            "external_asset_name": "GenericAgentLauncher-macos-arm64-1.2.4.dmg",
            "release_url": "https://github.com/example/release/v1.2.4",
            "readme_url": "https://example.com/README-macOS-arm64.txt",
            "sha256_url": "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.sha256",
            "metadata_url": "https://example.com/install-metadata-arm64.json",
        }
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(
            personal_usage.lz, "DATA_ROOT", install_state["data_root"]
        ), mock.patch.object(
            personal_usage.lz, "macos_installation_status", return_value=install_state
        ):
            payload = dummy._launcher_manual_update_payload(info, launcher_row={"latest_release_tag": "v1.2.4"})

        self.assertEqual(payload["recommended_install_target"], "/Applications/GenericAgent Launcher.app")
        self.assertEqual(payload["data_root"], install_state["data_root"])
        self.assertEqual(payload["readme_url"], "https://example.com/README-macOS-arm64.txt")
        self.assertEqual(payload["sha256_url"], "https://example.com/GenericAgentLauncher-macos-arm64-1.2.4.sha256")
        self.assertEqual(payload["metadata_url"], "https://example.com/install-metadata-arm64.json")
        self.assertIn("目标版本：1.2.4", payload["detail_text"])
        self.assertIn("建议替换路径：/Applications/GenericAgent Launcher.app", payload["detail_text"])
        self.assertIn("用户数据目录：/Users/tester/Library/Application Support/GenericAgentLauncher", payload["detail_text"])
        self.assertIn("优先推荐放到 /Applications", payload["detail_text"])
        self.assertIn("README-macOS-arm64.txt", payload["detail_text"])
        self.assertIn("install-metadata-arm64.json", payload["detail_text"])
        self.assertIn("System Settings -> Privacy & Security -> Open Anyway", payload["detail_text"])
        self.assertIn("Finder 右键应用并选择 Open", payload["detail_text"])

    def test_launcher_manual_update_payload_uses_user_install_target_and_release_page_fallback(self):
        class DummyUsage(PersonalUsageMixin):
            _display_local_user_path = PersonalUsageMixin._display_local_user_path
            _launcher_manual_update_payload = PersonalUsageMixin._launcher_manual_update_payload

        dummy = DummyUsage()
        install_state = {
            "recommended_install_target": "/Users/tester/Applications/GenericAgent Launcher.app",
            "user_applications_target": "/Users/tester/Applications/GenericAgent Launcher.app",
            "installed_to_user_applications": True,
            "data_root": "/Users/tester/Library/Application Support/GenericAgentLauncher",
        }
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(
            personal_usage.lz, "DATA_ROOT", install_state["data_root"]
        ), mock.patch.object(
            personal_usage.lz, "macos_installation_status", return_value=install_state
        ), mock.patch.object(
            personal_usage.os.path, "expanduser", return_value="/Users/tester"
        ):
            payload = dummy._launcher_manual_update_payload(
                {},
                launcher_row={
                    "latest_release_tag": "v1.2.4",
                    "latest_release_url": "https://github.com/example/release/v1.2.4",
                },
            )

        self.assertEqual(payload["target_version"], "1.2.4")
        self.assertEqual(payload["recommended_install_target"], "/Users/tester/Applications/GenericAgent Launcher.app")
        self.assertEqual(payload["release_url"], "https://github.com/example/release/v1.2.4")
        self.assertIn("建议替换路径：~/Applications/GenericAgent Launcher.app", payload["detail_text"])
        self.assertIn("当前检测到的用户级安装路径：~/Applications/GenericAgent Launcher.app", payload["detail_text"])
        self.assertIn("当前未识别到可直接安装的 macOS .dmg，请改用 Release 页面或 Actions 构建产物。", payload["detail_text"])

    def test_refresh_about_update_widgets_sets_disabled_tooltips_while_check_running(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _refresh_about_update_widgets = PersonalUsageMixin._refresh_about_update_widgets
            _refresh_about_update_diagnostics_widgets = PersonalUsageMixin._refresh_about_update_diagnostics_widgets
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _about_update_check_disabled_reason = PersonalUsageMixin._about_update_check_disabled_reason
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason
            _kernel_sync_disabled_reason = PersonalUsageMixin._kernel_sync_disabled_reason

            def __init__(self):
                self.agent_dir = "C:\\demo"
                self._update_check_running = True
                self._kernel_repo_sync_running = False
                self._last_update_check_result = {
                    "launcher": {
                        "status": "behind",
                        "update_info": {"install_mode": "external", "external_url": "https://example.com/update.exe"},
                    }
                }
                self.settings_about_update_status = DummyLabel()
                self.settings_about_update_diag_status = DummyLabel()
                self.settings_about_check_updates_btn = DummyButton()
                self.settings_about_install_update_btn = DummyButton()
                self.settings_about_sync_kernel_fetch_btn = DummyButton()
                self.settings_about_sync_kernel_pull_btn = DummyButton()

            def _update_history_brief_text(self, limit=3):
                return "history"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=True), mock.patch.object(
            personal_usage.os.path, "isfile", return_value=True
        ), mock.patch.object(
            lz, "updater_executable_path", return_value="C:\\demo\\updater.exe"
        ), mock.patch.object(
            lz, "PLATFORM_SUPPORTS_INTERNAL_UPDATER", True
        ):
            dummy._refresh_about_update_widgets()

        self.assertFalse(dummy.settings_about_check_updates_btn.enabled)
        self.assertEqual(dummy.settings_about_check_updates_btn.text, "正在检测…")
        self.assertEqual(dummy.settings_about_check_updates_btn.tooltip, "当前正在检测 GitHub 更新，请稍候。")
        self.assertFalse(dummy.settings_about_install_update_btn.enabled)
        self.assertEqual(dummy.settings_about_install_update_btn.tooltip, "当前正在检测 GitHub 更新，请稍候。")
        self.assertFalse(dummy.settings_about_sync_kernel_fetch_btn.enabled)
        self.assertFalse(dummy.settings_about_sync_kernel_pull_btn.enabled)
        self.assertEqual(dummy.settings_about_sync_kernel_fetch_btn.tooltip, "当前正在检测 GitHub 更新，请稍后再执行仓库同步。")
        self.assertEqual(dummy.settings_about_sync_kernel_pull_btn.tooltip, "当前正在检测 GitHub 更新，请稍后再执行仓库同步。")

    def test_refresh_about_update_widgets_explains_missing_updater_and_repo_dir(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _refresh_about_update_widgets = PersonalUsageMixin._refresh_about_update_widgets
            _refresh_about_update_diagnostics_widgets = PersonalUsageMixin._refresh_about_update_diagnostics_widgets
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _about_update_check_disabled_reason = PersonalUsageMixin._about_update_check_disabled_reason
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason
            _kernel_sync_disabled_reason = PersonalUsageMixin._kernel_sync_disabled_reason

            def __init__(self):
                self.agent_dir = ""
                self._update_check_running = False
                self._kernel_repo_sync_running = False
                self._last_update_check_result = {
                    "launcher": {
                        "status": "behind",
                        "update_info": {"install_mode": "internal"},
                    }
                }
                self.settings_about_update_status = DummyLabel()
                self.settings_about_update_diag_status = DummyLabel()
                self.settings_about_check_updates_btn = DummyButton()
                self.settings_about_install_update_btn = DummyButton()
                self.settings_about_sync_kernel_fetch_btn = DummyButton()
                self.settings_about_sync_kernel_pull_btn = DummyButton()

            def _update_history_brief_text(self, limit=3):
                return "history"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False), mock.patch.object(
            personal_usage.os.path, "isfile", return_value=False
        ), mock.patch.object(
            lz, "updater_executable_path", return_value="C:\\missing\\updater.exe"
        ), mock.patch.object(
            lz, "PLATFORM_SUPPORTS_INTERNAL_UPDATER", True
        ):
            dummy._refresh_about_update_widgets()

        self.assertFalse(dummy.settings_about_install_update_btn.enabled)
        self.assertEqual(dummy.settings_about_install_update_btn.tooltip, "当前缺少内置 updater，暂时不能直接安装更新。")
        self.assertFalse(dummy.settings_about_sync_kernel_fetch_btn.enabled)
        self.assertFalse(dummy.settings_about_sync_kernel_pull_btn.enabled)
        self.assertEqual(dummy.settings_about_sync_kernel_fetch_btn.tooltip, "当前没有可用的内核 Git 仓库目录。")
        self.assertEqual(dummy.settings_about_sync_kernel_pull_btn.tooltip, "当前没有可用的内核 Git 仓库目录。")

    def test_refresh_about_update_widgets_enables_manual_macos_update_when_release_page_exists(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _refresh_about_update_widgets = PersonalUsageMixin._refresh_about_update_widgets
            _refresh_about_update_diagnostics_widgets = PersonalUsageMixin._refresh_about_update_diagnostics_widgets
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _about_update_check_disabled_reason = PersonalUsageMixin._about_update_check_disabled_reason
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason
            _kernel_sync_disabled_reason = PersonalUsageMixin._kernel_sync_disabled_reason

            def __init__(self):
                self.agent_dir = ""
                self._update_check_running = False
                self._kernel_repo_sync_running = False
                self._last_update_check_result = {
                    "launcher": {
                        "status": "behind",
                        "latest_release_url": "https://github.com/example/release/v1.2.4",
                        "update_info": None,
                    }
                }
                self.settings_about_update_status = DummyLabel()
                self.settings_about_update_diag_status = DummyLabel()
                self.settings_about_check_updates_btn = DummyButton()
                self.settings_about_install_update_btn = DummyButton()
                self.settings_about_sync_kernel_fetch_btn = DummyButton()
                self.settings_about_sync_kernel_pull_btn = DummyButton()

            def _update_history_brief_text(self, limit=3):
                return "history"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False), mock.patch.object(
            personal_usage.os.path, "isfile", return_value=False
        ), mock.patch.object(
            lz, "updater_executable_path", return_value=""
        ), mock.patch.object(
            lz, "PLATFORM_SUPPORTS_INTERNAL_UPDATER", False
        ):
            dummy._refresh_about_update_widgets()

        self.assertTrue(dummy.settings_about_install_update_btn.enabled)
        self.assertEqual(dummy.settings_about_install_update_btn.text, "查看手动升级说明")
        self.assertEqual(dummy.settings_about_install_update_btn.tooltip, "查看当前版本对应的手动升级说明。")

    def test_about_update_install_disabled_reason_requires_manual_update_link_for_partial_macos_metadata(self):
        class DummyUsage(PersonalUsageMixin):
            _about_manual_update_action_target = PersonalUsageMixin._about_manual_update_action_target
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason

        dummy = DummyUsage()
        reason = dummy._about_update_install_disabled_reason(
            behind=True,
            update_info={"install_mode": "external", "target_version": "1.2.4"},
            supports_internal_update=False,
            manual_release_url="",
        )

        self.assertEqual(reason, "当前未拿到可用的发布页面或安装包链接，请先重新检测。")

    def test_open_launcher_install_recommended_dir_uses_user_applications_for_user_level_install(self):
        class DummyUsage(PersonalUsageMixin):
            _launcher_install_recommended_directory = PersonalUsageMixin._launcher_install_recommended_directory
            _open_launcher_install_recommended_dir = PersonalUsageMixin._open_launcher_install_recommended_dir

        dummy = DummyUsage()
        with mock.patch.object(personal_usage.lz, "IS_MACOS", True), mock.patch.object(
            personal_usage.lz,
            "macos_installation_status",
            return_value={
                "recommended_install_target": "/Users/tester/Applications/GenericAgent Launcher.app",
            },
        ), mock.patch.object(
            dummy, "_open_local_directory_path"
        ) as open_dir:
            dummy._open_launcher_install_recommended_dir()

        open_dir.assert_called_once_with("/Users/tester/Applications")

    def test_refresh_about_update_widgets_disables_manual_macos_update_without_links(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _refresh_about_update_widgets = PersonalUsageMixin._refresh_about_update_widgets
            _refresh_about_update_diagnostics_widgets = PersonalUsageMixin._refresh_about_update_diagnostics_widgets
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _about_update_check_disabled_reason = PersonalUsageMixin._about_update_check_disabled_reason
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason
            _kernel_sync_disabled_reason = PersonalUsageMixin._kernel_sync_disabled_reason

            def __init__(self):
                self.agent_dir = ""
                self._update_check_running = False
                self._kernel_repo_sync_running = False
                self._last_update_check_result = {
                    "launcher": {
                        "status": "behind",
                        "latest_release_url": "",
                        "update_info": None,
                    }
                }
                self.settings_about_update_status = DummyLabel()
                self.settings_about_update_diag_status = DummyLabel()
                self.settings_about_check_updates_btn = DummyButton()
                self.settings_about_install_update_btn = DummyButton()
                self.settings_about_sync_kernel_fetch_btn = DummyButton()
                self.settings_about_sync_kernel_pull_btn = DummyButton()

            def _update_history_brief_text(self, limit=3):
                return "history"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False), mock.patch.object(
            personal_usage.os.path, "isfile", return_value=False
        ), mock.patch.object(
            lz, "updater_executable_path", return_value=""
        ), mock.patch.object(
            lz, "PLATFORM_SUPPORTS_INTERNAL_UPDATER", False
        ):
            dummy._refresh_about_update_widgets()

        self.assertFalse(dummy.settings_about_install_update_btn.enabled)
        self.assertEqual(dummy.settings_about_install_update_btn.text, "查看手动升级说明")
        self.assertEqual(dummy.settings_about_install_update_btn.tooltip, "当前未拿到可用的发布页面或安装包链接，请先重新检测。")

    def test_refresh_about_update_widgets_disables_manual_macos_update_for_partial_metadata_without_links(self):
        class DummyLabel:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = str(text)

        class DummyButton:
            def __init__(self):
                self.enabled = None
                self.tooltip = ""
                self.text = ""

            def setEnabled(self, value):
                self.enabled = bool(value)

            def setToolTip(self, text):
                self.tooltip = str(text)

            def setText(self, text):
                self.text = str(text)

        class DummyUsage(PersonalUsageMixin):
            _about_manual_update_action_target = PersonalUsageMixin._about_manual_update_action_target
            _refresh_about_update_widgets = PersonalUsageMixin._refresh_about_update_widgets
            _refresh_about_update_diagnostics_widgets = PersonalUsageMixin._refresh_about_update_diagnostics_widgets
            _apply_personal_button_state = PersonalUsageMixin._apply_personal_button_state
            _about_update_check_disabled_reason = PersonalUsageMixin._about_update_check_disabled_reason
            _about_update_install_disabled_reason = PersonalUsageMixin._about_update_install_disabled_reason
            _kernel_sync_disabled_reason = PersonalUsageMixin._kernel_sync_disabled_reason

            def __init__(self):
                self.agent_dir = ""
                self._update_check_running = False
                self._kernel_repo_sync_running = False
                self._last_update_check_result = {
                    "launcher": {
                        "status": "behind",
                        "latest_release_url": "",
                        "update_info": {
                            "install_mode": "external",
                            "target_version": "1.2.4",
                        },
                    }
                }
                self.settings_about_update_status = DummyLabel()
                self.settings_about_update_diag_status = DummyLabel()
                self.settings_about_check_updates_btn = DummyButton()
                self.settings_about_install_update_btn = DummyButton()
                self.settings_about_sync_kernel_fetch_btn = DummyButton()
                self.settings_about_sync_kernel_pull_btn = DummyButton()

            def _update_history_brief_text(self, limit=3):
                return "history"

        dummy = DummyUsage()
        with mock.patch.object(lz, "is_valid_agent_dir", return_value=False), mock.patch.object(
            personal_usage.os.path, "isfile", return_value=False
        ), mock.patch.object(
            lz, "updater_executable_path", return_value=""
        ), mock.patch.object(
            lz, "PLATFORM_SUPPORTS_INTERNAL_UPDATER", False
        ):
            dummy._refresh_about_update_widgets()

        self.assertFalse(dummy.settings_about_install_update_btn.enabled)
        self.assertEqual(dummy.settings_about_install_update_btn.text, "查看手动升级说明")
        self.assertEqual(dummy.settings_about_install_update_btn.tooltip, "当前未拿到可用的发布页面或安装包链接，请先重新检测。")

    def test_launcher_bootstrap_uses_semver_like_sort_for_versions(self):
        with tempfile.TemporaryDirectory() as td:
            versions_dir = os.path.join(td, "app", "versions")
            os.makedirs(os.path.join(versions_dir, "1.9.9"), exist_ok=True)
            os.makedirs(os.path.join(versions_dir, "1.10.0"), exist_ok=True)
            main_exe_name = "GenericAgentLauncher.exe"
            older_exe = os.path.join(versions_dir, "1.9.9", main_exe_name)
            newer_exe = os.path.join(versions_dir, "1.10.0", main_exe_name)
            for path in (older_exe, newer_exe):
                with open(path, "wb") as f:
                    f.write(b"main")

            with mock.patch.object(launcher_bootstrap, "MAIN_EXE_NAME", main_exe_name), mock.patch.object(
                launcher_bootstrap, "load_version_state", return_value={}
            ), mock.patch.object(
                launcher_bootstrap, "resolved_versions_dir", return_value=versions_dir
            ), mock.patch.object(launcher_bootstrap, "set_current_version") as set_current:
                picked = launcher_bootstrap._pick_target_executable()

            self.assertEqual(os.path.normcase(os.path.normpath(picked)), os.path.normcase(os.path.normpath(newer_exe)))
            set_current.assert_called_once_with("1.10.0", previous_version="", pending_update={})

    def test_update_public_key_loader_walks_up_from_version_dir(self):
        with tempfile.TemporaryDirectory() as td:
            install_root = os.path.join(td, "Programs", "GenericAgentLauncher")
            version_dir = os.path.join(install_root, "app", "versions", "1.2.3")
            os.makedirs(version_dir, exist_ok=True)
            key_path = os.path.join(install_root, "update_public_key.pem")
            expected = "-----BEGIN PUBLIC KEY-----\nabc123\n-----END PUBLIC KEY-----"
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(expected + "\n")

            with mock.patch.object(constants, "APP_DIR", version_dir), mock.patch.dict(
                os.environ, {"GA_LAUNCHER_UPDATE_PUBLIC_KEY_PEM": ""}, clear=False
            ):
                loaded = constants._load_update_public_key()

            self.assertEqual(loaded, expected)

    def test_installer_uninstall_cleans_runtime_version_tree(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "installer", "GenericAgentLauncher.iss")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("CloseApplications=yes", src)
        self.assertIn("RestartApplications=no", src)
        self.assertIn('Type: filesandordirs; Name: "{app}\\app"', src)
        self.assertIn('StateDir := ExpandConstant(\'{localappdata}\\GenericAgentLauncher\\state\');', src)
        self.assertIn('SaveStringToFile(StatePath, LauncherStateJson(), False)', src)
        self.assertIn('"current_version": "{#MyVersion}"', src)

    def test_cleanup_old_versions_uses_version_aware_sort(self):
        with tempfile.TemporaryDirectory() as td:
            versions_dir = os.path.join(td, "app", "versions")
            os.makedirs(os.path.join(versions_dir, "1.9.9"), exist_ok=True)
            os.makedirs(os.path.join(versions_dir, "1.10.0"), exist_ok=True)
            os.makedirs(os.path.join(versions_dir, "1.10.1"), exist_ok=True)

            with mock.patch.object(runtime, "resolved_versions_dir", return_value=versions_dir), mock.patch.object(
                runtime, "load_version_state", return_value={"current_version": "1.10.1", "previous_version": "1.10.0"}
            ):
                removed = runtime.cleanup_old_versions(keep_count=2)

            self.assertEqual(removed, ["1.9.9"])
            self.assertFalse(os.path.isdir(os.path.join(versions_dir, "1.9.9")))
            self.assertTrue(os.path.isdir(os.path.join(versions_dir, "1.10.0")))
            self.assertTrue(os.path.isdir(os.path.join(versions_dir, "1.10.1")))

    def test_normalize_token_usage_from_bubbles(self):
        session = {
            "id": "s1",
            "channel_id": "unknown",
            "bubbles": [
                {"role": "user", "text": "hello"},
                {"role": "assistant", "text": "world"},
            ],
        }
        lz._normalize_token_usage_inplace(session)

        usage = session["token_usage"]
        self.assertEqual(session["channel_id"], "launcher")
        self.assertEqual(usage["mode"], "estimate_chars_div_2_5")
        self.assertEqual(usage["turns"], 1)
        self.assertEqual(len(usage["events"]), 1)
        self.assertGreater(usage["total_tokens"], 0)

    def test_normalize_token_usage_preserves_manual_clear_without_fallback_rebuild(self):
        session = {
            "id": "s1",
            "channel_id": "launcher",
            "bubbles": [
                {"role": "user", "text": "hello"},
                {"role": "assistant", "text": "world"},
            ],
            "token_usage": {"events": [], "launcher_usage_cleared": True},
        }
        lz._normalize_token_usage_inplace(session)
        usage = session["token_usage"]
        self.assertEqual(usage["events"], [])
        self.assertEqual(usage["total_tokens"], 0)
        self.assertTrue(usage.get("launcher_usage_cleared"))

    def test_fold_turns_returns_fold_section(self):
        text = (
            "prefix\n"
            "**LLM Running (Turn 1) ...**"
            "<summary>first turn summary</summary>\n"
            "turn1 body\n"
            "**LLM Running (Turn 2) ...**"
            "final body"
        )
        segments = lz.fold_turns(text)
        self.assertGreaterEqual(len(segments), 2)
        self.assertTrue(any(seg.get("type") == "fold" for seg in segments))

    def test_model_api_helpers(self):
        payload = {
            "data": [
                {"id": "gpt-4.1"},
                {"id": "gpt-4.1"},
                {"name": "claude-opus"},
            ]
        }
        models = model_api._extract_model_ids(payload)
        self.assertEqual(models, ["gpt-4.1", "claude-opus"])

        base = model_api._oai_models_base("https://api.openai.com/v1/chat/completions")
        self.assertEqual(base, "https://api.openai.com/v1")

    def test_save_then_load_session(self):
        with tempfile.TemporaryDirectory() as td:
            session = {
                "id": "case1",
                "title": "demo",
                "channel_id": "launcher",
                "bubbles": [
                    {"role": "user", "text": "u"},
                    {"role": "assistant", "text": "a"},
                ],
            }
            lz.save_session(td, session, touch=False)
            loaded = lz.load_session(td, "case1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["id"], "case1")
            self.assertIn("token_usage", loaded)

    def test_list_scheduled_tasks_reads_upstream_style_json(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "reflect"), exist_ok=True)
            os.makedirs(os.path.join(td, "sche_tasks", "done"), exist_ok=True)
            with open(os.path.join(td, "reflect", "scheduler.py"), "w", encoding="utf-8") as f:
                f.write("# scheduler")
            with open(os.path.join(td, "sche_tasks", "morning.json"), "w", encoding="utf-8") as f:
                f.write(
                    '{"schedule":"08:00","repeat":"daily","enabled":true,"prompt":"生成晨报","max_delay_hours":6}'
                )
            with open(os.path.join(td, "sche_tasks", "done", "2026-04-22_0800_morning.md"), "w", encoding="utf-8") as f:
                f.write("done")

            data = lz.list_scheduled_tasks(td, now=None)

        self.assertTrue(data["supported"])
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["id"], "morning")
        self.assertEqual(data["tasks"][0]["repeat"], "daily")
        self.assertEqual(data["tasks"][0]["schedule"], "08:00")
        self.assertEqual(data["tasks"][0]["report_count"], 1)
        self.assertEqual(data["enabled_count"], 1)

    def test_scheduled_task_save_load_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            payload = {
                "schedule": "09:30",
                "repeat": "weekday",
                "enabled": True,
                "prompt": "生成日报",
                "max_delay_hours": 4,
                "extra_fields": {"priority": "high"},
            }
            result = lz.save_scheduled_task(td, "day report", payload)
            loaded = lz.load_scheduled_task(td, result["task_id"])

            self.assertEqual(result["task_id"], "day_report")
            self.assertEqual(loaded["schedule"], "09:30")
            self.assertEqual(loaded["repeat"], "weekday")
            self.assertTrue(loaded["enabled"])
            self.assertEqual(loaded["extra_fields"]["priority"], "high")
            self.assertTrue(lz.delete_scheduled_task(td, result["task_id"]))
            self.assertFalse(os.path.exists(os.path.join(td, "sche_tasks", "day_report.json")))

    def test_normalize_scheduled_task_id_strips_invalid_filename_chars(self):
        self.assertEqual(lz.normalize_scheduled_task_id(' 早报 : 任务 ? '), "早报_任务")


if __name__ == "__main__":
    unittest.main()
