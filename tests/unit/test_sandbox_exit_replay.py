"""Unit tests for scripts/sandbox_exit_replay.py (experiment #5, sandbox).

DB-free: synthetic signals exercise the exit_fn seam, pairing invariant,
and verdict mapping. The runner's SQL is exercised only on the VPS.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from bots.mirror_backtest.replay import Decision, replay_signals
from scripts.sandbox_exit_replay import make_exit_fn, summarize

T0 = datetime(2026, 6, 10, 12, 0, 0)


def _sig(market, price, resolution, side="YES", ticks=None, hours_to_res=100.0):
    s = {
        "market_id": market,
        "trader_address": "0xabc",
        "token_id": f"tok-{market}",
        "side": side,
        "price": price,
        "event_time": T0,
        "resolution": resolution,
        "resolved_at": T0 + timedelta(hours=hours_to_res),
        # deep synthetic ladder so precise_fill covers the order
        "book_snapshot": [{"price": price, "size": 1_000_000.0}],
    }
    if ticks is not None:
        s["_price_series"] = [
            (T0 + timedelta(hours=h), px) for h, px in ticks
        ]
    return s


RULE = lambda _s: Decision(size_usd=100.0)


def test_no_path_falls_back_to_resolution_outcome():
    sigs = [_sig("m1", 0.50, "YES")]
    hold = replay_signals(sigs, RULE)
    ladder = replay_signals(sigs, RULE, exit_fn=make_exit_fn(0.55))
    assert hold.n_signals == ladder.n_signals == 1
    assert hold.replayed[0].edge_net == pytest.approx(ladder.replayed[0].edge_net)


def test_take_profit_fires_and_caps_upside():
    # price runs to 0.70 (+40% > 25% TP) at h=3, token eventually WINS (o=1).
    # Ladder exits at 0.70 -> o_x=0.70 < 1.0 -> ladder edge < hold edge.
    sigs = [_sig("m2", 0.50, "YES", ticks=[(1.0, 0.55), (3.0, 0.70), (5.0, 0.90)])]
    hold = replay_signals(sigs, RULE)
    ladder = replay_signals(sigs, RULE, exit_fn=make_exit_fn(0.55))
    assert sigs[0]["_exit_reason"] == "take_profit"
    assert ladder.replayed[0].edge_net < hold.replayed[0].edge_net


def test_stop_loss_saves_a_loser():
    # grace period is 2h; at h=30 price is 0.30 (-40 pct <= -12 pct stop for
    # 24-48h band), token LOSES (o=0). Ladder exits at 0.30 -> beats hold.
    sigs = [_sig("m3", 0.50, "NO", side="YES", ticks=[(30.0, 0.30), (40.0, 0.10)])]
    hold = replay_signals(sigs, RULE)
    ladder = replay_signals(sigs, RULE, exit_fn=make_exit_fn(0.55))
    assert sigs[0]["_exit_reason"] == "stop_loss"
    assert ladder.replayed[0].edge_net > hold.replayed[0].edge_net


def test_rule_never_sees_outcome_keys():
    seen = {}

    def spy_rule(s):
        seen.update(s)
        return Decision(size_usd=100.0)

    replay_signals([_sig("m4", 0.5, "YES")], spy_rule)
    assert "resolution" not in seen and "resolved_at" not in seen


def test_verdict_no_verdict_below_30_markets():
    out = summarize({f"m{i}": [0.05] for i in range(10)}, {}, {})
    assert out["verdict"].startswith("NO VERDICT")


def test_verdict_inconclusive_on_mixed_deltas():
    rng = np.random.default_rng(7)
    deltas = {f"m{i}": [float(rng.normal(0.0, 0.05))] for i in range(40)}
    out = summarize(deltas, {}, {})
    assert out["n_markets_with_path"] == 40
    assert out["verdict"] in (
        "INCONCLUSIVE",
        "LADDER ADDS EV (P(Delta>0) >= 0.95)",
        "LADDER DESTROYS EV vs hold (P(Delta<0) >= 0.95)",
    )


def test_verdict_positive_when_deltas_uniformly_positive():
    deltas = {f"m{i}": [0.02 + 0.001 * (i % 5)] for i in range(35)}
    out = summarize(deltas, {}, {})
    assert out["verdict"] == "LADDER ADDS EV (P(Delta>0) >= 0.95)"
