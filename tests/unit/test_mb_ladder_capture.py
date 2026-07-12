"""Unit tests for prototypes/mb_ladder_capture/ladder_capture.py (hand-over
prototype). Network-free: injected fetcher, injected clocks, tmp sink."""

import asyncio
import json

import pytest

from prototypes.mb_ladder_capture.ladder_capture import (
    CooldownGate, LadderCapture, LadderConfig, book_record, trim_book,
)


# ── pure core ────────────────────────────────────────────────────────────────
def test_trim_book_sorts_filters_and_truncates():
    raw = [
        {"price": "0.55", "size": "100"},
        {"price": "0.60", "size": "50"},
        {"price": "bad", "size": "1"},          # skipped
        {"price": "1.5", "size": "10"},          # out of (0,1) — skipped
        {"price": "0.50", "size": "0"},          # zero size — skipped
        {"price": "0.58", "size": "25"},
    ]
    asks = trim_book(raw, depth=2, side="asks")
    assert [l["price"] for l in asks] == [0.55, 0.58]          # asc, top-2
    bids = trim_book(raw, depth=3, side="bids")
    assert [l["price"] for l in bids] == [0.60, 0.58, 0.55]    # desc


def test_book_record_best_and_spread():
    rec = book_record(
        "tok1", "OK",
        bids=[{"price": 0.54, "size": 10}],
        asks=[{"price": 0.56, "size": 20}],
        meta={"trader": "0xabc"}, ts=1000.0, capture_lag_s=0.1234,
    )
    assert rec["best_bid"] == 0.54 and rec["best_ask"] == 0.56
    assert rec["spread"] == pytest.approx(0.02)
    assert rec["capture_lag_s"] == 0.123
    assert json.loads(json.dumps(rec))["meta"]["trader"] == "0xabc"


def test_book_record_empty_sides_yield_none_spread():
    rec = book_record("t", "NO_BOOK", [], [], None, 0.0, 0.0)
    assert rec["best_bid"] is None and rec["spread"] is None


def test_cooldown_gate_blocks_within_window():
    t = {"now": 100.0}
    gate = CooldownGate(10.0, clock=lambda: t["now"])
    assert gate.allow("tok")
    t["now"] += 5.0
    assert not gate.allow("tok")       # inside cooldown
    assert gate.allow("other")         # per-token, not global
    t["now"] += 6.0
    assert gate.allow("tok")           # window elapsed


# ── async shell ──────────────────────────────────────────────────────────────
def _cfg(tmp_path, **kw):
    return LadderConfig(sink_path=str(tmp_path / "ladders.jsonl"), **kw)


@pytest.mark.asyncio
async def test_capture_writes_jsonl_and_counts(tmp_path):
    async def fake_fetch(token_id, timeout_s):
        return {"bids": [{"price": "0.40", "size": "5"}],
                "asks": [{"price": "0.42", "size": "7"}]}

    lc = LadderCapture(_cfg(tmp_path), fetch_book=fake_fetch)
    lc.start()
    assert lc.submit("tok9", "OK", {"detect_lag_s": 2.5})
    await asyncio.wait_for(lc._q.join(), timeout=2)
    await lc.stop()

    lines = (tmp_path / "ladders.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["token_id"] == "tok9" and rec["reason"] == "OK"
    assert rec["best_ask"] == 0.42 and rec["meta"]["detect_lag_s"] == 2.5
    assert lc.stats.captured == 1 and lc.stats.book_missing == 0


@pytest.mark.asyncio
async def test_missing_book_recorded_not_fatal(tmp_path):
    async def dead_fetch(token_id, timeout_s):
        return None

    lc = LadderCapture(_cfg(tmp_path), fetch_book=dead_fetch)
    lc.start()
    lc.submit("tokX", "OK")
    await asyncio.wait_for(lc._q.join(), timeout=2)
    await lc.stop()

    rec = json.loads((tmp_path / "ladders.jsonl").read_text())
    assert rec["reason"].endswith("book_missing") and rec["bids"] == []
    assert lc.stats.book_missing == 1 and lc.stats.captured == 0


@pytest.mark.asyncio
async def test_cooldown_dedups_submissions(tmp_path):
    async def fake_fetch(token_id, timeout_s):
        return {"bids": [], "asks": [{"price": "0.50", "size": "1"}]}

    lc = LadderCapture(_cfg(tmp_path, cooldown_s=60.0), fetch_book=fake_fetch)
    lc.start()
    assert lc.submit("tok", "OK")
    assert not lc.submit("tok", "OK")          # cooldown skip, not queued
    await asyncio.wait_for(lc._q.join(), timeout=2)
    await lc.stop()
    assert lc.stats.cooldown_skips == 1
    assert len((tmp_path / "ladders.jsonl").read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_full_queue_drops_loudly_never_blocks(tmp_path):
    logged = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(token_id, timeout_s):
        started.set()
        await release.wait()
        return None

    lc = LadderCapture(
        _cfg(tmp_path, queue_max=1, cooldown_s=0.0),
        fetch_book=slow_fetch, log=logged.append,
    )
    lc.start()
    lc.submit("t1", "OK")
    await asyncio.wait_for(started.wait(), timeout=2)  # worker busy on t1
    lc.submit("t2", "OK")                              # fills queue (max 1)
    assert not lc.submit("t3", "OK")                   # drop — instant, no block
    assert lc.stats.queue_drops == 1
    assert any("DROP" in m for m in logged)
    release.set()
    await asyncio.wait_for(lc._q.join(), timeout=2)
    await lc.stop()


def test_from_env_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIRROR3_LADDER_CAPTURE", raising=False)
    assert LadderCapture.from_env() is None
    monkeypatch.setenv("MIRROR3_LADDER_CAPTURE", "false")
    assert LadderCapture.from_env() is None


def test_from_env_enabled_reads_config(monkeypatch, tmp_path):
    monkeypatch.setenv("MIRROR3_LADDER_CAPTURE", "true")
    monkeypatch.setenv("MIRROR3_LADDER_PATH", str(tmp_path / "x.jsonl"))
    monkeypatch.setenv("MIRROR3_LADDER_DEPTH", "7")
    lc = LadderCapture.from_env()
    assert lc is not None and lc.cfg.depth == 7
    assert lc.cfg.sink_path.endswith("x.jsonl")
