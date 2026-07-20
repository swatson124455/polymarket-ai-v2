#!/usr/bin/env python3
"""Kalshi ALL-SECTOR hierarchy: rewards + trading P&L -> net EV ranking. v2.

v2 after adversarial review (verdict on v1: UNSOUND — run-level instrumentation).
Fixes, mapped to the review's findings:
  C1 silent tape loss     -> fetch failures COUNTED + ticker EXCLUDED from all legs
                             (never "zero fills"); per-series excl count printed.
  C2 loss-leg censoring   -> fills/hour column + explicit short-window disclosure;
                             censoring cannot be normalized away, so it is LABELED.
  C3 unfloored ranking    -> series below cap_days>=20 or obs_h>=5 are listed
                             separately, never ranked.
  C4 settle conflation    -> settle() returns status (settled/unsettled/void/
                             fetch_fail); NETset printed only at >=50% settled
                             coverage. PARTIAL per fix-verifier: between 50-100%
                             settled, NETset BLENDS settlement marks with
                             liquidation marks for unsettled members — treat
                             NETset as indicative unless set% ~100.
  M5/M6 mark bias         -> final mark from the LAST valid book, liquidation-
                             aware: longs exit at bid, shorts at ask.
  M7 inventory capital    -> held |pos| charged mark-based collateral per window.
  M8 filled flag          -> gross fill volume (buys+sells), not net pos.
  M9 taker_side missing   -> row skipped + counted, never defaulted.
  M10 tape coverage       -> oldest fetched trade vs first snapshot checked;
                             suspect coverage counted.

Method core (unchanged, verified by the same review): per-window fill algebra,
cons/opt queue bracket, half-open windows, reward units, void handling,
resting-collateral formula. All $ remain MODEL ESTIMATES on real inputs.
"""
import collections
import glob
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

KDIR = r"C:/Users/samwa/.claude/projects/C--lockes-picks-polymarket-ai-v2/maker-backups/kalshi"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
UA = {"User-Agent": "kalshi-hierarchy/2.0"}
OUR_SIZE = 100.0
HTTP_TIMEOUT = 20
MAX_TICKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
OUT_JSON = KDIR + "/sector_hierarchy_20260720_v2.json"
FLOOR_CAP_DAYS = 20.0     # ranking eligibility
FLOOR_OBS_H = 5.0

SECTORS = [
    ("weather_temp", lambda s: s.startswith("KXTEMP")),
    ("wc_promo",     lambda s: s.startswith("KXWC") or s.startswith("KXWORLDCUP")),
    ("mentions",     lambda s: "MENTION" in s),
    ("climate_env",  lambda s: s in ("KXAQICITY", "KXFIRSTHURRICANE", "KXHIGHNYC")),
    ("econ_prices",  lambda s: s.startswith("KXAAAGAS") or s in
        ("KXEGGS", "KXAIRFARECPI", "KXEOWEEK", "KXMUSKWEALTH", "KXACQPYPL")
        or s.startswith("KXUST") or s.endswith("MS")),
    ("politics",     lambda s: s in ("KXGOVWINS", "KXGENERICBALLOTVOTEHUB", "KXMAMDANIEO",
        "KXNUMAPPROP", "KXMOVGANCSEN", "KXTRUMPBIBIMEET", "KXTRUMPACT",
        "KXTRUMPENDORSEMENTS", "KXTRUTHSOCIAL", "KXDATACENTERMORATORIUM",
        "KXTRUMPPHOTO", "KXTRUMPTIME")),
    ("sports_other", lambda s: s.startswith("KXMLB") or s.startswith("KXWNBA")
        or s.startswith("KXNBA") or s.startswith("KXFIGHT")),
    ("entertainment", lambda s: s in ("KXDWTSCAST", "KXEACNOM")
        or "MOVIE" in s or "PRODUCTION" in s),
]

FAILS = collections.Counter()   # global fetch-failure telemetry


def sector_of(series):
    for name, fn in SECTORS:
        if fn(series):
            return name
    return "other"


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def get(url, tries=4):
    """Returns (json|None, ok:bool). ok=False ONLY on fetch failure —
    a fetch failure is never allowed to look like an empty result (C1)."""
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return json.loads(r.read() or b"{}"), True
        except urllib.error.HTTPError as e:
            if e.code == 429 and a < tries - 1:
                time.sleep(2.0 * (a + 1))
                continue
            FAILS[f"http_{e.code}"] += 1
            return None, False
        except Exception:
            if a < tries - 1:
                time.sleep(0.5)
                continue
            FAILS["other"] += 1
            return None, False
    return None, False


def load_samples():
    by = collections.defaultdict(list)
    for f in sorted(glob.glob(KDIR + "/samples-*.jsonl*")):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt") as fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("t"):
                    by[r["t"]].append(r)
    for t in by:
        by[t].sort(key=lambda r: r["ts"])
    return by


def fetch_tape(tkr, first_snap_ts):
    """Returns (tape, ok, coverage_ok). ok=False if ANY page failed (C1).
    coverage_ok=False if pagination was exhausted at the cap while the oldest
    fetched trade is still newer than our first snapshot (M10)."""
    out, cursor, ok = [], None, True
    pages = 0
    for pages in range(1, 11):
        url = f"{BASE}/markets/trades?ticker={tkr}&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
        d, page_ok = get(url)
        if not page_ok:
            return out, False, False
        out.extend(d.get("trades") or [])
        cursor = d.get("cursor") or d.get("next_cursor")
        if not cursor:
            break
    parsed = []
    for t in out:
        try:
            t["_ts"] = iso(t["created_time"])
            t["_yes"] = float(t["yes_price_dollars"])
            t["_sz"] = float(t["count_fp"])
            parsed.append(t)
        except Exception:
            FAILS["tape_row_malformed"] += 1
    coverage_ok = True
    if cursor and pages >= 10 and parsed:
        oldest = min(p["_ts"] for p in parsed)
        if oldest > first_snap_ts:
            coverage_ok = False
    return parsed, ok, coverage_ok


def settle(tkr):
    """Returns (payoff|None, status) — status in settled/unsettled/void/fetch_fail (C4)."""
    d, ok = get(f"{BASE}/markets/{tkr}")
    if not ok:
        return None, "fetch_fail"
    m = (d or {}).get("market") or {}
    res = (m.get("result") or "").lower()
    if res == "yes":
        return 1.0, "settled"
    if res == "no":
        return 0.0, "settled"
    if res in ("void", "voided", "scratch", "all_no", "all_yes"):
        return None, "void"
    return None, "unsettled"


def analyse_ticker(rows, tape, payoff):
    st = {"cons": {"cash": 0.0, "pos": 0.0, "gross": 0.0},
          "opt": {"cash": 0.0, "pos": 0.0, "gross": 0.0}}
    rew = cap_days = obs_h = 0.0
    last_book = None            # (yb, ya) of the LAST valid row seen (M6)
    for a, b in zip(rows, rows[1:]):
        try:
            yb, ya = float(a["yb"]), float(a["ya"])
        except (TypeError, ValueError):
            continue
        if not (0 < yb < 1 and 0 < ya < 1 and yb < ya):
            continue
        t0, t1 = iso(a["ts"]), iso(b["ts"])
        dt = (t1 - t0).total_seconds()
        if dt <= 0 or dt > 3600:
            continue
        last_book = (yb, ya)
        # b's book supersedes as the final mark if valid (M6)
        try:
            byb, bya = float(b["yb"]), float(b["ya"])
            if 0 < byb < 1 and 0 < bya < 1 and byb < bya:
                last_book_candidate = (byb, bya)
            else:
                last_book_candidate = None
        except (TypeError, ValueError):
            last_book_candidate = None
        obs_h += dt / 3600.0
        cap_days += (yb + (1.0 - ya)) * OUR_SIZE * dt / 86400.0
        mid = (yb + ya) / 2.0
        # inventory collateral charge for held position during this window (M7)
        held = st["cons"]["pos"]
        if held > 0:
            cap_days += held * mid * dt / 86400.0
        elif held < 0:
            cap_days += (-held) * (1.0 - mid) * dt / 86400.0
        if not a.get("void"):
            rew += float(a.get("join") or 0.0) * float(a.get("usd_day") or 0.0) * (dt / 86400.0)
        rem = {m: {"bid": OUR_SIZE, "ask": OUR_SIZE} for m in ("cons", "opt")}
        for tr in tape:
            if not (t0 < tr["_ts"] <= t1):
                continue
            side = (tr.get("taker_side") or "").lower()
            if side not in ("yes", "no"):
                FAILS["taker_side_missing"] += 1   # (M9) never default
                continue
            px, sz = tr["_yes"], tr["_sz"]
            taker_yes = side == "yes"
            for mode, strict in (("cons", True), ("opt", False)):
                if taker_yes:
                    hit = (px > ya) if strict else (px >= ya)
                    if hit and rem[mode]["ask"] > 0:
                        f = min(sz, rem[mode]["ask"])
                        st[mode]["cash"] += ya * f
                        st[mode]["pos"] -= f
                        st[mode]["gross"] += f
                        rem[mode]["ask"] -= f
                else:
                    hit = (px < yb) if strict else (px <= yb)
                    if hit and rem[mode]["bid"] > 0:
                        f = min(sz, rem[mode]["bid"])
                        st[mode]["cash"] -= yb * f
                        st[mode]["pos"] += f
                        st[mode]["gross"] += f
                        rem[mode]["bid"] -= f
        if last_book_candidate:
            last_book = last_book_candidate
    if obs_h == 0:
        return None
    out = {"rew": rew, "cap_days": cap_days, "obs_h": obs_h,
           "settled": payoff is not None}
    for m in ("cons", "opt"):
        pos = st[m]["pos"]
        # liquidation-aware in-window mark (M5): longs exit at bid, shorts at ask
        if last_book:
            lyb, lya = last_book
            liq = lyb if pos > 0 else lya
        else:
            liq = 0.5
        sett_mark = payoff if payoff is not None else liq
        out[f"inwin_{m}"] = st[m]["cash"] + pos * liq
        out[f"sett_{m}"] = st[m]["cash"] + pos * sett_mark
        out[f"gross_{m}"] = st[m]["gross"]
    return out


def run():
    samples = load_samples()
    tickers = sorted(samples, key=lambda t: -len(samples[t]))[:MAX_TICKERS]
    print(f"markets={len(samples)} analysed={len(tickers)} "
          f"series={len({t.split('-')[0] for t in tickers})}")

    per_series = collections.defaultdict(lambda: collections.defaultdict(float))
    contrib_in, contrib_se = [], []
    excluded = collections.Counter()      # series -> tickers dropped on fetch failure
    t_start = time.time()

    for i, tkr in enumerate(tickers, 1):
        rows = samples[tkr]
        if len(rows) < 2:
            continue
        s = tkr.split("-")[0]
        first_ts = iso(rows[0]["ts"])
        tape, tape_ok, cov_ok = fetch_tape(tkr, first_ts)
        payoff, sstat = settle(tkr)
        time.sleep(0.10)
        if not tape_ok:
            excluded[s] += 1              # C1: exclusion, never zero-fill
            continue
        r = analyse_ticker(rows, tape, payoff)
        if r is None:
            continue
        if not cov_ok:
            per_series[s]["n_cov_suspect"] += 1
        for k in ("rew", "cap_days", "obs_h", "inwin_cons", "inwin_opt",
                  "sett_cons", "sett_opt", "gross_cons", "gross_opt"):
            per_series[s][k] += r[k]
        per_series[s]["n"] += 1
        per_series[s]["n_filled"] += 1 if r["gross_cons"] > 0 else 0    # M8
        per_series[s]["n_settled"] += 1 if sstat == "settled" else 0
        per_series[s][f"n_{sstat}"] += 0 if sstat == "settled" else 1
        contrib_in.append((tkr, r["rew"] + r["inwin_cons"]))
        if sstat == "settled":
            contrib_se.append((tkr, r["rew"] + r["sett_cons"]))
        if i % 100 == 0:
            el = time.time() - t_start
            print(f"  ...{i}/{len(tickers)}  ({el/60:.1f}min, "
                  f"eta {(el/i)*(len(tickers)-i)/60:.0f}min, "
                  f"fails={dict(FAILS) or 0} excl={sum(excluded.values())})",
                  flush=True)

    with open(OUT_JSON, "w") as f:
        json.dump({"series": {s: dict(v) for s, v in per_series.items()},
                   "excluded": dict(excluded), "fails": dict(FAILS)}, f, indent=1)
    print(f"\nraw aggregates -> {OUT_JSON}")

    # ---------- telemetry FIRST: is the loss leg trustworthy? (C1) ----------
    n_excl = sum(excluded.values())
    print(f"\nFETCH TELEMETRY: failures={dict(FAILS) or 'none'} | "
          f"tickers EXCLUDED (tape unfetchable): {n_excl}")
    if n_excl:
        for s, n in excluded.most_common(10):
            print(f"   excluded {n:>3} of series {s}")
        print("   -> excluded tickers contribute to NO leg (never zero-fill).")

    # ---------- concentration, both marks (nit) ----------
    for label, contrib in (("in-window", contrib_in), ("settle", contrib_se)):
        if not contrib:
            continue
        gross = sum(abs(v) for _, v in contrib) or 1.0
        top = sorted(contrib, key=lambda kv: -abs(kv[1]))[:5]
        share = 100 * sum(abs(v) for _, v in top) / gross
        net_tot = sum(v for _, v in contrib)
        print(f"\nCONCENTRATION ({label}, cons): top-5 = {share:.1f}% of gross "
              f"|contribution| across {len(contrib)} mkts")
        for t, v in top:
            print(f"   {t:<46}{v:+9.2f}")
        if share >= 50:
            print(f"   >=50% -> leave-one-out (drop {top[0][0]}): "
                  f"{net_tot - top[0][1]:+.2f} vs {net_tot:+.2f}")

    # ---------- ranked series table (floored, C3) ----------
    def metrics(a):
        net_in = a["rew"] + a["inwin_cons"]
        net_se = a["rew"] + a["sett_cons"]
        per_cap = net_in / a["cap_days"] if a["cap_days"] > 0 else 0.0
        return net_in, net_se, per_cap

    eligible, below = [], []
    for s, a in per_series.items():
        (eligible if (a["cap_days"] >= FLOOR_CAP_DAYS and a["obs_h"] >= FLOOR_OBS_H)
         else below).append((s, a))
    eligible.sort(key=lambda kv: -metrics(kv[1])[2])

    print(f"\nRANKED (floor: cap$d>={FLOOR_CAP_DAYS:.0f} & obs_h>={FLOOR_OBS_H:.0f}; "
          f"{len(eligible)} of {len(per_series)} series eligible)")
    print(f"{'#':<3}{'series':<24}{'sector':<14}{'mkts':>5}{'rew$':>9}"
          f"{'trade$':>9}{'NETin$':>9}{'NETset$':>9}{'set%':>5}"
          f"{'cap$d':>8}{'NET/cap/d':>10}{'fill/h':>7}")
    for rank, (s, a) in enumerate(eligible, 1):
        net_in, net_se, per_cap = metrics(a)
        setpct = 100 * a["n_settled"] / a["n"] if a["n"] else 0
        fph = a["gross_cons"] / a["obs_h"] if a["obs_h"] else 0
        setcol = f"{net_se:>9.1f}" if setpct >= 50 else f"{'n/a':>9}"
        print(f"{rank:<3}{s:<24}{sector_of(s):<14}{int(a['n']):>5}"
              f"{a['rew']:>9.1f}{a['inwin_cons']:>9.1f}{net_in:>9.1f}"
              f"{setcol}{setpct:>4.0f}%{a['cap_days']:>8.1f}"
              f"{per_cap:>10.3f}{fph:>7.2f}")
    if below:
        print(f"\nBELOW FLOOR (insufficient observation — NOT ranked): "
              f"{len(below)} series, "
              f"rew$ {sum(a['rew'] for _, a in below):.1f} total")

    # ---------- sector rollup (eligible series only) ----------
    sec = collections.defaultdict(lambda: collections.defaultdict(float))
    for s, a in eligible:
        d = sec[sector_of(s)]
        for k in ("rew", "cap_days", "obs_h", "inwin_cons", "inwin_opt",
                  "sett_cons", "sett_opt", "gross_cons", "n", "n_settled"):
            d[k] += a[k]
    print(f"\nSECTOR ROLLUP (eligible series only; cons queue; opt in JSON)")
    print(f"{'sector':<15}{'mkts':>6}{'rew$':>10}{'trade$in':>10}{'NETin$':>10}"
          f"{'NETset$':>10}{'set%':>6}{'cap$d':>9}{'NET/cap/d':>10}{'fill/h':>7}")
    order = sorted(sec.items(),
                   key=lambda kv: -(kv[1]["rew"] + kv[1]["inwin_cons"]) /
                                   max(kv[1]["cap_days"], 1e-9))
    for name, d in order:
        net_in = d["rew"] + d["inwin_cons"]
        net_se = d["rew"] + d["sett_cons"]
        pc = net_in / d["cap_days"] if d["cap_days"] else 0
        setpct = 100 * d["n_settled"] / d["n"] if d["n"] else 0
        fph = d["gross_cons"] / d["obs_h"] if d["obs_h"] else 0
        setcol = f"{net_se:>10.1f}" if setpct >= 50 else f"{'n/a':>10}"
        print(f"{name:<15}{int(d['n']):>6}{d['rew']:>10.1f}{d['inwin_cons']:>10.1f}"
              f"{net_in:>10.1f}{setcol}{setpct:>5.0f}%"
              f"{d['cap_days']:>9.1f}{pc:>10.3f}{fph:>7.2f}")

    print("\nMODEL ESTIMATES on real inputs. Conservative queue in tables; "
          "optimistic bracket in JSON.")
    print("CENSORING DISCLOSURE (cannot be normalized away): short-observed series "
          "(temp ~25min/mkt)\n  may simply not have SAMPLED their adverse fills; "
          "long-observed series (~2d) have.\n  Compare fill/h before trusting a "
          "cross-sector NET/cap/d gap; the pilot's own fills settle it.")
    print("NETset shown only where >=50% of a series' markets actually settled; "
          "unsettled marks are liquidation-aware last-book, not settlement.")


if __name__ == "__main__":
    run()
