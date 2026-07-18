"""Stress harness for the Kalshi recorder — NO network. Monkeypatches the HTTP
layer with adversarial fixtures and runs the FULL tick path (run_once) in a
temp dir, asserting clean behavior under each scenario.

Run: python3 stress_maker_kalshi_recorder.py   (exit 0 = all scenarios pass)
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def fresh_module(tmpdir):
    """Import a fresh recorder module bound to an isolated data dir."""
    shutil.copy(os.path.join(HERE, "maker_kalshi_recorder.py"),
                os.path.join(tmpdir, "maker_kalshi_recorder.py"))
    sys.path.insert(0, tmpdir)
    for m in list(sys.modules):
        if m == "maker_kalshi_recorder":
            del sys.modules[m]
    import maker_kalshi_recorder as rec
    sys.path.pop(0)
    assert rec.DATA_DIR == tmpdir, rec.DATA_DIR
    rec.REQ_SPACING_S = 0.0  # no throttling in stress runs
    return rec


def program(ticker, reward_cc=200000, target="1000.00", df_bps=5000,
            start="2026-07-17T00:00:00Z", end="2026-09-17T00:00:00Z"):
    return {"market_ticker": ticker, "period_reward": reward_cc,
            "target_size_fp": target, "discount_factor_bps": df_bps,
            "incentive_type": "liquidity", "start_date": start, "end_date": end}


def market(ranges=None):
    return {"price_ranges": ranges or [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
            "yes_bid_dollars": "0.5000", "yes_ask_dollars": "0.5100",
            "volume_24h_fp": "1.0"}


def install(rec, programs, books, markets=None, fail=None, hoard429=None):
    """Replace rec.get with a fixture server. fail: dict path-substr -> Exception.
    hoard429: set of path-substrings that always raise 429."""
    import urllib.error
    calls = {"n": 0}

    def fake_get(path, budget):
        calls["n"] += 1
        if budget.exhausted():
            raise RuntimeError("daily request budget exhausted")
        budget.spend()
        if hoard429:
            for s in hoard429:
                if s in path:
                    raise RuntimeError(f"retries exhausted {path}: 429")
        if fail:
            for s, exc in fail.items():
                if s in path:
                    raise exc
        if path.startswith("/incentive_programs"):
            return {"incentive_programs": programs, "next_cursor": ""}
        if path.endswith("/orderbook"):
            t = path.split("/markets/")[1].split("/orderbook")[0]
            return {"orderbook_fp": books.get(t, {})}
        if path.startswith("/markets/"):
            t = path.split("/markets/")[1]
            return {"market": (markets or {}).get(t, market())}
        raise AssertionError("unexpected path " + path)

    rec.get = fake_get
    return calls


def run(rec):
    try:
        rc = rec.run_once()
    finally:
        os.chdir(HERE)  # run_once chdirs into DATA_DIR; Windows can't rmdir the CWD
    assert rc == 0, f"run_once returned {rc}"
    return rc


def scenario(name):
    print(f"--- {name}")


def main():
    fails = 0

    def check(cond, msg):
        nonlocal fails
        if cond:
            print(f"    ok: {msg}")
        else:
            fails += 1
            print(f"    FAIL: {msg}")

    # 1. malformed books & market objects must not kill the tick
    with tempfile.TemporaryDirectory() as td:
        scenario("malformed inputs (garbage levels, empty books, crossed, huge, missing keys)")
        rec = fresh_module(td)
        programs = [program(f"KXS-{i}") for i in range(6)]
        books = {
            "KXS-0": {"yes_dollars": [["abc", "10"]], "no_dollars": [["0.5", "10"]]},   # garbage price
            "KXS-1": {},                                                                  # no keys
            "KXS-2": {"yes_dollars": [], "no_dollars": []},                               # empty
            "KXS-3": {"yes_dollars": [["0.99", "1e9"]], "no_dollars": [["0.99", "1e9"]]},# crossed/huge
            "KXS-4": {"yes_dollars": [["0.5", None]], "no_dollars": [["0.5", "5"]]},     # None size
            "KXS-5": {"yes_dollars": [["0.5000", "2000"], ["0.4900", "500"]],
                      "no_dollars": [["0.4900", "2000"]]},                               # healthy
        }
        install(rec, programs, books, markets={"KXS-5": market()})
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        acc = st.get("accum", {})
        check("KXS-5" in acc and acc["KXS-5"]["n_valid_join"] == 1,
              "healthy market scored despite 5 malformed siblings")
        check(acc["KXS-5"]["sum_join"] > 0, "join score accumulated")
        samples = open([os.path.join(td, f) for f in os.listdir(td) if f.startswith("samples")][0]).read()
        check("KXS-5" in samples, "sample row written for healthy market")

    # 2. budget exhaustion mid-tick: clean exit, state persisted
    with tempfile.TemporaryDirectory() as td:
        scenario("budget exhaustion mid-tick")
        rec = fresh_module(td)
        rec.DAY_BUDGET = 5   # census(1) + 2 markets then dead
        programs = [program(f"KXB-{i}") for i in range(10)]
        books = {f"KXB-{i}": {"yes_dollars": [["0.5000", "2000"]],
                              "no_dollars": [["0.4900", "2000"]]} for i in range(10)}
        install(rec, programs, books)
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        check(st["budget"]["used"] == 5, f"budget stopped exactly at cap (used={st['budget']['used']})")

    # 3. 429 storm on orderbooks: markets skipped, no crash, budget counted
    with tempfile.TemporaryDirectory() as td:
        scenario("429 storm on all orderbooks")
        rec = fresh_module(td)
        programs = [program(f"KX4-{i}") for i in range(4)]
        install(rec, programs, {}, hoard429={"/orderbook"})
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        check(st.get("accum", {}) == {}, "no phantom accum rows under 429 storm")

    # 4. STOP sentinel honored
    with tempfile.TemporaryDirectory() as td:
        scenario("STOP sentinel")
        rec = fresh_module(td)
        open(os.path.join(td, "STOP"), "w").close()
        install(rec, [program("KXST-1")], {})
        run(rec)
        check(not os.path.exists(os.path.join(td, "state.json")),
              "no work performed with STOP present")

    # 5. corrupted state.json: recovered, tick proceeds
    with tempfile.TemporaryDirectory() as td:
        scenario("corrupted state.json")
        rec = fresh_module(td)
        with open(os.path.join(td, "state.json"), "w") as f:
            f.write('{"budget": {"day"')  # torn write
        programs = [program("KXC-1")]
        books = {"KXC-1": {"yes_dollars": [["0.5000", "2000"]],
                           "no_dollars": [["0.4900", "2000"]]}}
        install(rec, programs, books)
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        check("KXC-1" in st.get("accum", {}), "state rebuilt after corruption")

    # 6. program feed shape change: empty footprint, loud warning, exit 0
    with tempfile.TemporaryDirectory() as td:
        scenario("program feed shape change (all params missing)")
        rec = fresh_module(td)
        broken = [{"market_ticker": "KXP-1", "period_reward": 200000,
                   "incentive_type": "liquidity",
                   "start_date": "2026-07-17T00:00:00Z", "end_date": "2026-09-17T00:00:00Z"}]
        install(rec, broken, {})
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        check(st.get("accum", {}) == {}, "no fabricated scoring on paramless programs")

    # 7. rotation + disk cap: yesterday's file gzipped; cap halts cleanly
    with tempfile.TemporaryDirectory() as td:
        scenario("rotation and disk cap")
        rec = fresh_module(td)
        old = os.path.join(td, "samples-20200101.jsonl")
        with open(old, "w") as f:
            f.write('{"x":1}\n')
        programs = [program("KXR-1")]
        books = {"KXR-1": {"yes_dollars": [["0.5000", "2000"]],
                           "no_dollars": [["0.4900", "2000"]]}}
        install(rec, programs, books)
        run(rec)
        check(not os.path.exists(old) and os.path.exists(old + ".gz"),
              "pass 1: ancient jsonl gzipped (listdir snapshot predates the new .gz)")
        run(rec)
        check(not os.path.exists(old + ".gz"),
              "pass 2: ancient .gz aged out (eventual cleanup, one tick later)")
        rec2 = fresh_module(td)
        rec2.DISK_CAP_MB = 0
        install(rec2, programs, books)
        run(rec2)   # must print halted and exit 0 — run() asserts rc==0
        check(True, "disk-cap halt exits 0 (no crash-spam)")

    # 8. sub-cent + banded tick market end-to-end
    with tempfile.TemporaryDirectory() as td:
        scenario("sub-cent banded ticks")
        rec = fresh_module(td)
        programs = [program("KXSC-1", target="300.00")]
        books = {"KXSC-1": {"yes_dollars": [["0.0500", "200"], ["0.0490", "200"]],
                            "no_dollars": [["0.9400", "400"]]}}
        ranges = [{"start": "0.0000", "end": "0.1000", "step": "0.0010"},
                  {"start": "0.1000", "end": "1.0000", "step": "0.0100"}]
        install(rec, programs, books, markets={"KXSC-1": market(ranges)})
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        a = st["accum"]["KXSC-1"]
        check(a["n_valid_join"] == 1, "banded-tick market scored")
        # yes side: ref .05 step .001 -> 0.049 is 1 tick (w=.5), not 10 ticks
        # join share yes = 100/(300+100)?? walk: .05:300, stop at 300>=300 w/ ours:
        # merged .05:300, .049:200 -> walk .05 (300+100=400>=300) stop; ours 100/400 weight1
        # no side: ref .94 maps to yes .06 -> step .001
        check(0 < a["sum_join"] < 2, f"plausible join score {a['sum_join']:.4f}")

    # 9. day rollover: budget resets
    with tempfile.TemporaryDirectory() as td:
        scenario("budget day rollover")
        rec = fresh_module(td)
        with open(os.path.join(td, "state.json"), "w") as f:
            json.dump({"budget": {"day": "1999-01-01", "used": 79999}}, f)
        programs = [program("KXD-1")]
        books = {"KXD-1": {"yes_dollars": [["0.5000", "2000"]],
                           "no_dollars": [["0.4900", "2000"]]}}
        install(rec, programs, books)
        run(rec)
        st = json.load(open(os.path.join(td, "state.json")))
        check(st["budget"]["used"] < 100 and st["budget"]["day"] != "1999-01-01",
              "stale-day budget reset")

    print(f"\n{'ALL SCENARIOS PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
