"""Unit tests for scripts/crypto_kill_test.py — pure logic only (the DB path
is exercised on the VPS; the self-test covers replay integration).
Run: python3 -m pytest tests/unit/test_crypto_kill_test.py --override-ini "addopts=" """
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import crypto_kill_test as ck  # noqa: E402


@dataclass
class R:
    market_id: str
    trader: str
    edge_net: float


def test_keep_crypto_uses_tested_bucketer():
    rows = [{"category": "Crypto"}, {"category": "Bitcoin above 100k"},
            {"category": "Ethereum"}, {"category": "NBA"},
            {"category": None}, {"category": "US Politics"}]
    kept = ck.keep_crypto(rows)
    assert len(kept) == 3
    assert all("category" in r for r in kept)


def test_boot_pooled_extremes_and_empty():
    good = ck.boot_pooled([0.10] * 40, 500, 7, 0.02)
    dead = ck.boot_pooled([-0.05] * 40, 500, 7, 0.02)
    assert good["p_above_floor"] == 1.0 and good["lower95"] > 0.02
    assert dead["upper95"] < 0.02 and dead["p_above_floor"] == 0.0
    empty = ck.boot_pooled([], 500, 7, 0.02)
    assert empty["mean"] is None and empty["upper95"] is None


def test_boot_pooled_deterministic_by_seed():
    a = ck.boot_pooled([0.1, -0.2, 0.05, 0.3], 500, 42, 0.02)
    b = ck.boot_pooled([0.1, -0.2, 0.05, 0.3], 500, 42, 0.02)
    assert a == b


def test_kill_verdict_pre_registered_rule():
    dead = {"upper95": -0.01, "p_above_floor": 0.0}
    good = {"upper95": 0.20, "p_above_floor": 1.0}
    murky = {"upper95": 0.10, "p_above_floor": 0.60}
    assert ck.kill_verdict(0.5, 40, dead, 0.4, 30, 0.02) == "KILLED"
    assert ck.kill_verdict(0.5, 40, good, 0.4, 30, 0.02) == "SURVIVES"
    assert ck.kill_verdict(0.5, 40, murky, 0.4, 30, 0.02) == "INCONCLUSIVE"
    # underpowered can NEVER kill — low coverage, few markets, or no stats
    assert ck.kill_verdict(0.2, 40, dead, 0.4, 30, 0.02) == "INCONCLUSIVE"
    assert ck.kill_verdict(0.5, 10, dead, 0.4, 30, 0.02) == "INCONCLUSIVE"
    assert ck.kill_verdict(None, 0, {"upper95": None}, 0.4, 30, 0.02) \
        == "INCONCLUSIVE"


def test_paired_decay_common_signals_only():
    rep0 = [R("m1", "a", 0.10), R("m2", "a", 0.08), R("m3", "b", 0.05)]
    repl = [R("m1", "a", 0.01), R("m2", "a", -0.02)]
    d = ck.paired_decay(rep0, repl)
    assert d["n_paired"] == 2
    assert abs(d["mean_decay"] - 0.095) < 1e-9
    assert ck.paired_decay(rep0, []) is None


def test_per_market_edges_cluster_aggregation():
    mk = ck.per_market_edges([R("m1", "a", 0.1), R("m1", "b", 0.3),
                              R("m2", "a", -0.1)])
    assert abs(mk["m1"] - 0.2) < 1e-9 and mk["m2"] == -0.1
