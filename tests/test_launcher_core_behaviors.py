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
from qt_chat_parts.api_editor import ApiEditorMixin


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
        self.assertIn("QTimer.singleShot(0, self, fn)", src)
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
        self.assertIn("_check_runtime_dependencies(purpose=\"载入内核\")", nav_src)
        self.assertIn("GenericAgent requirements.txt", runtime_src)
        self.assertIn("_ensure_runtime_dependencies", runtime_src)

    def test_channel_runtime_uses_channel_specific_dependency_check(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("def _channel_extra_packages", src)
        self.assertIn("lz._split_requirement_tokens(spec.get(\"pip\", \"\"))", src)
        self.assertIn("extra_packages=extra_packages", src)
        self.assertIn("visual=bool(show_errors)", src)

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
