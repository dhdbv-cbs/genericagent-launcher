from __future__ import annotations

import ast
import ctypes
import json
import types
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

import bridge
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QHelpEvent, QImage, QMouseEvent, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolTip
from launcher_app import app_icon as launcher_app_icon
from launcher_app import core as lz
from launcher_app import theme as launcher_theme
from launcher_app import window as launcher_window
from launcher_core_parts import channels as channels_mod
from launcher_core_parts import conductor_runtime as conductor_runtime_mod
from launcher_core_parts import model_api
from launcher_core_parts import python_env
from launcher_core_parts import sessions as sessions_mod
from launcher_core_parts import upstream_dependencies
from qt_chat_parts import personal_usage as personal_usage_mod
from qt_chat_parts.api_editor import ApiEditorMixin
from qt_chat_parts import common
from qt_chat_parts import settings_panel


def _workflow_named_steps(path: str) -> list[dict[str, object]]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    steps: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        match = re.match(r"^ {6}- name: (.+)$", line)
        if match:
            if current is not None:
                steps.append(current)
            current = {"name": match.group(1).strip(), "lines": []}
            continue
        if current is not None:
            cast_lines = current["lines"]
            assert isinstance(cast_lines, list)
            cast_lines.append(line)
    if current is not None:
        steps.append(current)
    return steps


def _workflow_step_map(path: str) -> dict[str, str]:
    steps = _workflow_named_steps(path)
    return {str(step["name"]): "\n".join(step["lines"]) for step in steps}


class LauncherCoreBehaviorTests(unittest.TestCase):
    def test_option_card_children_allow_parent_click_handling_and_release_triggers_command(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app.processEvents())

        triggered: list[str] = []
        card = common.OptionCard("⬇", "下载", "进入下载页", lambda: triggered.append("clicked"))
        card.resize(320, 96)

        labels = card.findChildren(QLabel)
        self.assertTrue(labels)
        for label in labels:
            self.assertTrue(label.testAttribute(Qt.WA_TransparentForMouseEvents), msg=label.text())

        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(24, 24),
            QPointF(24, 24),
            QPointF(24, 24),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(24, 24),
            QPointF(24, 24),
            QPointF(24, 24),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )

        card.mousePressEvent(press)
        card.mouseReleaseEvent(release)

        self.assertEqual(triggered, ["clicked"])

    def test_python_env_bootstrap_detector_catches_requests_and_simplejson_failures(self):
        self.assertTrue(
            python_env._should_bootstrap_python_runtime("ModuleNotFoundError: No module named 'requests'")
        )
        self.assertTrue(
            python_env._should_bootstrap_python_runtime(
                "ImportError: cannot import name JSONDecodeError from simplejson"
            )
        )
        self.assertFalse(python_env._should_bootstrap_python_runtime("SyntaxError: invalid syntax"))

    def test_setup_pages_imports_os_for_platform_fallbacks(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("import os", src)
        self.assertIn('os.name == "nt"', src)

    def test_requirements_include_packaged_runtime_dependencies(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "requirements.txt")
        with open(path, "r", encoding="utf-8") as f:
            requirements = {line.strip() for line in f if line.strip() and not line.lstrip().startswith("#")}
        for dependency in (
            "requests>=2.31",
            "simplejson>=3.19.3",
            "charset-normalizer>=3.3",
            "cryptography>=43.0",
        ):
            self.assertIn(dependency, requirements)

    def test_python_env_split_requirement_tokens(self):
        self.assertEqual(
            python_env._split_requirement_tokens("pycryptodome qrcode requests>=2.31"),
            ["pycryptodome", "qrcode", "requests>=2.31"],
        )

    def test_python_env_package_import_name_mapping(self):
        self.assertEqual(python_env._package_import_name("python-telegram-bot"), "telegram")
        self.assertEqual(python_env._package_import_name("qq-botpy>=1.0"), "botpy")
        self.assertEqual(python_env._package_import_name("requests>=2.31"), "requests")

    def test_python_env_version_floor_is_not_presence_only(self):
        self.assertTrue(python_env._version_meets_minimum("3.19.3", "3.19.3"))
        self.assertTrue(python_env._version_meets_minimum("3.20.0", "3.19.3"))
        self.assertFalse(python_env._version_meets_minimum("3.5.0", "3.19.3"))

    def test_python_env_probe_dependency_rejects_old_version(self):
        original = python_env._probe_python_module

        def fake_probe(_py, module_name):
            return True, f"版本 3.5.0 | {module_name}.py", {"version": "3.5.0", "path": f"{module_name}.py"}

        python_env._probe_python_module = fake_probe
        try:
            ok, detail, _payload = python_env._probe_python_dependency(
                "python.exe",
                "simplejson>=3.19.3",
                import_name="simplejson",
            )
        finally:
            python_env._probe_python_module = original
        self.assertFalse(ok)
        self.assertIn("版本过低", detail)
        self.assertIn(">= 3.19.3", detail)

    def test_python_env_sync_needed_when_state_mismatch_even_without_requirements(self):
        self.assertTrue(
            python_env._should_sync_runtime_dependencies(
                state_matches=False,
                extra_packages=[],
                sync_mode="",
                force_sync=False,
            )
        )
        self.assertFalse(
            python_env._should_sync_runtime_dependencies(
                state_matches=True,
                extra_packages=[],
                sync_mode="",
                force_sync=False,
            )
        )

    def test_python_env_core_runtime_packages_ready_checks_bootstrap_modules(self):
        original = python_env._probe_python_module
        calls = []

        def fake_probe(_py, module_name):
            calls.append(module_name)
            if module_name == "simplejson":
                return False, "missing", {}
            return True, "版本 2.33.1 | requests.py", {"version": "2.33.1", "path": "requests.py"}

        python_env._probe_python_module = fake_probe
        try:
            self.assertFalse(python_env._core_runtime_packages_ready("python.exe"))
        finally:
            python_env._probe_python_module = original
        self.assertIn("requests", calls)
        self.assertIn("simplejson", calls)

    def test_python_env_core_runtime_packages_ready_rejects_old_simplejson(self):
        original = python_env._probe_python_module

        def fake_probe(_py, module_name):
            if module_name == "simplejson":
                return True, "版本 3.5.0 | simplejson.py", {"version": "3.5.0", "path": "simplejson.py"}
            return True, "版本 2.33.1 | requests.py", {"version": "2.33.1", "path": "requests.py"}

        python_env._probe_python_module = fake_probe
        try:
            self.assertFalse(python_env._core_runtime_packages_ready("python.exe"))
        finally:
            python_env._probe_python_module = original

    def test_python_env_dependency_signature_tracks_requirements_file(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "requirements.txt")
            with open(fp, "w", encoding="utf-8") as f:
                f.write("requests>=2.31\n")
            sig1 = python_env._dependency_signature(td, extra_packages=["qrcode"])
            with open(fp, "w", encoding="utf-8") as f:
                f.write("requests>=2.31\nsimplejson>=3.19.3\n")
            sig2 = python_env._dependency_signature(td, extra_packages=["qrcode"])
        self.assertEqual(sig1["sync_mode"], "requirements")
        self.assertEqual(sig2["sync_mode"], "requirements")
        self.assertNotEqual(sig1["sync_hash"], sig2["sync_hash"])

    def test_python_env_dependency_signature_tracks_pyproject_sync_hash(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "pyproject.toml")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(
                    "[project]\n"
                    "name = 'genericagent'\n"
                    "version = '0.1.0'\n"
                    "dependencies = ['requests>=2.28']\n"
                )
            sig1 = python_env._dependency_signature(td, extra_packages=[])
            with open(fp, "w", encoding="utf-8") as f:
                f.write(
                    "[project]\n"
                    "name = 'genericagent'\n"
                    "version = '0.1.0'\n"
                    "dependencies = ['requests>=2.28', 'beautifulsoup4>=4.12']\n"
                )
            sig2 = python_env._dependency_signature(td, extra_packages=[])
        self.assertEqual(sig1["sync_mode"], "pyproject")
        self.assertEqual(sig2["sync_mode"], "pyproject")
        self.assertNotEqual(sig1["sync_hash"], sig2["sync_hash"])

    def test_python_env_macos_absolute_candidates_cover_homebrew_and_pyenv(self):
        existing = {
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
            "/Users/demo/.pyenv/shims/python3",
        }
        with mock.patch.object(python_env.sys, "platform", "darwin"), mock.patch.object(
            python_env.os.path, "expanduser", return_value="/Users/demo"
        ), mock.patch.object(python_env.os.path, "isfile", side_effect=lambda path: path in existing):
            candidates = python_env._macos_python_absolute_commands()

        self.assertIn(["/opt/homebrew/bin/python3"], candidates)
        self.assertIn(["/usr/local/bin/python3"], candidates)
        self.assertIn(["/Library/Frameworks/Python.framework/Versions/Current/bin/python3"], candidates)
        self.assertIn(["/Users/demo/.pyenv/shims/python3"], candidates)

    def test_python_env_system_commands_append_macos_absolute_fallbacks(self):
        with mock.patch.object(python_env.os, "name", "posix"), mock.patch.object(
            python_env, "load_config", return_value={}
        ), mock.patch.object(
            python_env, "_macos_python_absolute_commands", return_value=[["/opt/homebrew/bin/python3"]]
        ):
            candidates = python_env._system_python_commands()

        self.assertEqual(candidates[:2], [["python3"], ["python"]])
        self.assertIn(["/opt/homebrew/bin/python3"], candidates)

    def test_bridge_sanitize_agent_llmclients_filters_bad_dict_placeholders(self):
        class DummyBackend:
            def __init__(self):
                self.history = []

        class DummyClient:
            def __init__(self, name):
                self.backend = DummyBackend()
                self.name = name
                self.last_tools = "stale"

        class DummyAgent:
            def __init__(self):
                self.llmclients = [{"mixin_cfg": {"llm_nos": [0, 1]}}, DummyClient("ok")]
                self.llm_no = 0
                self.llmclient = self.llmclients[0]

        agent = DummyAgent()
        ok, msg = bridge._sanitize_agent_llmclients(agent)
        self.assertTrue(ok)
        self.assertIn("已忽略 1 个无效 LLM 配置条目", msg)
        self.assertEqual(len(agent.llmclients), 1)
        self.assertIs(agent.llmclient, agent.llmclients[0])
        self.assertEqual(agent.llm_no, 0)
        self.assertEqual(agent.llmclient.last_tools, "")

    def test_bridge_ui_llms_strips_provider_prefix_but_keeps_display_name(self):
        class DummyAgent:
            def list_llms(self):
                return [(0, "anthropic/claude-opus-4-6", True)]

        items = bridge._ui_llms(DummyAgent())
        self.assertEqual(items[0]["name"], "claude-opus-4-6")

    def test_bridge_claude_sse_patch_preserves_thinking_signature_field(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "bridge.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('current_block = {"type": "thinking", "thinking": "", "signature": ""}', src)
        self.assertIn('elif delta.get("type") == "signature_delta":', src)
        self.assertIn('current_block["signature"] = current_block.get("signature", "") + delta.get("signature", "")', src)

    def test_bridge_usage_patch_wraps_llmcore_record_usage_for_non_stream_paths(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "bridge.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _record_usage_patched(usage, api_mode):", src)
        self.assertIn("_store_current_usage(_normalize_provider_usage(usage))", src)
        self.assertIn('llmcore._record_usage = _record_usage_patched', src)
        self.assertIn('_record_usage_patched._ga_launcher_original = getattr(llmcore, "_record_usage", None)', src)

    def test_bridge_strips_pyinstaller_runtime_dir_from_sys_path(self):
        original_file = bridge.__file__
        original_sys_path = list(bridge.sys.path)
        try:
            with tempfile.TemporaryDirectory() as td:
                bridge.__file__ = os.path.join(td, "bridge.py")
                with open(os.path.join(td, "python312.dll"), "w", encoding="utf-8") as f:
                    f.write("stub")
                bridge.sys.path[:] = [td, os.path.join(td, "nested"), "C:\\safe"]
                bridge._strip_incompatible_pyinstaller_runtime_from_sys_path()
                self.assertNotIn(os.path.normcase(os.path.abspath(td)), [os.path.normcase(os.path.abspath(p)) for p in bridge.sys.path])
                self.assertIn("C:\\safe", bridge.sys.path)
        finally:
            bridge.__file__ = original_file
            bridge.sys.path[:] = original_sys_path

    def test_bridge_keeps_normal_script_dir_on_sys_path(self):
        original_file = bridge.__file__
        original_sys_path = list(bridge.sys.path)
        try:
            with tempfile.TemporaryDirectory() as td:
                bridge.__file__ = os.path.join(td, "bridge.py")
                bridge.sys.path[:] = [td, "C:\\safe"]
                bridge._strip_incompatible_pyinstaller_runtime_from_sys_path()
                self.assertEqual(bridge.sys.path[0], td)
        finally:
            bridge.__file__ = original_file
            bridge.sys.path[:] = original_sys_path

    def test_bridge_runtime_sanitizer_is_not_called_at_import_time(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "bridge.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        module = ast.parse(src, filename=path)
        strip_name = "_strip_incompatible_pyinstaller_runtime_from_sys_path"
        strip_defs = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == strip_name]
        self.assertEqual(len(strip_defs), 1)
        top_level_calls = [
            node for node in module.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == strip_name
        ]
        self.assertEqual(top_level_calls, [])
        main_defs = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
        self.assertEqual(len(main_defs), 1)
        main_calls = [
            node for node in ast.walk(main_defs[0])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == strip_name
        ]
        self.assertEqual(len(main_calls), 1)

    def test_theme_font_helpers_return_platform_appropriate_defaults(self):
        mac_ui = launcher_theme._default_ui_font_family("darwin")
        win_ui = launcher_theme._default_ui_font_family("win32")
        mac_mono = launcher_theme._default_mono_font_family("darwin")
        win_mono = launcher_theme._default_mono_font_family("win32")

        self.assertIn("PingFang SC", mac_ui)
        self.assertNotIn("Segoe UI", mac_ui)
        self.assertIn("Segoe UI", win_ui)
        self.assertIn("SF Mono", mac_mono)
        self.assertIn("Consolas", win_mono)

    def test_launcher_icon_svg_has_expected_shell_shape(self):
        svg = launcher_app_icon.launcher_icon_svg()
        self.assertIn("<svg", svg)
        self.assertIn("linearGradient", svg)
        self.assertIn('stroke="#22D3EE"', svg)
        self.assertIn('fill="#67E8F9"', svg)
        self.assertIn('fill="#08111F"', svg)

    def test_common_markdown_css_uses_theme_font_stacks(self):
        css = common._build_md_css()
        self.assertIn(f"font-family: {launcher_theme.F['font_family']};", css)
        self.assertIn(f"font-family: {launcher_theme.F['font_family_mono']};", css)

    def test_theme_font_settings_use_platform_preferred_family_helper(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("preferred_theme_font_families", src)
        self.assertIn("preferred = preferred_theme_font_families()", src)

    def test_theme_visual_presets_change_multiple_palette_roles(self):
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        try:
            launcher_theme.set_theme("light")
            launcher_theme.configure_visual_preferences({"theme_visual_preset": "paper", "theme_bg_preset": "default"})
            paper_palette = {
                key: launcher_theme.C[key]
                for key in ("bg", "panel", "sidebar_bg", "card", "field_alt", "accent")
            }

            launcher_theme.set_theme("light")
            launcher_theme.configure_visual_preferences({"theme_visual_preset": "mist", "theme_bg_preset": "default"})
            changed = [key for key, value in paper_palette.items() if launcher_theme.C.get(key) != value]

            self.assertGreaterEqual(len(changed), 6)
            self.assertEqual(launcher_theme.app_surface_background(), launcher_theme.C["bg"])
            self.assertEqual(launcher_theme._VISUAL_PREFS["visual_preset"], "mist")
            self.assertEqual(launcher_theme._VISUAL_PREFS["bg_preset"], "default")
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)

    def test_theme_visual_preset_compatibility_maps_legacy_background_values(self):
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        try:
            self.assertEqual(launcher_theme.resolve_theme_visual_preset({}), "graphite")
            self.assertEqual(launcher_theme.resolve_theme_visual_preset({"theme_bg_preset": "default"}), "graphite")
            self.assertEqual(launcher_theme.resolve_theme_visual_preset({"theme_bg_preset": "warm"}), "paper")
            self.assertEqual(launcher_theme.resolve_theme_visual_preset({"theme_bg_preset": "mist"}), "mist")
            self.assertEqual(launcher_theme.resolve_theme_visual_preset({"theme_bg_preset": "graphite"}), "graphite")
            self.assertEqual(launcher_theme.normalize_theme_background_mode("mist"), "default")
            self.assertEqual(launcher_theme.normalize_theme_background_mode("image"), "image")

            launcher_theme.set_theme("dark")
            launcher_theme.configure_visual_preferences({"theme_bg_preset": "graphite"})

            self.assertEqual(launcher_theme._VISUAL_PREFS["visual_preset"], "graphite")
            self.assertEqual(launcher_theme._VISUAL_PREFS["bg_preset"], "default")
            self.assertEqual(launcher_theme.app_surface_background(), launcher_theme.C["bg"])
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)

    def test_apply_tooltip_palette_tracks_theme_colors_for_light_and_dark_modes(self):
        app = QApplication.instance() or QApplication([])
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        try:
            for mode, preset in (("light", "paper"), ("dark", "graphite")):
                launcher_theme.set_theme(mode)
                launcher_theme.configure_visual_preferences({"theme_visual_preset": preset, "theme_bg_preset": "default"})
                launcher_theme.apply_tooltip_palette(app)
                pal = QToolTip.palette()
                tooltip_bg = QColor(str(launcher_theme.C["layer2"])).name()
                tooltip_text = QColor(str(launcher_theme.C["text"])).name()
                self.assertEqual(
                    pal.color(QPalette.Active, QPalette.ToolTipBase).name(),
                    tooltip_bg,
                )
                self.assertEqual(
                    pal.color(QPalette.Active, QPalette.ToolTipText).name(),
                    tooltip_text,
                )
                self.assertEqual(
                    app.palette().color(QPalette.Active, QPalette.ToolTipBase).name(),
                    tooltip_bg,
                )
                self.assertEqual(
                    app.palette().color(QPalette.Active, QPalette.ToolTipText).name(),
                    tooltip_text,
                )
                for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
                    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
                        self.assertEqual(
                            app.palette().color(group, role).name(),
                            tooltip_bg,
                        )
                    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.HighlightedText):
                        self.assertEqual(
                            app.palette().color(group, role).name(),
                            tooltip_text,
                        )
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)
            launcher_theme.apply_tooltip_palette(app)

    def test_live_tooltip_widget_tracks_theme_switch(self):
        app = QApplication.instance() or QApplication([])
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        tip = QLabel("tooltip text")
        try:
            seen_styles = []
            with mock.patch.object(launcher_theme, "_is_live_tooltip_widget", side_effect=lambda widget: widget is tip):
                for mode, preset in (("dark", "graphite"), ("light", "paper")):
                    launcher_theme.set_theme(mode)
                    launcher_theme.configure_visual_preferences({"theme_visual_preset": preset, "theme_bg_preset": "default"})
                    launcher_theme.apply_tooltip_palette(app)
                    app.setStyleSheet(launcher_theme.build_qss())
                    launcher_theme._apply_live_tooltip_widget_theme(tip, force=True)
                    tooltip_bg = QColor(str(launcher_theme.C["layer2"])).name()
                    tooltip_text = QColor(str(launcher_theme.C["text"])).name()
                    self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Window).name(), tooltip_bg)
                    self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Base).name(), tooltip_bg)
                    self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Text).name(), tooltip_text)
                    self.assertEqual(tip.palette().color(QPalette.Active, QPalette.WindowText).name(), tooltip_text)
                    self.assertIn(tooltip_bg, tip.styleSheet())
                    self.assertIn(tooltip_text, tip.styleSheet())
                    seen_styles.append(tip.styleSheet())
            self.assertEqual(len(seen_styles), 2)
            self.assertNotEqual(seen_styles[0], seen_styles[1])
        finally:
            tip.deleteLater()
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)
            launcher_theme.apply_tooltip_palette(app)
            try:
                app.setStyleSheet(launcher_theme.build_qss())
            except Exception:
                pass

    def test_standard_qtooltip_label_tracks_theme_switch(self):
        app = QApplication.instance() or QApplication([])
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        btn = QPushButton("hover me")
        btn.setToolTip("tooltip text")
        btn.resize(140, 40)
        btn.show()
        app.processEvents()

        def show_standard_tooltip():
            center = btn.rect().center()
            event = QHelpEvent(QEvent.ToolTip, center, btn.mapToGlobal(center))
            QApplication.sendEvent(btn, event)
            app.processEvents()
            for widget in app.topLevelWidgets():
                if launcher_theme._is_live_tooltip_widget(widget):
                    return widget
            return None

        try:
            seen_styles = []
            for mode, preset in (("light", "paper"), ("dark", "graphite")):
                launcher_theme.set_theme(mode)
                launcher_theme.configure_visual_preferences({"theme_visual_preset": preset, "theme_bg_preset": "default"})
                launcher_theme.apply_tooltip_palette(app)
                app.setStyleSheet(launcher_theme.build_qss())
                tip = show_standard_tooltip()
                self.assertIsNotNone(tip)
                tooltip_bg = QColor(str(launcher_theme.C["layer2"])).name()
                tooltip_text = QColor(str(launcher_theme.C["text"])).name()
                self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Window).name(), tooltip_bg)
                self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Base).name(), tooltip_bg)
                self.assertEqual(tip.palette().color(QPalette.Active, QPalette.Text).name(), tooltip_text)
                self.assertEqual(tip.palette().color(QPalette.Active, QPalette.WindowText).name(), tooltip_text)
                self.assertIn(tooltip_bg, tip.styleSheet())
                self.assertIn(tooltip_text, tip.styleSheet())
                seen_styles.append(tip.styleSheet())
            self.assertEqual(len(seen_styles), 2)
            self.assertNotEqual(seen_styles[0], seen_styles[1])
        finally:
            QToolTip.hideText()
            btn.close()
            btn.deleteLater()
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)
            launcher_theme.apply_tooltip_palette(app)
            try:
                app.setStyleSheet(launcher_theme.build_qss())
            except Exception:
                pass
            app.processEvents()

    def test_apply_mica_syncs_native_caption_text_and_border_colors(self):
        original_palette = dict(launcher_theme.C)
        light_palette = {}

        class DummyWindow:
            def winId(self):
                return 101

        captured: list[tuple[int, int, int]] = []

        class DummyDwm:
            @staticmethod
            def DwmSetWindowAttribute(hwnd, attr, value_ptr, value_size):
                value = ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_int)).contents.value
                captured.append((int(hwnd), int(attr), int(value)))
                return 0

        try:
            launcher_theme.set_theme("light")
            light_palette = dict(launcher_theme.C)
            with mock.patch.object(launcher_theme.sys, "platform", "win32"), mock.patch.object(
                launcher_theme.ctypes,
                "windll",
                types.SimpleNamespace(dwmapi=DummyDwm()),
                create=True,
            ):
                self.assertTrue(launcher_theme.apply_mica(DummyWindow(), dark=False))
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)

        expected = {
            20: 0,
            34: launcher_theme._to_win_colorref(str(light_palette["border"]), "#ffffff"),
            35: launcher_theme._to_win_colorref(str(light_palette["panel"]), "#ffffff"),
            36: launcher_theme._to_win_colorref(str(light_palette["text"]), "#111111"),
            38: 2,
        }
        self.assertEqual(len(captured), 5)
        self.assertTrue(all(hwnd == 101 for hwnd, _attr, _value in captured))
        attr_map = {attr: value for _hwnd, attr, value in captured}
        self.assertEqual(attr_map, expected)

    def test_theme_settings_source_uses_visual_presets_and_background_mode(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('self.settings_theme_auto_jump_latest = QCheckBox("发送或回复时自动跳到最新消息")', src)
        self.assertIn('visual_label = QLabel("主体预设")', src)
        self.assertIn('bg_label = QLabel("背景模式")', src)
        self.assertIn("self.settings_theme_visual_combo = _StablePopupComboBox()", src)
        self.assertIn('visual_preset = str(visual_combo.itemData(visual_combo.currentIndex()) or "graphite").strip()', src)
        self.assertIn('self.cfg["theme_chat_auto_jump_latest"] = auto_jump_latest', src)
        self.assertIn('self.cfg["theme_visual_preset"] = visual_preset', src)
        self.assertIn("resolve_theme_visual_preset(self.cfg)", src)
        self.assertIn('normalize_theme_background_mode(self.cfg.get("theme_bg_preset"))', src)

    def test_theme_application_updates_tooltip_palette_on_startup_and_runtime_switch(self):
        root = os.path.dirname(os.path.dirname(__file__))
        window_path = os.path.join(root, "launcher_app", "window.py")
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        self.assertIn("qt_theme.apply_tooltip_palette(app)", window_src)

        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        self.assertIn("qt_theme.apply_tooltip_palette(app)", shell_src)

    def test_theme_application_refreshes_custom_info_popup_style(self):
        root = os.path.dirname(os.path.dirname(__file__))
        session_shell_path = os.path.join(root, "qt_chat_parts", "session_shell.py")
        with open(session_shell_path, "r", encoding="utf-8") as f:
            session_shell_src = f.read()
        self.assertIn("def _refresh_info_popup_style(self):", session_shell_src)
        self.assertIn('popup.setStyleSheet(', session_shell_src)

        window_path = os.path.join(root, "launcher_app", "window.py")
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        self.assertIn('styler = getattr(self, "_refresh_info_popup_style", None)', window_src)

        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        self.assertIn('refresh_info_popup_style = getattr(self, "_refresh_info_popup_style", None)', shell_src)
        self.assertIn("refresh_info_popup_style()", shell_src)

    def test_theme_application_routes_popup_restyling_through_shared_helper(self):
        root = os.path.dirname(os.path.dirname(__file__))
        common_path = os.path.join(root, "qt_chat_parts", "common.py")
        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        window_path = os.path.join(root, "launcher_app", "window.py")
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(common_path, "r", encoding="utf-8") as f:
            common_src = f.read()
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn("def combo_popup_view_style() -> str:", common_src)
        self.assertIn("def combo_popup_container_style() -> str:", common_src)
        self.assertIn("def menu_popup_style() -> str:", common_src)
        self.assertIn("def apply_menu_popup_theme(menu: QMenu | None) -> None:", common_src)
        self.assertIn("def apply_combo_popup_theme(combo: QComboBox | None, *, combo_style: str = \"\") -> None:", common_src)
        self.assertIn("def refresh_theme_aware_popup_surfaces(root: QWidget | None, *, combo_style: str = \"\") -> None:", common_src)
        self.assertIn("def _refresh_slash_popup_theme(self):", common_src)
        self.assertIn("chat_common.refresh_theme_aware_popup_surfaces(self, combo_style=combo_style)", shell_src)
        self.assertIn("chat_common.refresh_theme_aware_popup_surfaces(self, combo_style=combo_style)", window_src)
        self.assertIn("chat_common.apply_menu_popup_theme(menu)", settings_src)

    def test_config_value_controls_ignore_incidental_wheel_events(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app.processEvents())

        class Wheel:
            def __init__(self):
                self.ignored = False

            def ignore(self):
                self.ignored = True

        combo = common.NoWheelComboBox()
        combo_wheel = Wheel()
        combo.wheelEvent(combo_wheel)
        self.assertTrue(combo_wheel.ignored)

        spin = common.NoWheelSpinBox()
        spin_wheel = Wheel()
        spin.wheelEvent(spin_wheel)
        self.assertTrue(spin_wheel.ignored)

        stable_combo = settings_panel._StablePopupComboBox()
        stable_wheel = Wheel()
        stable_combo.wheelEvent(stable_wheel)
        self.assertTrue(stable_wheel.ignored)

        root = os.path.dirname(os.path.dirname(__file__))
        files = {}
        for rel in (
            "qt_chat_parts/api_editor.py",
            "qt_chat_parts/schedule_runtime.py",
            "qt_chat_parts/personal_usage.py",
            "qt_chat_parts/setup_pages.py",
            "qt_chat_parts/settings_panel.py",
        ):
            with open(os.path.join(root, rel), "r", encoding="utf-8") as f:
                files[rel] = f.read()
        self.assertIn("format_box = NoWheelComboBox()", files["qt_chat_parts/api_editor.py"])
        self.assertIn("tpl_box = NoWheelComboBox()", files["qt_chat_parts/api_editor.py"])
        self.assertIn("model_box = NoWheelComboBox()", files["qt_chat_parts/api_editor.py"])
        self.assertIn("repeat_box = NoWheelComboBox()", files["qt_chat_parts/schedule_runtime.py"])
        self.assertIn("delay_spin = NoWheelSpinBox()", files["qt_chat_parts/schedule_runtime.py"])
        self.assertIn("spin = NoWheelSpinBox()", files["qt_chat_parts/personal_usage.py"])
        self.assertIn("self.locate_dependency_installer_combo = NoWheelComboBox()", files["qt_chat_parts/setup_pages.py"])
        self.assertIn("self.settings_vps_port_spin = chat_common.NoWheelSpinBox()", files["qt_chat_parts/settings_panel.py"])
        self.assertIn("self.settings_lan_port_spin = chat_common.NoWheelSpinBox()", files["qt_chat_parts/settings_panel.py"])

    def test_runtime_theme_switch_refreshes_transient_popup_surfaces(self):
        app = QApplication.instance() or QApplication([])
        win = None
        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)

        try:
            with mock.patch.object(launcher_window.lz, "load_config", return_value={}), mock.patch.object(
                launcher_window.QtChatWindow, "_schedule_session_index_warmup", autospec=True, side_effect=lambda self: None
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
            ):
                win = launcher_window.QtChatWindow(r"E:\\GenericAgent")
                win._drain_timer.stop()
                win._server_status_timer.stop()
                win._subagent_status_timer.stop()
                win._stream_flush_timer.stop()
                win.show()
                app.processEvents()

                floating = win._ensure_floating_chat_window()
                floating.show()
                app.processEvents()
                win._show_settings()
                app.processEvents()
                api_add_menu = getattr(win, "_settings_api_add_menu", None)
                self.assertIsNotNone(api_add_menu)

                win.input_box.set_slash_command_provider(
                    lambda query, editor=None: [{"command": "/status", "description": "查看当前状态"}]
                )
                win._apply_theme("light")
                light_panel = str(launcher_theme.C["panel"])
                light_popup_bg = str(launcher_theme.C["layer1"])
                win.input_box.setPlainText("/st")
                win.input_box._refresh_slash_command_popup()
                floating.session_combo.showPopup()
                app.processEvents()

                popup = getattr(win.input_box, "_slash_popup", None)
                self.assertIsNotNone(popup)
                self.assertTrue(popup.isVisible())
                combo_popup = floating.session_combo.view().window()
                light_api_menu_style = api_add_menu.styleSheet()
                light_info_popup_style = win._info_popup.styleSheet()
                light_slash_style = popup.styleSheet()
                light_combo_style = floating.session_combo.view().styleSheet()
                light_combo_popup_style = combo_popup.styleSheet()
                self.assertIn(light_popup_bg, light_api_menu_style)
                self.assertEqual(api_add_menu.palette().window().color().name().lower(), QColor(light_popup_bg).name().lower())
                self.assertIn(light_panel, light_info_popup_style)
                self.assertIn(light_panel, light_slash_style)
                self.assertIn(light_popup_bg, light_combo_style)
                self.assertIn(light_popup_bg, light_combo_popup_style)

                win._apply_theme("dark")
                app.processEvents()

                dark_panel = str(launcher_theme.C["panel"])
                dark_popup_bg = str(launcher_theme.C["layer1"])
                self.assertNotEqual(light_panel, dark_panel)
                self.assertIn(dark_popup_bg, api_add_menu.styleSheet())
                self.assertEqual(api_add_menu.palette().window().color().name().lower(), QColor(dark_popup_bg).name().lower())
                self.assertIn(dark_panel, win._info_popup.styleSheet())
                self.assertIn(dark_panel, popup.styleSheet())
                self.assertIn(dark_popup_bg, floating.session_combo.view().styleSheet())
                self.assertIn(dark_popup_bg, combo_popup.styleSheet())
                self.assertNotEqual(light_api_menu_style, api_add_menu.styleSheet())
                self.assertNotEqual(light_info_popup_style, win._info_popup.styleSheet())
                self.assertNotEqual(light_slash_style, popup.styleSheet())
                self.assertNotEqual(light_combo_style, floating.session_combo.view().styleSheet())
                self.assertNotEqual(light_combo_popup_style, combo_popup.styleSheet())
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)
            common.set_md_css(common._build_md_css())
            launcher_theme.apply_tooltip_palette(app)
            if app is not None:
                try:
                    app.setStyleSheet(launcher_theme.build_qss())
                except Exception:
                    pass
            if win is not None:
                try:
                    win.input_box._hide_slash_command_popup()
                except Exception:
                    pass
                try:
                    app.removeEventFilter(win)
                except Exception:
                    pass
                win.close()
                win.deleteLater()
                app.processEvents()

    def test_message_row_uses_custom_theme_avatar_image_when_configured(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app.processEvents())
        with tempfile.TemporaryDirectory() as td:
            avatar_path = os.path.join(td, "user_avatar.png")
            image = QImage(64, 64, QImage.Format_ARGB32)
            image.fill(QColor("#cc4b4b"))
            self.assertTrue(image.save(avatar_path, "PNG"))

            row = common.MessageRow(
                "hello",
                "user",
                avatar_cfg={"theme_user_avatar_image": avatar_path},
            )
            avatar = getattr(row, "_avatar_label", None)
            self.assertIsNotNone(avatar)
            pixmap = avatar.pixmap()
            self.assertIsNotNone(pixmap)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(avatar.property("avatarVariant"), "custom")
            center = pixmap.toImage().pixelColor(15, 15)
            self.assertGreater(center.red(), 150)
            self.assertLess(center.green(), 120)
            self.assertLess(center.blue(), 120)

    def test_message_row_skips_selectable_user_label_on_macos_github_actions(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app.processEvents())
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch.object(common.lz, "IS_MACOS", True):
            row = common.MessageRow("hello", "user")
        label = getattr(row, "_label", None)
        self.assertIsNotNone(label)
        self.assertEqual(label.property("_ga_selection_mode"), "disabled")

    def test_theme_settings_source_includes_chat_avatar_configuration(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        common_path = os.path.join(root, "qt_chat_parts", "common.py")
        chat_view_path = os.path.join(root, "qt_chat_parts", "chat_view.py")
        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        floating_path = os.path.join(root, "launcher_app", "window.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(common_path, "r", encoding="utf-8") as f:
            common_src = f.read()
        with open(chat_view_path, "r", encoding="utf-8") as f:
            chat_view_src = f.read()
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        with open(floating_path, "r", encoding="utf-8") as f:
            floating_src = f.read()
        self.assertIn('avatar_title = QLabel("聊天头像")', settings_src)
        self.assertIn("self.settings_theme_user_avatar_path = QLineEdit()", settings_src)
        self.assertIn("self.settings_theme_ai_avatar_path = QLineEdit()", settings_src)
        self.assertIn('self.cfg["theme_user_avatar_image"] = user_avatar_generated_rel', settings_src)
        self.assertIn('self.cfg["theme_ai_avatar_image"] = ai_avatar_generated_rel', settings_src)
        self.assertIn("def _choose_theme_chat_avatar(self, role: str):", settings_src)
        self.assertIn("def _clear_theme_chat_avatar(self, role: str):", settings_src)
        self.assertIn("def refresh_message_row_avatars(root: QWidget | None) -> None:", common_src)
        self.assertIn('avatar_cfg=getattr(self, "cfg", None)', chat_view_src)
        self.assertIn("chat_common.refresh_message_row_avatars(self)", shell_src)
        self.assertIn("chat_common.refresh_message_row_avatars(self)", floating_src)

    def test_window_sets_runtime_launcher_icon_for_app_and_windows(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("from launcher_app.app_icon import launcher_icon", src)
        self.assertIn("app.setWindowIcon(launcher_icon())", src)
        self.assertIn("self.setWindowIcon(app_icon)", src)
        self.assertIn('self.setWindowIcon(host.windowIcon() if host is not None else launcher_icon())', src)

    def test_all_lz_symbols_used_by_ui_exist(self):
        root = os.path.dirname(os.path.dirname(__file__))
        files = [
            os.path.join(root, "launcher_app", "window.py"),
            os.path.join(root, "launcher_app", "theme.py"),
        ]
        pattern = re.compile(r"\blz\.([A-Za-z_][A-Za-z0-9_]*)")
        names = set()
        for fp in files:
            with open(fp, "r", encoding="utf-8") as f:
                names.update(pattern.findall(f.read()))
        missing = [name for name in sorted(names) if not hasattr(lz, name)]
        self.assertEqual(missing, [], msg=f"missing symbols: {missing}")

    def test_ensure_mykey_file_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = lz._ensure_mykey_file(td)
            self.assertTrue(out["ok"])
            self.assertTrue(out["created"])
            self.assertTrue(os.path.isfile(out["path"]))

            out2 = lz._ensure_mykey_file(td)
            self.assertTrue(out2["ok"])
            self.assertFalse(out2["created"])

    def test_is_valid_agent_dir(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(lz.is_valid_agent_dir(td))
            with open(os.path.join(td, "launch.pyw"), "w", encoding="utf-8") as f:
                f.write("# test")
            with open(os.path.join(td, "agentmain.py"), "w", encoding="utf-8") as f:
                f.write("# test")
            self.assertTrue(lz.is_valid_agent_dir(td))

    def test_list_sessions_writes_and_reads_meta_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            session = {
                "id": "abc123",
                "title": "缓存测试",
                "updated_at": 1700000000.0,
                "pinned": True,
                "channel_id": "telegram",
                "device_scope": "remote",
                "device_id": "r1",
                "device_name": "测试服务器",
                "session_kind": "channel_process",
                "bubbles": [{"role": "user", "text": "hello"}],
            }
            sessions_mod.save_session(td, dict(session), touch=False)
            rows = sessions_mod.list_sessions(td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "abc123")
            self.assertEqual(rows[0]["title"], "缓存测试")
            self.assertTrue(rows[0]["pinned"])
            self.assertEqual(rows[0]["channel_id"], "telegram")
            self.assertEqual(rows[0]["device_scope"], "remote")
            self.assertEqual(rows[0]["device_id"], "r1")
            self.assertEqual(rows[0]["device_name"], "测试服务器")
            self.assertEqual(rows[0]["session_kind"], "channel_process")

            meta_path = os.path.join(td, "temp", "launcher_sessions_meta", "abc123.json")
            self.assertTrue(os.path.isfile(meta_path))

    def test_list_sessions_quick_read_keeps_remote_device_fields(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "temp", "launcher_sessions")
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, "remote.json")
            payload = {
                "id": "remote",
                "title": "远端进程",
                "updated_at": 1700000001.0,
                "channel_id": "launcher",
                "device_scope": "remote",
                "device_id": "remote_001",
                "device_name": "生产服务器",
                "session_kind": "channel_process",
                "bubbles": [{"role": "assistant", "text": "ok"}],
            }
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            rows = sessions_mod.list_sessions(td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["device_scope"], "remote")
            self.assertEqual(rows[0]["device_id"], "remote_001")
            self.assertEqual(rows[0]["device_name"], "生产服务器")

    def test_list_sessions_supports_large_session_without_full_load(self):
        with tempfile.TemporaryDirectory() as td:
            d = os.path.join(td, "temp", "launcher_sessions")
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, "big.json")
            huge_text = "x" * (350 * 1024)
            payload = {
                "id": "big",
                "title": "大文件会话",
                "updated_at": 1700000001.0,
                "bubbles": [{"role": "assistant", "text": huge_text}],
            }
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            rows = sessions_mod.list_sessions(td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "big")
            self.assertEqual(rows[0]["title"], "大文件会话")

    def test_parse_mykey_py_extracts_configs_and_extras(self):
        src = """
native_oai_config = {
    'name': 'Primary OpenAI',
    'apikey': 'k',
    'apibase': 'https://api.openai.com/v1',
    'model': 'gpt-5.4',
}
langfuse_config = {
    'public_key': 'pk-demo',
    'secret_key': 'sk-demo',
    'host': 'https://cloud.langfuse.com',
}
my_cookie = 'abc'
tg_bot_token = '123'
"""
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(src)
            out = lz.parse_mykey_py(fp)

        self.assertIsNone(out["error"])
        self.assertEqual(len(out["configs"]), 1)
        self.assertEqual(out["configs"][0]["kind"], "native_oai")
        self.assertEqual(out["configs"][0]["data"].get("name"), "Primary OpenAI")
        self.assertEqual(out["extras"].get("tg_bot_token"), "123")
        self.assertEqual(out["extras"].get("langfuse_config", {}).get("public_key"), "pk-demo")
        self.assertEqual(out["passthrough"][0]["name"], "my_cookie")

    def test_parse_mykey_source_supports_json_configs(self):
        payload = {
            "native_oai_config": {
                "name": "Primary OpenAI",
                "apikey": "k",
                "apibase": "https://api.openai.com/v1",
                "model": "gpt-5.4",
            },
            "langfuse_config": {
                "public_key": "pk-demo",
                "secret_key": "sk-demo",
                "host": "https://cloud.langfuse.com",
            },
            "tg_bot_token": "123",
        }
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            out = lz.parse_mykey_source(fp)
            resolved = lz.resolve_mykey_source_path(td)

        self.assertIsNone(out["error"])
        self.assertEqual(len(out["configs"]), 1)
        self.assertEqual(out["configs"][0]["kind"], "native_oai")
        self.assertEqual(out["configs"][0]["data"].get("name"), "Primary OpenAI")
        self.assertEqual(out["extras"].get("tg_bot_token"), "123")
        self.assertEqual(resolved, fp)

    def test_auto_config_var_increments(self):
        existing = {"native_oai_config", "native_oai_config2"}
        name = lz.auto_config_var("native_oai", existing)
        self.assertEqual(name, "native_oai_config3")

    def test_sync_config_var_kind_rewrites_family_and_preserves_suffix_when_possible(self):
        self.assertEqual(
            lz.sync_config_var_kind("native_claude", "native_oai_config2", {"native_claude_config"}),
            "native_claude_config2",
        )
        self.assertEqual(
            lz.sync_config_var_kind("native_claude", "native_oai_config", {"native_claude_config"}),
            "native_claude_config2",
        )

    def test_serialize_mykey_py_contains_blocks(self):
        text = lz.serialize_mykey_py(
            configs=[
                {
                    "var": "native_claude_config",
                    "kind": "native_claude",
                    "data": {"name": "Claude Prod", "apikey": "k", "apibase": "https://x", "model": "m"},
                }
            ],
            extras={"tg_bot_token": "abc", "langfuse_config": {"public_key": "pk", "secret_key": "sk"}},
            passthrough=[{"name": "my_cookie", "value": "cookie-v"}],
        )
        self.assertIn("native_claude_config", text)
        self.assertIn("'name': 'Claude Prod'", text)
        self.assertIn("tg_bot_token", text)
        self.assertIn("langfuse_config", text)
        self.assertIn("my_cookie", text)

    def test_serialize_mykey_py_preserves_cross_kind_config_order_on_round_trip(self):
        configs = [
            {
                "var": "native_oai_config",
                "kind": "native_oai",
                "data": {"name": "OpenAI First", "apikey": "sk-a", "apibase": "https://api.openai.com/v1", "model": "gpt-5.4"},
            },
            {
                "var": "mixin_config",
                "kind": "mixin",
                "data": {"name": "Failover", "llm_nos": ["OpenAI First", "OpenAI Last"], "max_retries": 3, "base_delay": 0.5},
            },
            {
                "var": "native_claude_config",
                "kind": "native_claude",
                "data": {"name": "Claude Middle", "apikey": "sk-b", "apibase": "https://api.anthropic.com", "model": "claude-opus-4-7[1m]"},
            },
            {
                "var": "native_oai_config2",
                "kind": "native_oai",
                "data": {"name": "OpenAI Last", "apikey": "sk-c", "apibase": "https://api.second.example/v1", "model": "gpt-5.4-mini"},
            },
        ]
        text = lz.serialize_mykey_py(configs=configs, extras={}, passthrough=[])
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
            parsed = lz.parse_mykey_py(fp)

        self.assertIsNone(parsed["error"])
        self.assertEqual(
            [cfg["var"] for cfg in parsed["configs"]],
            ["native_oai_config", "mixin_config", "native_claude_config", "native_oai_config2"],
        )
        self.assertEqual(parsed["configs"][1]["kind"], "mixin")
        self.assertEqual(parsed["configs"][1]["data"]["llm_nos"], ["OpenAI First", "OpenAI Last"])

    def test_validate_api_config_references_accepts_named_and_indexed_mixin_targets(self):
        configs = [
            {
                "var": "native_oai_config",
                "kind": "native_oai",
                "data": {"name": "primary", "apikey": "sk-a", "apibase": "https://api.openai.com/v1", "model": "gpt-5.4"},
            },
            {
                "var": "native_oai_config2",
                "kind": "native_oai",
                "data": {"name": "backup", "apikey": "sk-b", "apibase": "https://api.openai.com/v1", "model": "gpt-5.4-mini"},
            },
            {
                "var": "mixin_config",
                "kind": "mixin",
                "data": {"llm_nos": ["primary", 1], "max_retries": 3},
            },
        ]
        self.assertEqual(lz.validate_api_config_references(configs), [])

    def test_validate_api_config_references_reports_missing_named_target(self):
        configs = [
            {
                "var": "native_oai_config",
                "kind": "native_oai",
                "data": {"name": "mimo", "apikey": "sk-a", "apibase": "https://api.openai.com/v1", "model": "gpt-5.4"},
            },
            {
                "var": "mixin_config",
                "kind": "mixin",
                "data": {"llm_nos": ["gpt-native"], "max_retries": 3},
            },
        ]
        errors = lz.validate_api_config_references(configs)
        self.assertEqual(len(errors), 1)
        self.assertIn("gpt-native", errors[0])
        self.assertIn("mimo", errors[0])

    def test_api_editor_save_keeps_claude_card_as_native_claude_after_reload(self):
        class DummyApiEditor(ApiEditorMixin):
            def __init__(self):
                self._qt_api_hidden_configs = []
                self._qt_api_state = []

        editor = DummyApiEditor()
        editor._qt_api_state = [
            {
                "var": "native_oai_config",
                "format": "claude_native",
                "tpl_key": "anthropic",
                "apibase": "https://api.anthropic.com",
                "apikey": "sk-ant-demo",
                "model": "claude-opus-4-7[1m]",
                "advanced_values": {},
                "advanced_expanded": False,
                "raw_extra": {},
                "model_choices": [],
                "model_status": "",
                "model_fetching": False,
            }
        ]
        configs = editor._api_build_save_configs()
        self.assertEqual(configs[0]["kind"], "native_claude")
        self.assertEqual(configs[0]["var"], "native_claude_config")
        self.assertEqual(editor._qt_api_state[0]["var"], "native_claude_config")

        text = lz.serialize_mykey_py(configs=configs, extras={}, passthrough=[])
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "mykey.py")
            with open(fp, "w", encoding="utf-8") as f:
                f.write(text)
            parsed = lz.parse_mykey_py(fp)

        self.assertIsNone(parsed["error"])
        self.assertEqual(parsed["configs"][0]["kind"], "native_claude")
        self.assertEqual(parsed["configs"][0]["var"], "native_claude_config")

    def test_auto_update_check_with_changes_does_not_popup_message_box(self):
        class DummyPersonalUsage(personal_usage_mod.PersonalUsageMixin):
            def __init__(self):
                self.cfg = {}
                self._update_check_running = True
                self._last_update_check_result = None
                self._about_update_notice_text = ""
                self.status_messages = []
                self.refresh_calls = 0

            def _detect_version_changes(self, result):
                return list((result or {}).get("changes") or [])

            def _append_update_history(self, _result):
                return [1, 2, 3]

            def _update_result_summary(self, _result):
                return "summary"

            def _format_version_changes_text(self, changes):
                return "changes-text" if changes else ""

            def _refresh_about_update_widgets(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_messages.append(text)

        dummy = DummyPersonalUsage()
        info_calls = []
        save_calls = []
        original_info = personal_usage_mod.QMessageBox.information
        original_save = lz.save_config
        try:
            personal_usage_mod.QMessageBox.information = lambda *args, **kwargs: info_calls.append((args, kwargs))
            lz.save_config = lambda cfg: save_calls.append(dict(cfg))
            dummy._finish_update_check({"checked_at": 123.0, "changes": ["launcher"]}, manual=False)
        finally:
            personal_usage_mod.QMessageBox.information = original_info
            lz.save_config = original_save

        self.assertEqual(info_calls, [])
        self.assertEqual(dummy._about_update_notice_text, "最近一次自动检查发现版本变动，请在本页查看详情。\n\nchanges-text")
        self.assertIn("版本变动", dummy.status_messages[-1])
        self.assertGreaterEqual(dummy.refresh_calls, 2)
        self.assertEqual(save_calls[-1]["last_github_update_check_at"], 123.0)

    def test_manual_update_check_with_changes_still_popups_message_box(self):
        class DummyPersonalUsage(personal_usage_mod.PersonalUsageMixin):
            def __init__(self):
                self.cfg = {}
                self._update_check_running = True
                self._last_update_check_result = None
                self._about_update_notice_text = "stale"
                self.status_messages = []
                self.refresh_calls = 0

            def _detect_version_changes(self, result):
                return list((result or {}).get("changes") or [])

            def _append_update_history(self, _result):
                return [1]

            def _update_result_summary(self, _result):
                return "summary"

            def _format_version_changes_text(self, changes):
                return "changes-text" if changes else ""

            def _refresh_about_update_widgets(self):
                self.refresh_calls += 1

            def _set_status(self, text):
                self.status_messages.append(text)

        dummy = DummyPersonalUsage()
        info_calls = []
        original_info = personal_usage_mod.QMessageBox.information
        original_save = lz.save_config
        try:
            personal_usage_mod.QMessageBox.information = lambda *args, **kwargs: info_calls.append((args, kwargs))
            lz.save_config = lambda _cfg: None
            dummy._finish_update_check({"checked_at": 456.0, "changes": ["kernel"]}, manual=True)
        finally:
            personal_usage_mod.QMessageBox.information = original_info
            lz.save_config = original_save

        self.assertEqual(len(info_calls), 1)
        self.assertEqual(info_calls[0][0][1], "GitHub 更新检测")
        self.assertIn("changes-text", info_calls[0][0][2])
        self.assertEqual(dummy._about_update_notice_text, "")
        self.assertIn("已完成 GitHub 更新检测", dummy.status_messages[-1])

    def test_model_api_url_helpers(self):
        self.assertEqual(
            lz._strip_known_api_suffix("https://a.com/v1/chat/completions"),
            "https://a.com",
        )
        self.assertEqual(lz._join_url("https://a.com/v1/", "/models"), "https://a.com/v1/models")
        self.assertEqual(
            lz._oai_models_candidates("https://a.com/v1/chat/completions"),
            ["https://a.com/v1/models", "https://a.com/models"],
        )

        cands = lz._anthropic_models_candidates("https://api.example.com/claude/office")
        self.assertGreaterEqual(len(cands), 2)
        self.assertEqual(len(cands), len(set(cands)))

    def test_extract_model_ids_variants(self):
        payload = {
            "models": [
                {"id": "m1"},
                {"name": "m2"},
                {"model": "m3"},
                "m4",
            ]
        }
        out = lz._extract_model_ids(payload)
        self.assertEqual(out, ["m1", "m2", "m3", "m4"])

    def test_extract_model_ids_supports_nested_payloads(self):
        payload = {
            "result": {
                "items": [
                    {"model_id": "nested-1"},
                    {"id": "nested-2"},
                ]
            }
        }
        out = lz._extract_model_ids(payload)
        self.assertEqual(out, ["nested-1", "nested-2"])

    def test_fetch_remote_models_retries_with_alt_headers(self):
        original = model_api._http_json
        seen = []

        def fake_http_json(url, headers=None, timeout=12):
            seen.append((url, dict(headers or {})))
            if "Authorization" in (headers or {}):
                raise ValueError("Bearer rejected")
            if (headers or {}).get("x-api-key") == "secret":
                return {"data": [{"id": "model-a"}]}
            raise ValueError("unexpected headers")

        model_api._http_json = fake_http_json
        try:
            out = model_api._fetch_remote_models("oai_chat", "https://api.example.com/v1", "secret")
        finally:
            model_api._http_json = original
        self.assertEqual(out, ["model-a"])
        self.assertTrue(any(headers.get("Authorization") == "Bearer secret" for _url, headers in seen))
        self.assertTrue(any(headers.get("x-api-key") == "secret" for _url, headers in seen))

    def test_fetch_remote_models_retries_with_alt_oai_urls(self):
        original = model_api._http_json
        seen = []

        def fake_http_json(url, headers=None, timeout=12):
            seen.append(url)
            if url.endswith("/v1/models"):
                raise ValueError("v1 not supported")
            if url.endswith("/models"):
                return {"models": [{"id": "model-b"}]}
            raise ValueError("unexpected url")

        model_api._http_json = fake_http_json
        try:
            out = model_api._fetch_remote_models("oai_chat", "https://api.example.com/v1", "secret")
        finally:
            model_api._http_json = original
        self.assertEqual(out, ["model-b"])
        self.assertIn("https://api.example.com/v1/models", seen)
        self.assertIn("https://api.example.com/models", seen)

    def test_usage_mode_helpers(self):
        self.assertEqual(lz._usage_mode_from_sources([]), "estimate_chars_div_2_5")
        self.assertEqual(lz._usage_mode_from_sources(["provider"]), "provider_usage")
        self.assertEqual(lz._usage_mode_from_sources(["provider", "estimate"]), "mixed_provider_and_estimate")

        self.assertEqual(lz._usage_mode_label("provider_usage"), "真实")
        self.assertEqual(lz._usage_mode_label("mixed_provider_and_estimate"), "混合")
        self.assertEqual(lz._usage_mode_label("estimate_chars_div_2_5"), "估算")

    def test_usage_cost_helpers(self):
        row = {
            "input_tokens": 100,
            "output_tokens": 90,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 50,
            "api_calls": 3,
        }
        self.assertEqual(lz.usage_input_side_tokens(row), 170)
        self.assertEqual(lz.usage_total_consumed_tokens(row), 260)
        self.assertAlmostEqual(lz.usage_cache_hit_rate(row), 29.4118, places=4)

        summary = lz.summarize_usage_rows([row, {"input_tokens": 10, "output_tokens": 5, "api_calls": 1}])
        self.assertEqual(summary["input_side_tokens"], 180)
        self.assertEqual(summary["usage_total_tokens"], 275)
        self.assertEqual(summary["api_calls"], 4)
        self.assertEqual(summary["event_count"], 2)

    def test_usage_channel_helpers(self):
        self.assertEqual(lz._normalize_usage_channel_id("official", "launcher"), "launcher")
        self.assertEqual(lz._normalize_usage_channel_id("wechat", "launcher"), "wechat")
        self.assertEqual(lz._usage_channel_label("wechat"), "微信")

    def test_fallback_token_events_pairs_user_and_assistant(self):
        bubbles = [
            {"role": "user", "text": "hello world"},
            {"role": "assistant", "text": "response"},
        ]
        events = lz._fallback_token_events_from_bubbles(bubbles, base_ts=100.0, channel_id="launcher", model_name="m")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["channel_id"], "launcher")
        self.assertGreater(events[0]["input_tokens"], 0)
        self.assertGreater(events[0]["output_tokens"], 0)

    def test_normalize_snapshot_inplace_defaults(self):
        session = {"id": "s", "updated_at": time.time(), "token_usage": {"turns": 2}}
        lz._normalize_snapshot_inplace(session)
        self.assertIn("snapshot", session)
        self.assertEqual(session["snapshot"]["turns"], 2)

    def test_list_sessions_sort_and_delete(self):
        with tempfile.TemporaryDirectory() as td:
            d = lz.sessions_dir(td)
            data = [
                {"id": "a", "title": "A", "pinned": False, "updated_at": 1},
                {"id": "b", "title": "B", "pinned": True, "updated_at": 0},
                {"id": "c", "title": "C", "pinned": False, "updated_at": 3},
            ]
            for item in data:
                with open(os.path.join(d, f"{item['id']}.json"), "w", encoding="utf-8") as f:
                    json.dump(item, f, ensure_ascii=False)

            listed = lz.list_sessions(td)
            self.assertEqual([x["id"] for x in listed], ["b", "c", "a"])

            lz.delete_session(td, "a")
            self.assertFalse(os.path.exists(os.path.join(d, "a.json")))

    def test_quick_read_session_meta_keeps_channel_process_fields(self):
        with tempfile.TemporaryDirectory() as td:
            fp = os.path.join(td, "sample.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": "abc123",
                        "title": "微信 进程 04-24 09:30",
                        "updated_at": 123.45,
                        "pinned": True,
                        "channel_id": "wechat",
                        "session_kind": "channel_process",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            meta = sessions_mod._quick_read_session_meta(fp, "abc123")
            self.assertIsInstance(meta, dict)
            self.assertEqual(meta.get("channel_id"), "wechat")
            self.assertEqual(meta.get("session_kind"), "channel_process")
            self.assertTrue(meta.get("pinned"))

    def test_list_sessions_repairs_stale_launcher_index_for_channel_process(self):
        with tempfile.TemporaryDirectory() as td:
            sid = "wxproc1"
            session = {
                "id": sid,
                "title": "微信 进程 04-24 09:30",
                "created_at": 100.0,
                "updated_at": 200.0,
                "session_kind": "channel_process",
                "session_source_label": "微信",
                "channel_id": "wechat",
                "channel_label": "微信",
                "process_pid": 12345,
                "bubbles": [],
                "backend_history": [],
                "agent_history": [],
            }
            lz.save_session(td, session, touch=False)
            meta_fp = sessions_mod._session_meta_path(td, sid)
            if os.path.isfile(meta_fp):
                os.remove(meta_fp)
            stale_fp = os.path.join(lz.sessions_dir(td), f"{sid}.json")
            sessions_mod._save_sessions_index(
                td,
                {
                    sid: {
                        "id": sid,
                        "title": "微信 进程 04-24 09:30",
                        "updated_at": 200.0,
                        "pinned": False,
                        "channel_id": "launcher",
                        "channel_label": "启动器",
                        "session_kind": "",
                        "path": stale_fp,
                    }
                },
            )
            rows = lz.list_sessions(td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("channel_id"), "wechat")
            self.assertEqual(rows[0].get("session_kind"), "channel_process")
            repaired_index = sessions_mod._load_sessions_index(td)
            self.assertEqual(repaired_index[sid].get("channel_id"), "wechat")

    def test_purge_archived_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            arc_dir = lz.archived_sessions_dir(td, "wechat")
            os.makedirs(arc_dir, exist_ok=True)
            fp = os.path.join(arc_dir, "x.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"id": "x"}, f)

            removed = lz.purge_archived_sessions(td)
            self.assertEqual(removed, 1)

    def test_bridge_main_reconfigures_stdin_utf8(self):
        root = os.path.dirname(os.path.dirname(__file__))
        bridge_path = os.path.join(root, "bridge.py")
        with open(bridge_path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("os.environ['PYTHONIOENCODING'] = 'utf-8'", src)
        self.assertIn("os.environ['PYTHONUTF8'] = '1'", src)
        self.assertIn("os.environ.pop('PYTHONLEGACYWINDOWSSTDIO', None)", src)
        self.assertIn("sys.stdin.reconfigure(encoding='utf-8', errors='replace')", src)

    def test_bridge_runtime_sets_utf8_env_for_bridge_process(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("bridge_env = lz._external_subprocess_env()", src)
        self.assertIn("self.bridge_proc = lz._popen_external_subprocess(", src)
        self.assertIn("env=bridge_env", src)

    def test_bridge_runtime_notifies_when_reply_done(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _notify_reply_done", src)
        self.assertIn("def _reply_sound_enabled", src)
        self.assertIn("def _reply_message_enabled", src)
        self.assertIn("if not self._reply_sound_enabled():", src)
        self.assertIn("if not self._reply_message_enabled():", src)
        self.assertIn("winsound.MessageBeep", src)
        self.assertIn('launcher_tray_getter = getattr(self, "_ensure_launcher_tray_icon", None)', src)
        self.assertIn("tray = self._ensure_reply_notify_tray()", src)
        self.assertIn("tray.showMessage", src)
        self.assertNotIn("def _show_windows_reply_done_notification", src)
        self.assertNotIn("def _show_reply_done_system_notification", src)
        self.assertNotIn("powershell.exe", src)
        self.assertNotIn("def _show_reply_done_popup", src)
        self.assertIn("1500", src)
        self.assertIn("if not was_aborted", src)

    def test_launcher_main_sets_windows_app_identity_for_notifications(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GenericAgentLauncher")', src)
        self.assertIn('app.setApplicationName("GenericAgent Launcher")', src)
        self.assertIn('setter("GenericAgent Launcher")', src)
        self.assertIn('app.setOrganizationName("GenericAgent")', src)

    def test_bridge_runtime_exposes_attachment_and_llm_disabled_reason_helpers(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _apply_bridge_widget_state", src)
        self.assertIn("def _bridge_attachment_remove_disabled_reason", src)
        self.assertIn("def _bridge_llm_combo_disabled_reason", src)
        self.assertIn("当前这一轮还没有结束；本轮已附带图片会在回复完成后自动清除。", src)
        self.assertIn("当前还没有可用的 LLM 配置。", src)
        self.assertIn("把这张图片从下一轮输入中移除。", src)
        self.assertIn("切换当前会话使用的模型。", src)

    def test_navigation_channel_api_and_personal_pages_expose_consistent_disabled_reasons(self):
        root = os.path.dirname(os.path.dirname(__file__))
        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        channel_path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        api_path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        with open(channel_path, "r", encoding="utf-8") as f:
            channel_src = f.read()
        with open(api_path, "r", encoding="utf-8") as f:
            api_src = f.read()
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        self.assertIn("def _apply_navigation_widget_state", nav_src)
        self.assertIn("进入聊天页并开始准备当前内核环境。", nav_src)
        self.assertIn("请先选择有效的 GenericAgent 目录。", nav_src)
        self.assertIn("def _channel_source_action_disabled_reason", channel_src)
        self.assertIn("把当前通讯配置写回 channels/mykey.py。", channel_src)
        self.assertIn("手动刷新当前目标的渠道运行状态。", channel_src)
        self.assertIn("停止当前启动器托管的全部本地通讯渠道。", channel_src)
        self.assertIn("def _api_model_fetch_disabled_reason", api_src)
        self.assertIn("当前正在拉取该配置的模型列表，请稍候。", api_src)
        self.assertIn("从当前 API 地址拉取可用模型列表。", api_src)
        self.assertIn("def _lan_interface_form_disabled_reason", personal_src)
        self.assertIn("def _lan_interface_toggle_disabled_reason", personal_src)
        self.assertIn("def _langfuse_clear_disabled_reason", personal_src)
        self.assertIn("请先开启局域网 Web 接口，再调整这个选项。", personal_src)
        self.assertIn("当前还没有已保存的 Langfuse 配置可清除。", personal_src)

    def test_python_env_probe_forces_utf8_subprocess_env(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_external_subprocess_env", src)
        self.assertIn("_run_external_subprocess", src)
        self.assertIn('encoding="utf-8"', src)
        self.assertIn('errors="replace"', src)
        self.assertIn("env=_external_subprocess_env()", src)

    def test_channel_runtime_launch_uses_utf8_python_env(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("py_env = lz._external_subprocess_env()", src)
        self.assertIn("lz._popen_external_subprocess(", src)
        self.assertIn("env=py_env", src)

    def test_runtime_has_pyinstaller_external_subprocess_sanitizer(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _external_subprocess_env", src)
        self.assertIn("def _external_subprocess_runtime", src)
        self.assertIn("SetDllDirectoryW(None)", src)
        self.assertIn('env.pop(key, None)', src)
        self.assertIn('"PYTHONPATH"', src)
        self.assertIn('env.pop("_MEIPASS2", None)', src)
        self.assertIn('kwargs["env"] = _external_subprocess_env(kwargs.get("env"))', src)

    def test_external_subprocess_env_clears_python_path_vars(self):
        env = lz._external_subprocess_env(
            {
                "PATH": "C:\\bin",
                "PYTHONHOME": "C:\\bad-home",
                "PYTHONPATH": "C:\\bad-path",
                "PYTHONUSERBASE": "C:\\bad-user",
                "PYTHONNOUSERSITE": "1",
            }
        )
        self.assertEqual(env["PATH"], "C:\\bin")
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PYTHONUSERBASE", env)
        self.assertNotIn("PYTHONNOUSERSITE", env)

    def test_bridge_runtime_uses_external_subprocess_sanitizer(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("bridge_env = lz._external_subprocess_env()", src)
        self.assertIn("self.bridge_proc = lz._popen_external_subprocess(", src)

    def test_bridge_runtime_shows_trace_in_detailed_error_dialog(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('trace = ev.get("trace", "")', src)
        self.assertIn("box.setDetailedText(str(trace))", src)

    def test_api_editor_has_theme_aware_combo_style(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _api_combo_style", src)
        self.assertIn("format_box.setStyleSheet(self._api_combo_style())", src)
        self.assertIn("tpl_box.setStyleSheet(self._api_combo_style())", src)
        self.assertIn("QComboBox::down-arrow", src)
        self.assertIn("border-top: 6px solid", src)

    def test_api_editor_posts_model_fetch_results_back_to_ui_thread(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _api_on_ui_thread", src)
        self.assertIn("QTimer.singleShot(0, app, run)", src)
        self.assertIn('if "already deleted" in text or "Internal C++ object" in text:', src)
        self.assertIn("self._api_on_ui_thread(done_ok)", src)
        self.assertIn("err_text = str(e)", src)
        self.assertIn('state["model_status"] = f"拉取失败：{err_text}"', src)
        self.assertIn("self._api_on_ui_thread(done_err)", src)

    def test_api_editor_supports_native_claude_user_agent_field(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('for key in ("name", "apikey", "apibase", "model", "user_agent", "llm_nos")', src)
        self.assertIn('"advanced_values": advanced_values', src)
        self.assertIn("def _api_advanced_field_keys", src)
        self.assertIn("def _api_normalize_advanced_value", src)
        self.assertIn('data[key] = normalized', src)
        self.assertIn('advanced_toggle.setCheckable(True)', src)
        self.assertIn('sync_advanced_fold', src)
        self.assertIn('("▾ " if flag else "▸ ") + "高级参数"', src)
        self.assertIn('user_agent', src)

    def test_api_editor_has_template_specific_advanced_field_metadata(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_API_ADVANCED_FIELD_META", src)
        self.assertIn("_API_KIND_ADVANCED_FIELDS", src)
        self.assertIn('"fake_cc_system_prompt"', src)
        self.assertIn('"reasoning_effort"', src)
        self.assertIn('"user_agent"', src)
        self.assertIn('widget.addItem("跟随模板/默认", "")', src)

    def test_window_supports_tray_floating_chat_mode(self):
        root = os.path.dirname(os.path.dirname(__file__))
        window_path = os.path.join(root, "launcher_app", "window.py")
        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        bridge_path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        with open(bridge_path, "r", encoding="utf-8") as f:
            bridge_src = f.read()
        self.assertIn("class FloatingOrbWindow(QWidget):", window_src)
        self.assertIn("def toggle_panel(self):", window_src)
        self.assertIn('self.ball_btn.setToolTip("点击展开或收起悬浮对话")', window_src)
        self.assertIn("def _sync_floating_llm_combo(self):", window_src)
        self.assertIn("def _sync_floating_session_list(self):", window_src)
        self.assertIn("def _new_session_from_floating(self):", window_src)
        self.assertIn("def _regenerate_latest_from_floating(self):", window_src)
        self.assertIn("def _save_floating_orb_position(self, pos: QPoint):", window_src)
        self.assertIn("def _sync_draft_to_floating(self, *, force=False):", window_src)
        self.assertIn("self.llm_combo = QComboBox()", window_src)
        self.assertIn("self.session_combo = QComboBox()", window_src)
        self.assertIn("self.new_session_btn = QPushButton(\"新建\")", window_src)
        self.assertIn("self.regen_btn = QPushButton(\"重试\")", window_src)
        self.assertIn("def _enter_tray_floating_mode(self):", window_src)
        self.assertIn("def _restore_from_tray_mode(self):", window_src)
        self.assertIn("def _floating_hide_action_text(self) -> str:", window_src)
        self.assertIn("def _floating_default_status_text(self) -> str:", window_src)
        self.assertIn("def _functions_menu_floating_action_text(self) -> str:", window_src)
        self.assertIn("def _focus_visible_floating_chat_window(self, *, update_status: bool = True) -> bool:", window_src)
        self.assertIn("def _handle_functions_menu_floating_action(self) -> None:", window_src)
        self.assertIn("def _floating_window_visible(self) -> bool:", window_src)
        self.assertIn("def refresh_action_texts(self):", window_src)
        self.assertIn("if self.isVisible() and not self._tray_mode_active:", window_src)
        self.assertIn("def _show_floating_chat_window_only(self):", window_src)
        self.assertIn('_launcher_tray_signal_owner', window_src)
        self.assertIn('tray.activated.connect(self._on_launcher_tray_activated)', window_src)
        self.assertIn('tray_action = menu.addAction(chat_common._svg_icon("menu_floating"', shell_src)
        self.assertIn('action = getattr(self, "_handle_functions_menu_floating_action", None)', shell_src)
        self.assertIn('floating_label_getter = getattr(self, "_functions_menu_floating_action_text", None)', shell_src)
        self.assertIn("floating.apply_theme()", shell_src)
        self.assertIn("floating_sync = getattr(self, \"_sync_floating_llm_combo\", None)", bridge_src)

    def test_window_shell_rerenders_api_cards_after_theme_switch(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('renderer = getattr(self, "_render_api_cards", None)', src)
        self.assertIn("renderer()", src)

    def test_chat_view_supports_user_row_anchor_and_jump_latest_visibility(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "chat_view.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _scroll_row_to_top", src)
        self.assertIn("def _latest_user_row", src)
        self.assertIn("def _set_current_turn_user_row", src)
        self.assertIn("def _clear_current_turn_user_row", src)
        self.assertIn("def _tracked_current_turn_user_row", src)
        self.assertIn("target_user_row = self._tracked_current_turn_user_row()", src)
        self.assertIn("self._refresh_jump_latest_button()", src)
        self.assertIn("btn.setVisible(not near_bottom)", src)
        self.assertIn("def _add_message_row(self, role: str, text: str, finished: bool = True, *, auto_scroll: bool = True)", src)

    def test_text_input_accepts_image_paste_and_drop_for_single_turn_attachments(self):
        root = os.path.dirname(os.path.dirname(__file__))
        common_path = os.path.join(root, "qt_chat_parts", "common.py")
        window_path = os.path.join(root, "launcher_app", "window.py")
        bridge_path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        bridge_py_path = os.path.join(root, "bridge.py")
        with open(common_path, "r", encoding="utf-8") as f:
            common_src = f.read()
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        with open(bridge_path, "r", encoding="utf-8") as f:
            bridge_src = f.read()
        with open(bridge_py_path, "r", encoding="utf-8") as f:
            bridge_py_src = f.read()
        self.assertIn("def canInsertFromMimeData(self, source) -> bool", common_src)
        self.assertIn("def insertFromMimeData(self, source) -> None", common_src)
        self.assertIn("def dropEvent(self, event) -> None", common_src)
        self.assertIn("image_cb=self._handle_input_image_attachments", window_src)
        self.assertIn("image_cb=host._handle_input_image_attachments", window_src)
        self.assertIn("self.input_attachment_host", window_src)
        self.assertIn("self._pending_input_attachments_data = []", window_src)
        self.assertIn("def _attachment_bar_targets(self):", bridge_src)
        self.assertIn("def _attachment_bar_display_items(self):", bridge_src)
        self.assertIn("self._render_attachment_bar_target(host, layout, summary)", bridge_src)
        self.assertIn("items = list(self._attachment_bar_display_items())", bridge_src)
        self.assertIn("def _handle_input_image_attachments", bridge_src)
        self.assertIn("def _clear_active_turn_attachments", bridge_src)
        self.assertIn('"images": [str(item.get("path") or "").strip() for item in attachments]', bridge_src)
        self.assertIn("def _build_prompt_with_images", bridge_py_src)
        self.assertIn("def _build_multimodal_user_content", bridge_py_src)
        self.assertIn("def _scrub_last_user_history", bridge_py_src)
        self.assertIn("def _patch_agent_launcher_multimodal", bridge_py_src)
        self.assertIn('rich_content = _build_multimodal_user_content(prompt_text, images)', bridge_py_src)
        self.assertIn("initial_user_content=rich_content", bridge_py_src)
        self.assertIn("scrub_user_content=scrub_user_content", bridge_py_src)

    def test_remote_agent_dir_defaults_follow_username_and_migrate_legacy_path(self):
        self.assertEqual(common.remote_agent_dir_default(""), "/root/agant")
        self.assertEqual(common.remote_agent_dir_default("root"), "/root/agant")
        self.assertEqual(common.remote_agent_dir_default("ubuntu"), "/home/ubuntu/agant")
        self.assertEqual(common.normalize_remote_agent_dir("", username="ubuntu"), "/home/ubuntu/agant")
        self.assertEqual(common.normalize_remote_agent_dir("/opt/agant", username="ubuntu"), "/home/ubuntu/agant")
        self.assertEqual(common.normalize_remote_agent_dir("/srv/agant", username="ubuntu"), "/srv/agant")
        self.assertTrue(common.is_auto_remote_agent_dir("/root/agant"))
        self.assertTrue(common.is_auto_remote_agent_dir("/home/demo/agant"))
        self.assertFalse(common.is_auto_remote_agent_dir("/srv/agant"))

    def test_settings_panel_remote_targets_are_ssh_path_only(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('notice.setText(f"当前目标：远程设备（SSH 宿主机）。API/渠道配置会写入 `{path_text}`。")', src)
        self.assertIn('entries.append((f"{name}（SSH）", {"scope": "remote", "device_id": did, "host": host}))', src)
        self.assertNotIn("def _settings_target_remote_mode", src)
        self.assertNotIn("def _settings_target_uses_docker", src)
        self.assertNotIn("当前目标：远程 Docker 容器", src)
        self.assertNotIn("兼容配置：旧版 Docker 目标", src)

    def test_bridge_runtime_anchors_send_to_user_row(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        common_path = os.path.join(root, "qt_chat_parts", "common.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        with open(common_path, "r", encoding="utf-8") as f:
            common_src = f.read()
        self.assertIn("def chat_auto_jump_latest_enabled(cfg: dict | None) -> bool:", common_src)
        self.assertIn("def _arm_current_turn_auto_jump(self, user_row):", src)
        self.assertIn("auto_jump = self._chat_auto_jump_latest_enabled()", src)
        self.assertIn('display_text = text or f"[已发送 {len(attachments)} 张图片]"', src)
        self.assertIn('user_row = self._add_message_row("user", display_text, finished=True, auto_scroll=False)', src)
        self.assertIn('self._stream_row = self._add_message_row("assistant", "", finished=False, auto_scroll=False)', src)
        self.assertIn("self._user_scrolled_up = False", src)
        self.assertIn("self._scroll_row_to_top(user_row, preserve_scroll_state=True)", src)
        self.assertIn("self._arm_current_turn_auto_jump(user_row)", src)

    def test_bridge_build_multimodal_user_content_includes_data_url_for_image_only_turn(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"fake-image-bytes")
            image_path = tmp.name
        self.addCleanup(lambda: os.path.exists(image_path) and os.remove(image_path))

        content = bridge._build_multimodal_user_content("", [image_path])

        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn("data:image/png;base64,", content[1]["image_url"]["url"])

    def test_window_builds_jump_latest_button(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            "self.msg_layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Fixed))",
            src,
        )
        self.assertNotIn("self.msg_layout.addStretch(1)", src)
        self.assertIn("self.jump_latest_btn = QPushButton(self.scroll.viewport())", src)
        self.assertIn('self.jump_latest_btn.setToolTip("跳到最新对话")', src)
        self.assertIn("self.jump_latest_btn.clicked.connect(self._jump_to_latest_dialogue)", src)
        self.assertIn("self.scroll.viewport().installEventFilter(self)", src)

    def test_window_shell_jump_latest_targets_latest_user_row(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('getter = getattr(self, "_latest_user_row", None)', src)
        self.assertIn("latest_user_row = getter()", src)
        self.assertIn("self._scroll_row_to_top(latest_user_row)", src)
        self.assertIn("self._scroll_to_bottom(force=True)", src)

    def test_session_shell_repositions_jump_latest_button_on_viewport_resize(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "session_shell.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("event.type() in (QEvent.Resize, QEvent.Show)", src)
        self.assertIn('placer = getattr(self, "_place_jump_latest_button", None)', src)

    def test_sidebar_remote_session_status_texts_explain_cache_and_writeback(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "sidebar_sessions.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("已载入远程会话缓存，正在后台同步；可继续发送，新内容会尝试写回远端。", src)
        self.assertIn("已同步远程会话；后续发送会继续写回远端。", src)
        self.assertIn("远端同步失败，当前仍使用本地缓存：", src)

    def test_personal_settings_exposes_reply_notification_toggles(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn('notify_title = QLabel("回复提醒")', settings_src)
        self.assertIn("完成提示消息", settings_src)
        self.assertIn('self.settings_disable_reply_sound = QCheckBox("关闭提示音")', settings_src)
        self.assertIn('self.settings_disable_reply_message = QCheckBox("关闭提示消息")', settings_src)
        self.assertIn("notify_save_btn.clicked.connect(self._save_personal_preferences)", settings_src)

        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        self.assertIn("def _reload_personal_preferences", personal_src)
        self.assertIn('self.cfg["disable_reply_sound"]', personal_src)
        self.assertIn('self.cfg["disable_reply_message"]', personal_src)

    def test_personal_settings_exposes_lan_streamlit_interface(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        window_path = os.path.join(root, "launcher_app", "window.py")
        navigation_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        with open(navigation_path, "r", encoding="utf-8") as f:
            navigation_src = f.read()
        self.assertIn('lan_title = QLabel("局域网 Web 接口")', settings_src)
        self.assertIn('self.settings_lan_enabled = QCheckBox("启用局域网 Web 接口")', settings_src)
        self.assertIn('self.settings_lan_bind_all = QCheckBox("允许同一局域网设备访问（绑定 0.0.0.0）")', settings_src)
        self.assertIn('self.settings_lan_frontend_combo.addItem("默认 Streamlit（stapp.py）", "frontends/stapp.py")', settings_src)
        self.assertIn("self.settings_lan_save_btn.clicked.connect(self._save_lan_interface_settings)", settings_src)
        self.assertIn("def _lan_interface_cfg", personal_src)
        self.assertIn("_LAN_INTERFACE_EXTRA_PACKAGES = (\"streamlit>=1.28\",)", personal_src)
        self.assertIn('"--server.address"', personal_src)
        self.assertIn('"0.0.0.0" if bool(item.get("bind_all")) else "127.0.0.1"', personal_src)
        self.assertIn("def _schedule_lan_interface_autostart", personal_src)
        self.assertIn("def _detach_processes_for_shutdown", window_src)
        self.assertIn('self._append_shutdown_process(items, "lan-interface", lan_proc, lan_handle)', window_src)
        self.assertIn("_schedule_lan_interface_autostart", navigation_src)

    def test_usage_log_page_mentions_langfuse_and_richer_sections(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        channels_path = os.path.join(root, "launcher_core_parts", "channels.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        with open(channels_path, "r", encoding="utf-8") as f:
            channels_src = f.read()
        self.assertIn('("usage", "使用日志")', settings_src)
        self.assertIn('"使用日志"', settings_src)
        self.assertIn("Langfuse 追踪", personal_src)
        self.assertIn("日志来源", personal_src)
        self.assertIn("高消耗会话", personal_src)
        self.assertIn("最近活动", personal_src)
        self.assertIn("Token / 费用", personal_src)
        self.assertIn("清空当前目标日志", personal_src)
        self.assertIn("7 日 Token 趋势", personal_src)
        self.assertIn("渠道费用结构", personal_src)
        self.assertIn("高级模式 · Langfuse", personal_src)
        self.assertIn("def _usage_table_card", personal_src)
        self.assertIn("def _usage_metric_card", personal_src)
        self.assertIn("def _usage_chart_card", personal_src)
        self.assertIn("def _usage_line_chart_pixmap", personal_src)
        self.assertIn("def _usage_bar_chart_pixmap", personal_src)
        self.assertIn("def _clear_usage_logs_for_target", personal_src)
        self.assertIn("def _usage_cache_label", personal_src)
        self.assertIn("数据不足", personal_src)
        self.assertIn("def _load_langfuse_status", personal_src)
        self.assertIn("def _save_langfuse_config", personal_src)
        self.assertIn("def _clear_langfuse_config", personal_src)
        self.assertIn("保存并重启内核", personal_src)
        self.assertIn("使用官方云端", personal_src)
        self.assertIn("langfuse_config", personal_src)
        self.assertIn('"langfuse_config"', channels_src)

    def test_usage_chart_helpers_render_pixmaps(self):
        app = QApplication.instance() or QApplication([])
        self.addCleanup(lambda: app.processEvents())

        class DummyUsage(personal_usage_mod.PersonalUsageMixin):
            _usage_chart_pixmap = personal_usage_mod.PersonalUsageMixin._usage_chart_pixmap
            _usage_qcolor = personal_usage_mod.PersonalUsageMixin._usage_qcolor
            _usage_num = personal_usage_mod.PersonalUsageMixin._usage_num
            _usage_line_chart_pixmap = personal_usage_mod.PersonalUsageMixin._usage_line_chart_pixmap
            _usage_bar_chart_pixmap = personal_usage_mod.PersonalUsageMixin._usage_bar_chart_pixmap

        original_palette = dict(launcher_theme.C)
        original_prefs = dict(launcher_theme._VISUAL_PREFS)
        try:
            launcher_theme.set_theme("light")
            launcher_theme.configure_visual_preferences({})
            dummy = DummyUsage()
            line = dummy._usage_line_chart_pixmap(
                [
                    {"label": "05-19", "value": 1200, "value_label": "1,200"},
                    {"label": "05-20", "value": 1800, "value_label": "1,800"},
                    {"label": "05-21", "value": 900, "value_label": "900"},
                ]
            )
            bar = dummy._usage_bar_chart_pixmap(
                [
                    {"label": "主聊天区", "value": 1800, "value_label": "1,800", "detail": "provider"},
                    {"label": "微信", "value": 900, "value_label": "900", "detail": "estimate"},
                ]
            )
            self.assertFalse(line.isNull())
            self.assertFalse(bar.isNull())
        finally:
            launcher_theme.C.clear()
            launcher_theme.C.update(original_palette)
            launcher_theme._VISUAL_PREFS.clear()
            launcher_theme._VISUAL_PREFS.update(original_prefs)

    def test_load_langfuse_status_accepts_agentmain_hook_loader_chain(self):
        class DummyUsage(personal_usage_mod.PersonalUsageMixin):
            _load_langfuse_status = personal_usage_mod.PersonalUsageMixin._load_langfuse_status

            def __init__(self, agent_dir):
                self.agent_dir = agent_dir

        with tempfile.TemporaryDirectory() as td:
            plugins_dir = os.path.join(td, "plugins")
            os.makedirs(plugins_dir, exist_ok=True)
            with open(os.path.join(plugins_dir, "langfuse_tracing.py"), "w", encoding="utf-8") as f:
                f.write("# plugin\n")
            with open(os.path.join(plugins_dir, "hooks.py"), "w", encoding="utf-8") as f:
                f.write("def discover_and_load():\n    return None\n")
            with open(os.path.join(td, "agentmain.py"), "w", encoding="utf-8") as f:
                f.write("from plugins.hooks import discover_and_load; discover_and_load()\n")
            with open(os.path.join(td, "llmcore.py"), "w", encoding="utf-8") as f:
                f.write("# modern hook mode\n")
            with open(os.path.join(td, "mykey.py"), "w", encoding="utf-8") as f:
                f.write(
                    "langfuse_config = {\n"
                    "    'public_key': 'pk-demo',\n"
                    "    'secret_key': 'sk-demo',\n"
                    "    'host': 'https://cloud.langfuse.com',\n"
                    "}\n"
                )

            with mock.patch.object(personal_usage_mod.lz, "is_valid_agent_dir", return_value=True):
                status = DummyUsage(td)._load_langfuse_status()

        self.assertTrue(status["configured"])
        self.assertTrue(status["enabled"])
        self.assertTrue(status["agent_hook_loader"])
        self.assertTrue(status["hook_registry"])
        self.assertIn("hooks 自动发现", status["summary"])

    def test_schedule_page_recognizes_upstream_tasks(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        runtime_path = os.path.join(root, "qt_chat_parts", "schedule_runtime.py")
        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(runtime_path, "r", encoding="utf-8") as f:
            runtime_src = f.read()
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        self.assertIn('schedule_add_btn.clicked.connect(self._schedule_add_task_card)', settings_src)
        self.assertIn('schedule_refresh_btn.clicked.connect(self._reload_schedule_panel)', settings_src)
        self.assertIn("self.settings_schedule_notice", settings_src)
        self.assertIn("self.settings_schedule_list_layout", settings_src)
        self.assertIn("def _reload_schedule_panel", runtime_src)
        self.assertIn("def _render_schedule_task_cards", runtime_src)
        self.assertIn("def _schedule_save_task_state", runtime_src)
        self.assertIn("def _schedule_delete_task_state", runtime_src)
        self.assertIn("def _start_scheduler_process", runtime_src)
        self.assertIn("def _stop_scheduler_process", runtime_src)
        self.assertIn("新建任务", runtime_src)
        self.assertIn("保存任务", runtime_src)
        self.assertIn("高级参数", runtime_src)
        self.assertIn("detect_scheduler_lock", runtime_src)
        self.assertIn("启动调度器", runtime_src)
        self.assertIn("调度状态", runtime_src)
        self.assertIn("sche_tasks", runtime_src)
        self.assertIn("scheduler.log", runtime_src)
        self.assertIn("启动日志", runtime_src)
        self.assertIn("_start_autostart_scheduler", nav_src)

    def test_quick_start_skips_dependency_check_but_locate_page_keeps_it(self):
        root = os.path.dirname(os.path.dirname(__file__))
        setup_path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(setup_path, "r", encoding="utf-8") as f:
            setup_src = f.read()
        self.assertIn("self.enter_chat_btn.clicked.connect(self._quick_enter_chat)", setup_src)
        self.assertIn("欢迎页的“直接启动”不会先做这一步", setup_src)

        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        self.assertIn("def _quick_enter_chat(self):", nav_src)
        self.assertIn("def _can_skip_dependency_check_on_quick_enter", nav_src)
        self.assertIn("self._enter_chat(skip_dependency_check=self._can_skip_dependency_check_on_quick_enter())", nav_src)
        self.assertIn("def _enter_chat(self, *, skip_dependency_check=False):", nav_src)
        self.assertIn('if (not skip_dependency_check) and (not self._check_runtime_dependencies(purpose="载入内核")):', nav_src)

    def test_quick_start_always_skips_dependency_check(self):
        root = os.path.dirname(os.path.dirname(__file__))
        setup_path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(setup_path, "r", encoding="utf-8") as f:
            setup_src = f.read()
        self.assertIn("self.enter_chat_btn.clicked.connect(self._quick_enter_chat)", setup_src)
        self.assertIn("欢迎页的“直接启动”不会先做这一步", setup_src)

        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        self.assertIn("def _quick_enter_chat(self):", nav_src)
        self.assertIn("def _can_skip_dependency_check_on_quick_enter", nav_src)
        self.assertIn("return True", nav_src)
        self.assertIn("self._enter_chat(skip_dependency_check=self._can_skip_dependency_check_on_quick_enter())", nav_src)
        self.assertIn("def _enter_chat(self, *, skip_dependency_check=False):", nav_src)
        self.assertIn('if (not skip_dependency_check) and (not self._check_runtime_dependencies(purpose="载入内核")):', nav_src)

    def test_welcome_page_exposes_official_gui_entry(self):
        root = os.path.dirname(os.path.dirname(__file__))
        setup_path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(setup_path, "r", encoding="utf-8") as f:
            setup_src = f.read()
        self.assertIn("或许你想试试官方的？", setup_src)
        self.assertIn("直接拉起上游默认 GUI / 官方发布版桌面端", setup_src)
        self.assertIn("self._show_official_gui_page", setup_src)
        self.assertIn("self.official_gui_launch_btn.clicked.connect(self._launch_official_gui)", setup_src)
        self.assertIn("self.official_desktop_launch_btn.clicked.connect(self._launch_official_desktop_app)", setup_src)
        self.assertIn("self.official_desktop_release_btn.clicked.connect(self._open_official_desktop_release_page)", setup_src)
        self.assertIn("打开 Release 页面", setup_src)
        self.assertIn("官方桌面版（发布版）", setup_src)

        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        self.assertIn("def _launch_official_gui(self):", nav_src)
        self.assertIn("def _launch_official_desktop_app(self):", nav_src)
        self.assertIn("def _open_official_desktop_release_page(self):", nav_src)
        self.assertIn('purpose="启动官方 GUI"', nav_src)
        self.assertIn("launch_web_ui", nav_src)
        self.assertIn("launch.pyw", nav_src)
        self.assertIn("/releases/latest", nav_src)
        self.assertIn("GenericAgent.app", nav_src)
        self.assertIn("GenericAgent-windows-x64.exe", nav_src)

    def test_startup_pages_use_scroll_bodies_and_recent_card_can_grow(self):
        app = QApplication.instance() or QApplication([])
        win = None

        with mock.patch.object(launcher_window.lz, "load_config", return_value={}), mock.patch.object(
            launcher_window.lz, "is_valid_agent_dir", return_value=True
        ), mock.patch.object(
            launcher_window.QtChatWindow, "_schedule_session_index_warmup", autospec=True, side_effect=lambda self: None
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
        ):
            win = launcher_window.QtChatWindow(r"E:\\GenericAgent")
            win._drain_timer.stop()
            win._server_status_timer.stop()
            win._subagent_status_timer.stop()
            win._stream_flush_timer.stop()
            win.resize(1100, 520)
            win.show()
            app.processEvents()
            initial_recent_card_height = win.recent_card.height()

            for attr_name in ("_welcome_page", "_locate_page", "_official_gui_page"):
                page = getattr(win, attr_name)
                scrolls = page.findChildren(launcher_window.QScrollArea)
                self.assertTrue(scrolls, msg=attr_name)

            for attr_name in ("_locate_page", "_official_gui_page"):
                page = getattr(win, attr_name)
                win.pages.setCurrentWidget(page)
                app.processEvents()
                scroll = page.findChildren(launcher_window.QScrollArea)[0]
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0, msg=attr_name)

            long_path = "GenericAgent path with many spaced segments " * 8
            win.agent_dir = long_path
            win._refresh_welcome_state()
            win._show_welcome()
            app.processEvents()
            app.processEvents()

            self.assertEqual(win.recent_path_label.text(), long_path)
            self.assertGreater(win.recent_card.height(), initial_recent_card_height)
            self.assertGreaterEqual(win.recent_card.height(), win.recent_card.sizeHint().height())
            self.assertGreaterEqual(win.recent_card.minimumHeight(), 78)
            self.assertGreaterEqual(win.recent_card.minimumHeight(), win.recent_card.sizeHint().height())
            self.assertGreater(win.recent_card.maximumHeight(), 78)

        if win is not None:
            try:
                app.removeEventFilter(win)
            except Exception:
                pass
            win.close()
            win.deleteLater()
            app.processEvents()

    def test_spec_uses_local_hooks_dir(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "GenericAgentLauncher.spec")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('HOOKS_DIR = os.path.join(ROOT_DIR, "hooks")', src)
        self.assertIn("hookspath=[HOOKS_DIR]", src)

    def test_windows_specs_embed_launcher_icon(self):
        root = os.path.dirname(os.path.dirname(__file__))
        paths = [
            os.path.join(root, "GenericAgentLauncher.spec"),
            os.path.join(root, "LauncherBootstrap.spec"),
            os.path.join(root, "Updater.spec"),
        ]
        for path in paths:
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            self.assertIn('WINDOWS_ICON_PATH = os.path.join(ROOT_DIR, "assets", "launcher_app_icon.ico")', src)
            self.assertIn("icon=WINDOWS_ICON_PATH if os.path.isfile(WINDOWS_ICON_PATH) else None", src)

    def test_installer_shortcuts_set_explicit_bootstrap_icon_location(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "installer", "GenericAgentLauncher.iss")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("SetupIconFile=..\\assets\\launcher_app_icon.ico", src)
        self.assertIn('Source: "..\\assets\\launcher_app_icon.ico"; DestDir: "{app}"; Flags: ignoreversion', src)
        self.assertIn('Name: "{autoprograms}\\GenericAgent Launcher"; Filename: "{app}\\LauncherBootstrap.exe"; WorkingDir: "{app}"; IconFilename: "{app}\\launcher_app_icon.ico"; IconIndex: 0', src)
        self.assertIn('Name: "{autodesktop}\\GenericAgent Launcher"; Filename: "{app}\\LauncherBootstrap.exe"; WorkingDir: "{app}"; IconFilename: "{app}\\launcher_app_icon.ico"; IconIndex: 0; Check: WizardIsTaskSelected(\'desktopicon\') or ExistingDesktopShortcutExists()', src)
        self.assertIn('Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce', src)
        self.assertIn("function ExistingDesktopShortcutExists(): Boolean;", src)
        self.assertIn("Check: WizardIsTaskSelected('desktopicon') or ExistingDesktopShortcutExists()", src)
        self.assertIn("procedure SHChangeNotify(wEventId: Integer; uFlags: Integer; dwItem1: Integer; dwItem2: Integer);", src)
        self.assertIn("procedure RefreshShellIcons();", src)
        self.assertIn("if not Exec(Ie4uinitPath, '-ClearIconCache', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then", src)
        self.assertIn("if not Exec(Ie4uinitPath, '-show', '', SW_HIDE, ewNoWait, ResultCode) then", src)
        self.assertIn("RefreshShellIcons();", src)

    def test_claude_template_defaults_follow_upstream_model_names(self):
        self.assertEqual(lz.TEMPLATE_INDEX["anthropic"]["defaults"]["model"], "claude-opus-4-7[1m]")
        self.assertEqual(lz.TEMPLATE_INDEX["cc-switch"]["defaults"]["model"], "claude-opus-4-7")
        self.assertEqual(lz.TEMPLATE_INDEX["crs-claude"]["defaults"]["model"], "claude-opus-4-7[1m]")
        self.assertEqual(lz.TEMPLATE_INDEX["crs-gemini"]["defaults"]["model"], "claude-opus-4-7-thinking")
        self.assertEqual(lz.TEMPLATE_INDEX["openrouter"]["defaults"]["model"], "anthropic/claude-opus-4-7")
        self.assertEqual(lz.TEMPLATE_INDEX["commonstack"]["defaults"]["apibase"], "https://api.commonstack.ai/v1")
        self.assertEqual(lz.TEMPLATE_INDEX["commonstack"]["defaults"]["api_mode"], "chat_completions")
        self.assertIn("commonstack", lz.SIMPLE_FORMAT_RULES["oai_chat"]["templates"])

    def test_custom_importlib_resources_hook_guards_missing_trees_module(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "hooks", "hook-importlib_resources.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('find_spec("importlib_resources.trees") is not None', src)

    def test_custom_requests_hook_collects_runtime_imported_package(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "hooks", "hook-requests.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("collect_all", src)
        self.assertIn('collect_all("requests")', src)

        pyinstaller_hooks_module = types.ModuleType("PyInstaller.utils.hooks")
        calls = {}

        def _collect_all(name):
            calls["name"] = name
            return [("requests-data", ".")], ["requests-bin"], ["requests.hidden"]

        pyinstaller_hooks_module.collect_all = _collect_all
        namespace = {"__builtins__": __builtins__}
        with mock.patch.dict(sys.modules, {"PyInstaller.utils.hooks": pyinstaller_hooks_module}):
            exec(compile(src, path, "exec"), namespace)
        self.assertEqual(calls["name"], "requests")
        self.assertEqual(namespace["datas"], [("requests-data", ".")])
        self.assertEqual(namespace["binaries"], ["requests-bin"])
        self.assertEqual(namespace["hiddenimports"], ["requests.hidden"])

    def test_custom_charset_normalizer_and_simplejson_hooks_collect_runtime_smoke_packages(self):
        root = os.path.dirname(os.path.dirname(__file__))
        cases = [
            ("hook-charset_normalizer.py", "charset_normalizer"),
            ("hook-simplejson.py", "simplejson"),
        ]
        pyinstaller_hooks_module = types.ModuleType("PyInstaller.utils.hooks")
        calls = []

        def _collect_all(name):
            calls.append(str(name))
            return [(f"{name}-data", ".")], [f"{name}-bin"], [f"{name}.hidden"]

        pyinstaller_hooks_module.collect_all = _collect_all
        with mock.patch.dict(sys.modules, {"PyInstaller.utils.hooks": pyinstaller_hooks_module}):
            for filename, package_name in cases:
                path = os.path.join(root, "hooks", filename)
                with open(path, "r", encoding="utf-8") as f:
                    src = f.read()
                self.assertIn("collect_all", src)
                self.assertIn(f'collect_all("{package_name}")', src)
                namespace = {"__builtins__": __builtins__}
                exec(compile(src, path, "exec"), namespace)
                self.assertEqual(namespace["datas"], [(f"{package_name}-data", ".")])
                self.assertEqual(namespace["binaries"], [f"{package_name}-bin"])
                self.assertIn(f"{package_name}.hidden", namespace["hiddenimports"])

        self.assertEqual(calls, ["charset_normalizer", "simplejson"])

    def test_custom_charset_normalizer_hook_collects_mypyc_helper_module(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "hooks", "hook-charset_normalizer.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('endswith("__mypyc")', src)
        self.assertIn('importlib.import_module("charset_normalizer")', src)

        pyinstaller_hooks_module = types.ModuleType("PyInstaller.utils.hooks")

        def _collect_all(name):
            return [(f"{name}-data", ".")], [f"{name}-bin"], [f"{name}.hidden"]

        pyinstaller_hooks_module.collect_all = _collect_all
        namespace = {"__builtins__": __builtins__}
        with tempfile.TemporaryDirectory() as td:
            site_root = os.path.join(td, "site-packages")
            os.makedirs(os.path.join(site_root, "charset_normalizer"), exist_ok=True)
            package_mod = types.ModuleType("charset_normalizer")
            package_mod.__file__ = os.path.join(site_root, "charset_normalizer", "__init__.py")
            helper_name = "demo_hash__mypyc"
            helper_mod = types.ModuleType(helper_name)
            helper_mod.__file__ = os.path.join(site_root, f"{helper_name}.cp312-win_amd64.pyd")
            other_mod = types.ModuleType("unrelated__mypyc")
            other_mod.__file__ = os.path.join(td, "unrelated__mypyc.cp312-win_amd64.pyd")
            with mock.patch.dict(
                sys.modules,
                {
                    "PyInstaller.utils.hooks": pyinstaller_hooks_module,
                    "charset_normalizer": package_mod,
                    helper_name: helper_mod,
                    "unrelated__mypyc": other_mod,
                },
                clear=False,
            ):
                exec(compile(src, path, "exec"), namespace)
        self.assertIn(helper_name, namespace["hiddenimports"])
        self.assertNotIn("unrelated__mypyc", namespace["hiddenimports"])

    def test_private_python_installer_has_atomic_download_and_retry(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('temp_dest = dest + ".part"', src)
        self.assertIn("os.replace(temp_dest, dest)", src)
        self.assertIn("for attempt in range(2):", src)
        self.assertIn("准备重新下载安装包并重试一次", src)

    def test_private_python_installer_supports_mirror_fallback(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("mirrors.huaweicloud.com", src)
        self.assertIn("mirror.nju.edu.cn", src)
        self.assertIn("mirrors.bfsu.edu.cn", src)
        self.assertIn("mirrors.tuna.tsinghua.edu.cn", src)
        self.assertIn("mirrors.ustc.edu.cn", src)
        self.assertIn("spec.get(\"urls\") or [spec[\"url\"]]", src)

    def test_private_python_installer_detects_verification_pages(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("返回 HTML 页面而不是安装包", src)
        self.assertIn("下载内容疑似验证页面而非 exe", src)

    def test_private_python_installer_reports_download_speed(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("MB/s", src)
        self.assertIn("尝试下载源", src)
        self.assertIn("正在切换下一个源", src)

    def test_private_python_installer_uses_user_selected_sources(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("private_python_download_source_ids", src)
        self.assertIn("_private_python_selected_sources", src)
        self.assertIn("_rank_download_sources", src)
        self.assertIn("selected_urls or spec.get(\"urls\")", src)

    def test_private_python_installer_has_post_install_python_discovery(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_private_python_candidate_paths", src)
        self.assertIn("_resolve_private_python_exe", src)
        self.assertIn("wait_seconds=45", src)
        self.assertIn("已扫描路径", src)

    def test_private_python_installer_bootstraps_simplejson_with_requests(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"requests"', src)
        self.assertIn('"simplejson"', src)
        self.assertIn("--upgrade", src)
        self.assertIn("安装 requests / simplejson", src)

    def test_update_manager_persists_error_code_phase_and_two_stage_health(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "update_manager.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("class UpdateError", src)
        self.assertIn('"error_code"', src)
        self.assertIn('"phase"', src)
        self.assertIn("health-startup", src)
        self.assertIn("health-alive", src)
        self.assertIn("def _rollback_to_previous", src)

    def test_runtime_exposes_update_diagnostics_and_two_phase_ack(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def acknowledge_pending_update_alive", src)
        self.assertIn("def start_pending_update_alive_probe", src)
        self.assertIn("def read_updater_log_tail", src)
        self.assertIn("def latest_update_job", src)
        self.assertIn("def verify_authenticode_signature", src)

    def test_about_panel_has_update_diagnostics_card(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("更新诊断", src)
        self.assertIn("刷新诊断信息", src)
        self.assertIn("updater.log（最近 18 行）", src)
        self.assertIn("def _refresh_about_update_diagnostics_widgets", src)

    def test_launcher_update_supports_external_fallback_install_trigger(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _build_launcher_external_update_info", src)
        self.assertIn('"install_mode": "external"', src)
        self.assertIn('"readme_url"', src)
        self.assertIn('"sha256_url"', src)
        self.assertIn('"metadata_url"', src)
        self.assertIn("def _launcher_manual_update_payload", src)
        self.assertIn("def _show_launcher_manual_update_dialog", src)
        self.assertIn("下载更新安装包", src)
        self.assertIn("打开安装说明", src)
        self.assertIn("打开 sha256", src)
        self.assertIn("打开安装元数据", src)
        self.assertIn("打开 Release 页面", src)
        self.assertIn("QDesktopServices.openUrl", src)

    def test_launcher_update_proxy_ui_and_download_chain_are_wired(self):
        root = os.path.dirname(os.path.dirname(__file__))
        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        update_path = os.path.join(root, "launcher_core_parts", "update_manager.py")
        runtime_path = os.path.join(root, "launcher_core_parts", "runtime.py")
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        with open(update_path, "r", encoding="utf-8") as f:
            update_src = f.read()
        with open(runtime_path, "r", encoding="utf-8") as f:
            runtime_src = f.read()
        self.assertIn("更新代理", personal_src)
        self.assertIn("保存代理", personal_src)
        self.assertIn("launcher_update_proxy_url", personal_src)
        self.assertIn("proxy_url=proxy_url", personal_src)
        self.assertIn('"proxy_url": normalize_proxy_url(info.get("proxy_url"))', update_src)
        self.assertIn("download_to_file(package_url, package_file, timeout=timeout_seconds, proxy_url=proxy_url)", update_src)
        self.assertIn("def normalize_proxy_url", runtime_src)
        self.assertIn("def urlopen_with_proxy", runtime_src)

    def test_kernel_update_check_handles_unsynced_remote_without_false_diverged(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _git_has_commit", src)
        self.assertIn("def _git_try_fetch_origin", src)
        self.assertIn("def _git_ls_remote_default_head", src)
        self.assertIn("def _git_ls_remote_branch_head", src)
        self.assertIn('row["status"] = "need_sync"', src)
        self.assertIn("[需同步远端]", src)
        self.assertIn("kernel_update_auto_fetch_enabled", src)

    def test_update_diag_supports_kernel_repo_sync_actions(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("同步内核远端（fetch）", src)
        self.assertIn("拉取并快进（pull --ff-only）", src)
        self.assertIn("def _start_kernel_repo_sync", src)
        self.assertIn("def _sync_kernel_repo_fetch", src)
        self.assertIn("def _sync_kernel_repo_pull", src)
        self.assertIn("内核仓库同步完成", src)
        self.assertIn("def _git_fetch_origin_result", src)

    def test_update_summary_contains_api_source_hint(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("来源：", src)
        self.assertIn("GitHub API", src)
        self.assertIn("git ls-remote", src)

    def test_channel_specs_update_conflicts_and_dingtalk_min_version(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "channels.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"id": "wechat"', src)
        self.assertIn('"id": "qq"', src)
        self.assertIn('"conflicts_with": []', src)
        self.assertIn('"pip": "dingtalk-stream>=0.20"', src)

    def test_release_workflow_enforces_signing_secrets(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, ".github", "workflows", "release-installer.yml")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Validate signing secrets", src)
        self.assertIn("Missing secret: UPDATE_SIGNING_PRIVATE_KEY_PEM", src)
        self.assertIn("Missing secret: UPDATE_SIGNING_PUBLIC_KEY_PEM", src)
        self.assertIn("gh release create $tag", src)
        self.assertIn("tools/resolve_release_version.py", src)
        self.assertIn("release/VERSION", src)

    def test_setup_page_and_navigation_expose_visual_dependency_check(self):
        root = os.path.dirname(os.path.dirname(__file__))
        setup_path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        nav_path = os.path.join(root, "qt_chat_parts", "navigation.py")
        runtime_path = os.path.join(root, "qt_chat_parts", "dependency_runtime.py")
        with open(setup_path, "r", encoding="utf-8") as f:
            setup_src = f.read()
        with open(nav_path, "r", encoding="utf-8") as f:
            nav_src = f.read()
        with open(runtime_path, "r", encoding="utf-8") as f:
            runtime_src = f.read()
        self.assertIn("检查并补齐依赖", setup_src)
        self.assertIn("查看详细报告", setup_src)
        self.assertIn("载入前依赖检查", setup_src)
        self.assertIn("依赖安装器策略", setup_src)
        self.assertIn("自动（优先 uv，失败回退 pip）", setup_src)
        self.assertIn("_check_runtime_dependencies(purpose=\"载入内核\")", nav_src)
        self.assertIn("dependency_installer", nav_src)
        self.assertIn("GenericAgent requirements.txt", runtime_src)
        self.assertIn("_ensure_runtime_dependencies", runtime_src)
        self.assertIn("installer_mode", runtime_src)

    def test_settings_panel_supports_vps_connection_management(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        req_path = os.path.join(root, "requirements.txt")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(req_path, "r", encoding="utf-8") as f:
            req_src = f.read()
        self.assertIn('("vps", "VPS 管理")', settings_src)
        self.assertIn("def _settings_nav_icon_spec(self, key: str):", settings_src)
        self.assertIn("def _apply_settings_nav_button_icon(self, key: str, button, *, selected: bool = False) -> None:", settings_src)
        self.assertIn("self.settings_vps_username_edit", settings_src)
        self.assertIn("self.settings_vps_port_spin", settings_src)
        self.assertIn("self.settings_vps_key_path_edit", settings_src)
        self.assertIn("self.settings_vps_password_edit", settings_src)
        self.assertIn("安装 SSH 依赖", settings_src)
        self.assertIn("远程终端", settings_src)
        self.assertIn("一键直接部署", settings_src)
        self.assertIn("接管 agant", settings_src)
        self.assertIn("接管并注册 agant", settings_src)
        self.assertIn("agant 路径", settings_src)
        self.assertIn("上传本地 agant 项目", settings_src)
        self.assertIn("服务器拉取原始 agant", settings_src)
        self.assertIn("依赖策略", settings_src)
        self.assertIn("排除规则", settings_src)
        self.assertIn("def _reload_vps_panel", settings_src)
        self.assertIn("def _install_vps_dependencies", settings_src)
        self.assertIn("def _connect_vps_terminal", settings_src)
        self.assertIn("def _send_vps_terminal_command", settings_src)
        self.assertIn("def _deploy_vps_agent_direct", settings_src)
        self.assertIn("def _takeover_vps_agent", settings_src)
        self.assertNotIn("docker inspect -f", settings_src)
        self.assertIn("预检结果：", settings_src)
        self.assertIn("def _split_vps_upload_excludes", settings_src)
        self.assertIn("def _is_path_excluded_for_upload", settings_src)
        self.assertIn("开始执行远端直部署。", settings_src)
        self.assertIn("pip_mirror_url", settings_src)
        self.assertIn('if rel.lower() == "mykey.py"', settings_src)
        self.assertIn("已同步 mykey.py 到远端目录。", settings_src)
        self.assertIn("requirements.launcher_bootstrap.txt", settings_src)
        self.assertIn("def _vps_dep_install_source", settings_src)
        self.assertIn("def _vps_render_remote_requirement_install_cmd", settings_src)
        self.assertIn("def _refresh_vps_remote_dir_placeholder", settings_src)
        self.assertIn("def _sanitize_vps_feedback_text", settings_src)
        self.assertIn("def _append_vps_terminal_dependency_output", settings_src)
        self.assertIn("normalize_remote_agent_dir", settings_src)
        self.assertIn("remote_agent_dir_default", settings_src)
        self.assertIn("resolve_remote_fallback_requirement_specs", settings_src)
        self.assertIn("上游未提供 requirements.txt；当前改用 pyproject.toml 生成 fallback requirements。", settings_src)
        self.assertIn("上游未提供 requirements.txt；当前改用启动器维护的上游依赖表。", settings_src)
        self.assertIn("检测到远端 requirements.txt，优先使用仓库自带依赖表。", settings_src)
        self.assertIn("远端项目缺少 agentmain.py。", settings_src)
        self.assertIn("远端项目缺少 frontends 目录。", settings_src)
        self.assertIn("__NO_PIP__", settings_src)
        self.assertIn("def _save_vps_connection", settings_src)
        self.assertIn("def _test_vps_connection", settings_src)
        self.assertIn("_bootstrap_python_runtime", settings_src)
        self.assertIn("_install_python_packages", settings_src)
        self.assertIn("import paramiko", settings_src)
        self.assertIn('"vps": self._reload_vps_panel', settings_src)
        self.assertIn("paramiko>=3.4", req_src)

    def test_settings_panel_supports_remote_target_for_api_and_channel_configs(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        api_path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        channel_path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(api_path, "r", encoding="utf-8") as f:
            api_src = f.read()
        with open(channel_path, "r", encoding="utf-8") as f:
            channel_src = f.read()
        self.assertIn("配置目标设备", settings_src)
        self.assertIn("def _settings_target_context", settings_src)
        self.assertIn("def _settings_target_generation", settings_src)
        self.assertIn("def _bump_settings_target_generation", settings_src)
        self.assertIn("def _vps_task_result_stale", settings_src)
        self.assertIn("def _settings_target_read_mykey_text", settings_src)
        self.assertIn("def _settings_target_write_mykey_text", settings_src)
        self.assertIn("def _settings_category_scope_mode", settings_src)
        self.assertIn("def _refresh_settings_target_visibility", settings_src)
        self.assertIn("visible = bool(self._settings_category_uses_target_switch(category))", settings_src)
        self.assertIn("self._settings_target_section_visible = visible", settings_src)
        self.assertIn('self.settings_status_label.setText("当前页为启动器本机设置，不需要切换目标设备。")', settings_src)
        self.assertIn('f"当前页支持多设备切换。设置目标：{target_ctx.get(\'label\')}。"', settings_src)
        self.assertIn("只有“会话上限”卡片内的目标设备会跟随切换", settings_src)
        self.assertIn("self.settings_personal_target_combo = _StablePopupComboBox()", settings_src)
        self.assertIn("settingsPersonalTargetCombo", settings_src)
        self.assertIn("lambda index, combo=self.settings_personal_target_combo: self._on_settings_target_changed(index, combo=combo)", settings_src)
        self.assertIn("settings_target", settings_src)
        self.assertIn("class _StablePopupComboBox(QComboBox):", settings_src)
        self.assertIn("def _repair_popup_geometry(self):", settings_src)
        self.assertIn("self.settings_target_combo = _StablePopupComboBox()", settings_src)
        self.assertIn("self.settings_target_combo.setObjectName(\"settingsTargetCombo\")", settings_src)
        self.assertIn("self._apply_theme_combo_style(self.settings_target_combo)", settings_src)
        self.assertIn("self.settings_target_combo.currentIndexChanged.connect(self._on_settings_target_changed)", settings_src)
        self.assertIn("def _ensure_combo_popup_view", settings_src)
        self.assertIn("combo.setView(view)", settings_src)
        self.assertIn("QListView::item", settings_src)
        self.assertIn("QComboMenuDelegate", settings_src)
        self.assertIn("clicked.connect(lambda _=False: self._refresh_settings_target_combo(force=True))", settings_src)
        self.assertIn("def _settings_target_combo_entries", settings_src)
        self.assertIn("def _defocus_settings_target_combo", settings_src)
        self.assertIn("self._settings_target_combo_signature", settings_src)
        self.assertIn("if (not force) and current_signature == signature and combo.count() == len(entries):", settings_src)
        self.assertIn("_SETTINGS_SWITCH_RELOAD_DELAY_MS = 24", settings_src)
        self.assertIn("if current_widget is not target_widget:", settings_src)
        self.assertIn("QTimer.singleShot(delay_ms, self, run)", settings_src)
        self.assertIn("self._qt_api_remote_loading = False", settings_src)
        self.assertIn("self._qt_channel_remote_loading = False", settings_src)
        self.assertIn("self._settings_personal_remote_sync_running = False", settings_src)
        self.assertIn("self._settings_usage_remote_sync_running = False", settings_src)
        self.assertIn("stale = self._vps_task_result_stale(profile_id)", settings_src)
        self.assertIn("def _dismiss_combo_popup", settings_src)
        self.assertIn("self._dismiss_combo_popup(combo)", settings_src)
        self.assertIn("_settings_target_write_mykey_text", api_src)
        self.assertIn("if int(current_token or 0) != int(target_token or 0):", api_src)
        self.assertIn("已写入远端 mykey.py", api_src)
        self.assertIn("_settings_target_write_mykey_text", channel_src)
        self.assertIn("if int(current_token or 0) != int(target_token or 0):", channel_src)
        self.assertIn("远端配置模式", channel_src)
        self.assertIn('if category in ("api", "channels", "schedule", "sop", "usage"):', settings_src)

    def test_settings_panel_supports_sop_document_management(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn('("sop", "SOP")', settings_src)
        self.assertIn('"sop": chat_common._SVG_SPARKLE', settings_src)
        self.assertIn('self._settings_intro(', settings_src)
        self.assertIn("SOP 文档", settings_src)
        self.assertIn("self.settings_sop_doc_combo = _StablePopupComboBox()", settings_src)
        self.assertIn("self.settings_sop_doc_combo.currentIndexChanged.connect(self._load_selected_sop_document)", settings_src)
        self.assertIn("self.settings_sop_editor = QPlainTextEdit()", settings_src)
        self.assertIn("self.settings_sop_reload_btn.clicked.connect(self._reload_sop_panel)", settings_src)
        self.assertIn("self.settings_sop_save_btn.clicked.connect(self._save_selected_sop_document)", settings_src)
        self.assertIn("def _settings_sop_normalize_relpath", settings_src)
        self.assertIn("def _settings_target_list_sop_documents", settings_src)
        self.assertIn("def _settings_target_read_sop_text", settings_src)
        self.assertIn("def _settings_target_write_sop_text", settings_src)
        self.assertIn("def _reload_sop_panel", settings_src)
        self.assertIn('if name == "SKILL.md" or name.endswith("_sop.md"):', settings_src)
        self.assertIn("SOP 文档会读取并写回", settings_src)
        self.assertIn('"sop": self._reload_sop_panel', settings_src)

    def test_settings_panel_vps_actions_expose_disabled_reason_helpers(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn("def _apply_vps_button_state", settings_src)
        self.assertIn("def _vps_busy_reason", settings_src)
        self.assertIn("def _vps_terminal_connect_disabled_reason", settings_src)
        self.assertIn("def _vps_terminal_send_disabled_reason", settings_src)
        self.assertIn("def _vps_deploy_disabled_reason", settings_src)
        self.assertIn("请先新建至少一个服务器配置。", settings_src)
        self.assertIn("当前还没有服务器配置可切换。", settings_src)
        self.assertIn("当前终端已连接到另一台服务器，请先断开后再切换。", settings_src)
        self.assertIn("当前终端连接已在使用中，无需重复连接。", settings_src)
        self.assertIn("当前没有已连接的远程终端。", settings_src)
        self.assertIn("请先连接远程终端。", settings_src)
        self.assertIn("正在执行 VPS 直接部署，请等待当前任务完成。", settings_src)

    def test_schedule_personal_and_usage_pages_keep_target_device_support(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        personal_path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        schedule_path = os.path.join(root, "qt_chat_parts", "schedule_runtime.py")
        channel_path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        sidebar_path = os.path.join(root, "qt_chat_parts", "sidebar_sessions.py")
        api_path = os.path.join(root, "qt_chat_parts", "api_editor.py")
        bridge_path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        with open(personal_path, "r", encoding="utf-8") as f:
            personal_src = f.read()
        with open(schedule_path, "r", encoding="utf-8") as f:
            schedule_src = f.read()
        with open(channel_path, "r", encoding="utf-8") as f:
            channel_src = f.read()
        with open(sidebar_path, "r", encoding="utf-8") as f:
            sidebar_src = f.read()
        with open(api_path, "r", encoding="utf-8") as f:
            api_src = f.read()
        with open(bridge_path, "r", encoding="utf-8") as f:
            bridge_src = f.read()
        self.assertIn('"schedule": self._reload_schedule_panel', settings_src)
        self.assertIn('"personal": self._reload_personal_panel', settings_src)
        self.assertIn('"usage": self._reload_usage_panel', settings_src)
        self.assertIn("def _archive_limit_root", personal_src)
        self.assertIn('bucket = {"local:local": dict(bucket)}', personal_src)
        self.assertIn("def _settings_data_target_context", personal_src)
        self.assertIn("def _reload_personal_panel", personal_src)
        self.assertIn("下方“回复提醒”卡片不会跟随设备切换", personal_src)
        self.assertIn("def _apply_personal_button_state", personal_src)
        self.assertIn("def _about_update_check_disabled_reason", personal_src)
        self.assertIn("def _about_update_install_disabled_reason", personal_src)
        self.assertIn("def _kernel_sync_disabled_reason", personal_src)
        self.assertIn("当前正在检测 GitHub 更新，请稍候。", personal_src)
        self.assertIn("当前缺少内置 updater，暂时不能直接安装更新。", personal_src)
        self.assertIn("当前没有可用的内核 Git 仓库目录。", personal_src)
        self.assertIn("检测到端口已有外部 Streamlit 响应；请先关闭外部进程。", personal_src)
        self.assertIn("当前是外部启动的 Streamlit 进程，启动器无法直接停止。", personal_src)
        self.assertIn("当前还没有可用的局域网 Web 日志文件。", personal_src)
        self.assertIn("def _reload_usage_panel", personal_src)
        self.assertIn("if int(current_token or 0) != int(target_token or 0):", personal_src)
        self.assertIn("def _schedule_target_device", schedule_src)
        self.assertIn("def _schedule_remote_save_task", schedule_src)
        self.assertIn("def _schedule_remote_start", schedule_src)
        self.assertIn("def _schedule_remote_stop", schedule_src)
        self.assertIn("if int(current_generation or 0) != int(target_generation or 0):", schedule_src)
        self.assertIn('payload["is_remote"] = True', schedule_src)
        self.assertIn("保存、删除和启停操作都会直接写入远端 agant 目录", schedule_src)
        self.assertIn("def _schedule_open_report", schedule_src)
        self.assertIn("def _schedule_open_tasks_dir", schedule_src)
        self.assertIn("def _schedule_open_log_file", schedule_src)
        self.assertIn("def _apply_schedule_button_state", schedule_src)
        self.assertIn("def _schedule_runtime_start_disabled_reason", schedule_src)
        self.assertIn("def _schedule_runtime_stop_disabled_reason", schedule_src)
        self.assertIn("def _schedule_report_disabled_reason", schedule_src)
        self.assertIn("def _schedule_tasks_dir_disabled_reason", schedule_src)
        self.assertIn("def _schedule_log_disabled_reason", schedule_src)
        self.assertIn("下载并打开报告", schedule_src)
        self.assertIn("同步并打开目录", schedule_src)
        self.assertIn("下载并打开完整日志", schedule_src)
        self.assertIn("远端调度器已在运行；无需重复启动。", schedule_src)
        self.assertIn("当前是外部启动的调度器进程，启动器无法直接停止。", schedule_src)
        self.assertIn("当前远端任务还没有可同步的报告文件。", schedule_src)
        self.assertIn("当前远端{label}路径不可用，暂时无法下载。", schedule_src)
        self.assertIn("def _usage_export_current_report", personal_src)
        self.assertIn("导出当前摘要", personal_src)
        self.assertIn("打开会话缓存", personal_src)
        self.assertIn('export_btn.setToolTip("把当前设备的使用摘要导出到本地文件。")', personal_src)
        self.assertIn('cache_btn.setToolTip("打开当前启动器保存的会话缓存目录。")', personal_src)
        self.assertIn("正在同步 {target['label']} 的会话缓存；完成后会自动刷新，随后可继续调整会话上限。", personal_src)
        self.assertIn("已同步 {target['label']} 的远端会话缓存；当前页面已刷新，可继续调整会话上限。", personal_src)
        self.assertIn("正在同步 {target['label']} 的远端使用日志、会话与渠道快照；完成后会自动刷新，可能需要数秒。", personal_src)
        self.assertIn("已同步 {target['label']} 的远端使用日志、会话与渠道快照；当前页面已刷新。", personal_src)
        self.assertIn("def _remote_channel_status_check_age", channel_src)
        self.assertIn("def _remote_channel_last_checked_at", channel_src)
        self.assertIn("def _remote_channel_device_sync_state", channel_src)
        self.assertIn("def _remote_channel_check_hint", channel_src)
        self.assertIn("def _show_remote_channel_status_detail", channel_src)
        self.assertIn("def _request_remote_channel_status_refresh", channel_src)
        self.assertIn("def _remote_channel_label_text", channel_src)
        self.assertIn("def _remote_channel_log_loaded_status", channel_src)
        self.assertIn("def _channel_start_disabled_reason", channel_src)
        self.assertIn("def _channel_stop_disabled_reason", channel_src)
        self.assertIn("def _channel_bind_disabled_reason", channel_src)
        self.assertIn("def _channel_remote_aux_disabled_reason", channel_src)
        self.assertIn("def _apply_channel_button_state", channel_src)
        self.assertIn("正在校验远端状态", channel_src)
        self.assertIn("最近校验：", channel_src)
        self.assertIn("服务器连接异常", channel_src)
        self.assertIn("校验详情", channel_src)
        self.assertIn("远端微信未绑定，已转入远端扫码绑定；完成后会继续尝试启动。", channel_src)
        self.assertIn("已启动远端", channel_src)
        self.assertIn("如无新消息可再查看远端日志", channel_src)
        self.assertIn("当前会继续复用现有进程", channel_src)
        self.assertIn("远端停止失败，进程可能仍在运行。", channel_src)
        self.assertIn("当前未运行；无需重复停止。", channel_src)
        self.assertIn("可直接查看末尾输出继续排查。", channel_src)
        self.assertIn("当前还没有新的日志输出。", channel_src)
        self.assertIn("请检查 SSH 连接后重试：", channel_src)
        self.assertIn("检测到外部", channel_src)
        self.assertIn("启动器无法直接停止", channel_src)
        self.assertIn("当前远端设备信息不可用", channel_src)
        self.assertIn("无法读取", channel_src)
        self.assertIn("def _api_source_disabled_reason", api_src)
        self.assertIn("def _apply_api_button_state", api_src)
        self.assertIn("请先用“直接编辑文件”处理原文", api_src)
        self.assertIn("请先选择有效的 GenericAgent 目录。", api_src)
        self.assertIn("正在读取当前目标的 mykey.py，请稍候。", api_src)
        self.assertIn("服务器侧重启对应进程", api_src)
        self.assertIn("直接编辑当前目标的 mykey.py 原文。", api_src)
        self.assertIn("device_checked_map[did] = now", sidebar_src)
        self.assertIn("checked_map[(did, cid)] = now", sidebar_src)
        self.assertIn("sync_meta[\"fail_count\"] = int(sync_meta.get(\"fail_count\") or 0) + 1", sidebar_src)
        self.assertIn("raw_err = str(err or \"远端状态读取失败。\").strip() or \"远端状态读取失败。\"", sidebar_src)
        self.assertIn("sync_meta[\"last_error\"] = normalize_ssh_error_text(raw_err, context=\"SSH 连接\")", sidebar_src)
        self.assertIn("远端会话写回失败，当前内容仍保留在本地缓存：", sidebar_src)
        self.assertIn("远端同步失败，当前仍使用本地缓存：", sidebar_src)
        self.assertIn("可稍后重试同步或检查 SSH。", sidebar_src)
        self.assertIn("可稍后重试或先检查 SSH。", sidebar_src)
        self.assertIn("def _vps_decode_candidates", settings_src)
        self.assertIn("def _decode_vps_terminal_chunk", settings_src)
        self.assertIn("gb18030", settings_src)
        self.assertNotIn("export LANG=C.UTF-8 LC_ALL=C.UTF-8", settings_src)
        self.assertIn('self.settings_vps_dep_install_mode_combo.addItem("内置源（推荐，清华）", "offline")', settings_src)
        self.assertIn('self.settings_vps_dep_install_mode_combo.addItem("国际源（PyPI）", "global")', settings_src)
        self.assertIn('self.settings_vps_dep_install_mode_combo.addItem("自定义源", "mirror")', settings_src)
        self.assertIn('"global": "国际源（PyPI）"', settings_src)
        self.assertIn("if legacy and not merged:", settings_src)
        self.assertIn("normalize_remote_agent_dir", sidebar_src)
        self.assertIn("normalize_remote_agent_dir", channel_src)
        self.assertIn("normalize_remote_agent_dir", bridge_src)
        self.assertIn('"probe_url": "https://pypi.tuna.tsinghua.edu.cn/simple"', settings_src)
        self.assertIn('"probe_url": "https://pypi.org/simple"', settings_src)
        self.assertIn('self.settings_vps_deploy_btn = QPushButton("直接部署")', settings_src)
        self.assertIn("self.settings_vps_deploy_btn.clicked.connect(self._deploy_vps_agent_direct)", settings_src)
        self.assertIn("上游未提供 requirements.txt；当前改用启动器维护的上游依赖表。", settings_src)
        self.assertIn("服务器上的 Python 缺少 pip。", settings_src)
        self.assertIn("from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPalette, QPen, QPixmap, QTextCursor", settings_src)
        self.assertIn("palette.setColor(QPalette.PlaceholderText, QColor(\"#94a3b8\"))", settings_src)
        self.assertIn("viewport.setStyleSheet(\"background: #0f172a; color: #e2e8f0;\")", settings_src)
        self.assertIn("self.settings_vps_terminal_meta.setMinimumHeight(40)", settings_src)
        self.assertIn('bg = "rgba(34,197,94,0.14)"', settings_src)
        self.assertIn('label.setStyleSheet(', settings_src)
        self.assertIn("_VPS_DUPLICATED_PROMPT_RE = re.compile(", settings_src)
        self.assertIn("_VPS_PROMPT_TOKEN_RE = re.compile(", settings_src)
        self.assertIn("_SSH_DISCONNECT_HINTS = (", settings_src)
        self.assertIn("def _looks_like_ssh_disconnect", settings_src)
        self.assertIn("def _friendly_ssh_disconnect_reason", settings_src)
        self.assertIn('msg = _VPS_DUPLICATED_PROMPT_RE.sub(', settings_src)
        self.assertIn('not (127 <= ord(ch) <= 159)', settings_src)
        self.assertIn("class _VpsTerminalCommandEdit(QLineEdit):", settings_src)
        self.assertIn('self.settings_vps_terminal_input.returnPressed.connect(self._send_vps_terminal_command)', settings_src)
        self.assertIn('terminal_prompt = QLabel(">")', settings_src)
        self.assertIn('self.settings_vps_terminal_input.setPlaceholderText("输入命令后回车执行，↑/↓ 取历史命令")', settings_src)
        self.assertIn('self.settings_vps_terminal_send_btn = QPushButton("执行")', settings_src)
        self.assertIn('self.settings_vps_profile_light = QLabel()', settings_src)
        self.assertIn('chat_common.set_label_svg_icon(self.settings_vps_profile_light, "settings_vps_status", chat_common._SVG_DOT, color="#94a3b8", size=12)', settings_src)
        self.assertIn("def _vps_profile_health", settings_src)
        self.assertIn("self.settings_vps_terminal_output = QPlainTextEdit()", settings_src)
        self.assertIn("self.settings_vps_terminal_output.setMaximumBlockCount(4000)", settings_src)
        self.assertIn('self._append_vps_terminal_dependency_output("安装 SSH 依赖任务开始", banner=True)', settings_src)
        self.assertIn('heartbeat = f"安装中，已运行 {elapsed} 秒：{progress_state[\'last_msg\'] or \'正在执行依赖安装命令…\'}"', settings_src)
        self.assertIn('f"已连接到 {profile_name}，正在等待远端 shell 输出…"', settings_src)
        self.assertIn('f"已连接到 {target_name}。远端当前没有输出，可直接输入命令。"', settings_src)
        self.assertIn('state_text = f"终端已连接：{connected_name or target_text}。可直接输入命令。"', settings_src)
        self.assertIn("def _schedule_vps_terminal_prompt_refresh", settings_src)
        self.assertIn('channel.send("\\n")', settings_src)
        self.assertIn('self._vps_terminal_bootstrap_marker = ""', settings_src)
        self.assertIn("self._vps_terminal_bootstrap_done = True", settings_src)
        self.assertIn("self._schedule_vps_terminal_prompt_refresh(delay_ms=360)", settings_src)
        self.assertIn("cursor.movePosition(QTextCursor.End)", settings_src)
        self.assertIn('box.setPlaceholderText("")', settings_src)
        self.assertIn("transport.set_keepalive(20)", settings_src)
        self.assertIn('event_queue.put({"event": "disconnect", "text": reason})', settings_src)
        self.assertIn('self._disconnect_vps_terminal(reason=disconnect_reason)', settings_src)
        self.assertIn("def _append_vps_terminal_deploy_output", settings_src)
        self.assertIn('line = f"================ {prefix}{raw} ================\\n"', settings_src)
        self.assertIn('self._append_vps_terminal_deploy_output(f"部署任务开始：{target_name}", banner=True)', settings_src)
        self.assertIn("self._append_vps_terminal_deploy_output(msg, banner=True)", settings_src)
        self.assertNotIn("clear\\n", settings_src)
        self.assertNotIn('self._append_vps_terminal_output("$ [命令已发送]")', settings_src)
        self.assertIn("def _vps_profile_combo_label", settings_src)
        self.assertIn("连通通过", settings_src)
        self.assertIn("部署失败", settings_src)
        self.assertIn("def _update_vps_profile_runtime_summary", settings_src)
        self.assertIn("最近连接成功", settings_src)
        self.assertIn("最近部署成功", settings_src)
        self.assertIn("QTimer.singleShot(0, app, run)", api_src)
        self.assertIn("Internal C++ object", api_src)
        self.assertIn("QTimer.singleShot(0, app, callback)", sidebar_src)

    def test_window_has_server_connection_indicator_light(self):
        root = os.path.dirname(os.path.dirname(__file__))
        shell_path = os.path.join(root, "qt_chat_parts", "window_shell.py")
        window_path = os.path.join(root, "launcher_app", "window.py")
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(shell_path, "r", encoding="utf-8") as f:
            shell_src = f.read()
        with open(window_path, "r", encoding="utf-8") as f:
            window_src = f.read()
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn("def _request_server_connection_probe", shell_src)
        self.assertIn("def _refresh_server_status_indicator", shell_src)
        self.assertIn("server_status_btn", window_src)
        self.assertIn("startup_channel_starter = getattr(self, \"_schedule_local_channel_autostart\", None)", window_src)
        self.assertIn("startup_scheduler_starter = getattr(self, \"_start_autostart_scheduler\", None)", window_src)
        self.assertIn("app.installEventFilter(self)", window_src)
        self.assertIn("def _begin_window_trace", window_src)
        self.assertIn("def _append_window_trace_log", window_src)
        self.assertIn("focus_combo=", window_src)
        self.assertIn("watched_combo=", window_src)
        self.assertIn("suppressed_blank_dialog", window_src)
        self.assertIn("self._server_status_timer = QTimer(self)", window_src)
        self.assertIn("head_layout.addWidget(self.server_status_btn", window_src)
        self.assertIn("probe(force=True)", settings_src)

    def test_session_shell_composer_actions_have_disabled_reason_helpers(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "session_shell.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _apply_composer_widget_state", src)
        self.assertIn("def _composer_send_disabled_reason", src)
        self.assertIn("def _composer_stop_disabled_reason", src)
        self.assertIn("def _composer_llm_disabled_reason", src)
        self.assertIn("渠道进程会话仅用于回顾日志与快照，不能在这里继续发送消息。", src)
        self.assertIn("当前正在等待模型回复，请稍候或先停止当前任务。", src)
        self.assertIn("当前会话在远程设备执行，这里不支持直接停止远端任务。", src)
        self.assertIn("当前没有正在执行的本地回复任务。", src)
        self.assertIn("当前还没有可用的 LLM 配置。", src)

    def test_channel_runtime_uses_channel_specific_dependency_check(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _channel_extra_packages", src)
        self.assertIn("lz._split_requirement_tokens(spec.get(\"pip\", \"\"))", src)
        self.assertIn("extra_packages=extra_packages", src)
        self.assertIn("visual=bool(show_errors)", src)

    def test_channel_runtime_remote_wechat_qr_login_tracks_login_identity(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("login_id = f'{int(issued_at * 1000)}-{uuid.uuid4().hex}'", src)
        self.assertIn("'launcher_login_id': login_id", src)
        self.assertIn("'launcher_login_issued_at': issued_at", src)
        self.assertIn("token_login_id == login_id", src)
        self.assertIn("\"remote_login_id\": str(payload.get(\"login_id\") or \"\").strip() if remote_dev else \"\"", src)
        self.assertIn("import json, os, site, subprocess, sys, time, uuid", src)
        self.assertIn("user_site = str(site.getusersitepackages() or '').strip()", src)
        self.assertIn("proc_env['PYTHONPATH'] = user_site if not prev_path else (user_site + os.pathsep + prev_path)", src)
        self.assertIn("proc_env['PYTHONUSERBASE'] = user_base", src)
        self.assertIn("def extract_fields(data):", src)
        self.assertIn("for key in ('data', 'result', 'payload'):", src)
        self.assertIn("except requests.exceptions.ReadTimeout:", src)

    def test_channel_runtime_local_wechat_health_watch_detects_session_timeout(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _clear_wx_token_info", src)
        self.assertIn("def _probe_local_wechat_token", src)
        self.assertIn("def _local_begin_wechat_qr_login", src)
        self.assertIn("def _local_wechat_qr_state", src)
        self.assertIn("def _wx_qr_state_path", src)
        self.assertIn("def _wx_qr_debug_log_path", src)
        self.assertIn("def _append_wx_qr_debug_log", src)
        self.assertIn('"qr_state.json"', src)
        self.assertIn('"qr_login_debug.log"', src)
        self.assertIn('self._write_wx_qr_state_file(state)', src)
        self.assertIn('self._append_wx_qr_debug_log(', src)
        self.assertIn("threading.Thread(target=worker, daemon=True, name=f\"wechat-local-qr-", src)
        self.assertIn('f"{lz.WX_BOT_API}/ilink/bot/getupdates"', src)
        self.assertIn('if errcode == -14:', src)
        self.assertIn("def _wechat_session_timeout_log_hit", src)
        self.assertIn('"[getupdates] err: -14" in lowered', src)
        self.assertIn('"session timeout" in lowered', src)
        self.assertIn("def _start_wechat_health_watch", src)
        self.assertIn('self._start_wechat_health_watch(show_errors=show_errors)', src)
        self.assertIn('token_login_id == str(result.get("local_login_id") or "").strip()', src)
        self.assertIn('token_payload["launcher_login_id"] = str(result.get("local_login_id") or "").strip()', src)
        self.assertIn("def _extract_wechat_token_fields", src)
        self.assertIn("for key in (\"data\", \"result\", \"payload\")", src)
        self.assertIn("def post_dialog_ui", src)
        self.assertIn('self._channel_post_ui(run, action_name=action_name)', src)
        self.assertIn("扫码已确认但接口未返回 bot_token。", src)
        self.assertIn('self._probe_local_wechat_token(token_info, timeout=6)', src)
        self.assertIn('ok, payload, err = self._local_begin_wechat_qr_login(timeout=45)', src)
        self.assertIn('ok, payload, err = self._local_wechat_qr_state(result.get("local_login_id"))', src)
        self.assertIn("def _start_channel_process_autostart", src)
        self.assertIn('allow_interactive=False', src)
        self.assertIn('skip_wechat_token_probe=True', src)
        self.assertIn('skip_dependency_check=True', src)
        self.assertIn('force_local=True', src)
        self.assertIn('self._channel_post_ui(run_next, action_name="启动自动启动队列")', src)
        self.assertIn('return "待扫码绑定", C["danger_text"]', src)
        self.assertIn('return "正在自动启动", C["text_soft"]', src)
        self.assertIn('return "等待自动启动", C["text_soft"]', src)
        self.assertIn("本次会转入重新扫码", src)
        self.assertIn("如需启动请先手动扫码绑定", src)
        self.assertIn("本次启动可能仍会失败", src)
        self.assertIn("如仍异常请重新扫码", src)
        self.assertIn("self._autostart_channel_pending_ids = set(pending)", src)
        self.assertIn('self._autostart_channel_current = str(channel_id or "").strip()', src)
        self.assertIn('defocus(fallback=getattr(self, "settings_channels_list", None))', src)
        self.assertIn('trace("channels_remote_load", duration_ms=3200, suppress_blank_dialogs=True)', src)
        self.assertIn('trace("channels_remote_sync", duration_ms=3200, suppress_blank_dialogs=True)', src)

    def test_bridge_runtime_remote_chat_reuses_bridge_protocol_and_state(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _remote_bridge_source_text", src)
        self.assertIn("def _remote_parse_bridge_event_text", src)
        self.assertIn("self._remote_stage_bridge_runtime(client, remote_dir)", src)
        self.assertIn('"cmd": "set_state"', src)
        self.assertIn('"cmd": "send"', src)
        self.assertIn('"event": "remote_next"', src)
        self.assertIn('"event": "remote_turn_snapshot"', src)
        self.assertIn('f"\\\"$PY_BIN\\\" -u {shlex.quote(remote_bridge)} {shlex.quote(remote_dir)}"', src)

    def test_dependency_runtime_supports_visual_and_silent_modes(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "dependency_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("visual=True", src)
        self.assertIn("if not visual:", src)
        self.assertIn("_dependency_check_desc_text", src)
        self.assertIn("_apply_dependency_check_result", src)
        self.assertIn("依赖检查报告", src)
        self.assertIn("优先 uv，失败回退 pip", src)

    def test_navigation_defers_chat_runtime_bootstrap(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "navigation.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _defer_chat_runtime_bootstrap", src)
        self.assertIn("def _can_skip_dependency_check_on_quick_enter", src)
        self.assertIn("def _schedule_local_channel_autostart", src)
        self.assertIn("self._local_channel_autostart_scheduled = True", src)
        self.assertIn("self._start_autostart_channels()", src)
        self.assertIn("self._schedule_local_channel_autostart()", src)
        self.assertIn("skip_dependency_check=self._can_skip_dependency_check_on_quick_enter()", src)
        self.assertIn("QTimer.singleShot(160, self, run)", src)
        self.assertIn("self._defer_chat_runtime_bootstrap()", src)

    def test_python_env_supports_uv_installer_with_pip_fallback(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _dependency_installer_mode", src)
        self.assertIn("def _detect_uv_command", src)
        self.assertIn("def _run_dependency_install", src)
        self.assertIn('"pip", "install", "--python"', src)
        self.assertIn("GA_LAUNCHER_DEP_INSTALLER", src)
        self.assertIn("GA_LAUNCHER_UV_EXE", src)

    def test_python_env_report_builder_contains_summary_and_sections(self):
        report = python_env._build_dependency_report(
            agent_dir="C:\\nonexistent-agent-dir",
            py="",
            candidate_meta={},
            failures=[],
            extra_packages=["telegram"],
            error="demo error",
        )
        self.assertIn("summary", report)
        self.assertIn("sections", report)
        self.assertIn("检查项", report["text"])
        self.assertTrue(any(sec.get("title") == "最终错误" for sec in report["sections"]))
        self.assertTrue(any(sec.get("title") == "项目文件" for sec in report["sections"]))
        self.assertTrue(any(sec.get("title") == "上游依赖来源" for sec in report["sections"]))
        self.assertTrue(any(sec.get("title") == "已配置渠道" for sec in report["sections"]))
        self.assertTrue(any(sec.get("title") == "渠道专属可选" for sec in report["sections"]))

    def test_channel_report_uses_info_for_unconfigured_missing_dependencies(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('status = "ok" if ok else ("error" if configured else "info")', src)

    def test_upstream_dependency_table_file_exists_and_is_wired_into_report(self):
        root = os.path.dirname(os.path.dirname(__file__))
        dep_path = os.path.join(root, "launcher_core_parts", "upstream_dependencies.py")
        pyenv_path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(dep_path, "r", encoding="utf-8") as f:
            dep_src = f.read()
        with open(pyenv_path, "r", encoding="utf-8") as f:
            pyenv_src = f.read()
        self.assertIn("LAUNCHER_BOOTSTRAP_DEPENDENCIES", dep_src)
        self.assertIn("UPSTREAM_DEPENDENCY_SOURCES", dep_src)
        self.assertIn("UPSTREAM_FRONTEND_DEPENDENCY_GROUPS", dep_src)
        self.assertIn("pywebview", dep_src)
        self.assertIn("resolve_upstream_dependency_manifest", dep_src)
        self.assertIn("上游未提供 requirements.txt；当前改用启动器维护的上游依赖表", pyenv_src)
        self.assertIn("上游未提供 requirements.txt；当前优先改用 pyproject.toml 依赖声明", pyenv_src)
        self.assertIn("上游依赖来源", pyenv_src)

    def test_upstream_dependency_groups_use_user_facing_categories(self):
        root = os.path.dirname(os.path.dirname(__file__))
        dep_path = os.path.join(root, "launcher_core_parts", "upstream_dependencies.py")
        with open(dep_path, "r", encoding="utf-8") as f:
            dep_src = f.read()
        self.assertIn("主聊天必需", dep_src)
        self.assertIn("上游默认 GUI 可选", dep_src)
        self.assertIn("Qt 前端可选", dep_src)
        self.assertIn("Streamlit 前端可选", dep_src)

    def test_upstream_dependency_manifest_prefers_pyproject_and_expands_remote_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "pyproject.toml"), "w", encoding="utf-8") as f:
                f.write(
                    "[project]\n"
                    "name = 'genericagent'\n"
                    "version = '0.1.0'\n"
                    "requires-python = '>=3.10,<3.14'\n"
                    "dependencies = [\n"
                    "  'requests>=2.28',\n"
                    "  'beautifulsoup4>=4.12',\n"
                    "  'bottle>=0.12',\n"
                    "]\n"
                    "[project.optional-dependencies]\n"
                    "ui = ['streamlit>=1.28', 'pywebview>=4.0', 'textual>=0.70', 'prompt_toolkit>=3.0,<4', 'rich>=13.0', 'pillow>=9.0']\n"
                    "all-frontends = ['pycryptodome>=3.19', 'qrcode>=7.4']\n"
                )
            manifest = upstream_dependencies.resolve_upstream_dependency_manifest(td)
        self.assertTrue(manifest["pyproject_used"])
        self.assertEqual(manifest["requires_python"], ">=3.10,<3.14")
        self.assertEqual(manifest["sync_mode"], "pyproject")
        self.assertIn("beautifulsoup4>=4.12", manifest["sync_specs"])
        self.assertIn("charset-normalizer>=3.3", manifest["sync_specs"])
        self.assertIn("streamlit>=1.28", manifest["remote_fallback_specs"])
        self.assertIn("prompt_toolkit>=3.0,<4", manifest["remote_fallback_specs"])
        self.assertIn("rich>=13.0", manifest["remote_fallback_specs"])
        self.assertIn("pillow>=9.0", manifest["remote_fallback_specs"])
        self.assertIn("qrcode>=7.4", manifest["remote_fallback_specs"])
        groups = {str(group.get("id") or ""): group for group in manifest["frontend_groups"]}
        launch_items = [str(item.get("package") or "") for item in groups["launch_web_ui"]["items"]]
        self.assertTrue(any(item.startswith("pywebview") for item in launch_items))
        self.assertTrue(any(item.startswith("streamlit") for item in launch_items))
        conductor_items = [str(item.get("package") or "") for item in groups["conductor_frontend"]["items"]]
        self.assertIn("fastapi", conductor_items)
        self.assertTrue(any(item.startswith("uvicorn") for item in conductor_items))
        self.assertIn("pydantic", conductor_items)
        self.assertTrue(any(str(item.get("source") or "") == "frontends/conductor.py" for item in manifest["dependency_sources"]))
        self.assertTrue(any(str(item.get("source") or "") == "frontends/tui_v3.py" for item in manifest["dependency_sources"]))
        self.assertTrue(any(str(item.get("source") or "") == "frontends/slash_cmds.py" for item in manifest["dependency_sources"]))
        self.assertTrue(any(str(item.get("source") or "") == "ga_cli/cli.py" for item in manifest["dependency_sources"]))

    def test_prepare_python_runtime_candidate_rejects_version_outside_upstream_requires_python(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "pyproject.toml"), "w", encoding="utf-8") as f:
                f.write(
                    "[project]\n"
                    "name = 'genericagent'\n"
                    "version = '0.1.0'\n"
                    "requires-python = '>=3.10,<3.14'\n"
                    "dependencies = ['requests>=2.28']\n"
                )
            info = {"path": "C:\\Python314\\python.exe", "version": "3.14.0"}
            with mock.patch.object(python_env, "_dependency_state_matches", return_value=True), mock.patch.object(
                python_env, "_core_runtime_packages_ready", return_value=True
            ), mock.patch.object(
                python_env, "_core_runtime_packages_import_ready", return_value=True
            ), mock.patch.object(
                python_env, "_probe_python_agent_compat", return_value=(True, "")
            ), mock.patch.object(
                python_env, "_missing_dependency_specs", return_value=[]
            ), mock.patch.object(
                python_env, "_should_sync_runtime_dependencies", return_value=False
            ):
                ok, detail, meta = python_env._prepare_python_runtime_candidate(info, td)
        self.assertFalse(ok)
        self.assertIn(">=3.10,<3.14", detail)
        self.assertIn("3.11 / 3.12", detail)
        self.assertEqual(meta.get("requires_python"), ">=3.10,<3.14")

    def test_probe_python_agent_compat_checks_bridge_boot_path_and_tolerates_missing_llm(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["args"] = list(args)
            seen["kwargs"] = dict(kwargs)
            return types.SimpleNamespace(returncode=0, stdout="NO_LLM_OK\n", stderr="")

        with mock.patch.object(python_env, "_run_external_subprocess", side_effect=fake_run):
            ok, detail = python_env._probe_python_agent_compat("/usr/bin/python3", "/tmp/GenericAgent")

        self.assertTrue(ok)
        self.assertEqual(detail, "")
        self.assertEqual(seen["args"][0], "/usr/bin/python3")
        self.assertEqual(seen["args"][1], "-c")
        self.assertIn("import requests", seen["args"][2])
        self.assertIn("agentmain.GeneraticAgent()", seen["args"][2])
        self.assertIn("except IndexError:", seen["args"][2])
        self.assertEqual(seen["args"][3], "/tmp/GenericAgent")

    def test_channel_registry_includes_terminal_tui_channel(self):
        spec = lz.COMM_CHANNEL_INDEX.get("tui") or {}
        self.assertEqual(spec.get("script"), "tui_v3.py")
        self.assertEqual(list(spec.get("script_candidates") or []), ["tui_v3.py", "tuiapp_v2.py", "tuiapp.py"])
        self.assertEqual(spec.get("pip"), "prompt_toolkit rich Pillow textual")
        self.assertEqual(spec.get("launch_mode"), "terminal")
        self.assertEqual(spec.get("fields"), [])
        self.assertEqual(spec.get("required"), [])
        self.assertEqual(lz.COMM_CHANNEL_INDEX.get("discord", {}).get("launch_mode"), None)

    def test_channel_registry_includes_local_only_conductor_web_channel(self):
        spec = lz.COMM_CHANNEL_INDEX.get("conductor") or {}
        self.assertEqual(spec.get("script"), "conductor.py")
        self.assertEqual(spec.get("launch_mode"), "web")
        self.assertTrue(spec.get("local_only"))
        self.assertEqual(spec.get("web_url"), "http://127.0.0.1:8900/")
        self.assertIn("fastapi", str(spec.get("pip") or ""))
        self.assertEqual(spec.get("fields"), [])
        self.assertEqual(spec.get("required"), [])

    def test_channel_script_resolution_prefers_existing_candidate_and_keeps_rel_paths(self):
        with tempfile.TemporaryDirectory() as td:
            frontends = os.path.join(td, "frontends")
            os.makedirs(frontends, exist_ok=True)
            legacy = os.path.join(frontends, "tuiapp.py")
            with open(legacy, "w", encoding="utf-8") as f:
                f.write("print('legacy')\n")

            self.assertEqual(lz.resolve_channel_script("tui"), "tui_v3.py")
            self.assertEqual(lz.resolve_channel_script("tui", agent_dir=td, existing_only=True), "tuiapp.py")
            self.assertEqual(lz.channel_script_rel("tui"), "frontends/tui_v3.py")
            self.assertEqual(lz.channel_script_rel("tui", agent_dir=td, existing_only=True), "frontends/tuiapp.py")
            self.assertEqual(
                lz.channel_script_rel_candidates("tui"),
                ["frontends/tui_v3.py", "frontends/tuiapp_v2.py", "frontends/tuiapp.py"],
            )
            self.assertTrue(str(lz.channel_script_path(td, "tui", existing_only=True)).endswith(os.path.join("frontends", "tuiapp.py")))

    def test_conductor_channel_uses_launcher_owned_runtime_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = os.path.join(td, "runtime", "conductor")
            with mock.patch.object(conductor_runtime_mod, "launcher_data_path", return_value=runtime_root), mock.patch.object(
                channels_mod, "_conductor_runtime", conductor_runtime_mod
            ):
                paths = conductor_runtime_mod.ensure_launcher_conductor_runtime()
                script_path = lz.channel_script_path("C:\\demo", "conductor", existing_only=True)

            self.assertTrue(os.path.isfile(paths["script"]))
            self.assertTrue(os.path.isfile(paths["html"]))
            self.assertEqual(script_path, paths["script"])
            self.assertEqual(lz.channel_script_rel("conductor"), "frontends/conductor.py")
            self.assertEqual(lz.channel_script_rel_candidates("conductor"), ["frontends/conductor.py"])
            with open(paths["script"], "r", encoding="utf-8") as f:
                script_src = f.read()
            self.assertIn("GA_LAUNCHER_AGENT_DIR", script_src)
            self.assertIn("mark_all_user_messages_read()", script_src)
            with open(paths["html"], "r", encoding="utf-8") as f:
                html_src = f.read()
            self.assertIn("Conductor", html_src)

    def test_dependency_reporting_no_longer_duplicates_tui_as_frontend_category(self):
        root = os.path.dirname(os.path.dirname(__file__))
        dep_path = os.path.join(root, "launcher_core_parts", "upstream_dependencies.py")
        py_env_path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(dep_path, "r", encoding="utf-8") as f:
            dep_src = f.read()
        with open(py_env_path, "r", encoding="utf-8") as f:
            py_env_src = f.read()
        self.assertNotIn("terminal_frontend", dep_src)
        self.assertIn("frontends/tui_v3.py", dep_src)
        self.assertIn("frontends/tuiapp_v2.py", dep_src)
        self.assertIn("frontends/slash_cmds.py", dep_src)
        self.assertIn("desktop_bridge.py", dep_src)
        self.assertIn("genericagent_acp_bridge.py", py_env_src)
        self.assertIn("tui_v3.py", py_env_src)

        ok_result = mock.Mock(returncode=0, stdout="demo\n", stderr="")
        with mock.patch.object(
            python_env, "_probe_command_version", return_value={"status": "ok", "name": "mock", "detail": "ok"}
        ), mock.patch.object(
            python_env, "_run_python_command", return_value=ok_result
        ), mock.patch.object(
            python_env, "_probe_python_dependency", return_value=(True, "ok", {})
        ), mock.patch.object(
            python_env, "_probe_python_compile", return_value=(True, "语法检查通过")
        ), mock.patch.object(
            python_env, "_probe_python_import", return_value=(True, "可导入")
        ):
            report = python_env._build_dependency_report(
                agent_dir="C:\\demo",
                py=sys.executable,
                candidate_meta={},
                failures=[],
                extra_packages=[],
                error="",
            )

        titles = [str(section.get("title") or "") for section in report.get("sections") or []]
        self.assertNotIn("终端前端可选", titles)
        frontend_section = next((section for section in report.get("sections") or [] if section.get("title") == "前端脚本"), {})
        frontend_names = [str(item.get("name") or "") for item in (frontend_section.get("items") or [])]
        self.assertIn("tuiapp_v2.py", frontend_names)
        self.assertIn("tui_v3.py", frontend_names)
        self.assertIn("desktop_bridge.py", frontend_names)
        self.assertIn("genericagent_acp_bridge.py", frontend_names)
        optional_section = next((section for section in report.get("sections") or [] if section.get("title") == "渠道专属可选"), {})
        items = list(optional_section.get("items") or [])
        self.assertTrue(any(str(item.get("name") or "") == "终端 TUI 依赖 prompt_toolkit" for item in items))
        self.assertTrue(any(str(item.get("name") or "") == "终端 TUI 依赖 rich" for item in items))
        self.assertTrue(any(str(item.get("name") or "") == "终端 TUI 依赖 Pillow" for item in items))
        self.assertTrue(any(str(item.get("name") or "") == "终端 TUI 依赖 textual" for item in items))

    def test_private_python_installer_has_source_precheck_logs(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("正在预检下载源可用性", src)
        self.assertIn("下载源预检通过", src)
        self.assertIn("下载源预检失败", src)
        self.assertIn("目录已存在但不完整", src)
        self.assertIn("是否删除这个目录后重新下载", src)
        self.assertIn("已清理残留目录，准备重新下载", src)

    def test_setup_page_has_download_source_checkboxes(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Python 安装包下载源（可多选", src)
        self.assertIn("download_source_checkboxes", src)
        self.assertIn("_on_private_python_source_toggled", src)

    def test_download_flow_has_macos_project_venv_mode(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("PLATFORM_SUPPORTS_PRIVATE_PYTHON_INSTALLER", src)
        self.assertIn("构建项目虚拟环境", src)
        self.assertIn("_select_project_venv_seed_python", src)
        self.assertIn("Homebrew Python 3.11 / 3.12", src)
        self.assertIn("不会写入系统 Python", src)

    def test_setup_page_mentions_macos_project_venv_mode(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("构建项目虚拟环境", src)
        self.assertIn("不会污染系统 Python", src)
        self.assertIn("seed Python", src)
        self.assertNotIn("python / python3 / 常见 Homebrew 绝对路径", src)

    def test_setup_page_uses_project_venv_copy_on_macos(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("git clone 和项目虚拟环境构建都会在这里实时输出", src)
        self.assertIn("只会借用现有 Python 执行 -m venv", src)
        self.assertIn("python3 / python / 常见 Homebrew 绝对路径", src)
        self.assertIn("留空时会自动尝试 python / python3；mac 下也会补试常见 Homebrew 绝对路径。", src)
        self.assertIn("如果你已有项目虚拟环境，也可以手动填写 venv/bin/python。", src)

    def test_setup_page_uses_platform_neutral_python_wording(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("可填 python / python3 / venv/bin/python", src)
        self.assertIn("这里建议选择具体的 Python 可执行文件", src)
        self.assertIn("而不是 uv 本身", src)
        self.assertNotIn("这里建议选择具体的 python.exe", src)
        self.assertNotIn("uv.exe 本身", src)

    def test_python_env_uv_error_message_is_platform_neutral(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_core_parts", "python_env.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("GA_LAUNCHER_UV_EXE 指向 uv 可执行文件", src)
        self.assertNotIn("GA_LAUNCHER_UV_EXE 指向 uv.exe", src)
        self.assertIn("未找到 python3 / python", src)
        self.assertIn("/opt/homebrew/bin/python3", src)
        self.assertIn("/usr/local/bin/python3", src)
        self.assertIn("python_exe 指向对应绝对路径，例如 /opt/homebrew/bin/python3 或 venv/bin/python", src)
        self.assertIn("系统 Python 兼容性失败", src)
        self.assertIn("如果上面的错误里包含 pip / uv / requirements", src)

    def test_download_flow_uses_platform_neutral_git_guidance(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("未检测到 Git。请先安装 Git：", src)
        self.assertIn("https://git-scm.com/downloads", src)
        self.assertNotIn("Git for Windows", src)

    def test_download_flow_has_system_python_completion_guidance(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("请使用系统 Python 进入聊天；首次载入时会自动执行依赖检查。", src)

    def test_about_panel_has_manual_macos_update_mode(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("查看手动升级说明", src)
        self.assertIn("mac 版当前不支持应用内自动更新", src)
        self.assertIn("PLATFORM_SUPPORTS_INTERNAL_UPDATER", src)

    def test_about_panel_has_macos_install_status_card(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "personal_usage.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("安装状态", src)
        self.assertIn("刷新安装状态", src)
        self.assertIn("打开推荐安装目录", src)
        self.assertIn("打开当前 App 位置", src)
        self.assertIn("打开用户数据目录", src)
        self.assertIn("macos_installation_status", src)
        self.assertIn("~/Applications", src)
        self.assertIn("_display_local_user_path", src)
        self.assertIn("_launcher_install_recommended_directory", src)

    def test_window_startup_schedules_macos_install_hint(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('startup_install_hinter = getattr(self, "_schedule_startup_install_hint", None)', src)
        self.assertIn("startup_install_hinter()", src)

    def test_window_supports_smoke_exit_env(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("GA_LAUNCHER_SMOKE_EXIT_MS", src)
        self.assertIn("QTimer.singleShot", src)

    def test_launcher_packaged_import_smoke_requires_frozen_runtime(self):
        import launcher

        with mock.patch.dict(os.environ, {"GA_LAUNCHER_PACKAGED_IMPORT_SMOKE": "1"}, clear=False):
            with mock.patch.object(launcher.sys, "frozen", False, create=True):
                with self.assertRaises(RuntimeError) as ctx:
                    launcher._maybe_run_packaged_import_smoke()
        self.assertIn("requires a packaged launcher runtime", str(ctx.exception))

    def test_launcher_packaged_import_smoke_reports_key_runtime_dependencies(self):
        import launcher

        stub_simplejson = types.SimpleNamespace(__file__=os.path.join(os.getcwd(), "simplejson-stub.py"))
        with mock.patch.dict(os.environ, {"GA_LAUNCHER_PACKAGED_IMPORT_SMOKE": "1"}, clear=False):
            with mock.patch.object(launcher.sys, "frozen", True, create=True):
                with mock.patch.dict(sys.modules, {"simplejson": stub_simplejson}, clear=False):
                    with mock.patch("builtins.print") as print_mock:
                        exit_code = launcher._maybe_run_packaged_import_smoke()
        self.assertEqual(exit_code, 0)
        print_mock.assert_called_once()
        line = print_mock.call_args.args[0]
        self.assertTrue(line.startswith("PACKAGED_IMPORT_SMOKE_OK "))
        payload = json.loads(line.split(" ", 1)[1])
        self.assertTrue(payload["frozen"])
        self.assertTrue(os.path.isfile(payload["asset_candidates"][0]))
        self.assertIn("requests", payload["modules"])
        self.assertIn("simplejson", payload["modules"])
        self.assertIn("bridge", payload["modules"])
        self.assertIn("PySide6.QtSvg", payload["modules"])

    def test_quality_gate_tool_runs_focused_macos_preflight_targets(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "tools", "check_launcher_quality.py")
        namespace: dict[str, object] = {}
        with open(path, "r", encoding="utf-8") as f:
            exec(compile(f.read(), path, "exec"), namespace)

        self.assertEqual(
            namespace["MACOS_PREFLIGHT_RUFF_TARGETS"],
            (
                "launcher.py",
                "launcher_core_parts/runtime.py",
                "tools/build_macos_release.py",
                "tools/check_launcher_quality.py",
                "tools/validate_macos_release.py",
                "tests/test_build_macos_release.py",
                "tests/test_validate_macos_release.py",
                "tests/test_launcher_core_behaviors.py",
            ),
        )
        self.assertEqual(
            namespace["MACOS_PREFLIGHT_PYTEST_TARGETS"],
            (
                "tests/test_build_macos_release.py",
                "tests/test_validate_macos_release.py",
                "tests/test_launcher_core_behaviors.py",
            ),
        )

    def test_macos_spec_bundle_exists(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "GenericAgentLauncher.mac.spec")
        main_spec_path = os.path.join(root, "GenericAgentLauncher.spec")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        with open(main_spec_path, "r", encoding="utf-8") as f:
            main_spec_src = f.read()
        self.assertIn("BUNDLE(", src)
        self.assertIn("GenericAgent Launcher.app", src)
        self.assertIn('APP_ICON_SVG_PATH = os.path.join(ROOT_DIR, "assets", "launcher_app_icon.svg")', src)
        self.assertIn("(APP_ICON_SVG_PATH, \"assets\")", src)
        self.assertIn("LAUNCHER_SCRIPT = os.path.join(ROOT_DIR, \"launcher.py\")", src)
        self.assertIn("hookspath=[HOOKS_DIR]", src)
        self.assertIn("MACOS_ICON_PATH", src)
        self.assertIn("icon=MACOS_ICON_PATH if os.path.isfile(MACOS_ICON_PATH) else None", src)

        collect_all_calls = []

        def _collect_data_files(_package, subdir=None):
            return [(f"stub:{subdir}", subdir or ".")]

        def _collect_all(package):
            collect_all_calls.append(str(package))
            return [(f"{package}-data", ".")], [f"{package}-bin"], [f"{package}.hidden"]

        class _AnalysisResult:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                self.pure = ["pure"]
                self.scripts = ["script"]
                self.binaries = ["binary"]
                self.datas = ["data"]

        pyinstaller_hooks_module = types.ModuleType("PyInstaller.utils.hooks")
        pyinstaller_hooks_module.collect_data_files = _collect_data_files
        pyinstaller_hooks_module.collect_all = _collect_all
        namespace_base = {
            "__builtins__": __builtins__,
            "Analysis": _AnalysisResult,
            "PYZ": lambda *args, **kwargs: ("PYZ", args, kwargs),
            "EXE": lambda *args, **kwargs: ("EXE", args, kwargs),
            "COLLECT": lambda *args, **kwargs: ("COLLECT", args, kwargs),
            "BUNDLE": lambda *args, **kwargs: ("BUNDLE", args, kwargs),
        }

        with mock.patch.dict(sys.modules, {"PyInstaller.utils.hooks": pyinstaller_hooks_module}):
            main_namespace = dict(namespace_base, __file__=main_spec_path)
            exec(compile(main_spec_src, main_spec_path, "exec"), main_namespace)

            namespace = dict(namespace_base, __file__=path)
            exec(compile(src, path, "exec"), namespace)
            self.assertEqual(namespace["ROOT_DIR"], root)
            self.assertEqual(namespace["LAUNCHER_SCRIPT"], os.path.join(root, "launcher.py"))
            self.assertEqual(namespace["HOOKS_DIR"], os.path.join(root, "hooks"))
            for dependency in ("requests", "simplejson", "charset_normalizer", "cryptography"):
                self.assertIn(
                    dependency,
                    namespace["hiddenimports"],
                    msg=f"mac spec is missing required runtime dependency: {dependency}",
                )
            self.assertTrue(
                set(main_namespace["hiddenimports"]).issubset(set(namespace["hiddenimports"])),
                msg=f"mac spec hiddenimports drifted from main spec: {sorted(set(main_namespace['hiddenimports']) - set(namespace['hiddenimports']))}",
            )

            namespace = dict(namespace_base, SPEC=path)
            exec(compile(src, path, "exec"), namespace)
            self.assertEqual(namespace["ROOT_DIR"], root)

            with mock.patch("os.getcwd", return_value=root):
                namespace = dict(namespace_base)
                exec(compile(src, path, "exec"), namespace)
            self.assertEqual(namespace["ROOT_DIR"], root)
            self.assertEqual(
                collect_all_calls,
                [
                    "requests",
                    "simplejson",
                    "charset_normalizer",
                    "cryptography",
                    "requests",
                    "simplejson",
                    "charset_normalizer",
                    "cryptography",
                    "requests",
                    "simplejson",
                    "charset_normalizer",
                    "cryptography",
                    "requests",
                    "simplejson",
                    "charset_normalizer",
                    "cryptography",
                ],
            )

    def test_macos_build_script_creates_dmg_and_sha256(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "tools", "build_macos_release.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hdiutil", src)
        self.assertIn(".dmg", src)
        self.assertIn(".sha256", src)
        self.assertIn("README-macOS.txt", src)
        self.assertIn("install-metadata.json", src)
        self.assertIn('"channel": "stable"', src)
        self.assertIn('MACOS_VERSION_JSON_RELATIVE_PATH = "Contents/Resources/version.json"', src)
        self.assertIn('"version_json": MACOS_VERSION_JSON_RELATIVE_PATH', src)
        self.assertIn('MACOS_INSTALL_TARGET = f"/Applications/{APP_BUNDLE_NAME}"', src)
        self.assertIn("~/Library/Application Support/GenericAgentLauncher", src)
        self.assertIn("build_macos_icon_assets", src)
        self.assertIn("def _repo_root", src)
        self.assertIn("def _resolve_path", src)
        self.assertIn("def _prepare_macos_bundle_icon", src)
        self.assertIn("root = _repo_root()", src)
        self.assertIn('dist_dir = _resolve_path(root, str(args.dist or "dist"))', src)
        self.assertIn('out_root = _resolve_path(root, str(args.out or "release"))', src)
        self.assertIn("bundle_icon_path = _prepare_macos_bundle_icon(root)", src)
        self.assertIn('print(f"- bundle icon: {bundle_icon_path}")', src)

    def test_schedule_remote_delete_task_executes_remote_script_before_result_check(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "schedule_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ok, payload_data, err = self._schedule_remote_exec_json(device, script, timeout=120)", src)
        self.assertIn('raise RuntimeError(str(err or "远端删除任务失败。").strip() or "远端删除任务失败。")', src)

    def test_release_workflow_has_macos_job(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, ".github", "workflows", "release-installer.yml")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        steps = _workflow_step_map(path)
        ordered_names = [step["name"] for step in _workflow_named_steps(path)]
        self.assertIn("build-macos", src)
        self.assertIn("matrix:", src)
        self.assertIn("x86_64", src)
        self.assertIn("arm64", src)
        self.assertIn("macos-15-intel", src)
        self.assertIn("macos-15", src)
        self.assertIn("contents: write", src)
        self.assertIn("GA_MACOS_RUNNER_LABEL", src)
        self.assertIn("GA_MACOS_EXPECTED_ARCH", src)
        self.assertEqual(
            ordered_names[ordered_names.index("Mac preflight quality gate") : ordered_names.index("Publish macOS release assets") + 1],
            [
                "Mac preflight quality gate",
                "Run tests",
                "Source startup smoke",
                "Build macOS app and dmg",
                "Packaged app import smoke",
                "Packaged app startup smoke",
                "Runner and bundle diagnostics",
                "Validate macOS release contract",
                "Upload macOS build artifacts",
                "Prepare macOS release sidecars",
                "Publish macOS release assets",
            ],
        )
        self.assertIn("python -m pip install pytest ruff", steps["Install dependencies"])
        self.assertIn("python tools/check_launcher_quality.py --scope macos-preflight", steps["Mac preflight quality gate"])
        self.assertIn("python -m pytest tests -q", steps["Run tests"])
        self.assertIn('GA_LAUNCHER_SMOKE_EXIT_MS: "1200"', steps["Source startup smoke"])
        self.assertIn("QT_QPA_PLATFORM: offscreen", steps["Source startup smoke"])
        self.assertIn("python launcher.py", steps["Source startup smoke"])
        self.assertIn("python tools/build_macos_release.py", steps["Build macOS app and dmg"])
        self.assertIn("--commit", steps["Build macOS app and dmg"])
        self.assertIn("github.sha", steps["Build macOS app and dmg"])
        self.assertIn('GA_LAUNCHER_PACKAGED_IMPORT_SMOKE: "1"', steps["Packaged app import smoke"])
        self.assertNotIn("GA_LAUNCHER_SMOKE_EXIT_MS", steps["Packaged app import smoke"])
        self.assertIn("set +e", steps["Packaged app import smoke"])
        self.assertIn('output="$("$app" 2>&1)"', steps["Packaged app import smoke"])
        self.assertIn("status=$?", steps["Packaged app import smoke"])
        self.assertIn('echo "Packaged app import smoke exited with status $status" >&2', steps["Packaged app import smoke"])
        self.assertIn('grep -q "PACKAGED_IMPORT_SMOKE_OK"', steps["Packaged app import smoke"])
        self.assertIn("GenericAgent Launcher.app/Contents/MacOS/GenericAgentLauncher", steps["Packaged app import smoke"])
        self.assertIn('GA_LAUNCHER_SMOKE_EXIT_MS: "1200"', steps["Packaged app startup smoke"])
        self.assertIn("QT_QPA_PLATFORM: offscreen", steps["Packaged app startup smoke"])
        self.assertIn('"$app"', steps["Packaged app startup smoke"])
        self.assertIn("codesign --verify --deep --strict", steps["Runner and bundle diagnostics"])
        self.assertIn("spctl --assess --type execute --verbose=4", steps["Runner and bundle diagnostics"])
        self.assertIn("python tools/validate_macos_release.py", steps["Validate macOS release contract"])
        self.assertIn("--expected-arch", steps["Validate macOS release contract"])
        self.assertIn("--expected-runner-label", steps["Validate macOS release contract"])
        self.assertIn("README-macOS-$arch.txt", steps["Prepare macOS release sidecars"])
        self.assertIn("install-metadata-$arch.json", steps["Prepare macOS release sidecars"])
        self.assertIn("GenericAgentLauncher-macos-$arch-$v.dmg", steps["Publish macOS release assets"])
        self.assertIn("GenericAgentLauncher-macos-$arch-$v.sha256", steps["Publish macOS release assets"])
        self.assertIn("README-macOS.txt", src)
        self.assertIn("install-metadata.json", src)

    def test_macos_validate_workflow_runs_tests_smoke_and_bundle_validation(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, ".github", "workflows", "macos-validate.yml")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        steps = _workflow_step_map(path)
        ordered_names = [step["name"] for step in _workflow_named_steps(path)]
        self.assertIn("macos-validate", src)
        self.assertIn("workflow_dispatch:", src)
        self.assertIn("inputs:", src)
        self.assertIn("version:", src)
        self.assertIn("matrix:", src)
        self.assertIn("x86_64", src)
        self.assertIn("arm64", src)
        self.assertIn("macos-15-intel", src)
        self.assertIn("macos-15", src)
        self.assertIn("pull_request", src)
        self.assertIn("branches:", src)
        self.assertIn("main", src)
        self.assertIn("GA_MACOS_RUNNER_LABEL", src)
        self.assertIn("GA_MACOS_EXPECTED_ARCH", src)
        self.assertEqual(
            ordered_names[ordered_names.index("Mac preflight quality gate") : ordered_names.index("Upload macOS validation artifacts") + 1],
            [
                "Mac preflight quality gate",
                "Run tests",
                "Source startup smoke",
                "Build macOS app and dmg",
                "Packaged app import smoke",
                "Packaged app startup smoke",
                "Runner and bundle diagnostics",
                "Validate macOS release contract",
                "Upload macOS validation artifacts",
            ],
        )
        self.assertIn("python -m pip install pytest ruff", steps["Install dependencies"])
        self.assertIn("python tools/check_launcher_quality.py --scope macos-preflight", steps["Mac preflight quality gate"])
        self.assertIn("python -m pytest tests -q", steps["Run tests"])
        self.assertIn('GA_LAUNCHER_SMOKE_EXIT_MS: "1200"', steps["Source startup smoke"])
        self.assertIn("QT_QPA_PLATFORM: offscreen", steps["Source startup smoke"])
        self.assertIn("python launcher.py", steps["Source startup smoke"])
        self.assertIn("python tools/build_macos_release.py", steps["Build macOS app and dmg"])
        self.assertIn("--commit", steps["Build macOS app and dmg"])
        self.assertIn("github.sha", steps["Build macOS app and dmg"])
        self.assertIn('GA_LAUNCHER_PACKAGED_IMPORT_SMOKE: "1"', steps["Packaged app import smoke"])
        self.assertNotIn("GA_LAUNCHER_SMOKE_EXIT_MS", steps["Packaged app import smoke"])
        self.assertIn("set +e", steps["Packaged app import smoke"])
        self.assertIn('output="$("$app" 2>&1)"', steps["Packaged app import smoke"])
        self.assertIn("status=$?", steps["Packaged app import smoke"])
        self.assertIn('echo "Packaged app import smoke exited with status $status" >&2', steps["Packaged app import smoke"])
        self.assertIn('grep -q "PACKAGED_IMPORT_SMOKE_OK"', steps["Packaged app import smoke"])
        self.assertIn("GenericAgent Launcher.app/Contents/MacOS/GenericAgentLauncher", steps["Packaged app import smoke"])
        self.assertIn('GA_LAUNCHER_SMOKE_EXIT_MS: "1200"', steps["Packaged app startup smoke"])
        self.assertIn("QT_QPA_PLATFORM: offscreen", steps["Packaged app startup smoke"])
        self.assertIn('"$app"', steps["Packaged app startup smoke"])
        self.assertIn("codesign --verify --deep --strict", steps["Runner and bundle diagnostics"])
        self.assertIn("python tools/validate_macos_release.py", steps["Validate macOS release contract"])
        self.assertIn("--expected-arch", steps["Validate macOS release contract"])
        self.assertIn("--expected-runner-label", steps["Validate macOS release contract"])
        self.assertIn("launcher-macos-validate-${{ matrix.arch }}-${{ steps.ver.outputs.value }}", src)

    def test_docs_cover_macos_manual_install_contract_and_smoke_checklist(self):
        root = os.path.dirname(os.path.dirname(__file__))
        readme_path = os.path.join(root, "README.md")
        smoke_path = os.path.join(root, "docs", "macos-manual-smoke-checklist.md")
        runbook_path = os.path.join(root, "docs", "macos-release-runbook.md")
        report_path = os.path.join(root, "docs", "macos-smoke-report-template.md")
        with open(readme_path, "r", encoding="utf-8") as f:
            readme = f.read()
        with open(smoke_path, "r", encoding="utf-8") as f:
            smoke = f.read()
        with open(runbook_path, "r", encoding="utf-8") as f:
            runbook = f.read()
        with open(report_path, "r", encoding="utf-8") as f:
            report = f.read()
        self.assertIn("GenericAgentLauncher-macos-arm64-<version>.dmg", readme)
        self.assertIn("README-macOS-arm64.txt", readme)
        self.assertIn("install-metadata-arm64.json", readme)
        self.assertIn("System Settings -> Privacy & Security -> Open Anyway", readme)
        self.assertIn("不提供 Apple Developer 签名", readme)
        self.assertIn("Apple Silicon Mac", readme)
        self.assertIn("Intel Mac", readme)
        self.assertIn("只支持手动替换 `.app`", readme)
        self.assertIn("Open Anyway", smoke)
        self.assertIn("Finder 右键 `Open` 作为兼容性备选路径", smoke)
        self.assertIn("install-metadata-<arch>.json", smoke)
        self.assertIn("build_arch / runner_label", smoke)
        self.assertIn("LAN Web", smoke)
        self.assertIn("手动替换 `/Applications/GenericAgent Launcher.app`", smoke)
        self.assertIn("tools/validate_macos_release.py", runbook)
        self.assertIn("install-metadata-arm64.json", runbook)
        self.assertIn("Commit：", report)
        self.assertIn("建议是否允许公开发布", report)

    def test_validate_macos_release_tool_checks_dmg_contract_and_mount_layout(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "tools", "validate_macos_release.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hdiutil", src)
        self.assertIn("attach", src)
        self.assertIn("detach", src)
        self.assertIn('APP_BUNDLE_NAME = f"{APP_NAME}.app"', src)
        self.assertIn('os.readlink(applications_alias) != "/Applications"', src)
        self.assertIn("mounted dmg is missing install-metadata.json", src)
        self.assertIn('["codesign", "--verify", "--deep", "--strict", app_path]', src)
        self.assertIn('expected_prefixes = ("Contents/Frameworks/", "Contents/Resources/")', src)
        self.assertIn("expected_arch=expected_arch", src)
        self.assertIn('MACOS_VERSION_JSON_RELATIVE_PATH = "Contents/Resources/version.json"', src)

    def test_build_bat_auto_loads_local_update_signing_key_files(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "build.bat")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("tools\\resolve_release_version.py", src)
        self.assertIn("release\\VERSION", src)
        self.assertIn("Canonical release version", src)
        self.assertIn('set "ISCC_EXE=%INNO_ISCC%"', src)
        self.assertIn("local_keys\\update_signing_private_key.pem", src)
        self.assertIn("local_keys\\update_signing_public_key.pem", src)
        self.assertIn("GA_LAUNCHER_UPDATE_PRIVATE_KEY_FILE", src)
        self.assertIn("GA_LAUNCHER_UPDATE_PUBLIC_KEY_FILE", src)

    def test_updater_and_main_spec_are_packaging_safe_for_installs(self):
        root = os.path.dirname(os.path.dirname(__file__))
        updater_path = os.path.join(root, "updater.py")
        spec_path = os.path.join(root, "GenericAgentLauncher.spec")
        with open(updater_path, "r", encoding="utf-8") as f:
            updater_src = f.read()
        with open(spec_path, "r", encoding="utf-8") as f:
            spec_src = f.read()
        self.assertIn("from launcher_core_parts.runtime import updater_log", updater_src)
        self.assertIn("from launcher_core_parts.update_manager import apply_update_job", updater_src)
        self.assertNotIn("from launcher_app import core as lz", updater_src)
        self.assertIn('"cryptography"', spec_src)

    def test_markup_helpers(self):
        raw = """
<summary>hello</summary>
<tool_use>{\"name\":\"x\"}</tool_use>
[FILE:demo.py]
"""
        visible = lz._assistant_visible_markup(raw)
        self.assertIn("tool_use", visible)
        self.assertIn("demo.py", visible)

        summary_only = lz._assistant_visible_markup("<summary>hello</summary>")
        self.assertEqual(summary_only, "hello")

        wrapped = "**LLM Running (Turn 3) ...**\nbody"
        self.assertEqual(lz._turn_marker_title(wrapped), "LLM Running (Turn 3) ...")
        self.assertEqual(lz._strip_turn_marker(wrapped), "body")


if __name__ == "__main__":
    unittest.main()
