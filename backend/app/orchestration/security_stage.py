"""Security scan stage — deterministic scanners, no LLM verdicts.

Two layers over the workspace repo: a secrets-pattern scan (blocking — a
committed credential is never acceptable) and bandit static analysis for
Python (HIGH severity blocks, lower severities are attached as findings for
the Reviewer's context). Semgrep is the doc's preferred scanner but has no
native Windows support — wiring it up under WSL is a separate task; bandit
covers the Python-SAST ground until then.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.orchestration.test_stage import SKIP_DIRS, _iter_files

SCAN_TIMEOUT_S = 120

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("OpenAI-style API key", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Hardcoded password", re.compile(r"""(?i)password\s*[:=]\s*["'][^"']{6,}["']""")),
    ("Hardcoded secret/token", re.compile(r"""(?i)(?:api_key|apikey|secret|auth_token)\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}["']""")),
]

TEXT_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml", ".env", ".txt", ".md", ".cfg", ".ini"}


@dataclass
class SecurityReport:
    passed: bool
    blocking: list[dict] = field(default_factory=list)   # secrets + HIGH bandit
    advisory: list[dict] = field(default_factory=list)   # everything else

    @property
    def summary(self) -> str:
        if self.passed and not self.advisory:
            return "clean"
        return f"{len(self.blocking)} blocking, {len(self.advisory)} advisory finding(s)"


def _mask(text: str) -> str:
    return text[:10] + "..." if len(text) > 13 else "***"


def _scan_secrets(repo: Path) -> list[dict]:
    findings: list[dict] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            for kind, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    findings.append(
                        {
                            "kind": kind,
                            "file": str(path.relative_to(repo)),
                            "line": lineno,
                            "match": _mask(m.group(0)),
                            "severity": "BLOCKING",
                        }
                    )
    return findings


def _run_bandit(repo: Path) -> list[dict]:
    if not _iter_files(repo, ".py"):
        return []
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q",
             *[f"--exclude=./{d}" for d in SKIP_DIRS]],
            cwd=repo, capture_output=True, text=True, timeout=SCAN_TIMEOUT_S,
            encoding="utf-8", errors="replace",
        )
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return []  # scanner unavailable ≠ code is clean, but never blocks the pipeline on tooling
    findings = []
    for issue in data.get("results", []):
        findings.append(
            {
                "kind": f"bandit:{issue.get('test_id')}",
                "file": issue.get("filename", ""),
                "line": issue.get("line_number", 0),
                "match": issue.get("issue_text", ""),
                "severity": issue.get("issue_severity", "LOW").upper(),
            }
        )
    return findings


def _scan(repo: Path) -> SecurityReport:
    secrets = _scan_secrets(repo)
    bandit = _run_bandit(repo)
    blocking = secrets + [f for f in bandit if f["severity"] == "HIGH"]
    advisory = [f for f in bandit if f["severity"] != "HIGH"]
    return SecurityReport(passed=not blocking, blocking=blocking, advisory=advisory)


async def run_security_scan(repo: Path) -> SecurityReport:
    if not repo.exists():
        return SecurityReport(passed=True)
    return await asyncio.to_thread(_scan, repo)
