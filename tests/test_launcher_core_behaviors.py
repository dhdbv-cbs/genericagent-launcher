from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest

import bridge
from launcher_app import core as lz
from launcher_core_parts import model_api
from launcher_core_parts import python_env
from launcher_core_parts import sessions as sessions_mod
from qt_chat_parts import personal_usage as personal_usage_mod
from qt_chat_parts.api_editor import ApiEditorMixin
from qt_chat_parts import common


class LauncherCoreBehaviorTests(unittest.TestCase):
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
                requirements_path="",
                force_sync=False,
            )
        )
        self.assertFalse(
            python_env._should_sync_runtime_dependencies(
                state_matches=True,
                extra_packages=[],
                requirements_path="",
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
        self.assertNotEqual(sig1["requirements_hash"], sig2["requirements_hash"])

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

    def test_bridge_ui_llms_keeps_provider_prefix(self):
        class DummyAgent:
            def list_llms(self):
                return [(0, "anthropic/claude-opus-4-6", True)]

        items = bridge._ui_llms(DummyAgent())
        self.assertEqual(items[0]["name"], "anthropic/claude-opus-4-6")

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

    def test_bridge_claude_sse_patch_preserves_thinking_signature_field(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "bridge.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('current_block = {"type": "thinking", "thinking": "", "signature": ""}', src)
        self.assertIn('elif delta.get("type") == "signature_delta":', src)
        self.assertIn('current_block["signature"] = current_block.get("signature", "") + delta.get("signature", "")', src)

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
        self.assertEqual(out["extras"].get("tg_bot_token"), "123")
        self.assertEqual(out["extras"].get("langfuse_config", {}).get("public_key"), "pk-demo")
        self.assertEqual(out["passthrough"][0]["name"], "my_cookie")

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
                    "data": {"apikey": "k", "apibase": "https://x", "model": "m"},
                }
            ],
            extras={"tg_bot_token": "abc", "langfuse_config": {"public_key": "pk", "secret_key": "sk"}},
            passthrough=[{"name": "my_cookie", "value": "cookie-v"}],
        )
        self.assertIn("native_claude_config", text)
        self.assertIn("tg_bot_token", text)
        self.assertIn("langfuse_config", text)
        self.assertIn("my_cookie", text)

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
        self.assertIn("tray.showMessage", src)
        self.assertIn("1500", src)
        self.assertIn("if not was_aborted", src)

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
        self.assertIn('for key in ("name", "apikey", "apibase", "model", "user_agent")', src)
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
        self.assertIn("def _sync_draft_to_floating(self):", window_src)
        self.assertIn("self.llm_combo = QComboBox()", window_src)
        self.assertIn("self.session_combo = QComboBox()", window_src)
        self.assertIn("self.new_session_btn = QPushButton(\"新建\")", window_src)
        self.assertIn("self.regen_btn = QPushButton(\"重试\")", window_src)
        self.assertIn("def _enter_tray_floating_mode(self):", window_src)
        self.assertIn("def _restore_from_tray_mode(self):", window_src)
        self.assertIn("if self.isVisible() and not self._tray_mode_active:", window_src)
        self.assertIn("def _show_floating_chat_window_only(self):", window_src)
        self.assertIn('menu.addAction("🗕  缩小到托盘，仅保留悬浮窗")', shell_src)
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
        self.assertIn("self._render_attachment_bar_target(host, layout, summary)", bridge_src)
        self.assertIn("def _handle_input_image_attachments", bridge_src)
        self.assertIn("def _clear_active_turn_attachments", bridge_src)
        self.assertIn('"images": [str(item.get("path") or "").strip() for item in attachments]', bridge_src)
        self.assertIn('agent.put_task(cmd.get("text", ""), source="user", images=images)', bridge_py_src
        )

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

    def test_bridge_runtime_anchors_send_to_user_row(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('display_text = text or f"[已发送 {len(attachments)} 张图片]"', src)
        self.assertIn('user_row = self._add_message_row("user", display_text, finished=True, auto_scroll=False)', src)
        self.assertIn('self._stream_row = self._add_message_row("assistant", "", finished=False, auto_scroll=False)', src)
        self.assertIn("self._scroll_row_to_top(user_row)", src)

    def test_window_builds_jump_latest_button(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "launcher_app", "window.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
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

    def test_personal_settings_exposes_reply_notification_toggles(self):
        root = os.path.dirname(os.path.dirname(__file__))
        settings_path = os.path.join(root, "qt_chat_parts", "settings_panel.py")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings_src = f.read()
        self.assertIn('notify_title = QLabel("回复提醒")', settings_src)
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
        self.assertIn('("usage", "🧾  使用日志")', settings_src)
        self.assertIn('"使用日志"', settings_src)
        self.assertIn("Langfuse 追踪", personal_src)
        self.assertIn("日志来源", personal_src)
        self.assertIn("高消耗会话", personal_src)
        self.assertIn("最近活动", personal_src)
        self.assertIn("高级模式 · Langfuse", personal_src)
        self.assertIn("def _usage_table_card", personal_src)
        self.assertIn("def _usage_metric_card", personal_src)
        self.assertIn("def _usage_cache_label", personal_src)
        self.assertIn("数据不足", personal_src)
        self.assertIn("def _load_langfuse_status", personal_src)
        self.assertIn("def _save_langfuse_config", personal_src)
        self.assertIn("def _clear_langfuse_config", personal_src)
        self.assertIn("保存并重启内核", personal_src)
        self.assertIn("使用官方云端", personal_src)
        self.assertIn("langfuse_config", personal_src)
        self.assertIn('"langfuse_config"', channels_src)

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
        self.assertIn("self._enter_chat(skip_dependency_check=True)", nav_src)
        self.assertIn("def _enter_chat(self, *, skip_dependency_check=False):", nav_src)
        self.assertIn('if (not skip_dependency_check) and (not self._check_runtime_dependencies(purpose="载入内核")):', nav_src)

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
        self.assertIn("self._enter_chat(skip_dependency_check=True)", nav_src)
        self.assertIn("def _enter_chat(self, *, skip_dependency_check=False):", nav_src)
        self.assertIn('if (not skip_dependency_check) and (not self._check_runtime_dependencies(purpose="载入内核")):', nav_src)

    def test_spec_uses_local_hooks_dir(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "GenericAgentLauncher.spec")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("hookspath=['hooks']", src)

    def test_claude_template_defaults_follow_upstream_model_names(self):
        self.assertEqual(lz.TEMPLATE_INDEX["anthropic"]["defaults"]["model"], "claude-opus-4-7[1m]")
        self.assertEqual(lz.TEMPLATE_INDEX["cc-switch"]["defaults"]["model"], "claude-opus-4-7")
        self.assertEqual(lz.TEMPLATE_INDEX["crs-claude"]["defaults"]["model"], "claude-opus-4-7[1m]")
        self.assertEqual(lz.TEMPLATE_INDEX["crs-gemini"]["defaults"]["model"], "claude-opus-4-7-thinking")
        self.assertEqual(lz.TEMPLATE_INDEX["openrouter"]["defaults"]["model"], "anthropic/claude-opus-4-7")

    def test_custom_importlib_resources_hook_guards_missing_trees_module(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "hooks", "hook-importlib_resources.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('find_spec("importlib_resources.trees") is not None', src)

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
        self.assertIn("下载更新安装包", src)
        self.assertIn("QDesktopServices.openUrl", src)

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
        self.assertIn('("vps", "🖥️  VPS 管理")', settings_src)
        self.assertIn("self.settings_vps_username_edit", settings_src)
        self.assertIn("self.settings_vps_port_spin", settings_src)
        self.assertIn("self.settings_vps_key_path_edit", settings_src)
        self.assertIn("self.settings_vps_password_edit", settings_src)
        self.assertIn("安装 SSH 依赖", settings_src)
        self.assertIn("远程终端", settings_src)
        self.assertIn("一键 Docker 部署", settings_src)
        self.assertIn("上传本地 agant 项目", settings_src)
        self.assertIn("服务器拉取原始 agant", settings_src)
        self.assertIn("依赖策略", settings_src)
        self.assertIn("排除规则", settings_src)
        self.assertIn("def _reload_vps_panel", settings_src)
        self.assertIn("def _install_vps_dependencies", settings_src)
        self.assertIn("def _connect_vps_terminal", settings_src)
        self.assertIn("def _send_vps_terminal_command", settings_src)
        self.assertIn("def _deploy_vps_agent_docker", settings_src)
        self.assertIn("预检结果：", settings_src)
        self.assertIn("def _split_vps_upload_excludes", settings_src)
        self.assertIn("def _is_path_excluded_for_upload", settings_src)
        self.assertIn("自动生成生产级 Docker 模板", settings_src)
        self.assertIn("pip_mirror_url", settings_src)
        self.assertIn('if rel.lower() == "mykey.py"', settings_src)
        self.assertIn("已同步 mykey.py 到远端目录。", settings_src)
        self.assertIn("docker --version", settings_src)
        self.assertIn("requirements.docker.txt", settings_src)
        self.assertIn("def _vps_render_bootstrap_dockerfile", settings_src)
        self.assertIn("def _refresh_vps_remote_dir_placeholder", settings_src)
        self.assertIn("def _sanitize_vps_feedback_text", settings_src)
        self.assertIn("def _validate_vps_docker_image_name", settings_src)
        self.assertIn("def _append_vps_terminal_dependency_output", settings_src)
        self.assertIn("normalize_remote_agent_dir", settings_src)
        self.assertIn("remote_agent_dir_default", settings_src)
        self.assertIn("__SYNC_REBUILD__", settings_src)
        self.assertIn("__SYNC_ROLLBACK__", settings_src)
        self.assertIn("同名镜像同步重建失败，已自动回滚到旧容器。", settings_src)
        self.assertIn("__NO_DOCKERFILE__", settings_src)
        self.assertIn("未检测到 Dockerfile/compose，已停止部署。", settings_src)
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
        self.assertIn('section.setVisible(self._settings_category_uses_target_switch(category))', settings_src)
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
        self.assertIn('if category in ("api", "channels", "schedule", "usage"):', settings_src)

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
        self.assertIn("下载并打开报告", schedule_src)
        self.assertIn("同步并打开目录", schedule_src)
        self.assertIn("下载并打开完整日志", schedule_src)
        self.assertIn("def _usage_export_current_report", personal_src)
        self.assertIn("导出当前摘要", personal_src)
        self.assertIn("打开会话缓存", personal_src)
        self.assertIn("def _remote_channel_status_check_age", channel_src)
        self.assertIn("def _remote_channel_last_checked_at", channel_src)
        self.assertIn("def _remote_channel_device_sync_state", channel_src)
        self.assertIn("def _remote_channel_check_hint", channel_src)
        self.assertIn("def _show_remote_channel_status_detail", channel_src)
        self.assertIn("def _request_remote_channel_status_refresh", channel_src)
        self.assertIn("正在校验远端状态", channel_src)
        self.assertIn("最近校验：", channel_src)
        self.assertIn("服务器连接异常", channel_src)
        self.assertIn("校验详情", channel_src)
        self.assertIn("device_checked_map[did] = now", sidebar_src)
        self.assertIn("checked_map[(did, cid)] = now", sidebar_src)
        self.assertIn("sync_meta[\"fail_count\"] = int(sync_meta.get(\"fail_count\") or 0) + 1", sidebar_src)
        self.assertIn("raw_err = str(err or \"远端状态读取失败。\").strip() or \"远端状态读取失败。\"", sidebar_src)
        self.assertIn("sync_meta[\"last_error\"] = normalize_ssh_error_text(raw_err, context=\"SSH 连接\")", sidebar_src)
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
        self.assertIn('probe_url = "https://pypi.tuna.tsinghua.edu.cn/simple"', settings_src)
        self.assertIn('probe_url = "https://pypi.org/simple"', settings_src)
        self.assertIn('self.settings_vps_docker_image_edit.setPlaceholderText("请填写你自己的镜像名；不要留空，也不会再自动改名")', settings_src)
        self.assertIn('self.settings_vps_docker_container_edit.setPlaceholderText("请填写你自己的容器名；同名时会做同步重建")', settings_src)
        self.assertIn("请先填写镜像名称。启动器不会再替你自动改名。", settings_src)
        self.assertIn('QMessageBox.warning(self, "无法部署", image_error)', settings_src)
        self.assertIn('QMessageBox.warning(self, "无法部署", "请先填写容器名称。")', settings_src)
        self.assertIn("from PySide6.QtGui import QColor, QFontDatabase, QImage, QPainter, QPalette, QPen, QPixmap, QTextCursor", settings_src)
        self.assertIn("palette.setColor(QPalette.PlaceholderText, QColor(\"#94a3b8\"))", settings_src)
        self.assertIn("viewport.setStyleSheet(\"background: #0f172a; color: #e2e8f0;\")", settings_src)
        self.assertIn("self.settings_vps_terminal_meta.setMinimumHeight(40)", settings_src)
        self.assertIn('bg = "rgba(34,197,94,0.14)"', settings_src)
        self.assertIn('label.setStyleSheet(', settings_src)
        self.assertIn("_VPS_DUPLICATED_PROMPT_RE = re.compile(", settings_src)
        self.assertIn("_VPS_PROMPT_TOKEN_RE = re.compile(", settings_src)
        self.assertIn("_DOCKER_REGISTRY_RE = re.compile(", settings_src)
        self.assertIn("_DOCKER_REPOSITORY_SEGMENT_RE = re.compile(", settings_src)
        self.assertIn("_DOCKER_TAG_RE = re.compile(", settings_src)
        self.assertIn("_SSH_DISCONNECT_HINTS = (", settings_src)
        self.assertIn("def _looks_like_ssh_disconnect", settings_src)
        self.assertIn("def _friendly_ssh_disconnect_reason", settings_src)
        self.assertIn('msg = _VPS_DUPLICATED_PROMPT_RE.sub(', settings_src)
        self.assertIn('not (127 <= ord(ch) <= 159)', settings_src)
        self.assertIn("镜像名称的仓库部分必须全小写", settings_src)
        self.assertIn("class _VpsTerminalCommandEdit(QLineEdit):", settings_src)
        self.assertIn('self.settings_vps_terminal_input.returnPressed.connect(self._send_vps_terminal_command)', settings_src)
        self.assertIn('terminal_prompt = QLabel(">")', settings_src)
        self.assertIn('self.settings_vps_terminal_input.setPlaceholderText("输入命令后回车执行，↑/↓ 取历史命令")', settings_src)
        self.assertIn('self.settings_vps_terminal_send_btn = QPushButton("执行")', settings_src)
        self.assertIn('self.settings_vps_profile_light = QLabel("●")', settings_src)
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
        self.assertIn("app.installEventFilter(self)", window_src)
        self.assertIn("def _begin_window_trace", window_src)
        self.assertIn("def _append_window_trace_log", window_src)
        self.assertIn("focus_combo=", window_src)
        self.assertIn("watched_combo=", window_src)
        self.assertIn("suppressed_blank_dialog", window_src)
        self.assertIn("self._server_status_timer = QTimer(self)", window_src)
        self.assertIn("head_layout.addWidget(self.server_status_btn", window_src)
        self.assertIn("probe(force=True)", settings_src)

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
        self.assertIn("def _schedule_local_channel_autostart", src)
        self.assertIn("self._local_channel_autostart_scheduled = True", src)
        self.assertIn("self._start_autostart_channels()", src)
        self.assertIn("self._schedule_local_channel_autostart()", src)
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
        self.assertIn("上游未提供 requirements.txt；当前改用启动器维护的上游依赖表", pyenv_src)
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

    def test_private_python_installer_has_source_precheck_logs(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "downloads.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("正在预检下载源可用性", src)
        self.assertIn("下载源预检通过", src)
        self.assertIn("下载源预检失败", src)

    def test_setup_page_has_download_source_checkboxes(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "setup_pages.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("Python 安装包下载源（可多选", src)
        self.assertIn("download_source_checkboxes", src)
        self.assertIn("_on_private_python_source_toggled", src)

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
