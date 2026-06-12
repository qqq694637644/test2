"""Install the template project package from a GitHub Actions Python step."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PIP_PROXY = "http://192.168.1.249:10810"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-e",
        ".[dev]",
        "--proxy",
        PIP_PROXY,
    ]
    print("Installing project package:", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=str(repo_root()), check=False)
    return int(completed.returncode)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
