"""Deterministic Build-phase stages: test runner, security scan, risk tiers."""
from __future__ import annotations

import asyncio

import pytest

from app.knowledge import store
from app.orchestration import risk
from app.orchestration.security_stage import run_security_scan
from app.orchestration.test_stage import run_repo_tests


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "knowledge-test.db")


def run(coro):
    return asyncio.run(coro)


# ---------- automated test stage ----------
def test_repo_tests_pass_on_clean_pytest_suite(tmp_path):
    (tmp_path / "convert.py").write_text("def c2f(c):\n    return c * 9 / 5 + 32\n")
    (tmp_path / "test_convert.py").write_text(
        "from convert import c2f\n\ndef test_freezing():\n    assert c2f(0) == 32\n"
    )
    report = run(run_repo_tests(tmp_path))
    assert report.passed and "pytest" in report.ran


def test_repo_tests_fail_on_broken_assertion(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_wrong():\n    assert 1 == 2\n")
    report = run(run_repo_tests(tmp_path))
    assert not report.passed
    assert "assert 1 == 2" in report.output


def test_repo_tests_catch_syntax_error(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n")
    report = run(run_repo_tests(tmp_path))
    assert not report.passed and "syntax" in report.ran[0]


def test_repo_without_tests_passes_on_syntax_alone(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    report = run(run_repo_tests(tmp_path))
    assert report.passed and report.ran == ["python syntax check"]


# ---------- security stage ----------
def test_security_scan_blocks_planted_secret(tmp_path):
    (tmp_path / "config.py").write_text('API = "gsk_' + "a1B2c3D4e5F6g7H8i9J0k1L2" + '"\n')
    report = run(run_security_scan(tmp_path))
    assert not report.passed
    assert report.blocking[0]["kind"] == "Groq API key"
    # the finding must never echo the full secret back
    assert "a1B2c3D4e5F6g7H8i9J0k1L2" not in str(report.blocking)


def test_security_scan_blocks_hardcoded_password(tmp_path):
    (tmp_path / "settings.py").write_text('password = "hunter2secret"\n')
    report = run(run_security_scan(tmp_path))
    assert not report.passed


def test_security_scan_clean_repo_passes(tmp_path):
    (tmp_path / "main.py").write_text("import os\nkey = os.environ.get('API_KEY')\n")
    report = run(run_security_scan(tmp_path))
    assert report.passed and report.blocking == []


# ---------- risk tiers ----------
DOCS_TICKET = {"id": "T-1", "title": "Update the README documentation"}
AUTH_TICKET = {"id": "T-2", "title": "Add login token validation"}
LOGIC_TICKET = {"id": "T-3", "title": "Compute the running average"}


def seed_outcomes(category: str, results: list[str]):
    for r in results:
        run(store.record_outcome("p", {"id": "t", "title": "x"}, category, "developer", "review", r))


def test_security_category_always_tier2():
    seed_outcomes("security", ["pass"] * 10)
    a = run(risk.assess_ticket(AUTH_TICKET))
    assert a.tier == 2 and a.category == "security"


def test_docs_with_clean_history_reaches_tier0():
    seed_outcomes("docs", ["pass"] * 5)
    a = run(risk.assess_ticket(DOCS_TICKET))
    assert a.tier == 0


def test_docs_without_history_capped_at_tier1():
    # Tier 0 must be earned: no track record means Tier 1 at best.
    a = run(risk.assess_ticket(DOCS_TICKET))
    assert a.tier == 1


def test_blocking_security_finding_forces_tier2_everywhere():
    a = run(risk.assess_ticket(DOCS_TICKET, security_blocking=1))
    assert a.tier == 2 and a.score == 100


def test_failing_tests_force_tier2():
    a = run(risk.assess_ticket(DOCS_TICKET, tests_failed=True))
    assert a.tier == 2


def test_rising_failure_rate_auto_downgrades():
    seed_outcomes("docs", ["pass", "fail", "fail", "pass", "fail"])
    a = run(risk.assess_ticket(DOCS_TICKET))
    assert a.tier == 2
    assert any("auto-downgrade" in r for r in a.reasons)


def test_non_eligible_category_never_autonomous():
    seed_outcomes("logic", ["pass"] * 10)
    a = run(risk.assess_ticket(LOGIC_TICKET))
    assert a.tier == 2
    assert any("not autonomy-eligible" in r for r in a.reasons)
