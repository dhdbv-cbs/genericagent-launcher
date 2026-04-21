from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest

import bridge
from launcher_app import core as lz
from launcher_core_parts import python_env


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
        self.assertEqual(out["passthrough"][0]["name"], "my_cookie")

    def test_auto_config_var_increments(self):
        existing = {"native_oai_config", "native_oai_config2"}
        name = lz.auto_config_var("native_oai", existing)
        self.assertEqual(name, "native_oai_config3")

    def test_serialize_mykey_py_contains_blocks(self):
        text = lz.serialize_mykey_py(
            configs=[
                {
                    "var": "native_claude_config",
                    "kind": "native_claude",
                    "data": {"apikey": "k", "apibase": "https://x", "model": "m"},
                }
            ],
            extras={"tg_bot_token": "abc"},
            passthrough=[{"name": "my_cookie", "value": "cookie-v"}],
        )
        self.assertIn("native_claude_config", text)
        self.assertIn("tg_bot_token", text)
        self.assertIn("my_cookie", text)

    def test_model_api_url_helpers(self):
        self.assertEqual(
            lz._strip_known_api_suffix("https://a.com/v1/chat/completions"),
            "https://a.com",
        )
        self.assertEqual(lz._join_url("https://a.com/v1/", "/models"), "https://a.com/v1/models")

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
        self.assertIn("winsound.MessageBeep", src)
        self.assertIn("tray.showMessage", src)
        self.assertIn("3000", src)
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
        self.assertIn('env.pop("_MEIPASS2", None)', src)
        self.assertIn('kwargs["env"] = _external_subprocess_env(kwargs.get("env"))', src)

    def test_bridge_runtime_uses_external_subprocess_sanitizer(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "bridge_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("bridge_env = lz._external_subprocess_env()", src)
        self.assertIn("self.bridge_proc = lz._popen_external_subprocess(", src)

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
