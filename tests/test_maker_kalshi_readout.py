"""Offline tests for the readout's pure aggregation functions. (pytest-collected copy; loads via importlib, no network)"""
import importlib.util
import pathlib
import sys

_SPEC = importlib.util.spec_from_file_location(
    "maker_kalshi_readout", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "maker_kalshi_readout.py")
maker_kalshi_readout = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(maker_kalshi_readout)

per_series_table = maker_kalshi_readout.per_series_table
competition_arrival = maker_kalshi_readout.competition_arrival
share_stability = maker_kalshi_readout.share_stability
census_timeline = maker_kalshi_readout.census_timeline
data_quality = maker_kalshi_readout.data_quality
is_wc = maker_kalshi_readout.is_wc
read_jsonl = maker_kalshi_readout.read_jsonl



def S(ts, t, join, act, void, cap=None, usd_day=100.0):
    return {"ts": ts, "t": t, "join": join, "act": act, "void": void,
            "act_cap": cap, "usd_day": usd_day}


def test_per_series_table_math():
    rows = [
        S("2026-07-18T00:00:00+00:00", "KXA-1", 0.2, 0.4, False, 50, 100),
        S("2026-07-18T00:05:00+00:00", "KXA-1", 0.4, 0.6, False, 70, 100),
        S("2026-07-18T00:00:00+00:00", "KXA-2", None, 1.0, True, 30, 200),
    ]
    t = per_series_table(rows)
    assert len(t) == 1
    r = t[0]
    assert r["series"] == "KXA" and r["n_markets"] == 2 and r["samples"] == 3
    assert r["pool_usd_day"] == 300                      # 100 + 200, latest per mkt
    assert abs(r["join_usd_day"] - (0.3 / 2) * 300) < 1e-9   # mean join .3 -> share .15
    assert abs(r["act_usd_day"] - ((0.4 + 0.6 + 1.0) / 3 / 2) * 300) < 1e-9
    assert abs(r["void_rate"] - 1 / 3) < 1e-9
    assert abs(r["cap_avg"] - 50) < 1e-9                 # (50+70+30)/3


def test_competition_arrival_flip_and_still():
    rows = [
        S("2026-07-18T00:00:00+00:00", "KXV-1", None, 1.9, True),
        S("2026-07-18T02:00:00+00:00", "KXV-1", 0.1, 0.1, False),   # flipped @2h
        S("2026-07-18T00:00:00+00:00", "KXV-2", None, 1.9, True),
        S("2026-07-18T03:00:00+00:00", "KXV-2", None, 1.9, True),   # still void
        S("2026-07-18T00:00:00+00:00", "KXC-1", 0.2, 0.2, False),   # never void
    ]
    c = competition_arrival(rows)
    assert c["first_seen_void"] == 2
    assert c["flipped_to_contested"] == 1
    assert c["still_void"] == 1
    assert abs(c["median_hours_to_flip"] - 2.0) < 1e-9


def test_share_stability_erosion():
    rows = []
    for i in range(9):   # early third joins 0.6, late third 0.2
        rows.append(S(f"2026-07-18T00:{i:02d}:00+00:00", "KXE-1",
                      0.6 if i < 3 else (0.4 if i < 6 else 0.2), 0.5, False))
    st = share_stability(rows)
    assert abs(st["KXE"]["early"] - 0.3) < 1e-9   # 0.6/2
    assert abs(st["KXE"]["late"] - 0.1) < 1e-9    # 0.2/2


def test_per_series_realizable_uses_rw():
    rows = [
        dict(S("2026-07-18T00:00:00+00:00", "KXR-1", 0.4, 0.4, False, 10, 2400.0),
             rw=100.0, pend="x"),   # $100 program at a 2400/d rate (1h window)
        dict(S("2026-07-18T00:05:00+00:00", "KXR-1", 0.4, 0.4, False, 10, 2400.0),
             rw=100.0, pend="x"),
    ]
    t = per_series_table(rows)
    r = t[0]
    assert r["pool_reward_usd"] == 100.0
    assert abs(r["join_realizable_usd"] - 0.2 * 100.0) < 1e-9   # share .2 x $100
    # rate view would claim .2 x 2400 = $480/d — realizable caps it at $20
    assert abs(r["join_usd_day"] - 480.0) < 1e-9


def test_census_timeline_wc_split():
    census = [{"ts": "2026-07-18T00:00:00+00:00", "total_scheduled_usd": 1000.0,
               "n_programs": 10,
               "top_series_usd": [["KXWCADS", 300.0], ["KXWORLDCUPHALFTIME", 200.0],
                                  ["KXEGGS", 100.0]]}]
    t = census_timeline(census)
    assert t[0]["wc_topN"] == 500.0
    # floor = non-WC money we can SEE in the (truncated) top list = KXEGGS 100
    assert t[0]["ex_wc_topN_floor"] == 100.0
    # ceiling = total - visible-WC (over-counts ex-WC because top list is truncated)
    assert t[0]["ex_wc_topN_ceiling"] == 500.0
    assert is_wc("KXWCADS") and is_wc("KXWORLDCUPHALFTIME") and not is_wc("KXEGGS")


def test_read_jsonl_survives_truncated_gz(tmp_path):
    import gzip
    good = tmp_path / "samples-20260101.jsonl"
    good.write_text('{"ts":"2026-01-01T00:00:00+00:00","t":"A","join":0.1}\n')
    # a deliberately corrupt .gz (not valid gzip) must not abort the whole read
    bad = tmp_path / "samples-20260102.jsonl.gz"
    bad.write_bytes(b"\x1f\x8b not really gzip \x00\x01\x02")
    rows = read_jsonl(str(tmp_path / "samples-*.jsonl*"))
    assert len(rows) == 1 and rows[0]["t"] == "A"   # good file read, bad file skipped


def test_median_even_length_averages():
    rows = []
    # two markets void-then-flip at 2h and 10h -> median must be 6.0, not 10
    for t, h in [("KXA-1", 2), ("KXB-1", 10)]:
        rows.append(S(f"2026-07-18T00:00:00+00:00", t, None, 1.9, True))
        rows.append(S(f"2026-07-18T{h:02d}:00:00+00:00", t, 0.1, 0.1, False))
    c = competition_arrival(rows)
    assert c["median_hours_to_flip"] == 6.0


def test_data_quality_counts():
    rows = [S("2026-07-18T00:00:10+00:00", "KXA-1", 0.1, 0.1, False),
            S("2026-07-18T00:05:10+00:00", "KXA-1", 0.1, 0.1, False),
            S("2026-07-18T01:00:10+00:00", "KXB-1", 0.1, 0.1, False)]
    q = data_quality(rows)
    assert q["n"] == 3 and q["n_markets"] == 2 and q["hours_covered"] == 2
    assert q["ticks_per_hour"]["07-18 00"] == 2


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
