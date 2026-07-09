"""Isolated Debugger verification — no Scope/Architect/Planner/Developer calls.

Sets up in workspace/test-debugger-iso/repo/ (via setup done manually before
this script) a real git repo on branch ticket/T-001 with a genuinely buggy
temp_convert.py (wrong Celsius->Fahrenheit formula), then calls
`debugger.run()` directly with a realistic PipelineState and test_feedback
describing the real, reproducible bug. This proves the Debugger's tool loop
(read_file/write_file/git_commit/run_command/submit_fix) works without
burning quota re-running the expensive earlier stages on every attempt.

Run from backend/:  .venv/Scripts/python.exe -m scripts.test_debugger_isolated
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

from app.agents import debugger  # noqa: E402
from app.config import get_settings  # noqa: E402

PROJECT_ID = "test-debugger-iso"

TICKET = {
    "id": "T-001",
    "title": "Create project skeleton and runnable entry point",
    "description": "A single-file Python CLI, temp_convert.py, converting a temperature between Celsius and Fahrenheit.",
    "acceptance_criteria": ["python temp_convert.py <value> <unit> prints the converted value"],
    "test_checklist": ["Run `python temp_convert.py 100 C` and confirm it prints 212.00"],
    "dependencies": [],
    "effort": "S",
    "status": "failed",
}

STATE = {
    "project_id": PROJECT_ID,
    "architecture_doc": "Single-file Python 3 stdlib-only CLI (temp_convert.py) with argparse, validation, and Celsius<->Fahrenheit conversion functions.",
    "branch": "ticket/T-001",
    "test_feedback": (
        "Running `python temp_convert.py 100 C` prints 87.56, but the correct Fahrenheit "
        "value for 100C is 212.00. The Celsius-to-Fahrenheit conversion looks wrong."
    ),
}


async def main() -> None:
    repo = get_settings().workspace_dir / PROJECT_ID / "repo"
    before = (repo / "temp_convert.py").read_text(encoding="utf-8")

    summary = await debugger.run(STATE, TICKET)
    print("\nDEBUGGER SUMMARY:", summary)

    after = (repo / "temp_convert.py").read_text(encoding="utf-8")
    print("\nFILE CHANGED:", before != after)

    result = subprocess.run(
        [sys.executable, "temp_convert.py", "100", "C"], cwd=repo, capture_output=True, text=True
    )
    print("RUNTIME CHECK: python temp_convert.py 100 C ->", result.stdout.strip(), "| stderr:", result.stderr.strip())
    print("BUG FIXED:", result.stdout.strip() == "212.00")

    log = subprocess.run(["git", "log", "--oneline", "-5"], cwd=repo, capture_output=True, text=True)
    print("\nGIT LOG:\n" + log.stdout)


if __name__ == "__main__":
    asyncio.run(main())
