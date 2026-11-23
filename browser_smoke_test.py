#!/usr/bin/env python3
"""Browser smoke test runner for homework-submissions SQLite v2.

Executes the real Playwright headless Chromium end-to-end verification flow,
covering student group unlocking, student identity distinction, assignment selection,
file uploads, admin review status updates, and visual evidence screenshot generation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_dir = Path(__file__).resolve().parent
    print("🚀 Starting SQLite v2 Playwright browser smoke test...")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(repo_dir / "tests/audit/test_browser_flow.py"),
        "-v",
        "-s",
    ]
    res = subprocess.run(cmd, cwd=str(repo_dir), check=False)
    if res.returncode == 0:
        print("✅ Browser smoke test completed successfully!")
    else:
        print("❌ Browser smoke test failed.")
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
