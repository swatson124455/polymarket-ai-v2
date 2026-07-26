"""MULTI-CYCLE CHAOS — the stress test the single-pass suites do NOT provide.

WHY THIS EXISTS: test_stress.py fuzzes the INPUTS of one function and runs in under a second.
smoke_dryrun.py runs ONE cycle. Neither exercises anything that only goes wrong ACROSS cycles or
UNDER FAULT: state persisted between runs, drop-grace actually expiring, the score cache
accumulating and decaying, order lifecycle (create -> amend -> cancel -> fill), venue failures,
rate limiting, partial fills, and the emergency paths. The bot runs as a fresh PROCESS every two
minutes, so cross-cycle behaviour lives entirely in files on disk — the least tested surface there
is, and the one where a bug persists rather than passes.

Each test drives run_once REPEATEDLY against a fault-injecting client, then asserts invariants
that must hold over the whole run, not just one pass.
"""
import json
import os
import random

import pytest

from test_live_hardening import q, MockClient


BOOK = {"yes_dollars": [["0.50", "600"], ["0.49", "500"]],
        "no_dollars": [["0.49", "600"], ["0.48", "500"]]}


class ChaosClient(MockClient):
    """MockClient that fails the way a real venue fails: intermittently, and differently each call."""

    def __init__(self, rng, fail_rate=0.25, **kw):
        super().__init__(**kw)
        self.rng = rng
        self.fail_rate = fail_rate
        self.calls = {}
        self.next_oid = 0

    def _maybe_fail(self, what):
        self.calls[what] = self.calls.get(what, 0) + 1
        if self.rng.random() < self.fail_rate:
            raise RuntimeError(f"chaos: {what} failed (429/503/timeout)")

    def get_orders(self, status="resting"):
        self._maybe_fail("get_orders")
        return super().get_orders(status)

    def get_positions(self):
        self._maybe_fail("get_positions")
        return super().get_positions()

    def get_balance(self):
        self._maybe_fail("get_balance")
        return super().get_balance()

    def cancel_order(self, oid):
        self._maybe_fail("cancel")
        return super().cancel_order(oid)

    def create_quote(self, ticker, side, price, count, post_only=True, client_order_id=None):
        self._maybe_fail("create")
        self.next_oid += 1
        oid = f"chaos-{self.next_oid}"
        self.created.append({"ticker": ticker, "side": side, "price": price, "count": count})
        # the order now RESTS, so the next cycle sees it and must diff against it
        self._resting.append({
            "order_id": oid, "ticker": ticker, "outcome_side": side,
            f"{side}_price_dollars": f"{price:.4f}", "remaining_count_fp": f"{count:.2f}"})
        return {"order": {"order_id": oid}}

    def amend_quote(self, order_id, ticker, outcome, price_dollars, count, client_order_id=None):
        self._maybe_fail("amend")
        for o in self._resting:
            if o["order_id"] == order_id:
                o["remaining_count_fp"] = f"{count:.2f}"
        return {"order": {"order_id": order_id}}


def _cfg_chaos(monkeypatch, tmpdir, *, footprint, cap=250.0, gate=1, grace=3, rank=1, amend=1):
    monkeypatch.setattr(q, "DATA_DIR", tmpdir)
    monkeypatch.setattr(q, "STATE_FILE", os.path.join(tmpdir, "state.json"))
    monkeypatch.setattr(q, "STOP_FILE", os.path.join(tmpdir, "STOP"))
    monkeypatch.setattr(q, "LOCK_FILE", os.path.join(tmpdir, "lock"))
    monkeypatch.setattr(q, "SCORE_PATH", os.path.join(tmpdir, "scores.json"))
    monkeypatch.setattr(q, "MAX_TOTAL_CAPITAL", cap)
    monkeypatch.setattr(q, "MAX_MARKET_CAPITAL", 15.0)
    monkeypatch.setattr(q, "JOIN_SIZE", 20)
    monkeypatch.setattr(q, "PRESENCE_GATE", gate)
    monkeypatch.setattr(q, "DROP_GRACE", grace)
    monkeypatch.setattr(q, "SCORE_RANK", rank)
    monkeypatch.setattr(q, "SCORES", {})
    monkeypatch.setattr(q, "AMEND_DECREASE", amend)
    monkeypatch.setattr(q, "MAX_DAYS_TO_CLOSE", 0.0)
    monkeypatch.setattr(q, "public_get", lambda p: (
        {"incentive_programs": [], "next_cursor": ""} if "incentive" in p
        else {"orderbook_fp": BOOK}))
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: footprint)


def _mk(t, pool=100.0):
    return {"ticker": t, "usd_day": pool, "target": 1, "end": "2099-01-01T00:00:00+00:00",
            "life_min": 10080.0, "df": 0.5}


def _run_cycles(monkeypatch, client, tmpdir, n):
    rows = []
    orig = q.KalshiOrderClient
    q.KalshiOrderClient = lambda *a, **k: client
    try:
        for _ in range(n):
            try:
                q.run_once()
            except Exception as e:      # a cycle must NEVER escape — that is the headline invariant
                pytest.fail(f"run_once raised on cycle {_}: {e!r}")
    finally:
        q.KalshiOrderClient = orig
    for f in sorted(os.listdir(tmpdir)):
        if f.startswith("plans-"):
            for l in open(os.path.join(tmpdir, f)):
                if l.strip():
                    rows.append(json.loads(l))
    return rows


# ---------------------------------------------------------------------------------------------
def test_fifty_cycles_under_25pct_venue_failure_never_crash(monkeypatch, tmp_path):
    """THE HEADLINE. A quarter of every venue call fails, for 50 consecutive cycles, with all
    flags on. No cycle may raise, and every cycle must still emit a plan row."""
    d = str(tmp_path)
    fp = [_mk(f"CHAOS-{i}") for i in range(12)]
    _cfg_chaos(monkeypatch, d, footprint=fp)
    c = ChaosClient(random.Random(1234), fail_rate=0.25)
    rows = _run_cycles(monkeypatch, c, d, 50)
    assert len(rows) == 50, f"every cycle must leave a plan row, got {len(rows)}"
    assert c.calls.get("create", 0) > 0, "the fixture must have exercised the order path"
    # failures must be COUNTED, never lost
    assert sum(r.get("create_fail", 0) + r.get("cancel_fail", 0) for r in rows) > 0, \
        "25% injected failure produced zero counted failures — the counters are lying"


def test_capital_cap_holds_across_every_cycle_under_chaos(monkeypatch, tmp_path):
    """The cap must bind on EVERY cycle, not on average. A cap that leaks once is a cap that
    leaks — and committed capital is the number that stops us over-committing real money."""
    d = str(tmp_path)
    fp = [_mk(f"CAP-{i}") for i in range(20)]
    _cfg_chaos(monkeypatch, d, footprint=fp, cap=60.0)
    rows = _run_cycles(monkeypatch, ChaosClient(random.Random(7), fail_rate=0.2), d, 30)
    for i, r in enumerate(rows):
        assert (r.get("est_capital_usd") or 0) <= 60.0 + 1e-6, \
            f"cycle {i}: est capital {r.get('est_capital_usd')} exceeded the $60 cap"


def test_state_survives_and_grace_expires_across_cycles(monkeypatch, tmp_path):
    """Cross-cycle state is written to disk and re-read by a FRESH process each run. Drop-grace
    must count up and then RELEASE — a grace that never expires pins capital in a market we
    stopped choosing."""
    d = str(tmp_path)
    fp = [_mk("KEEP-1"), _mk("KEEP-2")]
    _cfg_chaos(monkeypatch, d, footprint=fp, grace=2, rank=0, amend=0)
    c = ChaosClient(random.Random(3), fail_rate=0.0)
    _run_cycles(monkeypatch, c, d, 3)                 # build a resting book
    # now the footprint collapses — both tickers rotate out entirely
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [])
    rows = _run_cycles(monkeypatch, c, d, 6)
    tail = rows[3:]
    retained = [r.get("grace_retained", 0) for r in tail]
    assert max(retained) > 0, "grace never engaged when the footprint collapsed"
    assert retained[-1] == 0, f"grace never released: {retained}"
    st = json.load(open(os.path.join(d, "state.json")))
    assert isinstance(st.get("drop_grace", {}), dict)


def test_score_cache_persists_and_stays_bounded(monkeypatch, tmp_path):
    """The score file is read and rewritten every cycle by a fresh process. It must persist, and
    it must not grow without bound — an ever-growing cache is a slow disk/memory leak."""
    d = str(tmp_path)
    fp = [_mk(f"S-{i}") for i in range(8)]
    _cfg_chaos(monkeypatch, d, footprint=fp)
    _run_cycles(monkeypatch, ChaosClient(random.Random(11), fail_rate=0.15), d, 20)
    p = os.path.join(d, "scores.json")
    assert os.path.exists(p), "score cache was never written"
    data = json.load(open(p))
    assert data.get("schema") == 1
    assert 0 < len(data["markets"]) <= 8, "cache should hold at most the markets we saw"
    for row in data["markets"].values():
        assert row["ts"] > 0 and "capture" in row


def test_no_duplicate_orders_on_the_same_ticker_side_price(monkeypatch, tmp_path):
    """THE ONE THAT COSTS MONEY. Orders rest between cycles; the diff must recognise them. If it
    does not, every cycle stacks another order on the same level and exposure multiplies silently
    — the exact failure the swallowed 'unreadable standing order' handler could cause."""
    d = str(tmp_path)
    fp = [_mk(f"DUP-{i}") for i in range(6)]
    _cfg_chaos(monkeypatch, d, footprint=fp, grace=0, amend=0)
    c = ChaosClient(random.Random(5), fail_rate=0.0)     # NO chaos: pure diff correctness
    _run_cycles(monkeypatch, c, d, 15)
    seen = {}
    for o in c._resting:
        k = (o["ticker"], o["outcome_side"], o[f"{o['outcome_side']}_price_dollars"])
        seen[k] = seen.get(k, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, f"duplicate resting orders stacked across cycles: {dupes}"


def test_stop_sentinel_halts_within_one_cycle_even_under_chaos(monkeypatch, tmp_path):
    """The emergency brake must work when the venue is also misbehaving — that is exactly when it
    gets used."""
    d = str(tmp_path)
    fp = [_mk(f"STOP-{i}") for i in range(6)]
    _cfg_chaos(monkeypatch, d, footprint=fp)
    c = ChaosClient(random.Random(9), fail_rate=0.3)
    _run_cycles(monkeypatch, c, d, 5)
    before = len(c.created)
    open(os.path.join(d, "STOP"), "w").close()
    _run_cycles(monkeypatch, c, d, 5)
    assert len(c.created) == before, "orders were created AFTER the STOP sentinel appeared"


def test_plan_row_is_always_written_even_when_everything_fails(monkeypatch, tmp_path):
    """Bookkeeping runs in a `finally`. If the plan row can go missing under load we lose the only
    record of what the bot did — and every audit in this lane reads those rows."""
    d = str(tmp_path)
    fp = [_mk(f"F-{i}") for i in range(5)]
    _cfg_chaos(monkeypatch, d, footprint=fp)
    rows = _run_cycles(monkeypatch, ChaosClient(random.Random(2), fail_rate=0.9), d, 25)
    assert len(rows) == 25, f"only {len(rows)}/25 plan rows survived 90% failure"
    for r in rows:
        assert r.get("ts")
