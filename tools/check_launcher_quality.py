from __future__ import annotations

import argparse
import os
import subprocess
import sys


MACOS_PREFLIGHT_RUFF_TARGETS = (
    "launcher.py",
    "launcher_core_parts/runtime.py",
    "tools/build_macos_release.py",
    "tools/check_launcher_quality.py",
    "tools/validate_macos_release.py",
    "tests/test_build_macos_release.py",
    "tests/test_validate_macos_release.py",
    "tests/test_launcher_core_behaviors.py",
)

MACOS_PREFLIGHT_PYTEST_TARGETS = (
    "tests/test_build_macos_release.py",
    "tests/test_validate_macos_release.py",
    "tests/test_launcher_core_behaviors.py",
)


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(cmd: list[str], *, cwd: str) -> None:
    print(f"+ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(result.returncode or 1)


def run_macos_preflight() -> None:
    root = _repo_root()
    _run([sys.executable, "-m", "ruff", "check", *MACOS_PREFLIGHT_RUFF_TARGETS], cwd=root)
    _run([sys.executable, "-m", "pytest", *MACOS_PREFLIGHT_PYTEST_TARGETS, "-q"], cwd=root)


def _parse_args():
    parser = argparse.ArgumentParser(description="Run focused launcher quality gates before packaging")
    parser.add_argument(
        "--scope",
        default="macos-preflight",
        choices=("macos-preflight",),
        help="Focused quality gate scope to execute",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.scope == "macos-preflight":
        run_macos_preflight()
        return 0
    raise SystemExit(f"unsupported scope: {args.scope}")


if __name__ == "__main__":
    raise SystemExit(main())
