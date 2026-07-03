"""Runner startup regression (the bug class that broke the 2026-07-02 VPS run).

mirror_scoring_run.py crashed at startup with AttributeError: 'Database' object
has no attribute 'initialize' — the runner's DB lifecycle had never been
executed by any test (the 45 package tests all stub below the runner). These
tests run main() end-to-end against a stubbed Database so the startup path,
fail-fast ordering, report writing, and exit codes are locked. A future API
mismatch (init/close/get_session) or fail-fast regression breaks HERE instead
of on the VPS.
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

import scripts.mirror_scoring_run as runner


class _StubResult:
    def fetchall(self):
        return []


class _StubSession:
    async def execute(self, *_a, **_k):
        return _StubResult()


class _StubSessionCtx:
    async def __aenter__(self):
        return _StubSession()

    async def __aexit__(self, *exc):
        return False


class StubDatabase:
    """Mimics the exact Database surface the runner + package use:
    init() / close() / get_session() / session_factory. NOT initialize() —
    that was the crash. init() sets session_factory (like the real class);
    set fail_init=True to emulate the real init()'s never-raises failure mode
    (session_factory stays None)."""

    fail_init = False

    def __init__(self):
        self.inited = False
        self.closed = False
        self.session_factory = None

    async def init(self):
        self.inited = True
        if not self.fail_init:
            self.session_factory = object()

    async def close(self):
        self.closed = True

    def get_session(self):
        return _StubSessionCtx()


@pytest.fixture()
def stub_db(monkeypatch, tmp_path):
    created: list[StubDatabase] = []

    def _factory():
        db = StubDatabase()
        created.append(db)
        return db

    monkeypatch.setattr(runner, "Database", _factory)
    monkeypatch.chdir(tmp_path)  # REPORT_DIR is cwd-relative
    return created


def _run(argv: list[str]) -> int:
    old = sys.argv
    sys.argv = ["mirror_scoring_run.py"] + argv
    try:
        return asyncio.run(runner.main())
    finally:
        sys.argv = old


class TestRunnerStartup:
    def test_stage_q_empty_universe_exits_0_and_writes_report(self, stub_db, tmp_path):
        rc = _run(["--stage", "q", "--cutoff", "2026-05-15T00:00:00"])
        assert rc == 0
        db = stub_db[0]
        assert db.inited, "runner must call db.init() (NOT initialize())"
        assert db.closed, "runner must close the db in finally"
        reports = list((tmp_path / "scoring_reports").glob("mirror_scoring_q_*.json"))
        assert len(reports) == 1
        body = json.loads(reports[0].read_text())
        assert body["n_scored"] == 0 and body["label"] == "UNVERIFIED"

    def test_validate_without_cutoff_exits_2_BEFORE_any_db_work(self, stub_db):
        """2026-07-02 audit blocker: this exact invocation previously ran the
        full universe scan before failing. Must now exit pre-DB."""
        rc = _run(["--stage", "validate"])
        assert rc == 2
        assert stub_db == [], "cutoff check must run before Database() is built"

    def test_tz_aware_cutoff_normalized_no_typeerror(self, stub_db):
        """Audit blocker F4: aware cutoff used to TypeError against naive
        resolved_at AFTER the full fetch. Now normalized at parse."""
        rc = _run(["--stage", "q", "--cutoff", "2026-05-15T00:00:00+00:00"])
        assert rc == 0

    def test_bad_iso_cutoff_fails_fast_pre_db(self, stub_db):
        with pytest.raises(ValueError):
            _run(["--stage", "q", "--cutoff", "not-a-date"])
        assert stub_db == [], "bad cutoff must fail before Database() is built"

    def test_init_failure_exits_3_with_message(self, stub_db, monkeypatch, capsys):
        """Audit caveat db-3/RUN-4: Database.init() never raises; the runner
        must fail fast on session_factory=None instead of dying downstream."""
        monkeypatch.setattr(StubDatabase, "fail_init", True)
        rc = _run(["--stage", "q", "--cutoff", "2026-05-15T00:00:00"])
        assert rc == 3
        assert "DATABASE_URL" in capsys.readouterr().err
        assert stub_db[0].closed  # finally must still close

    def test_db_lifecycle_names_locked(self):
        """Source-level lock against the initialize() regression."""
        import inspect

        src = inspect.getsource(runner)
        assert "db.init()" in src
        assert "db.initialize()" not in src
