from __future__ import annotations

import json
import os
import re
import tempfile
import time
import unittest

from launcher_app import core as lz


class LauncherCoreBehaviorTests(unittest.TestCase):
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
        self.assertIn('bridge_env["PYTHONIOENCODING"] = "utf-8"', src)
        self.assertIn('bridge_env["PYTHONUTF8"] = "1"', src)
        self.assertIn('bridge_env.pop("PYTHONLEGACYWINDOWSSTDIO", None)', src)
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
        self.assertIn("_python_utf8_subprocess_env", src)
        self.assertIn('encoding="utf-8"', src)
        self.assertIn('errors="replace"', src)
        self.assertIn("env=_python_utf8_subprocess_env()", src)

    def test_channel_runtime_launch_uses_utf8_python_env(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "qt_chat_parts", "channel_runtime.py")
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("py_env = lz._python_utf8_subprocess_env()", src)
        self.assertIn("env=py_env", src)

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
