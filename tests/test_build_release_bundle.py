from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest


class BuildReleaseBundleTests(unittest.TestCase):
    def _make_dist_tree(self, root: str) -> str:
        dist_dir = os.path.join(root, "dist")
        app_dir = os.path.join(dist_dir, "GenericAgentLauncher")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "launcher.txt"), "w", encoding="utf-8") as f:
            f.write("ok")
        with open(os.path.join(dist_dir, "LauncherBootstrap.exe"), "wb") as f:
            f.write(b"bootstrap")
        with open(os.path.join(dist_dir, "Updater.exe"), "wb") as f:
            f.write(b"updater")
        return dist_dir

    def test_allow_unsigned_build_skips_empty_manifest_sig(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with tempfile.TemporaryDirectory() as td:
            dist_dir = self._make_dist_tree(td)
            out_dir = os.path.join(td, "release")
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/build_release_bundle.py",
                    "--version",
                    "9.9.9-test",
                    "--dist",
                    dist_dir,
                    "--out",
                    out_dir,
                    "--allow-unsigned",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={k: v for k, v in os.environ.items() if not k.startswith("GA_LAUNCHER_UPDATE_")},
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            release_dir = os.path.join(out_dir, "9.9.9-test", "update")
            manifest_sig = os.path.join(release_dir, "manifest.sig")
            self.assertFalse(os.path.exists(manifest_sig), msg="unsigned local build should not leave manifest.sig behind")
            with open(os.path.join(release_dir, "sha256sums.txt"), "r", encoding="utf-8") as f:
                sums = f.read()
            self.assertNotIn("manifest.sig", sums)

    def test_release_build_without_private_key_fails(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with tempfile.TemporaryDirectory() as td:
            dist_dir = self._make_dist_tree(td)
            out_dir = os.path.join(td, "release")
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/build_release_bundle.py",
                    "--version",
                    "9.9.9-test",
                    "--dist",
                    dist_dir,
                    "--out",
                    out_dir,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={k: v for k, v in os.environ.items() if not k.startswith("GA_LAUNCHER_UPDATE_")},
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("update signing key is missing", result.stderr or result.stdout)
