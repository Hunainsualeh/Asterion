"""Automated Test stage — deterministic, no LLM in the loop.

Detects what the generated repo can actually run (pytest suites, an npm test
script, or at minimum a full Python syntax compile) and runs it. Failures
route to the Debugger with the real output; the LLM's job is fixing, not
deciding whether tests passed. Everything runs with a hard timeout and cwd
pinned inside the project's sandboxed workspace repo.
"""
from __future__ import annotations

import asyncio
import py_compile
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next"}
OUTPUT_TAIL = 4000
TEST_TIMEOUT_S = 180


@dataclass
class TestReport:
    passed: bool
    ran: list[str] = field(default_factory=list)
    output: str = ""

    @property
    def summary(self) -> str:
        what = ", ".join(self.ran) or "nothing runnable"
        return f"{'PASS' if self.passed else 'FAIL'} ({what})"


def _iter_files(repo: Path, suffix: str) -> list[Path]:
    return [
        p for p in repo.rglob(f"*{suffix}")
        if not any(part in SKIP_DIRS for part in p.parts)
    ]


def _syntax_check(repo: Path) -> tuple[bool, str]:
    errors: list[str] = []
    for py in _iter_files(repo, ".py"):
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(str(exc))
    return not errors, "\n".join(errors)


def _has_pytest_suite(repo: Path) -> bool:
    if (repo / "tests").is_dir() or (repo / "test").is_dir():
        return True
    return any(
        p.name.startswith("test_") or p.name.endswith("_test.py")
        for p in _iter_files(repo, ".py")
    )


def _npm_test_script(repo: Path) -> bool:
    pkg = repo / "package.json"
    if not pkg.exists():
        return False
    import json

    try:
        script = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}).get("test", "")
    except (json.JSONDecodeError, OSError):
        return False
    return bool(script) and "no test specified" not in script


def _run(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=TEST_TIMEOUT_S,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TEST_TIMEOUT_S}s: {' '.join(cmd)}"
    except OSError as exc:
        return False, f"could not run {' '.join(cmd)}: {exc}"
    out = (proc.stdout + "\n" + proc.stderr)[-OUTPUT_TAIL:]
    return proc.returncode == 0, out


def _run_all(repo: Path) -> TestReport:
    report = TestReport(passed=True)

    ok, errors = _syntax_check(repo)
    report.ran.append("python syntax check")
    if not ok:
        return TestReport(passed=False, ran=report.ran, output=errors[-OUTPUT_TAIL:])

    if _has_pytest_suite(repo):
        ok, out = _run([sys.executable, "-m", "pytest", "-q", "--no-header"], repo)
        report.ran.append("pytest")
        if not ok:
            return TestReport(passed=False, ran=report.ran, output=out)

    if _npm_test_script(repo):
        npm = shutil.which("npm")
        if npm:
            ok, out = _run([npm, "test", "--silent"], repo)
            report.ran.append("npm test")
            if not ok:
                return TestReport(passed=False, ran=report.ran, output=out)

    return report


async def run_repo_tests(repo: Path) -> TestReport:
    if not repo.exists() or not any(repo.iterdir()):
        return TestReport(passed=True, ran=["empty repo — nothing to test"])
    return await asyncio.to_thread(_run_all, repo)
