"""ANY-LOSS COOLDOWN SHADOW (operator-named 4b, 2026-08-01) — watch-only pricing of the
sub-rung drip gap. The invariant under test everywhere: the shadow writes ONLY
st["anyloss_shadow"] and plan["anyloss_sh_*"]; it must never touch reentry_cool,
mkt_loss_tripped, mkt_out, or any other governor state. Enabling real benching requires
operator naming plus receipts (drips are fine — we make money on drips)."""
import copy
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maker_kalshi_quoter as q                     # noqa: E402


def _dt(s):
    return datetime.datetime.fromisoformat(s + "+00:00")


NOW = _dt("2026-08-01T12:00:00")
DAY = "2026-08-01"


def _run(st=None, realized=None, base=None, tripped=(), out=(), cooling=(),
         now=NOW, day=DAY):
    st = st if st is not None else {}
    plan = {}
    q._anyloss_shadow(st, realized or {}, base or {}, set(tripped), set(out),
                      set(cooling), now, day, plan)
    return st, plan


class TestTripClassification:
    def test_new_trip_counts_and_bleeds(self):
        st, plan = _run(realized={"T-A": -1.00}, base={"T-A": 0.0})
        f = st["anyloss_shadow"]["floors"]
        # -1.00 delta trips floors 0.25, 0.50, 1.00 but not 2.00
        for fk, hit in (("0.25", 1), ("0.50", 1), ("1.00", 1), ("2.00", 0)):
            assert f[fk]["new"] == hit, fk
            assert plan["anyloss_sh_%s_new" % fk] == hit
        assert f["0.25"]["bled_usd"] == -1.0
        assert f["2.00"]["trips"] == 0

    def test_existing_brake_is_redundant_not_new(self):
        for brake in ({"tripped": {"T-A"}}, {"out": {"T-A"}}, {"cooling": {"T-A"}}):
            st, _ = _run(realized={"T-A": -3.0}, base={"T-A": 0.0}, **brake)
            rec = st["anyloss_shadow"]["floors"]["0.25"]
            assert rec["redundant"] == 1 and rec["new"] == 0, brake
            assert rec["bled_usd"] == 0.0, "redundant trips add no bled_usd"
            assert rec["tickers"] == [], "redundant trips are not collected"

    def test_no_baseline_means_no_trip(self):
        # first-seen ticker: base.get(t, v) -> delta 0 -> fail-open, same as the governor
        st, _ = _run(realized={"T-NEW": -50.0})
        assert st["anyloss_shadow"]["floors"]["0.25"]["trips"] == 0

    def test_gain_never_trips(self):
        st, _ = _run(realized={"T-A": 4.0}, base={"T-A": 0.0})
        assert st["anyloss_shadow"]["floors"]["0.25"]["trips"] == 0


class TestShadowCooldownSemantics:
    def test_active_shadow_cooldown_suppresses_retrip(self):
        st, _ = _run(realized={"T-A": -1.0}, base={"T-A": 0.0})
        st2, plan2 = _run(st=st, realized={"T-A": -1.5}, base={"T-A": 0.0},
                          now=NOW + datetime.timedelta(minutes=5))
        rec = st2["anyloss_shadow"]["floors"]["0.25"]
        assert rec["trips"] == 1, "still inside the shadow cooldown -> no second trip"
        assert plan2["anyloss_sh_0.25_active"] == 1

    def test_expired_shadow_cooldown_allows_retrip(self, monkeypatch):
        monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 60.0)
        st, _ = _run(realized={"T-A": -1.0}, base={"T-A": 0.0})
        st2, _ = _run(st=st, realized={"T-A": -1.5}, base={"T-A": 0.0},
                      now=NOW + datetime.timedelta(seconds=120))
        assert st2["anyloss_shadow"]["floors"]["0.25"]["trips"] == 2

    def test_unparseable_stamp_simply_expires(self):
        st = {"anyloss_shadow": {"day": DAY, "floors": {
            "0.25": {"active": {"T-A": "garbage"}, "trips": 1, "new": 1,
                     "redundant": 0, "bled_usd": -1.0, "tickers": ["T-A"]}}}}
        st2, plan = _run(st=st, realized={}, base={})
        assert st2["anyloss_shadow"]["floors"]["0.25"]["active"] == {}
        assert plan["anyloss_sh_0.25_active"] == 0

    def test_zero_cooldown_knob_falls_back_to_3600(self, monkeypatch):
        monkeypatch.setattr(q, "REENTRY_COOLDOWN_S", 0)
        st, _ = _run(realized={"T-A": -1.0}, base={"T-A": 0.0})
        exp = st["anyloss_shadow"]["floors"]["0.25"]["active"]["T-A"]
        delta = (q.parse_iso(exp) - NOW).total_seconds()
        assert abs(delta - 3600.0) < 1.0


class TestDayRoll:
    def test_new_day_resets_counters(self):
        st, _ = _run(realized={"T-A": -1.0}, base={"T-A": 0.0})
        st2, _ = _run(st=st, realized={}, base={}, day="2026-08-02",
                      now=NOW + datetime.timedelta(days=1))
        rec = st2["anyloss_shadow"]["floors"]["0.25"]
        assert rec["trips"] == 0 and rec["new"] == 0 and rec["tickers"] == []
        assert st2["anyloss_shadow"]["day"] == "2026-08-02"


class TestWatchOnlyInvariant:
    def test_touches_nothing_but_its_own_key(self):
        st = {"reentry_cool": {"T-B": "2026-08-01T13:00:00+00:00"},
              "mkt_loss_tripped": ["T-C"], "mkt_out": ["T-D"],
              "mkt_realized_base": {"T-A": 0.0}}
        before = copy.deepcopy(st)
        st_out, _ = _run(st=st, realized={"T-A": -2.5}, base={"T-A": 0.0},
                         tripped={"T-C"}, out={"T-D"}, cooling={"T-B"})
        added = set(st_out) - set(before)
        assert added == {"anyloss_shadow"}
        for k in before:
            assert st_out[k] == before[k], "%s mutated by watch-only shadow" % k

    def test_plan_keys_are_namespaced(self):
        _, plan = _run(realized={"T-A": -1.0}, base={"T-A": 0.0})
        assert plan and all(k.startswith("anyloss_sh_") for k in plan)
        # all four floors report all four gauges, every cycle
        assert len(plan) == 16

    def test_ticker_collection_capped(self, monkeypatch):
        monkeypatch.setattr(q, "ANYLOSS_SHADOW_MAX_TICKERS", 3)
        realized = {"T-%03d" % i: -1.0 for i in range(10)}
        base = {t: 0.0 for t in realized}
        st, _ = _run(realized=realized, base=base)
        rec = st["anyloss_shadow"]["floors"]["0.25"]
        assert len(rec["tickers"]) == 3
        assert rec["new"] == 10, "cap bounds the ticker list, never the counters"


class TestCallSiteWiring:
    def test_call_site_exists_and_is_guarded(self):
        # the run-loop call must exist, sit under the ladder-enabled branch, and swallow
        # faults into its own counter — never the governor's.
        import inspect
        src = inspect.getsource(q)
        i = src.rindex("_anyloss_shadow(st,")         # the call site, not the def
        window = src[i - 600:i + 600]
        assert 'anyloss_shadow_fail' in window
        assert "MKT_DAY_LOSS_EXITONLY_USD > 0" in window
