#!/usr/bin/env python3
"""CONCENTRATION STUDY — sandbox, READ-ONLY, NO KEYS, NO MONEY, NEVER TRADES.

THE QUESTION (handoff owed-item #3, "biggest untested lever"): the LIP minimum payout
is **$1.00 per market**, and every market is its own program. Spreading a fixed $85
across many markets can therefore compute to $0.40 here and $0.60 there and pay
**nothing** in most of them. Does concentrating the same capital into fewer markets
pay more?

Verified rule text (running tab / auditor rebuttal A1, quoted verbatim from Kalshi LIP):
    "Minimum payout: $1.00 (rounded down to nearest cent)"
That wording is genuinely ambiguous — "minimum payout $1.00" reads as a THRESHOLD
(below $1 pays zero), while "rounded down to nearest cent" reads as ordinary rounding.
This study does NOT pick one and hide it: every result is reported BOTH ways
(`floor` = threshold applied, `nofloor` = raw pro-rata). If the two disagree the answer
is threshold-dependent and that is stated in the output, not smoothed over.

METHOD — paired, identical snapshots:
  Phase 1 (sample):  store the RAW orderbook levels + program params per market.
                     Storing raw books (not pre-computed shares, which is what
                     kalshi_ab_throttle_study.py did) is what lets every arm be
                     rescored offline at any size without refetching — so all arms
                     see byte-identical books and no arm can be advantaged by drift.
  Phase 2 (score):   for K = 1..N, allocate the SAME total capital across the top-K
                     markets by pool, quote both sides, score with the recorder's
                     real CFTC core, apply the $1 rule, sum.

Capital model mirrors the deployed config, read from the frozen live.env:
    KALSHI_MAX_TOTAL_CAPITAL=85     KALSHI_MAX_MARKET_CAPITAL=15
so per-market dollars = min(TOTAL/K, MAX_MARKET). Note this means the per-market cap
BINDS below K=6: you cannot concentrate harder than ~6 markets without raising
MAX_MARKET_CAPITAL. The study reports where that bind starts, because if the answer is
"concentrate", the cap — not the strategy — is the thing standing in the way.

SCOPE / WHAT THIS DOES NOT COVER (standing operator directive, running tab §A0):
  * REWARD SIDE ONLY. Fill rate and adverse selection are NOT simulatable without queue
    position. Concentration puts MORE inventory into FEWER markets, which is strictly
    worse for the risk side — the exact axis this cannot measure. A concentration result
    is half an answer and must never be deployed on its own.
  * Assumes we quote both sides evenly and that pool splits evenly across the two sides
    (payout = pool x (yes_share + no_share) / 2, consistent with the recorder's
    "snapshot score = yes_share + no_share, max 2.0").
  * Instantaneous snapshots. Real payout integrates over a period; competitors requote.
  * Ranking is by pool. The deployed bot ranks by usd_day, which is a different (and per
    the auditor, worse) ordering — so this measures the LEVER, not the current bot.

SELECTION BIAS — FIXED 2026-07-23 (canon §M6b, defect D1). The sampler used to
`continue` past any contract whose book was empty on a side. Those contracts never
entered the dataset, which made every rate computed from it CONDITIONAL on both books
being non-empty: §M2's "86.1% two-sided" had a pre-filtered denominator and §M1's
capture figures covered only non-empty books. One-sided books are now RECORDED and
SCORED AS R3 FAILURES (payout 0) — which is what they are: under R3 a snapshot without
two-sided liquidity at Target Size is excluded and pays nobody, ours or anyone's.
Set `CONC_KEEP_ONESIDED=0` to reproduce the old, filtered sampling.
The committed dataset `concentration_samples.jsonl` is UNCHANGED (it is the evidence the
committed numbers refer to); `--report` now states which kind of dataset it loaded and
refuses to present its two-sided rate as unconditional.

Run:     python kalshi_concentration_study.py [minutes]      # sample
Report:  python kalshi_concentration_study.py --report
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "concentration_samples.jsonl")
PUB = "https://api.elections.kalshi.com/trade-api/v2"
ALLOW = ("KXAAAGASD", "KXAAAGASW", "KXTEMPDCH", "KXTEMPAUSH",
         "KXTEMPLAXH", "KXTEMPNYCH", "KXTEMPCHIH")

TOTAL_CAPITAL = float(os.environ.get("CONC_TOTAL_CAPITAL", 85))   # frozen live.env
MAX_MARKET = float(os.environ.get("CONC_MAX_MARKET", 15))         # frozen live.env
JOIN_SIZE = float(os.environ.get("CONC_JOIN_SIZE", 20))           # frozen live.env (contracts/side)
MIN_PAYOUT = float(os.environ.get("CONC_MIN_PAYOUT", 1.00))       # LIP rule text
# D1 (canon §M6b): keep contracts whose book is EMPTY on a side. They are real members
# of the population and score as R3 failures; dropping them biases every rate upward.
KEEP_ONESIDED = os.environ.get("CONC_KEEP_ONESIDED", "1").lower() not in ("0", "false", "no")
TICK = 0.01
SPACING_S = 0.35
_last = [0.0]


def _load_scoring():
    """Import the recorder's PURE scoring functions (no side effects, no auth)."""
    import importlib.util
    for cand in (os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"),
                 os.path.join(HERE, "maker_kalshi_recorder.py")):
        if os.path.exists(cand):
            spec = importlib.util.spec_from_file_location("_rec", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("maker_kalshi_recorder.py not found (need its CFTC scoring core)")


REC = _load_scoring()


def get(path):
    wait = SPACING_S - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(PUB + path, headers={"User-Agent": "kalshi-conc-study/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        _last[0] = time.time()
        return json.loads(r.read())


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def sample_once():
    """One paired snapshot: every in-allowlist program's raw book + reward params."""
    progs, cur = [], ""
    for _ in range(4):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    ours = [p for p in progs if (p.get("market_ticker") or "").split("-")[0] in ALLOW]
    rows = []
    for p in ours:
        t = p.get("market_ticker")
        target = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        pool = float(p.get("period_reward") or 0) / 10000.0
        if target <= 0 or df <= 0 or pool <= 0:
            continue
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            continue
        yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
        if (not yl or not nl) and not KEEP_ONESIDED:
            continue        # legacy pre-filter — produced the frozen dataset, see §M6b
        rows.append({"t": t, "target": target, "df": df, "pool": pool,
                     "start": p.get("start_date"), "end": p.get("end_date"),
                     "yl": yl, "nl": nl})
    return rows


def window_days(row):
    """Length of this program's Time Period, in days. RULEBOOK: `period_reward` is the
    TIME PERIOD REWARD -- the total for the whole window, NOT a per-hour rate. Windows
    differ wildly across our own allowlist (gas-daily 13.15h vs gas-weekly 156.08h, both
    $100), so summing raw pool dollars across markets silently over-weights long-window
    programs. Everything comparative must be normalised to $/day."""
    try:
        a = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(row["end"].replace("Z", "+00:00"))
        d = (b - a).total_seconds() / 86400.0
        return d if d > 0 else None
    except Exception:
        return None


def score_market(row, dollars):
    """Payout for ONE market if we commit up to `dollars` of capital, split evenly
    across both sides and quoted AT REFERENCE (best bid each side).

    SIZE MODEL — this is the part that is easy to get catastrophically wrong.
    The deployed bot rests JOIN_SIZE **contracts** per side, capped by capital:
        size_ct = min(JOIN_SIZE, half_dollars / price)
    An earlier version of this study used `half_dollars / price` alone. On a deep-OTM
    ladder strike with a $0.01 best bid that is 1500 contracts against a 439-contract
    book — it handed us ~100% of the qualifying set and produced $156/period on an $85
    account, roughly 24x the live clean-interval measurement. Capital does NOT convert
    to size at cheap strikes; JOIN_SIZE binds first, and pretending otherwise inflates
    share by more than an order of magnitude exactly where the ladder lives.

    Returns (payout_raw, our_contracts, yes_share, no_share, binding) where `binding`
    is 'size' if JOIN_SIZE bound on both sides, 'capital' if capital bound, else 'mixed'.

    D1: a book that is EMPTY on a side scores 0 with binding 'r3_empty'. It is an R3
    failure — the snapshot is excluded and pays nobody — NOT a row to drop. Pre-fix this
    branch was unreachable only because the sampler had already discarded such rows, and
    the `max()` below would have raised ValueError on the empty side.
    """
    yl, nl = [(p, s) for p, s in row["yl"]], [(p, s) for p, s in row["nl"]]
    if not yl or not nl:
        return 0.0, 0.0, 0.0, 0.0, "r3_empty"
    best_y = max(p for p, _ in yl)
    best_n = max(p for p, _ in nl)
    if best_y <= 0 or best_n <= 0:
        return 0.0, 0.0, 0.0, 0.0, "none"
    half = dollars / 2.0
    cap_y, cap_n = half / best_y, half / best_n
    ct_y, ct_n = min(JOIN_SIZE, cap_y), min(JOIN_SIZE, cap_n)
    if ct_y <= 0 or ct_n <= 0:
        return 0.0, 0.0, 0.0, 0.0, "none"
    nbind = sum(1 for c, cap in ((ct_y, cap_y), (ct_n, cap_n)) if c < cap - 1e-9)
    binding = "size" if nbind == 2 else ("capital" if nbind == 0 else "mixed")
    ys, _, _, _ = REC.side_share(yl, [(best_y, ct_y)], row["target"], row["df"], TICK)
    ns, _, _, _ = REC.side_share(nl, [(best_n, ct_n)], row["target"], row["df"], TICK)
    # pool splits evenly across the two sides; score = ys + ns, max 2.0
    return row["pool"] * (ys + ns) / 2.0, ct_y + ct_n, ys, ns, binding


def two_sided_stats(snaps):
    """R3 MARKET-level two-sided census over every market-snapshot in `snaps`.

    Returns (ok, n, empty_side, thin) where
        ok         = both books reach Target Size  -> the snapshot can pay
        empty_side = at least one book is EMPTY
        thin       = both books non-empty but at least one misses Target Size
    R3 is a TARGET SIZE test, not a non-empty test, so `thin` is a failure too —
    conflating the two is how a "two-sided" rate gets quoted too high.
    Nothing about OUR orders enters this: per §M2 our 20 ct was the marginal maker in
    0/304, so a book that misses Target Size without us misses it with us."""
    ok = empty = thin = n = 0
    for s in snaps:
        for r in s.get("rows") or []:
            n += 1
            yl, nl = r.get("yl") or [], r.get("nl") or []
            if not yl or not nl:
                empty += 1
            elif (REC.qualifying_walk(yl, r["target"])[0] is not None
                  and REC.qualifying_walk(nl, r["target"])[0] is not None):
                ok += 1
            else:
                thin += 1
    return ok, n, empty, thin


def dataset_provenance(snaps):
    """Was this dataset sampled WITH or WITHOUT the one-sided filter? (D1/§M6b)

    A rate computed from a filtered dataset is conditional on both books being
    non-empty and must never be quoted as unconditional. Samples taken after the fix
    carry an explicit `keep_onesided` stamp; older ones do not, and for those the only
    honest answer is UNKNOWN — 'zero one-sided rows observed' is exactly what both a
    pre-filtered sample and a genuinely two-sided venue look like."""
    flags = {s.get("keep_onesided") for s in snaps}
    onesided = sum(1 for s in snaps for r in (s.get("rows") or [])
                   if not (r.get("yl") or []) or not (r.get("nl") or []))
    if False in flags:
        return ("PRE-FILTERED (sampler dropped one-sided books) — every rate below is "
                "CONDITIONAL on both books being non-empty; the unconditional rate is "
                "lower. Re-sample with CONC_KEEP_ONESIDED=1 for an unconditional read.")
    if flags == {True} or (True in flags and None not in flags):
        return (f"UNFILTERED (sampler kept one-sided books; {onesided} one-sided "
                f"market-snapshots recorded) — rates below are UNCONDITIONAL.")
    if onesided:
        return (f"UNFILTERED by inference ({onesided} one-sided market-snapshots present, "
                f"no provenance stamp) — rates below are unconditional.")
    return ("UNKNOWN provenance — no `keep_onesided` stamp and zero one-sided books "
            "recorded. That is exactly what the pre-2026-07-23 filter produced "
            "(canon §M6b), so treat every rate below as CONDITIONAL on both books "
            "being non-empty, NOT as a venue-wide rate.")


def score_snapshot(rows, k, mode="oracle"):
    """Allocate TOTAL_CAPITAL across the top-k markets by pool. Returns dict."""
    per = min(TOTAL_CAPITAL / k, MAX_MARKET)
    # RANKING — must not be degenerate. Every pool in the allowlist is $100, so
    # "top-K by pool" collapses to insertion order and measures nothing. A real
    # concentrator picks the markets where OUR capital buys the most share, so rank
    # by this snapshot's own achievable payout at this K's allocation. That is an
    # ORACLE (it uses same-snapshot knowledge a live bot would not have), which makes
    # it an UPPER BOUND on concentration — if concentration still loses here, it loses.
    if mode == "oracle":
        ranked = sorted(rows, key=lambda r: -score_market(r, per)[0])[:k]
    else:                                   # 'asis' control: whatever order the venue gave
        ranked = list(rows)[:k]
    if not ranked:
        return None
    raw, floored, paying, used, shares, sizebound = 0.0, 0.0, 0, 0.0, [], 0
    perday, nowin = 0.0, 0
    two_sided, empty_side = 0, 0
    for r in ranked:
        pay, ct, ys, ns, binding = score_market(r, per)
        raw += pay
        # D1: R3 is MARKET-level and it is the denominator question. Count it here so the
        # report can state the two-sided rate over the population that was actually
        # sampled, instead of over a pre-filtered one.
        if not r["yl"] or not r["nl"]:
            empty_side += 1
        elif (REC.qualifying_walk(r["yl"], r["target"])[0] is not None
              and REC.qualifying_walk(r["nl"], r["target"])[0] is not None):
            two_sided += 1
        # RULEBOOK: the $1 test applies to the TIME PERIOD payout (pay), but comparing
        # across markets requires the per-DAY rate. Both are tracked; never mix them.
        wd = window_days(r)
        if wd:
            perday += (pay if pay >= MIN_PAYOUT else 0.0) / wd
        else:
            nowin += 1
        shares += [ys, ns]
        if binding == "size":
            sizebound += 1
        # capital ACTUALLY used, not capital allotted — JOIN_SIZE often binds first.
        # D1: nothing is deployed into a one-sided book (we would be quoting into an
        # excluded snapshot), so it contributes 0 used capital rather than raising.
        if r["yl"] and r["nl"]:
            by = max(p for p, _ in r["yl"])
            bn = max(p for p, _ in r["nl"])
            used += min(JOIN_SIZE, per / 2 / by) * by + min(JOIN_SIZE, per / 2 / bn) * bn
        if pay >= MIN_PAYOUT:
            floored += pay
            paying += 1
    return {"k": k, "per_market_usd": per, "deployed_usd": used,
            "raw": raw, "floored": floored, "paying": paying,
            "perday": perday, "nowin": nowin,
            "mean_share": sum(shares) / len(shares) if shares else 0.0,
            "sizebound": sizebound,
            "scored": len(ranked), "two_sided": two_sided, "empty_side": empty_side,
            "capped": per >= MAX_MARKET - 1e-9}


def main(minutes):
    end = time.time() + minutes * 60
    n = 0
    while time.time() < end:
        try:
            rows = sample_once()
        except Exception as e:
            print(f"sample error: {e!r}")
            time.sleep(10)
            continue
        if rows:
            ts = datetime.now(timezone.utc).isoformat()
            with open(OUT, "a") as fh:
                # D1: stamp the sampling provenance ON the snapshot. Inferring it later
                # from "no one-sided rows present" is ambiguous — that is precisely why
                # the frozen dataset's bias took a code read to find.
                fh.write(json.dumps({"ts": ts, "keep_onesided": KEEP_ONESIDED,
                                     "rows": rows}, separators=(",", ":")) + "\n")
            n += 1
            one = sum(1 for r in rows if not r["yl"] or not r["nl"])
            print(f"{ts[11:19]} snapshot {n}: {len(rows)} markets "
                  f"({one} one-sided) "
                  f"(pools ${min(r['pool'] for r in rows):.0f}-${max(r['pool'] for r in rows):.0f})")
        time.sleep(30)
    return 0


def report(MODE="oracle"):
    snaps = []
    try:
        for line in open(OUT):
            try:
                d = json.loads(line)
                if d.get("rows"):
                    snaps.append(d)
            except json.JSONDecodeError:
                pass
    except FileNotFoundError:
        print("no samples yet — run the sampler first")
        return 0
    if not snaps:
        print("no samples yet — run the sampler first")
        return 0

    counts = [len(s["rows"]) for s in snaps]
    kmax = max(counts)
    print(f"CONCENTRATION STUDY — {len(snaps)} paired snapshots, "
          f"{sum(counts)} market-snapshots, {min(counts)}-{kmax} markets available")
    print(f"  ranking: {MODE.upper()}"
          + ("  (upper bound on concentration: picks best-K with same-snapshot hindsight)"
             if MODE == "oracle" else "  (control: venue order, no selection skill)"))
    print(f"  window {snaps[0]['ts'][11:19]}..{snaps[-1]['ts'][11:19]}Z   "
          f"capital ${TOTAL_CAPITAL:.0f}, per-market cap ${MAX_MARKET:.0f}, "
          f"min payout ${MIN_PAYOUT:.2f}")
    # D1 / canon §M6b — the denominator has to be declared before any rate is shown.
    ok, n_ms, empty, thin = two_sided_stats(snaps)
    print()
    print("  SAMPLING: " + dataset_provenance(snaps))
    if n_ms:
        print(f"  R3 two-sided (MARKET-level, Target Size on BOTH books): "
              f"{ok}/{n_ms} = {100.0 * ok / n_ms:.1f}%"
              f"   [one side EMPTY {empty}, below Target {thin}]")
    print()
    print("  K = number of markets the SAME capital is spread across, top-K by pool")
    print()
    print(f"  {'K':>3} {'$/mkt':>7} {'deployed':>9} {'RAW $':>9} {'FLOORED $':>10} "
          f"{'paying':>7} {'share':>7} {'$/DAY':>9}")
    print(f"  {'-'*3} {'-'*7} {'-'*9} {'-'*9} {'-'*10} {'-'*7} {'-'*7} {'-'*8}")
    agg = {}
    for k in range(1, kmax + 1):
        got = [score_snapshot(s["rows"], k, MODE) for s in snaps]
        got = [g for g in got if g]
        if not got:
            continue
        n = len(got)
        row = {kk: sum(g[kk] for g in got) / n
               for kk in ("per_market_usd", "deployed_usd", "raw", "floored", "paying",
                          "mean_share", "sizebound", "perday", "nowin")}
        row["capped"] = all(g["capped"] for g in got)
        agg[k] = row
        print(f"  {k:>3} {row['per_market_usd']:>7.2f} {row['deployed_usd']:>9.2f} "
              f"{row['raw']:>9.4f} {row['floored']:>10.4f} {row['paying']:>7.2f} "
              f"{row['mean_share']:>7.4f} {row['perday']:>9.4f}")
    if not agg:
        print("  (nothing scoreable)")
        return 0

    print()
    best_f = max(agg.items(), key=lambda kv: kv[1]["perday"])
    best_r = max(agg.items(), key=lambda kv: kv[1]["raw"])
    print(f"  BEST by $/DAY (the only comparable unit): K={best_f[0]} "
          f"(${best_f[1]['perday']:.4f}/day)")
    print(f"  ($1 threshold is RULEBOOK-CONFIRMED: <$1.00 per Time Period pays ZERO,")
    print(f"   >=$1.00 pays rounded down to the cent. The floor is applied above.)")
    capped_from = [k for k, v in agg.items() if v["capped"]]
    if capped_from:
        print(f"  per-market cap ${MAX_MARKET:.0f} BINDS at K<={max(capped_from)} "
              f"— below that, capital cannot all be deployed without raising it")
    print()
    print("REWARD SIDE ONLY. Fill rate / adverse selection NOT simulatable (no queue")
    print("position). Concentration puts MORE inventory into FEWER markets, which is")
    print("strictly worse on exactly the axis this cannot measure. Half an answer —")
    print("do NOT change live config on this alone.")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(report("asis" if "--asis" in sys.argv else "oracle"))
    mins = next((float(x) for x in sys.argv[1:] if x.replace(".", "").isdigit()), 15.0)
    sys.exit(main(mins))
