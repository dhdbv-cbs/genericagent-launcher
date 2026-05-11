from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from launcher_core_parts import update_manager


class UpdateManagerLockTests(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.root = self._tempdir.name
        self.updates_dir = os.path.join(self.root, "updates")
        os.makedirs(self.updates_dir, exist_ok=True)
        self.lock_path = os.path.join(self.updates_dir, "update.lock")

    def _launcher_data_path(self, *parts):
        return os.path.join(self.root, *parts)

    def test_update_lock_acquires_and_cleans_up_normally(self):
        with mock.patch.object(update_manager, "launcher_data_path", side_effect=self._launcher_data_path):
            with update_manager._update_lock(timeout_seconds=3) as lock_path:
                self.assertEqual(os.path.normpath(lock_path), os.path.normpath(self.lock_path))
                self.assertTrue(os.path.isfile(lock_path))
                with open(lock_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), str(os.getpid()))

        self.assertFalse(os.path.exists(self.lock_path))

    def test_update_lock_removes_stale_pid_lock_and_recovers(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write("424242")

        with mock.patch.object(update_manager, "launcher_data_path", side_effect=self._launcher_data_path), mock.patch.object(
            update_manager, "_update_lock_owner_running", return_value=False
        ), mock.patch.object(update_manager, "updater_log") as updater_log:
            with update_manager._update_lock(timeout_seconds=3) as lock_path:
                self.assertEqual(os.path.normpath(lock_path), os.path.normpath(self.lock_path))
                with open(lock_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), str(os.getpid()))

        self.assertFalse(os.path.exists(self.lock_path))
        self.assertTrue(any("removed stale update.lock" in str(call.args[0]) for call in updater_log.call_args_list))

    def test_update_lock_active_owner_still_times_out(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write("424242")

        fake_times = iter([100.0, 100.5, 103.2])
        with mock.patch.object(update_manager, "launcher_data_path", side_effect=self._launcher_data_path), mock.patch.object(
            update_manager, "_update_lock_owner_running", return_value=True
        ), mock.patch.object(update_manager.time, "time", side_effect=lambda: next(fake_times)), mock.patch.object(
            update_manager.time, "sleep", return_value=None
        ):
            with self.assertRaises(update_manager.UpdateError) as cm:
                with update_manager._update_lock(timeout_seconds=3):
                    pass

        self.assertEqual(cm.exception.code, update_manager.ERR_LOCK_TIMEOUT)
        self.assertEqual(cm.exception.phase, "prepare")
        self.assertIn("active: pid=424242", cm.exception.detail)
        self.assertTrue(os.path.exists(self.lock_path))

    def test_update_lock_removes_old_malformed_payload_and_recovers(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write("{broken")
        stale_time = os.path.getmtime(self.lock_path) - 10
        os.utime(self.lock_path, (stale_time, stale_time))

        with mock.patch.object(update_manager, "launcher_data_path", side_effect=self._launcher_data_path), mock.patch.object(
            update_manager, "updater_log"
        ) as updater_log:
            with update_manager._update_lock(timeout_seconds=3) as lock_path:
                self.assertEqual(os.path.normpath(lock_path), os.path.normpath(self.lock_path))
                with open(lock_path, "r", encoding="utf-8") as f:
                    self.assertEqual(f.read().strip(), str(os.getpid()))

        self.assertFalse(os.path.exists(self.lock_path))
        self.assertTrue(any("invalid lock payload" in str(call.args[0]) for call in updater_log.call_args_list))

    def test_classify_update_lock_keeps_recent_malformed_payload_uncertain(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write("{broken")
        os.utime(self.lock_path, (100.0, 100.0))

        with mock.patch.object(update_manager.time, "time", return_value=101.0):
            state, detail = update_manager._classify_update_lock(self.lock_path)

        self.assertEqual(state, "uncertain")
        self.assertEqual(detail, "lock payload not ready")

    def test_update_lock_stale_cleanup_failure_times_out_with_detail(self):
        with open(self.lock_path, "w", encoding="utf-8") as f:
            f.write("424242")

        fake_times = iter([100.0, 100.5, 103.2])
        with mock.patch.object(update_manager, "launcher_data_path", side_effect=self._launcher_data_path), mock.patch.object(
            update_manager, "_classify_update_lock", return_value=("stale", "pid=424242 not running")
        ), mock.patch.object(update_manager.os, "remove", side_effect=PermissionError("denied")), mock.patch.object(
            update_manager.time, "time", side_effect=lambda: next(fake_times)
        ), mock.patch.object(update_manager.time, "sleep", return_value=None):
            with self.assertRaises(update_manager.UpdateError) as cm:
                with update_manager._update_lock(timeout_seconds=3):
                    pass

        self.assertEqual(cm.exception.code, update_manager.ERR_LOCK_TIMEOUT)
        self.assertIn("stale cleanup failed", cm.exception.detail)
        self.assertIn("denied", cm.exception.detail)
        self.assertTrue(os.path.exists(self.lock_path))


if __name__ == "__main__":
    unittest.main()
