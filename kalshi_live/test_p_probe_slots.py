"""Fix P (operator-ruled 2026-08-06 "we should add"): PROBE SLOT quality.

Measured 2026-08-05T23:59:54Z cycle: 4 of 5 probe slots burned on gate-refused books —
3 same-series KXAPRPOTUS strikes ($125 pools, one-sided all night) + one July-dated
KXEOWEEK market ($333) — while $1,000/day successor-candidate series (KXTRUTHSOCIAL,
KXMAXSHIPSHORMUZ, W16 report 00:10Z) never got sampled. Two defects: slots ranked by
PER-MARKET pool (a $333 single market beats every row of a $1,000 series split 10
ways) with no series diversity, and a slot stays burned forever on a book the quote
gates refuse every cycle.

Rule: probe candidates are picked (1) lowest gate-refusal streak first (a market that
rested resets to 0; deprioritization not a ban — refused markets still fill leftover
slots), (2) round-robin one-per-series ordered by SERIES total pool, (3) row pool as
the within-series key. Streaks live in quoter state (probe_gate_refused), maintained
by run_once from what actually rested.

Pins:
  P1 series diversity: 3 same-series strikes cannot take 3 slots while a second
     series waits
  P2 series TOTAL pool ranks: a $1,000-total series beats a single $333 market
  P3 gate-refusal streak deprioritizes; streak-0 newcomer beats streak-3 veteran
  P4 non-probe (allowlist) rows never touched; overall row order preserved
  P5 probe_slots telemetry lists the winners
"""
import maker_kalshi_quoter as q


def _row(t, pool, explore=True):
    r = {"ticker": t, "usd_day": pool}
    if explore:
        r["explore"] = True
    return r


def _select(monkeypatch, rows, slots=2, refused=None):
    monkeypatch.setattr(q, "SERIES_ALLOW", ["KXALLOW"])
    monkeypatch.setattr(q, "ALLOW_PROBE_EXCEPTION", 1)
    monkeypatch.setattr(q, "PROBE_MAX_SLOTS", slots)
    monkeypatch.setattr(q, "_PROBE_GATE_REFUSED", dict(refused or {}))
    kept = q._cap_probe_slots(list(rows), q.FP_DROPS)
    return kept


def test_p1_series_diversity(monkeypatch):
    q.FP_DROPS.clear()
    rows = [_row("KXAPR-1", 125), _row("KXAPR-2", 125), _row("KXAPR-3", 125),
            _row("KXTRUTH-1", 100)]
    kept = _select(monkeypatch, rows, slots=2)
    probes = [r["ticker"] for r in kept if r.get("explore")]
    assert len(probes) == 2
    assert {t.split("-")[0] for t in probes} == {"KXAPR", "KXTRUTH"}, \
        "second series must get a slot before any series doubles up"


def test_p2_series_total_pool_ranks(monkeypatch):
    q.FP_DROPS.clear()
    # KXBIG: $1,000 across 10 rows of $100; KXONE: single $333 market. ONE slot.
    rows = [_row("KXONE-1", 333.33)] + [_row(f"KXBIG-{i}", 100) for i in range(10)]
    kept = _select(monkeypatch, rows, slots=1)
    probes = [r["ticker"] for r in kept if r.get("explore")]
    assert probes and probes[0].startswith("KXBIG"), \
        "series TOTAL pool must outrank a single fat market"


def test_p3_gate_refusal_streak_rotates(monkeypatch):
    q.FP_DROPS.clear()
    rows = [_row("KXVET-1", 500), _row("KXNEW-1", 100)]
    kept = _select(monkeypatch, rows, slots=1, refused={"KXVET-1": 3})
    probes = [r["ticker"] for r in kept if r.get("explore")]
    assert probes == ["KXNEW-1"], \
        "a 3x gate-refused veteran yields its slot to an untried candidate"
    # but with no competition the refused market still gets sampled (not a ban)
    q.FP_DROPS.clear()
    kept2 = _select(monkeypatch, [_row("KXVET-1", 500)], slots=1,
                    refused={"KXVET-1": 3})
    assert [r["ticker"] for r in kept2 if r.get("explore")] == ["KXVET-1"]


def test_p4_allowlist_rows_untouched_and_order_preserved(monkeypatch):
    q.FP_DROPS.clear()
    rows = [_row("KXALLOW-1", 50, explore=False), _row("KXPRB-1", 10),
            _row("KXALLOW-2", 40, explore=False), _row("KXPRB2-1", 999)]
    kept = _select(monkeypatch, rows, slots=1)
    tickers = [r["ticker"] for r in kept]
    assert "KXALLOW-1" in tickers and "KXALLOW-2" in tickers
    assert tickers.index("KXALLOW-1") < tickers.index("KXALLOW-2"), \
        "non-probe relative order must be preserved"
    assert "KXPRB2-1" in tickers and "KXPRB-1" not in tickers
    assert dict(q.FP_DROPS).get("probe_slots_dropped") == 1


def test_p5_probe_slots_telemetry(monkeypatch):
    q.FP_DROPS.clear()
    rows = [_row("KXPRB-1", 10), _row("KXPRB2-1", 999)]
    _select(monkeypatch, rows, slots=2)
    assert set(q.FP_SHAPE.get("probe_slots") or []) == {"KXPRB-1", "KXPRB2-1"}
