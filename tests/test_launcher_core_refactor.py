from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from launcher_app import core as lz
from launcher_core_parts import model_api, runtime


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
