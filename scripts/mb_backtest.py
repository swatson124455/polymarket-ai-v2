#!/usr/bin/env python3
"""MB WALK-FORWARD BACKTEST HARNESS — build 1 of the 2026-09-06 mandate
(operator "build": backtest-as-discovery replaces the 4-week forward wait
as the GATE and becomes the DISCOVERY engine).

WHAT IT DOES: replays any trader through the DEPLOYED qualification rules
day-by-day with no lookahead, then judges them ONLY on out-of-sample data
(train/holdout split is mandatory — the screen parameters and the
population study were tuned on capture <= 2026-09-01, so the default
split pins holdout to 2026-09-02T00:00Z onward). Copy lens ONLY (operator
2026-09-06: fade is dead). Ranked output = LCB dollars-per-week at the
$100/market reference basis (feedback_dollars_per_day_is_the_test),
HYPOTHETICAL by standing rule — real money only via docs/MB_GO_CHECKLIST.

CANON CONSUMPTION (MEASUREMENT_CANON rule — no re-implementation);
BASIS = ROI + NET WINNINGS, LADDER-AWARE (operator hardcode 2026-09-06):
  wager ROI atoms (ladder-aware)           -> mb_canon.wager_rois
  fee precedence                           -> mb_canon.canon_fee (inside)
  e-process (per-shift subgrid)            -> mb_canon.roi_e_value
  LCB inversion at the ruled e>=20 bar     -> mb_canon.roi_lcb
  e>=20 bar + $100/wk floor                -> cohort5_qualification consts
  futility                                 -> TIME-BASED 1 week (ruled
                                              2026-09-06; replaces 300)
  token -> outcome mapping                 -> shadow_readout.supplement_outcomes

TWO TRADER CLASSES, one engine:
  roster    priced at OUR recorded shadow fills (mirror3_shadow.jsonl —
            real books, real gate verdicts; ~7wk of priced history)
  firehose  priced at THEIR fill + a MEASURED follow-cost haircut (the
            `haircut` subcommand measures shadow_fill - whale_price over
            roster OK first-buys — no invented constant). Book-dependent
            gates (chase/spread) are NOT replayable without books; the
            price-only PRICE_NO_UPSIDE gate (max_fill 0.98, deployed
            default) IS applied. Both disclosed in output.

DISCLOSED LIMITATIONS (named, not silent — class-not-instance rule):
  * capture left-censoring: "first BUY per token" is first-in-capture;
    capture starts 2026-08-26.
  * the >=1 month history eligibility (operator ruling) is NOT measurable
    from ~11d of capture — every candidate row carries observed-days and
    the 1-month check is a promotion-time data-api step, printed as such.
  * label coverage: tokens outside the ingestion DB cannot be resolved by
    shadow_label_supplement's markets-table join; coverage is printed and
    UNKNOWN-labeled markets are excluded from edges (never guessed).
  * replay day grid is 00:00Z day boundaries (the live grader runs 11:40Z;
    sub-day timing differences are immaterial at daily granularity).

WRITES NO LOCKS, changes no live test. Pure analysis view. Firehose
finds are PROPOSALS for roster promotion — an operator gate, always.

    PYTHONPATH=<mb_readout> python scripts/mb_backtest.py <subcommand> ...
    ... self-test        # offline, no DB, no network
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (load_records)
import band_tracker as bt  # noqa: E402  (e_value, Y_MIN — canon e-process)
import cohort5_qualification as cq  # noqa: E402  (ruled bars, floor)
import mb_canon as mc  # noqa: E402  (canonical estimand)
import mb_sizer as msz  # noqa: E402  (LCB inversion)
import shadow_readout as sr  # noqa: E402  (supplement_outcomes)

# Holdout boundary: everything the screen/study machinery ever saw ends
# 2026-09-01 (population study + window study + funnel params). 09-02
# onward was never used to tune anything — the out-of-sample judge window.
SPLIT_DEFAULT = "2026-09-02T00:00:00Z"
# Operator eligibility ruling 2026-09-06: >= 25 trades (and >= 1 month
# history — the latter is a promotion-time data-api check, see module doc).
ELIGIBILITY_MIN_TRADES = 25
MAX_FILL_DEPLOYED = 0.98  # deployed PRICE_NO_UPSIDE default (copy_watcher)
DAY_S = 86400.0

CACHE_DIR = "/opt/pa2-shared/mb_copyable_data/copyable_cache"
FIREHOSE_DIR = "/opt/pa2-shared/mb_copyable_data/firehose"


def parse_iso_z(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def fmt_num(v, spec: str, dash: str = "-") -> str:
    return dash if v is None else format(v, spec)


# ── pure core ────────────────────────────────────────────────────────────────
def measure_haircut(recs: list[dict]) -> dict | None:
    """Follow-cost distribution: (our shadow fill - whale fill) over roster
    OK first-buys. THE measured price of copying at our latency; the sweep's
    haircut comes from here, never invented. None when no measurable pairs."""
    ds = sorted(float(r["shadow_fill"]) - float(r["whale_price"])
                for r in recs
                if r.get("first_buy") and r.get("verdict") == "OK"
                and isinstance(r.get("shadow_fill"), (int, float))
                and isinstance(r.get("whale_price"), (int, float)))
    if not ds:
        return None
    n = len(ds)

    def q(p: float) -> float:
        return ds[min(n - 1, int(p * n))]

    return {"n": n, "mean": sum(ds) / n, "med": q(0.50), "p75": q(0.75),
            "p90": q(0.90)}


def synth_records(rows: list[dict], trader: str, haircut: float,
                  max_fill: float = MAX_FILL_DEPLOYED) -> tuple[list, int]:
    """Copy-lens canon-shaped records from one wallet's firehose rows
    (time-ascending). LADDER-AWARE (operator hardcode 2026-09-06: "score
    per market can be multiple wagers if they ladder" — first-buy-only
    was FLAWED): one WAGER per (tx, token) — same-tx fills merged at
    their size-weighted VWAP (one order lands as several fills) — and
    EVERY buy wager kept, adds included. Priced at their VWAP + haircut;
    wagers above max_fill gated (the deployed price-only gate); book
    gates not replayable here (module doc). first_buy marks the token's
    first wager (diagnostic only). Returns (records, n_gated)."""
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in rows:
        if r.get("s") != "BUY":
            continue
        tok = str(r.get("tok") or "")
        try:
            p, z, ts = float(r["p"]), float(r["z"]), float(r["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if not tok or z <= 0:
            continue
        key = (str(r.get("tx") or f"~{ts}"), tok)
        g = groups.get(key)
        if g is None:
            groups[key] = {"tok": tok, "cost": p * z, "z": z, "ts": ts}
            order.append(key)
        else:
            g["cost"] += p * z
            g["z"] += z
            g["ts"] = min(g["ts"], ts)
    out: list[dict] = []
    gated = 0
    seen_tok: set[str] = set()
    for key in order:
        g = groups[key]
        fill = g["cost"] / g["z"] + haircut
        first = g["tok"] not in seen_tok
        seen_tok.add(g["tok"])
        if not (0.0 < fill < 1.0) or fill > max_fill:
            gated += 1
            continue
        out.append({"trader": trader, "token_id": g["tok"],
                    "detect_ts": g["ts"], "first_buy": first,
                    "verdict": "OK", "shadow_fill": fill})
    out.sort(key=lambda r: r["detect_ts"])
    return out, gated


def wallet_exits(rows: list[dict]) -> dict[str, float]:
    """token -> first SELL ts at/after that token's first BUY ts (the
    wallet's own exit; refines resolution-only concurrency)."""
    first_buy: dict[str, float] = {}
    exits: dict[str, float] = {}
    for r in rows:
        tok = str(r.get("tok") or "")
        if not tok:
            continue
        try:
            ts = float(r["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if r.get("s") == "BUY":
            first_buy.setdefault(tok, ts)
        elif r.get("s") == "SELL" and tok in first_buy and tok not in exits \
                and ts >= first_buy[tok]:
            exits[tok] = ts
    return exits


def peak_concurrency_replay(records: list[dict], exits: dict[str, float],
                            res_at: dict[str, float], end_ts: float) -> int:
    """Peak simultaneous open copies over the replayed entries. Exit =
    earliest of the wallet's own SELL, market resolution, else end_ts
    (open-to-end upper bound, same lens as the population study)."""
    events: list[tuple[float, int]] = []
    for r in records:
        ts = float(r["detect_ts"])
        tok = str(r["token_id"])
        t_end = min([t for t in (exits.get(tok), res_at.get(tok))
                     if t is not None] or [end_ts])
        events.append((ts, +1))
        if t_end > ts:
            events.append((t_end, -1))
    cur = peak = 0
    for _, d in sorted(events):
        cur += d
        peak = max(peak, cur)
    return peak


FUTILITY_DAYS = 7.0  # operator ruling 2026-09-06: "futility bar time
#                      based 1 week go" — replaces the 300-wager count
#                      in the discovery replay (live grader unconverted)


def daily_replay(records: list[dict], outcomes: dict, res_at: dict,
                 frm: dict, fee_map: dict, epoch: float, end_ts: float,
                 e_bar: float = cq.C1_E_REJECT,
                 futility_days: float = FUTILITY_DAYS,
                 floor_wk: float = cq.WEEKLY_FLOOR_USD) -> dict:
    """Replay the anytime-valid qualification DAY-BY-DAY with no
    lookahead, on the RULED BASIS (operator hardcode 2026-09-06: ROI +
    net winnings, ladder-aware): atoms = per-WAGER ROI (mc.wager_rois,
    all buys incl. ladder adds); e-process = mc.mixture_e_value (same
    mixture grid as the band tracker, ROI support bound); FIRST crossing
    of e_bar locks, with the money floor on NET WINNINGS at the $100/
    wager reference: LCB-ROI x $100 x wagers/elapsed_day x 7 >= floor_wk.
    Futility is TIME-BASED: 1 week from the trial's first wager —
    without e>=e_bar (operator ruling 2026-09-06, replaces the
    300-count). NOTE: the LIVE grader still runs the superseded first-buy
    per-market basis pending its conversion/re-registration — replay
    verdicts here follow the RULING, and may differ from live locks.
    Tokens with an outcome but NO res_at cannot be placed in time ->
    excluded, counted."""
    tokens = {str(r["token_id"]) for r in records}
    placeable = {t: res_at[t] for t in tokens if t in res_at and t in outcomes}
    n_unplaceable = sum(1 for t in tokens if t in outcomes and t not in res_at)
    verdict, verdict_ts = "ACCRUING", None
    last = {"n": 0, "e": None, "roi": None, "lcb": None}
    d = (int(epoch // DAY_S) + 1) * DAY_S
    while d <= end_ts:
        day_out = {t: outcomes[t] for t, rt in placeable.items() if rt <= d}
        # correlated-atom fix (operator "fix go" 2026-09-06): one atom
        # per market; the money rate stays conservative (markets/day).
        seq = mc.market_position_rois(records, day_out, frm or {},
                                      fee_map or {}, epoch=epoch)
        rois = [x for _, _, x, _ in seq]
        n = len(rois)
        if n:
            ev = mc.mixture_e_value(rois)
            lcb = mc.roi_lcb(rois, e_bar=e_bar)
            last = {"n": n, "e": ev, "roi": sum(rois) / n, "lcb": lcb}
            if ev >= e_bar:
                el_days = max((d - epoch) / DAY_S, 1e-9)
                wk = (lcb * 100.0 * (n / el_days) * 7.0
                      if lcb is not None else None)
                verdict = ("QUALIFIES" if wk is not None and wk >= floor_wk
                           else "E-PASS BELOW MONEY FLOOR")
                verdict_ts = d
                break
        if (d - epoch) >= futility_days * DAY_S:
            verdict = "NOT DEMONSTRATED (futility 1wk)"
            verdict_ts = d
            break
        d += DAY_S
    return {"verdict": verdict, "verdict_ts": verdict_ts,
            "unplaceable_resolved": n_unplaceable, **last}


def holdout_metrics(records: list[dict], outcomes: dict, frm: dict,
                    fee_map: dict, split_ts: float, end_ts: float,
                    res_at: dict | None = None) -> dict:
    """Judged ONLY out-of-sample, on the RULED BASIS (ROI + net winnings,
    ladder-aware): per-wager ROIs over wagers with detect_ts >= split_ts;
    LCB over those atoms alone (train never touches the ranking number).
    Reported BOTH ways per the hardcode: roi_lcb / roi_realized (dollars
    returned per dollar staked) and wk_net_lcb / wk_net_real (net
    winnings per week at the $100/wager reference — HYPOTHETICAL).

    Label-lookahead guard: a market whose KNOWN resolved_at is after
    end_ts was not resolved at judge time — excluded. Tokens without a
    res_at (DB-sourced labels) cannot be time-gated and pass through —
    the disclosed asymmetry from cmd_replay's merge note."""
    if res_at:
        outcomes = {t: o for t, o in outcomes.items()
                    if res_at.get(t) is None or res_at[t] <= end_ts}
    seq = mc.market_position_rois(records, outcomes, frm or {},
                                  fee_map or {}, epoch=split_ts)
    rois = [x for _, _, x, _ in seq]
    n_wagers = sum(k for _, _, _, k in seq)
    n = len(rois)
    days = max((end_ts - split_ts) / DAY_S, 1e-9)
    out = {"n_holdout": n, "wagers": n_wagers,
           "holdout_days": round(days, 2),
           "roi_lcb": None, "roi_realized": None,
           "wk_net_lcb": None, "wk_net_real": None}
    if not n:
        return out
    lcb = mc.roi_lcb(rois)
    mean_roi = sum(rois) / n
    out["roi_lcb"] = lcb
    out["roi_realized"] = mean_roi
    if lcb is not None:
        out["wk_net_lcb"] = lcb * 100.0 * (n / days) * 7.0
    out["wk_net_real"] = mean_roi * 100.0 * (n / days) * 7.0
    return out


# ── WITH-EXITS LENS (operator activation 2026-09-07: "with-exits D1 = b,
# D2 fresh epoch at activation, D3 analysis lens") ───────────────────────
WE_EPOCH_ISO = "2026-09-07T03:30:00Z"
# D2: the lens's own fresh epoch, set at activation (clock measured
# 03:10:39Z the same session; next clean boundary). IMMUTABLE — epochs
# never move. Only positions ENTERED at/after this instant are graded.
SELLS_SINK_DEFAULT = "/opt/pa2-shared/mirror3_shadow_sells.jsonl"


def sells_to_exits(rows: list[dict]) -> dict[str, dict[str, tuple]]:
    """trader -> {token -> (first SELL detect_ts, whale_price)} from the
    SELL sink's records. First SELL per (trader, token) wins. Pure."""
    out: dict[str, dict[str, tuple]] = {}
    for r in rows:
        if r.get("side") != "SELL":
            continue
        tr = str(r.get("trader") or "").lower()
        tok = str(r.get("token_id") or "")
        try:
            ts, px = float(r["detect_ts"]), float(r["whale_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if not tr or not tok:
            continue
        d = out.setdefault(tr, {})
        if tok not in d or ts < d[tok][0]:
            d[tok] = (ts, px)
    return out


def with_exits_metrics(records: list[dict], outcomes: dict, frm: dict,
                       fee_map: dict, exits: dict, haircut: float,
                       epoch: float, end_ts: float,
                       res_at: dict | None = None) -> dict | None:
    """Per-wallet WITH-EXITS lens numbers (D3: analysis only — reported
    beside the registered estimand, never a gate/stake). None when the
    wallet has no graded post-epoch positions."""
    seq = mc.market_position_rois_with_exits(
        records, outcomes, frm or {}, fee_map or {}, exits, haircut,
        epoch=epoch, res_at=res_at)
    rois = [x for _, _, x, _, _ in seq]
    n = len(rois)
    if not n:
        return None
    n_ex = sum(1 for *_, e in seq if e)
    days = max((end_ts - epoch) / DAY_S, 1e-9)
    mean = sum(rois) / n
    lcb = mc.roi_lcb(rois)
    return {"n": n, "exited": n_ex, "roi_mean": mean, "roi_lcb": lcb,
            "wk_net_real": mean * 100.0 * (n / days) * 7.0,
            "wk_net_lcb": (lcb * 100.0 * (n / days) * 7.0)
            if lcb is not None else None}


COV_FLAG_DEFAULT = 0.5  # ROI-review A3 (2026-09-06): per-wallet label
#                         coverage on the leaderboard, sub-threshold rows
#                         UNKNOWN-flagged. 0.5 = the majority-unknown line
#                         (a flagged row is judged on FEWER THAN HALF of
#                         its entry tokens) — display flag only, never a
#                         gate; ranking and verdicts unchanged.


def wallet_coverage(records: list[dict], outcomes: dict) -> tuple[int, int]:
    """(distinct entry tokens, labeled among them) for ONE wallet — the
    per-row version of cmd_replay's board-wide coverage line. Pure."""
    toks = {str(r["token_id"]) for r in records if r.get("token_id")}
    return len(toks), sum(1 for t in toks if t in outcomes)


def res_at_map(cache: dict) -> dict[str, float]:
    """token -> resolved_at epoch seconds from the resolutions cache
    (both legs; same pattern as trader_funnel's res_at block)."""
    out: dict[str, float] = {}
    for m in cache.values():
        if not (isinstance(m, dict) and m.get("resolved_at")):
            continue
        try:
            t_end = datetime.fromisoformat(
                str(m["resolved_at"]).replace("Z", "+00:00"))
            if t_end.tzinfo is None:
                t_end = t_end.replace(tzinfo=timezone.utc)
            ts = t_end.timestamp()
        except ValueError:
            continue
        for k in ("yes_token_id", "no_token_id"):
            if m.get(k):
                out[str(m[k])] = ts
    return out


def screen_candidates(wrows: list[dict], conc: dict[str, int],
                      min_trades: int, max_conc: int,
                      sens_bars: tuple = (3, 5, 10, 20, 50)) -> tuple:
    """(candidates, sensitivity, n_conc_unknown). Tailability = measured
    peak concurrency <= max_conc (operator-supplied — hyper-concurrent
    whales are NOT tailable, ruling 2026-09-06; no default here).
    A wallet with >= min_trades but NO concurrency row is UNKNOWN — the
    alarm state — excluded and counted, never silently passed."""
    cands, unknown = [], 0
    sens = {b: 0 for b in sens_bars}
    for w in wrows:
        if int(w.get("n") or 0) < min_trades:
            continue
        pc = conc.get(str(w.get("w")))
        if pc is None:
            unknown += 1
            continue
        for b in sens_bars:
            if pc <= b:
                sens[b] += 1
        if pc <= max_conc:
            cands.append({"w": str(w["w"]), "n": int(w["n"]),
                          "peak_conc": int(pc),
                          "usd_sum": float(w.get("usd_sum") or 0.0),
                          "buy": int(w.get("buy") or 0)})
    return cands, sens, unknown


# ── I/O shell ────────────────────────────────────────────────────────────────
def _load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    return out


def cmd_haircut(args) -> int:
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    h = measure_haircut(recs)
    if h is None:
        print("[haircut] 0 measurable (OK first-buy) pairs — cannot measure")
        return 2
    print(f"[haircut] follow-cost = shadow_fill - whale_price over "
          f"{h['n']} roster OK first-buys:")
    print(f"  mean {h['mean']:+.4f}  med {h['med']:+.4f}  "
          f"p75 {h['p75']:+.4f}  p90 {h['p90']:+.4f}")
    print("  (sweep default = med; run sensitivity at p75 — disclosed "
          "parameter, measured never invented)")
    return 0


def cmd_screen(args) -> int:
    wrows = _load_jsonl(args.wallets)
    assert wrows, "EMPTY wallets file - ABORT"
    conc = {str(r["w"]): int(r["peak_conc"]) for r in _load_jsonl(args.conc)}
    assert conc, "EMPTY peak-concurrency file - ABORT"
    cands, sens, unknown = screen_candidates(
        wrows, conc, args.min_trades, args.max_conc)
    print(f"[screen] wallets={len(wrows)}  >= {args.min_trades} trades + "
          f"conc known: candidates at max_conc={args.max_conc}: {len(cands)}")
    print(f"[screen] conc-UNKNOWN among trade-eligible: {unknown} "
          f"(excluded, counted — unknown is the alarm)")
    print("[screen] sensitivity (candidates if max_conc were): " +
          ", ".join(f"<={b}: {n}" for b, n in sorted(sens.items())))
    with open(args.out, "w") as f:
        for c in sorted(cands, key=lambda x: -x["usd_sum"]):
            f.write(json.dumps(c) + "\n")
    print(f"[screen] wrote {len(cands)} candidates -> {args.out}")
    return 0


def cmd_extract(args) -> int:
    keep = {str(c["w"]) for c in _load_jsonl(args.candidates)}
    assert keep, "EMPTY candidate list - ABORT"
    n_in = n_kept = 0
    toks: set[str] = set()
    with open(args.out, "w") as out:
        for path in args.files:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt") as f:
                for ln in f:
                    n_in += 1
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if str(r.get("w")) in keep:
                        out.write(ln if ln.endswith("\n") else ln + "\n")
                        n_kept += 1
                        if r.get("tok"):
                            toks.add(str(r["tok"]))
    with open(args.tokens_out, "w") as f:
        for t in sorted(toks):
            f.write(json.dumps({"token_id": t}) + "\n")
    print(f"[extract] {n_in} rows read, {n_kept} kept for {len(keep)} "
          f"candidates; {len(toks)} distinct tokens -> {args.tokens_out} "
          f"(label via shadow_label_supplement.py --shadow {args.tokens_out})")
    assert n_kept, "0 candidate rows extracted - ABORT (query-shape check)"
    return 0


def files_to_process(all_files: list[str], processed: set[str],
                     today_yyyymmdd: str) -> list[str]:
    """Complete, unprocessed firehose day files. A file whose name date is
    >= today is still being written — excluded. Pure."""
    import re
    out = []
    for p in sorted(all_files):
        base = os.path.basename(p)
        m = re.search(r"firehose_(\d{8})\.jsonl\.gz$", base)
        if not m or m.group(1) >= today_yyyymmdd or base in processed:
            continue
        out.append(p)
    return out


def should_rescreen(force: bool, cand_exists: bool, weekday: int,
                    last_rescreen: str, today: str) -> bool:
    """Monday (UTC) once per day, first run ever, or --rescreen. Pure."""
    return force or not cand_exists or (weekday == 0
                                        and last_rescreen != today)


def cmd_daily_extract(args) -> int:
    """Incremental daily extraction (cron stage, operator GO 2026-09-06).
    Mondays (or --rescreen): re-run the screen against the CURRENT study
    files (frozen Sept-2 inputs today — a fresh study is picked up
    automatically when those files change) and rebuild rows from scratch.
    Other days: append rows from newly-completed day files only; emit the
    NEW tokens for the label supplement. 0 new files is a normal no-op."""
    os.makedirs(args.outdir, exist_ok=True)
    state_path = os.path.join(args.outdir, "daily_state.json")
    rows_path = os.path.join(args.outdir, "candidate_rows.jsonl")
    cand_path = os.path.join(args.outdir, "candidates.jsonl")
    tokens_path = os.path.join(args.outdir, "sweep_tokens.jsonl")
    newtok_path = os.path.join(args.outdir, "sweep_tokens_new.jsonl")
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y%m%d")
    state = json.load(open(state_path)) if os.path.exists(state_path) \
        else {"processed": [], "last_rescreen": ""}
    rescreen = should_rescreen(args.rescreen, os.path.exists(cand_path),
                               now.weekday(),
                               state.get("last_rescreen", ""), today)
    if rescreen:
        from types import SimpleNamespace as NS
        rc = cmd_screen(NS(wallets=args.wallets, conc=args.conc,
                           min_trades=args.min_trades,
                           max_conc=args.max_conc, out=cand_path))
        if rc:
            return rc
        state = {"processed": [], "last_rescreen": today}
        for p in (rows_path, tokens_path):
            if os.path.exists(p):
                os.remove(p)   # full rebuild under the fresh screen
    files = files_to_process(args.files, set(state["processed"]), today)
    keep = {str(c["w"]) for c in _load_jsonl(cand_path)}
    assert keep, "EMPTY candidate list - ABORT"
    known = {str(r["token_id"]) for r in _load_jsonl(tokens_path)} \
        if os.path.exists(tokens_path) else set()
    n_in = n_kept = 0
    new_toks: set[str] = set()
    with open(rows_path, "a") as out:
        for path in files:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt") as f:
                for ln in f:
                    n_in += 1
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if str(r.get("w")) in keep:
                        out.write(ln if ln.endswith("\n") else ln + "\n")
                        n_kept += 1
                        t = str(r.get("tok") or "")
                        if t and t not in known:
                            new_toks.add(t)
    for path, mode in ((newtok_path, "w"), (tokens_path, "a")):
        with open(path, mode) as f:
            for t in sorted(new_toks):
                f.write(json.dumps({"token_id": t}) + "\n")
    state["processed"] = sorted(set(state["processed"])
                                | {os.path.basename(p) for p in files})
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)
    print(f"[daily] {'RESCREEN(max_conc=' + str(args.max_conc) + ') + ' if rescreen else ''}"
          f"{len(files)} new file(s) ({n_in} rows read), {n_kept} rows "
          f"appended, {len(new_toks)} new tokens -> {newtok_path}")
    return 0


def cmd_daily_replay(args) -> int:
    """Daily leaderboard refresh: measure the follow-cost haircut from the
    live shadow sink (no hardcode — printed), then run BOTH boards."""
    from types import SimpleNamespace as NS
    recs = az.load_records(args.log)
    assert recs, "EMPTY shadow log - ABORT"
    h = measure_haircut(recs)
    assert h is not None, "no measurable haircut pairs - ABORT"
    print(f"[daily] measured haircut med {h['med']:+.4f} p90 "
          f"{h['p90']:+.4f} (n={h['n']}) - med used for firehose pricing")
    common = dict(resolutions=args.resolutions,
                  fee_rate_map=args.fee_rate_map, fee_map=args.fee_map,
                  split=args.split, end=None, top=args.top,
                  cov_flag=getattr(args, "cov_flag", COV_FLAG_DEFAULT),
                  sells=getattr(args, "sells", SELLS_SINK_DEFAULT))
    rc1 = cmd_replay(NS(source="roster", rows=args.log, haircut=None,
                        out=os.path.join(args.outdir,
                                         "leaderboard_roster.jsonl"),
                        **common))
    rc2 = cmd_replay(NS(source="firehose", rows=args.rows,
                        haircut=h["med"],
                        out=os.path.join(args.outdir,
                                         "leaderboard_firehose.jsonl"),
                        **common))
    return rc1 or rc2


def cmd_replay(args) -> int:
    split_ts = parse_iso_z(args.split)
    end_ts = parse_iso_z(args.end) if args.end else \
        datetime.now(timezone.utc).timestamp()
    cache = json.load(open(args.resolutions))
    r_at = res_at_map(cache)
    frm = json.load(open(args.fee_rate_map)) \
        if os.path.exists(args.fee_rate_map) else {}
    fee_map = json.load(open(args.fee_map)) \
        if os.path.exists(args.fee_map) else {}

    per_wallet: dict[str, list[dict]] = {}
    if args.source == "firehose":
        assert args.haircut is not None, \
            "--haircut required for firehose (measure via `haircut` cmd)"
        rows = _load_jsonl(args.rows)
        assert rows, "EMPTY rows file - ABORT"
        rows.sort(key=lambda r: float(r.get("t") or 0))
        by_w: dict[str, list[dict]] = {}
        for r in rows:
            by_w.setdefault(str(r.get("w")), []).append(r)
        gated_total = 0
        exits_by_w = {}
        for w, rws in by_w.items():
            recs, gated = synth_records(rws, w, args.haircut)
            gated_total += gated
            per_wallet[w] = recs
            exits_by_w[w] = wallet_exits(rws)
        print(f"[replay] firehose: {len(by_w)} wallets, haircut "
              f"{args.haircut:+.4f} (measured — see `haircut`), "
              f"{gated_total} entries gated by max_fill "
              f"{MAX_FILL_DEPLOYED} (book gates not replayable — disclosed)")
    else:
        recs = az.load_records(args.rows)
        assert recs, "EMPTY shadow log - ABORT"
        we_hc = measure_haircut(recs)  # with-exits lens D1(b) transfer
        for r in recs:
            per_wallet.setdefault(str(r.get("trader", "")).lower(),
                                  []).append(r)
        exits_by_w = {w: {} for w in per_wallet}
        print(f"[replay] roster shadow records: {len(per_wallet)} traders "
              f"(recorded fills + real gate verdicts; no haircut)")

    all_tokens = sorted({str(r["token_id"]) for recs in per_wallet.values()
                         for r in recs if r.get("token_id")})
    supp = sr.supplement_outcomes(args.resolutions, all_tokens)
    # grader parity: the deployed pipeline merges DB outcomes + supplement
    # (trader_funnel.run); cache-only labels would silently diverge the
    # replayed e-values from the rules being replayed. DB tokens lack a
    # res_at, so they count in holdout edges but stay unplaceable (counted)
    # on the day-by-day grid — disclosed asymmetry, never guessed around.
    if os.environ.get("DATABASE_URL"):
        import asyncio
        db_out = asyncio.run(sr.fresh_outcomes(all_tokens))
        outcomes = sr.merge_outcomes(db_out, supp)
        src_note = f"DB {len(db_out)} + cache {len(supp)} merged"
    else:
        outcomes = supp
        src_note = "cache ONLY (no DATABASE_URL — grader merges DB too)"
    n_lab = len(set(all_tokens) & set(outcomes))
    print(f"[replay] label coverage: {n_lab}/{len(all_tokens)} entry tokens "
          f"({100.0 * n_lab / max(len(all_tokens), 1):.1f}%; {src_note}) "
          f"— unresolved excluded from edges, never guessed")

    lb = []
    for w, recs in sorted(per_wallet.items()):
        if not recs:
            continue
        recs.sort(key=lambda r: float(r.get("detect_ts") or 0))
        epoch = float(recs[0].get("detect_ts") or 0)
        rep = daily_replay(recs, outcomes, r_at, frm, fee_map, epoch, end_ts)
        hold = holdout_metrics(recs, outcomes, frm, fee_map, split_ts,
                               end_ts, res_at=r_at)
        pc = peak_concurrency_replay(recs, exits_by_w.get(w, {}), r_at,
                                     end_ts)
        obs_days = (float(recs[-1]["detect_ts"]) - epoch) / DAY_S
        n_tok, n_tok_lab = wallet_coverage(recs, outcomes)
        lb.append({"w": w, "entries": len(recs),
                   "observed_days": round(obs_days, 1),
                   "peak_conc_replay": pc,
                   "tokens": n_tok, "tokens_labeled": n_tok_lab,
                   "cov_pct": round(100.0 * n_tok_lab / max(n_tok, 1), 1),
                   **rep,
                   **{f"ho_{k}": v for k, v in hold.items()}})
    lb.sort(key=lambda x: -(x["ho_wk_net_lcb"]
                            if x["ho_wk_net_lcb"] is not None else -1e18))
    with open(args.out, "w") as f:
        for row in lb:
            f.write(json.dumps(row) + "\n")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"===== {stamp} MB BACKTEST LEADERBOARD (source={args.source}, "
          f"split={args.split}, basis=ROI+NET-WINNINGS ladder-aware "
          f"[operator hardcode 2026-09-06], HYPOTHETICAL $100/wager ref) "
          f"=====")
    print("[judged OUT-OF-SAMPLE only: holdout wagers >= split; LCB over "
          "holdout ROI atoms alone; verdicts = day-by-day replay on the "
          "ruled basis (live grader still on superseded basis pending "
          "conversion); >=1mo-history check = promotion-time data-api]")
    cov_flag = getattr(args, "cov_flag", COV_FLAG_DEFAULT)
    n_low = sum(1 for r in lb if r["cov_pct"] < cov_flag * 100.0)
    print(f"[cov% = labeled/distinct entry tokens per wallet (review A3); "
          f"! = below {cov_flag * 100:.0f}%: row judged on a MINORITY of "
          f"its markets, UNKNOWN-flagged — {n_low}/{len(lb)} wallets "
          f"flagged; display only, ranking unchanged]")
    print(f"{'WALLET':<14} {'$wk_net_lcb':>11} {'$wk_net_real':>12} "
          f"{'roi_lcb':>8} {'roi_real':>8} {'n_ho':>5} {'conc':>5} "
          f"{'wagers':>6} {'cov%':>6} {'verdict(replay)'}")
    shown = 0
    for row in lb:
        if shown >= args.top:
            break
        shown += 1
        cov_cell = (f"{row['cov_pct']:.0f}"
                    + ("!" if row["cov_pct"] < cov_flag * 100.0 else " "))
        print(f"{row['w'][:12] + '..':<14} "
              f"{fmt_num(row['ho_wk_net_lcb'], '+.2f'):>11} "
              f"{fmt_num(row['ho_wk_net_real'], '+.2f'):>12} "
              f"{fmt_num(row['ho_roi_lcb'], '+.3f'):>8} "
              f"{fmt_num(row['ho_roi_realized'], '+.3f'):>8} "
              f"{row['ho_n_holdout']:>5} {row['peak_conc_replay']:>5} "
              f"{row['entries']:>6} {cov_cell:>6} {row['verdict']}")
    print(f"[replay] full leaderboard ({len(lb)} wallets) -> {args.out}")

    # WITH-EXITS LENS (operator activation 2026-09-07: D1=b, D2 fresh
    # epoch, D3 analysis lens) — roster only (exits from the SELL sink);
    # a firehose variant from the wallets' own SELL rows is NAMED future
    # work. Printed AFTER and BESIDE the board above; changes nothing.
    sells_path = getattr(args, "sells", None)
    if args.source == "roster" and sells_path and os.path.exists(sells_path):
        we_epoch = parse_iso_z(WE_EPOCH_ISO)
        exits_all = sells_to_exits(_load_jsonl(sells_path))
        hc = we_hc["med"] if we_hc else None
        if hc is None:
            print("[with-exits] no measurable BUY follow-cost pairs — "
                  "lens NOT computed (D1(b) needs the measured median)")
        else:
            rows_we = []
            for w, wrecs in sorted(per_wallet.items()):
                m = with_exits_metrics(wrecs, outcomes, frm, fee_map,
                                       exits_all.get(w, {}), hc,
                                       we_epoch, end_ts, res_at=r_at)
                if m is not None:
                    rows_we.append({"w": w, **m})
            rows_we.sort(key=lambda x: -x["wk_net_real"])
            print(f"===== WITH-EXITS LENS (activated 2026-09-07: D1=b "
                  f"exit @ whale SELL - buy-side follow-cost med "
                  f"{hc:+.4f} [transfer, disclosed]; D2 epoch "
                  f"{WE_EPOCH_ISO}; D3 ANALYSIS ONLY - never a "
                  f"gate/stake; HYPOTHETICAL $100/wager) =====")
            if not rows_we:
                print("[with-exits] 0 wallets with graded post-epoch "
                      "positions yet — accruing (expected while the "
                      "epoch is young)")
            else:
                print(f"{'WALLET':<14} {'$wk_net_real':>12} "
                      f"{'$wk_net_lcb':>11} {'roi_mean':>8} "
                      f"{'roi_lcb':>8} {'n':>4} {'exited':>6}")
                for row in rows_we[:args.top]:
                    print(f"{row['w'][:12] + '..':<14} "
                          f"{row['wk_net_real']:>+12.2f} "
                          f"{fmt_num(row['wk_net_lcb'], '+.2f'):>11} "
                          f"{row['roi_mean']:>+8.3f} "
                          f"{fmt_num(row['roi_lcb'], '+.3f'):>8} "
                          f"{row['n']:>4} {row['exited']:>6}")
                print(f"[with-exits] {len(rows_we)} wallets with graded "
                      f"post-epoch positions ({sum(r['exited'] for r in rows_we)} "
                      f"exited positions lens-wide)")
    return 0


# ── self-test (offline) ──────────────────────────────────────────────────────
def _self_test() -> int:
    print("SELF-TEST — mb_backtest (offline)\n")
    ok = True
    # [haircut] median/percentiles over synthetic pairs
    recs = [{"first_buy": True, "verdict": "OK", "shadow_fill": 0.52,
             "whale_price": 0.50},
            {"first_buy": True, "verdict": "OK", "shadow_fill": 0.61,
             "whale_price": 0.60},
            {"first_buy": False, "verdict": "OK", "shadow_fill": 0.9,
             "whale_price": 0.1},   # not first-buy: excluded
            {"first_buy": True, "verdict": "NO_BOOK", "shadow_fill": None,
             "whale_price": 0.5}]   # not OK: excluded
    h = measure_haircut(recs)
    ok1 = h is not None and h["n"] == 2 and abs(h["med"] - 0.02) < 1e-12 \
        and measure_haircut([]) is None
    print(f"  [haircut] OK-first-buy pairs only; empty -> None : {ok1}")
    ok &= ok1
    # [synth] LADDER-AWARE (hardcode 2026-09-06): same-tx fills merge to
    # one wager at VWAP; adds in new txs are SEPARATE wagers; SELLs
    # ignored; max_fill gate; haircut applied
    rows = [{"s": "BUY", "tok": "t1", "p": 0.50, "z": 10.0, "t": 100.0,
             "tx": "0xa"},
            {"s": "BUY", "tok": "t1", "p": 0.60, "z": 30.0, "t": 101.0,
             "tx": "0xa"},   # same tx: merges -> VWAP 0.575
            {"s": "SELL", "tok": "t1", "p": 0.55, "z": 5.0, "t": 150.0,
             "tx": "0xs"},
            {"s": "BUY", "tok": "t1", "p": 0.52, "z": 5.0, "t": 200.0,
             "tx": "0xb"},   # LADDER ADD: separate wager, kept
            {"s": "BUY", "tok": "t2", "p": 0.975, "z": 5.0, "t": 300.0,
             "tx": "0xc"}]   # gated @0.98
    sy, gated = synth_records(rows, "0xw", 0.02)
    ok2 = (len(sy) == 2 and gated == 1
           and abs(sy[0]["shadow_fill"] - 0.595) < 1e-12  # VWAP+haircut
           and abs(sy[1]["shadow_fill"] - 0.54) < 1e-12   # add wager
           and sy[0]["first_buy"] is True
           and sy[1]["first_buy"] is False               # add, not first
           and all(r["verdict"] == "OK" for r in sy))
    print(f"  [synth] tx-merge VWAP + ladder adds kept + gate : {ok2}")
    ok &= ok2
    # [exits] first SELL at/after first BUY only
    ex = wallet_exits(rows)
    ok3 = ex == {"t1": 150.0}
    print(f"  [exits] first SELL after entry only : {ok3}")
    ok &= ok3
    # [conc] SELL exit beats resolution; open-to-end bound
    e_recs = [{"token_id": "t1", "detect_ts": 100.0},
              {"token_id": "t3", "detect_ts": 120.0}]
    ok4 = (peak_concurrency_replay(e_recs, {"t1": 150.0}, {}, 1000.0) == 2
           and peak_concurrency_replay(e_recs, {"t1": 110.0}, {}, 1000.0) == 1
           and peak_concurrency_replay([], {}, {}, 1000.0) == 0)
    print(f"  [conc] SELL-exit refinement + empty=0 : {ok4}")
    ok &= ok4
    # [replay] no lookahead: verdict day = first day resolutions suffice.
    # 30 markets, edges +0.5 (e_value([0.5]*30) > 20): all entered day 0,
    # resolutions land day 3 -> crossing must be day 3+1 boundary, not day 1.
    t0 = parse_iso_z("2026-09-01T00:00:00Z")
    recs30 = [{"trader": "0xw", "token_id": f"m{i}", "detect_ts": t0 + i,
               "first_buy": True, "verdict": "OK", "shadow_fill": 0.48}
              for i in range(30)]
    outc = {f"m{i}": 1 for i in range(30)}
    r_at3 = {f"m{i}": t0 + 3 * DAY_S + 60 for i in range(30)}
    rep = daily_replay(recs30, outc, r_at3, {}, {}, t0, t0 + 10 * DAY_S)
    exp_day = (int((t0 + 3 * DAY_S + 60) // DAY_S) + 1) * DAY_S
    ok5 = (rep["verdict"] == "QUALIFIES" and rep["verdict_ts"] == exp_day
           and rep["n"] == 30)
    print(f"  [replay] locks on first day labels EXIST, not before : {ok5}")
    ok &= ok5
    # [replay] money floor: under 1-week futility an e-pass implies
    # throughput, so the below-floor branch fires only for MARGINAL
    # crossings (lcb ~ 0). Calibrated live 2026-09-06: 33 wagers of ROI
    # +0.2 -> e=32.6 crosses, roi_lcb=0.0242, wk = 0.0242*100*(33/6)*7
    # ~= $93 < $100 floor.
    recs_marg = [{"trader": "0xw", "token_id": f"s{i}",
                  "detect_ts": t0 + i, "first_buy": True,
                  "verdict": "OK", "shadow_fill": 0.5} for i in range(33)]
    # outcome 0.6103 makes each ROI exactly +0.2 at flat 2% fee:
    # (o - 0.5 - 0.01)/0.5 = 0.2 => o = 0.61
    outc_s = {f"s{i}": 0.61 for i in range(33)}
    r_at_s = {f"s{i}": t0 + 5.5 * DAY_S for i in range(33)}
    rep2 = daily_replay(recs_marg, outc_s, r_at_s, {}, {}, t0,
                        t0 + 10 * DAY_S)
    ok6 = rep2["verdict"] == "E-PASS BELOW MONEY FLOOR"
    print(f"  [replay] marginal e-pass fails $100/wk floor : {ok6}")
    ok &= ok6
    # [replay] futility: 300 resolved null-edge markets -> NOT DEMONSTRATED
    recs_null = [{"trader": "0xw", "token_id": f"n{i}", "detect_ts": t0 + i,
                  "first_buy": True, "verdict": "OK",
                  "shadow_fill": 0.50} for i in range(300)]
    outc_n = {f"n{i}": (1 if i % 2 == 0 else 0) for i in range(300)}
    r_at_n = {f"n{i}": t0 + DAY_S / 2 for i in range(300)}
    rep3 = daily_replay(recs_null, outc_n, r_at_n, {}, {}, t0,
                        t0 + 10 * DAY_S)
    exp_fut_day = (int(t0 // DAY_S) + 1) * DAY_S
    while exp_fut_day - t0 < FUTILITY_DAYS * DAY_S:
        exp_fut_day += DAY_S
    ok7 = (rep3["verdict"] == "NOT DEMONSTRATED (futility 1wk)"
           and rep3["verdict_ts"] == exp_fut_day and rep3["n"] == 300)
    # a trial younger than a week with e<20 must NOT be futilitied
    rep3b = daily_replay(recs_null, outc_n, r_at_n, {}, {}, t0,
                         t0 + 6 * DAY_S)
    ok7 = ok7 and rep3b["verdict"] == "ACCRUING"
    print(f"  [replay] TIME futility: locks at 1wk, never before "
          f"(ruled 2026-09-06) : {ok7}")
    ok &= ok7
    # [replay] unplaceable resolved tokens counted, excluded
    rep4 = daily_replay(recs30[:1], {"m0": 1}, {}, {}, {}, t0,
                        t0 + 2 * DAY_S)
    ok8 = rep4["unplaceable_resolved"] == 1 and rep4["n"] == 0
    print(f"  [replay] outcome w/o resolved_at -> excluded + counted : {ok8}")
    ok &= ok8
    # [holdout] split excludes train markets from the ranking number
    split = t0 + 5 * DAY_S
    recs_mix = ([{"trader": "0xw", "token_id": f"tr{i}", "detect_ts": t0 + i,
                  "first_buy": True, "verdict": "OK", "shadow_fill": 0.48}
                 for i in range(10)]
                + [{"trader": "0xw", "token_id": f"ho{i}",
                    "detect_ts": split + i, "first_buy": True,
                    "verdict": "OK", "shadow_fill": 0.48}
                   for i in range(5)])
    outc_m = {f"tr{i}": 1 for i in range(10)} | {f"ho{i}": 1 for i in range(5)}
    hm = holdout_metrics(recs_mix, outc_m, {}, {}, split, split + 7 * DAY_S)
    # ROI basis exact: (1 - 0.48 - 0.02*0.48)/0.48 per winning wager
    roi_exp = (1.0 - 0.48 - 0.02 * 0.48) / 0.48
    ok9 = (hm["n_holdout"] == 5 and hm["holdout_days"] == 7.0
           and abs(hm["roi_realized"] - roi_exp) < 1e-12
           and abs(hm["wk_net_real"] - roi_exp * 100 * (5 / 7.0) * 7) < 1e-9)
    print(f"  [holdout] train excluded; ROI + $net exact : {ok9}")
    ok &= ok9
    # [canon-roi] ladder atoms: repeats count (first_buy NOT required);
    # ROI support bound honored by the generalized mixture
    lad = [{"detect_ts": 1.0, "first_buy": True, "verdict": "OK",
            "shadow_fill": 0.5, "token_id": "L"},
           {"detect_ts": 2.0, "first_buy": False, "verdict": "OK",
            "shadow_fill": 0.25, "token_id": "L"}]
    seq_l = mc.wager_rois(lad, {"L": 1}, {}, {})
    ok9b = (len(seq_l) == 2
            and abs(seq_l[0][2] - (1 - 0.5 - 0.01) / 0.5) < 1e-12
            and abs(seq_l[1][2] - (1 - 0.25 - 0.005) / 0.25) < 1e-12
            and mc.mixture_e_value([-1.06]) > 0.0)  # band bound would assert
    print(f"  [canon-roi] ladder adds scored; ROI bound wider : {ok9b}")
    ok &= ok9b
    # [with-exits] activation 2026-09-07 (D1=b, D2 fresh epoch, D3
    # analysis lens): exit grades at clamp(sell - haircut, 0, 1) with
    # canon fees on BOTH fills; ladder adds share the exit; pre-entry and
    # post-resolution SELLs ignored; unexited falls back to resolution;
    # unresolved unexited skipped; epoch filters entries.
    we_recs = [{"token_id": "e1", "detect_ts": 10.0, "verdict": "OK",
                "shadow_fill": 0.5, "first_buy": True},
               {"token_id": "e1", "detect_ts": 11.0, "verdict": "OK",
                "shadow_fill": 0.25, "first_buy": False},   # ladder add
               {"token_id": "e2", "detect_ts": 10.0, "verdict": "OK",
                "shadow_fill": 0.5, "first_buy": True},
               {"token_id": "e3", "detect_ts": 10.0, "verdict": "OK",
                "shadow_fill": 0.5, "first_buy": True},
               {"token_id": "e4", "detect_ts": 10.0, "verdict": "OK",
                "shadow_fill": 0.5, "first_buy": True},
               {"token_id": "e5", "detect_ts": 1.0, "verdict": "OK",
                "shadow_fill": 0.5, "first_buy": True}]     # pre-epoch
    we_exits = {"e1": (50.0, 0.8),      # honored: x=0.78, both fees
                "e2": (50.0, 0.005),    # honored, clamps to 0
                "e3": (5.0, 0.9),       # PRE-ENTRY sell -> ignored
                "e4": (200.0, 0.9)}     # after known res_at -> ignored
    seq_we = mc.market_position_rois_with_exits(
        we_recs, {"e3": 1}, {}, {}, we_exits, 0.02, epoch=5.0,
        res_at={"e4": 150.0})
    by_tok = {t: (roi, k, ex) for _, t, roi, k, ex in seq_we}
    # e1: o_eff = 0.78 - 0.02*0.78 = 0.7644; rois (0.7644-0.51)/0.5 and
    # (0.7644-0.255)/0.25 -> mean 1.2732. e2: o_eff 0 -> -1.02.
    # e3: outcome 1 -> 0.98 unexited. e4: no outcome, sell ignored ->
    # skipped. e5: pre-epoch -> absent.
    okwe = (set(by_tok) == {"e1", "e2", "e3"}
            and abs(by_tok["e1"][0] - 1.2732) < 1e-9
            and by_tok["e1"][1] == 2 and by_tok["e1"][2] is True
            and abs(by_tok["e2"][0] - (-1.02)) < 1e-12
            and by_tok["e2"][2] is True
            and abs(by_tok["e3"][0] - 0.98) < 1e-12
            and by_tok["e3"][2] is False)
    print(f"  [with-exits] canon lens: fees both fills, clamp, guards, "
          f"epoch : {okwe}")
    ok &= okwe
    # [with-exits] sells_to_exits: first SELL per (trader, token) wins;
    # non-SELL and malformed rows skipped
    ex_map = sells_to_exits([
        {"trader": "0xA", "token_id": "t", "side": "SELL",
         "whale_price": 0.7, "detect_ts": 20.0},
        {"trader": "0xA", "token_id": "t", "side": "SELL",
         "whale_price": 0.6, "detect_ts": 10.0},   # earlier: wins
        {"trader": "0xa", "token_id": "u", "side": "BUY",
         "whale_price": 0.5, "detect_ts": 5.0},    # not a SELL
        {"trader": "0xa", "token_id": "v", "side": "SELL",
         "detect_ts": 5.0}])                       # no price: skipped
    okse = ex_map == {"0xa": {"t": (10.0, 0.6)}}
    print(f"  [with-exits] sells_to_exits first-per-token + guards : {okse}")
    ok &= okse
    # [with-exits] metrics: $100/wager rate math; None when nothing graded
    wem = with_exits_metrics(we_recs, {"e3": 1}, {}, {}, we_exits, 0.02,
                             5.0, 5.0 + 7 * DAY_S, res_at={"e4": 150.0})
    okwm = (wem is not None and wem["n"] == 3 and wem["exited"] == 2
            and abs(wem["wk_net_real"]
                    - (wem["roi_mean"] * 100.0 * (3 / 7.0) * 7)) < 1e-9
            and with_exits_metrics([], {}, {}, {}, {}, 0.02, 5.0,
                                   5.0 + DAY_S) is None)
    print(f"  [with-exits] metrics rate math + empty -> None : {okwm}")
    ok &= okwm
    # [cov] per-wallet label coverage (review A3): distinct tokens (ladder
    # adds dedupe), labeled counted against outcomes; empty -> (0, 0)
    cov_recs = [{"token_id": "a"}, {"token_id": "a"},   # ladder add: 1 token
                {"token_id": "b"}, {"token_id": "c"}]
    okc = (wallet_coverage(cov_recs, {"a": 1, "c": 0}) == (3, 2)
           and wallet_coverage([], {}) == (0, 0)
           # the 0.5 default flags a minority-labeled wallet (1/3), not 2/3
           and (1 / 3) * 100 < COV_FLAG_DEFAULT * 100
           and (2 / 3) * 100 >= COV_FLAG_DEFAULT * 100)
    print(f"  [cov] per-wallet coverage counts + majority line : {okc}")
    ok &= okc
    # [screen] eligibility, tailability, UNKNOWN counted
    wrows = [{"w": "0xa", "n": 30, "usd_sum": 10.0},   # ok
             {"w": "0xb", "n": 10, "usd_sum": 99.0},   # too few trades
             {"w": "0xc", "n": 40, "usd_sum": 5.0},    # conc too high
             {"w": "0xd", "n": 40, "usd_sum": 5.0}]    # conc UNKNOWN
    conc = {"0xa": 4, "0xb": 1, "0xc": 90}
    cands, sens, unk = screen_candidates(wrows, conc, ELIGIBILITY_MIN_TRADES,
                                         5)
    ok10 = ([c["w"] for c in cands] == ["0xa"] and unk == 1
            and sens[3] == 0 and sens[5] == 1)
    print(f"  [screen] min-trades + tailable + UNKNOWN alarm : {ok10}")
    ok &= ok10
    # [res_at] naive timestamps treated as UTC; both legs mapped
    ram = res_at_map({"c1": {"resolved_at": "2026-04-11T00:00:00",
                             "yes_token_id": "y1", "no_token_id": "n1"}})
    ok11 = ram.get("y1") == ram.get("n1") == parse_iso_z(
        "2026-04-11T00:00:00Z")
    print(f"  [res_at] naive ISO = UTC, both legs : {ok11}")
    ok &= ok11
    # [canon] consumption not re-implementation: the module must not define
    # its own e_value / per_market_edges / lcb_edge / canon_fee
    import inspect
    src = inspect.getsource(sys.modules[__name__])
    ok12 = all(f"def {n}(" not in src for n in
               ("e_value", "per_market_edges", "lcb_edge", "canon_fee"))
    print(f"  [canon] no re-implementation of canon primitives : {ok12}")
    ok &= ok12
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="MB walk-forward backtest harness (build 1, 2026-09-06)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("haircut", help="measure follow-cost from shadow sink")
    p.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")

    p = sub.add_parser("screen", help="tailable candidate set + sensitivity")
    p.add_argument("--wallets", default=os.path.join(
        FIREHOSE_DIR, "population_study_stage1.txt.wallets.jsonl"))
    p.add_argument("--conc", default=os.path.join(FIREHOSE_DIR,
                                                  "peak_conc.jsonl"))
    p.add_argument("--min-trades", type=int, default=ELIGIBILITY_MIN_TRADES,
                   help="operator eligibility ruling 2026-09-06 (>= 25)")
    p.add_argument("--max-conc", type=int, required=True,
                   help="tailability bar — OPERATOR-supplied, no default "
                        "(hyper-concurrent whales are not tailable)")
    p.add_argument("--out", required=True)

    p = sub.add_parser("extract", help="stream firehose gz -> candidate rows")
    p.add_argument("--candidates", required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--tokens-out", required=True)

    p = sub.add_parser("replay", help="walk-forward replay + holdout ranking")
    p.add_argument("--source", choices=("firehose", "roster"), required=True)
    p.add_argument("--rows", required=True,
                   help="extract output (firehose) or shadow sink (roster)")
    p.add_argument("--resolutions", default=os.path.join(
        CACHE_DIR, "gamma_resolutions.json"))
    p.add_argument("--fee-rate-map", dest="fee_rate_map",
                   default=os.path.join(CACHE_DIR, "fee_rate_map.json"))
    p.add_argument("--fee-map", dest="fee_map",
                   default=os.path.join(CACHE_DIR, "fee_map.json"))
    p.add_argument("--split", default=SPLIT_DEFAULT,
                   help="holdout boundary (default = first day never used "
                        "to tune anything)")
    p.add_argument("--end", default=None, help="end of judge window (ISO Z; "
                                               "default now)")
    p.add_argument("--haircut", type=float, default=None,
                   help="measured follow-cost (from `haircut`); required "
                        "for --source firehose")
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--cov-flag", dest="cov_flag", type=float,
                   default=COV_FLAG_DEFAULT,
                   help="label-coverage UNKNOWN-flag line (review A3; "
                        "default 0.5 = judged on a minority of markets; "
                        "display only, never a gate)")
    p.add_argument("--sells", default=SELLS_SINK_DEFAULT,
                   help="SELL sink for the with-exits ANALYSIS lens "
                        "(roster only; activated 2026-09-07 D1=b/D2/D3)")
    p.add_argument("--out", required=True)

    p = sub.add_parser("daily-extract",
                       help="cron: append newly-complete day files")
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--wallets", default=os.path.join(
        FIREHOSE_DIR, "population_study_stage1.txt.wallets.jsonl"))
    p.add_argument("--conc", default=os.path.join(FIREHOSE_DIR,
                                                  "peak_conc.jsonl"))
    p.add_argument("--min-trades", type=int, default=ELIGIBILITY_MIN_TRADES)
    p.add_argument("--max-conc", type=int, required=True,
                   help="tailability bar (operator-ruled 20, 2026-09-06)")
    p.add_argument("--rescreen", action="store_true")

    p = sub.add_parser("daily-replay",
                       help="cron: measure haircut + both leaderboards")
    p.add_argument("--rows", required=True)
    p.add_argument("--outdir", required=True)
    p.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    p.add_argument("--resolutions", default=os.path.join(
        CACHE_DIR, "gamma_resolutions.json"))
    p.add_argument("--fee-rate-map", dest="fee_rate_map",
                   default=os.path.join(CACHE_DIR, "fee_rate_map.json"))
    p.add_argument("--fee-map", dest="fee_map",
                   default=os.path.join(CACHE_DIR, "fee_map.json"))
    p.add_argument("--split", default=SPLIT_DEFAULT)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--cov-flag", dest="cov_flag", type=float,
                   default=COV_FLAG_DEFAULT,
                   help="label-coverage UNKNOWN-flag line (review A3)")
    p.add_argument("--sells", default=SELLS_SINK_DEFAULT,
                   help="SELL sink for the with-exits ANALYSIS lens")

    sub.add_parser("self-test", help="offline self-test")

    args = ap.parse_args()
    if args.cmd == "self-test":
        raise SystemExit(_self_test())
    raise SystemExit({"haircut": cmd_haircut, "screen": cmd_screen,
                      "extract": cmd_extract, "replay": cmd_replay,
                      "daily-extract": cmd_daily_extract,
                      "daily-replay": cmd_daily_replay}[args.cmd](args))
