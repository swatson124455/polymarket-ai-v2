"""L2 PAIR-CARRY MAKER UNWIND (operator-named 2026-07-31 'DO if guarded + blast-radius-
verified'; built DARK, KALSHI_PAIR_UNWIND default 0). Guards under test:
- flag OFF => byte-identical (no orders, no plan keys, provable no-op)
- whole contracts only; count can never exceed (never flip) the paired quantity
- BOTH legs' books must be fresh this cycle
- combined proceeds must beat the $1 settlement floor by MIN_EDGE
- no double-resting on a ticker+side (an occupied side stands down)
- exit-only markets still receive their pair-unwind legs (reason='unwind' survives strips)
- _ladder_pairs stays in lockstep with ladder_pairing (pairs + naked == held)"""
import json
import os

from test_live_hardening import q, MockClient, _run, _cfg


def _pos(t, pos, exposure="0.25"):
    return {"ticker": t, "position_fp": pos, "market_exposure_dollars": exposure,
            "realized_pnl_dollars": "0.00"}


LOW = "KXLAD-26AUG01-3.00"      # long yes here
HIGH = "KXLAD-26AUG01-7.00"     # long no here (position_fp negative)


def _fp_lad(monkeypatch):
    monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
        {"ticker": LOW, "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"},
        {"ticker": HIGH, "usd_day": 99.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])


def _books(monkeypatch, low=("0.40", "0.55"), high=("0.30", "0.65")):
    """Per-ticker (yes_bid, no_bid) book tops. Pair-unwind proceeds =
    (1 - no_bid(LOW)) + (1 - yes_bid(HIGH))."""
    tops = {LOW: low, HIGH: high}
    def _pg(p):
        if "incentive" in p:
            return {"incentive_programs": [], "next_cursor": ""}
        for t, (y, n) in tops.items():
            if t in p:
                return {"orderbook_fp": {"yes_dollars": [[y, "500"]],
                                         "no_dollars": [[n, "500"]]}}
        return {"orderbook_fp": {"yes_dollars": [["0.50", "9999"]],
                                 "no_dollars": [["0.49", "9999"]]}}
    monkeypatch.setattr(q, "public_get", _pg)


def _pu_legs(created, count=None):
    return [o for o in created
            if (o["ticker"], o["side"]) in ((LOW, "no"), (HIGH, "yes"))
            and (count is None or o["count"] == count)]


class TestLadderPairsLockstep:
    def test_pairs_plus_naked_reconstruct_held(self):
        held = {LOW: 5.0, HIGH: -3.0, "KXLAD-26AUG01-5.00": 2.0,
                "KXOTHER-26AUG01-T1.0": -4.0}
        naked = q.ladder_pairing(dict(held))
        pairs = q._ladder_pairs(dict(held))
        rebuilt = dict(naked)
        for lt, st, m in pairs:
            rebuilt[lt] = rebuilt.get(lt, 0.0) + m
            rebuilt[st] = rebuilt.get(st, 0.0) - m
        assert {t: round(v, 9) for t, v in rebuilt.items()} == held
        assert all(m > 0 for _, _, m in pairs)

    def test_unfloored_combo_never_paired(self):
        # long yes HIGH + long no LOW = NO floor -> zero pairs (matches ladder_pairing)
        held = {LOW: -3.0, HIGH: 3.0}
        assert q._ladder_pairs(dict(held)) == []
        assert q.ladder_pairing(dict(held)) == held

    def test_categorical_tickers_excluded(self):
        held = {"KXCAT-26AUG01-ABC": 5.0, "KXCAT-26AUG01-DEF": -5.0}
        assert q._ladder_pairs(dict(held)) == []


def _arm(monkeypatch, edge=0.02, on=True):
    _cfg(monkeypatch)
    _fp_lad(monkeypatch)
    _books(monkeypatch)
    # exit-only both tickers: the REAL pair-carry case (banned/tripped markets hold pairs
    # with no accumulating quotes resting) AND it frees the ticker+side slots the standard
    # join would otherwise occupy.
    monkeypatch.setattr(q, "MKT_DAY_LOSS_EXITONLY_USD", 3.0)
    import datetime as _dt
    _today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    if on:
        monkeypatch.setattr(q, "PAIR_UNWIND", 1)
        monkeypatch.setattr(q, "PAIR_UNWIND_MIN_EDGE", edge)
    return _today


def _seed_exit_only(tmp_path, today, tickers=(LOW, HIGH)):
    with open(os.path.join(str(tmp_path), "quoter_state.json"), "w") as fh:
        json.dump({"mkt_realized_day": today, "mkt_loss_tripped": list(tickers),
                   "mkt_realized_base": {}}, fh)


class TestPairUnwindDark:
    def test_default_off_is_noop(self, monkeypatch, tmp_path):
        assert q.PAIR_UNWIND == 0, "must ship dark"
        today = _arm(monkeypatch, on=False)
        _seed_exit_only(tmp_path, today)
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "3.00"), _pos(HIGH, "-3.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        assert "pair_unwind_quotes" not in row
        assert _pu_legs(c.created) == [], "no pair-unwind orders when dark"


class TestPairUnwindOn:
    def test_places_both_legs_when_edge_clears(self, monkeypatch, tmp_path):
        today = _arm(monkeypatch)
        _seed_exit_only(tmp_path, today)
        # proceeds = (1-0.55) + (1-0.30) = 1.15 -> edge 0.15 >= 0.02
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "3.00"), _pos(HIGH, "-3.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        legs = _pu_legs(c.created, count=3)
        assert len(legs) == 2, f"both pair legs must rest: {c.created}"
        assert row.get("pair_unwind_quotes") == 2
        assert row.get("pair_unwind_edge_usd") is not None

    def test_edge_below_min_stands_down(self, monkeypatch, tmp_path):
        today = _arm(monkeypatch, edge=0.30)   # edge 0.15 < 0.30 -> settlement wins
        _seed_exit_only(tmp_path, today)
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "3.00"), _pos(HIGH, "-3.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        assert "pair_unwind_quotes" not in row
        assert _pu_legs(c.created) == []

    def test_fractional_pairs_stand_down(self, monkeypatch, tmp_path):
        today = _arm(monkeypatch)
        _seed_exit_only(tmp_path, today)
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "0.70"), _pos(HIGH, "-0.70")])
        row = _run(monkeypatch, c, str(tmp_path))
        assert "pair_unwind_quotes" not in row, "sub-1-contract pairs cannot be quoted"

    def test_count_floors_to_paired_quantity(self, monkeypatch, tmp_path):
        today = _arm(monkeypatch)
        _seed_exit_only(tmp_path, today)
        # paired = min(2.6, 3.4) = 2.6 -> 2 whole contracts, never 3 (3 would flip LOW)
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "2.60"), _pos(HIGH, "-3.40")])
        _run(monkeypatch, c, str(tmp_path))
        legs = _pu_legs(c.created)
        assert legs and all(o["count"] == 2 for o in legs), c.created

    def test_missing_book_on_one_leg_stands_down(self, monkeypatch, tmp_path):
        """HIGH not in the footprint -> its book was never read this cycle -> BOTH legs
        stand down (a one-book pair quote would price the partner blind)."""
        today = _arm(monkeypatch)
        _seed_exit_only(tmp_path, today)
        monkeypatch.setattr(q, "select_footprint", lambda progs, now: [
            {"ticker": LOW, "usd_day": 100.0, "target": 1, "end": "2099-01-01T00:00:00Z"}])
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "3.00"), _pos(HIGH, "-3.00")])
        _before = q._SILENT.get("pair_unwind_fail", 0)
        row = _run(monkeypatch, c, str(tmp_path))
        assert "pair_unwind_quotes" not in row
        assert _pu_legs(c.created) == []
        assert q._SILENT.get("pair_unwind_fail", 0) == _before, \
            "a missing book is a GUARDED stand-down, not a swallowed exception"

    def test_occupied_side_stands_down_no_double_rest(self, monkeypatch, tmp_path):
        """NOT exit-only: the standard join occupies both sides of both tickers -> the
        pair-unwind legs must stand down rather than double-rest a side."""
        _arm(monkeypatch)          # no exit-only seed: markets quote normally
        c = MockClient(mode="live",
                       positions=[_pos(LOW, "3.00"), _pos(HIGH, "-3.00")])
        row = _run(monkeypatch, c, str(tmp_path))
        by_ts = {}
        for o in c.created:
            by_ts[(o["ticker"], o["side"])] = by_ts.get((o["ticker"], o["side"]), 0) + 1
        assert all(v == 1 for v in by_ts.values()), \
            f"a ticker+side must never carry two resting orders: {by_ts}"
