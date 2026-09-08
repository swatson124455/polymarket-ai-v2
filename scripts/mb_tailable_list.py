#!/usr/bin/env python3
"""MB TAILABLE LIST - the verified tailable-trader list, generated from
artifacts, never hand-typed (docs/MB_TAILABLE_PLAN.md P1; operator
deliverable 2026-09-08: "the top traders we can tail verified and on a list
as well as a set up to monitor them and new possible candidates").

ONE ROW PER WALLET, every column sourced from a named artifact:
  boards       backtest/leaderboard_{firehose,roster}.jsonl (stage 9)
  dives        newest dossier per wallet across cohort5_qualification.
               ADMIT_DIRS_DEFAULT (file mtime decides; ADMIT/INSUFF/REJECT)
  eligibility  data-api activity?user=..&sortDirection=ASC (query shape
               proven 2026-09-07 + re-proven 2026-09-08T19:18Z against
               0x2c50852938 first trade 2026-05-24T14:01:35Z); cached in
               tailable_elig.json with the read timestamp (PASS is
               permanent - a first trade never moves; FAIL/ERROR re-read)
  roster       chain_audit.json clean[] + post-conversion groups
  concurrency  firehose/peak_conc.jsonl (POSITION-level, the screen's
               authority) beside the board's replay value
  forward      deep_dive/cohort5_forward_status.json (the grader's own
               rows: epoch, n, roi, lcb, tripwire) + last recorded fill
               from the shadow sink
  selection    tailable_null.json (the 2026-09-08 audit's null
               replication; absent = 'untested', never assumed)
  labels       label vintage = key count + mtime of gamma_resolutions.json
               (a routine crawl moved one wallet +$1,310 -> +$64/wk with
               zero lookahead: every $ carries its vintage)

TIERS (plan P1): VERIFIED = eligibility PASS AND conc<=20 (position-level)
AND cov>=50% AND holdout ROI LCB > 0 AND dive ADMIT. PENDING = dive or
eligibility outstanding / LCB not yet positive / dive INSUFFICIENT.
FLAGGED = cov<50% or conc>20. DROPPED = the forward tripwire fired (P2,
label from the grader). EXCLUDED = dive REJECT (deliberate exclusion).
A tier is a LABEL: nothing here removes a wallet from anything.

MONEY (operator ruling 2026-09-08 #2): the headline money column is
$algo/wk - LCB net winnings per week at OUR sizer's stake per wager,
as-if-approved (board field ho_wk_net_algo_lcb, P3). $ref100/wk (the
$100/wager reference) stays as the comparison column. EVERY dollar on
this list is HYPOTHETICAL: no real order exists; real money only via
docs/MB_GO_CHECKLIST.md.

Outputs (OUTSIDE the readout clone - the clone reset --hards daily):
  <out-dir>/tailable_list.json   machine-readable, full provenance
  <out-dir>/MB_TAILABLE_LIST.md  the list doc (a snapshot is synced into
                                 docs/ by the session's docs PR)

    python mb_tailable_list.py                 # cron stage 11
    python mb_tailable_list.py --self-test     # offline
    python mb_tailable_list.py --no-net        # cache-only eligibility
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az            # noqa: E402
import cohort5_qualification as cq     # noqa: E402

DAY_S = 86400.0
ELIG_MIN_DAYS = 30        # operator ruling 2026-09-06: >= 1 month history
ELIG_MIN_TRADES = 25      # operator ruling 2026-09-06: >= 25 trades
CONC_BAR = 20             # operator ruling 2026-09-06: tailability bar
COV_BAR = 50.0            # plan P1: cov >= 50% or FLAGGED
DATA_API = "https://data-api.polymarket.com/activity"
UA = {"User-Agent": "PolymarketAI/1.0 (https://github.com; data)",
      "Accept": "application/json"}
TIER_ORDER = ["VERIFIED", "PENDING", "FLAGGED", "DROPPED", "EXCLUDED"]
BASE = "/opt/pa2-shared/mb_copyable_data"


def utc_iso(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_vintage(path: str) -> dict:
    """mtime + size of an input, or absent - provenance for every $."""
    try:
        st = os.stat(path)
        return {"path": path, "mtime_utc": utc_iso(st.st_mtime),
                "bytes": st.st_size}
    except OSError:
        return {"path": path, "mtime_utc": None, "bytes": None,
                "note": "ABSENT"}


def load_jsonl(path: str) -> list[dict]:
    """Tolerant of a torn tail line (live writers)."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    return out


# ── inputs ────────────────────────────────────────────────────────────────
def read_boards(firehose: str, roster: str) -> tuple[dict, dict]:
    """{wallet: row} for each board (last row wins per wallet)."""
    fh = {str(r.get("w", "")).lower(): r for r in load_jsonl(firehose)}
    ro = {str(r.get("w", "")).lower(): r for r in load_jsonl(roster)}
    fh.pop("", None)
    ro.pop("", None)
    return fh, ro


def newest_dossiers(dirs: list) -> dict:
    """wallet -> newest dossier summary across dirs (file mtime decides).
    Same rule as cohort5_qualification.roster_admit_groups."""
    newest: dict = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "0x*.json")):
            try:
                blob = json.load(open(f))
                mt = os.path.getmtime(f)
            except (OSError, ValueError):
                continue
            a = str(blob.get("address", "")).lower()
            if not a:
                continue
            if a not in newest or mt > newest[a]["mtime"]:
                t3 = blob.get("tier3_skill") or {}
                newest[a] = {
                    "verdict": str(blob.get("verdict", "")),
                    "dir": os.path.basename(os.path.dirname(f)),
                    "mtime": mt, "mtime_utc": utc_iso(mt),
                    "span_days": blob.get("span_days"),
                    "n_markets": t3.get("n_markets"),
                    "p": t3.get("p"),
                    "reason": (blob.get("reasons") or [""])[0][:160],
                }
    return newest


def peak_conc_map(path: str) -> dict:
    return {str(r.get("w", "")).lower(): r.get("peak_conc")
            for r in load_jsonl(path) if r.get("w")}


def roster_info(chain_audit_path: str) -> tuple[set, dict]:
    """(clean roster set, {wallet: (group, admitted_utc)} for every
    group carrying an admitted_utc - cohort2..N)."""
    audit = json.load(open(chain_audit_path))
    clean = {str(a).lower() for a in audit.get("clean", [])}
    grp: dict = {}
    for name, g in audit.items():
        if isinstance(g, dict) and "addresses" in g and "admitted_utc" in g:
            for a in g.get("addresses", []):
                grp[str(a).lower()] = (name, str(g["admitted_utc"]))
    return clean, grp


def forward_status_map(path: str) -> tuple[dict | None, dict]:
    """(header, {wallet: row}); a retrial (#r1) row outranks the base."""
    if not os.path.exists(path):
        return None, {}
    try:
        blob = json.load(open(path))
    except (OSError, ValueError):
        return None, {}
    rows: dict = {}
    for r in blob.get("rows", []):
        a = str(r.get("address", "")).lower()
        if not a:
            continue
        if a in rows and not str(r.get("lock_key", "")).endswith("#r1"):
            continue
        rows[a] = r
    hdr = {k: v for k, v in blob.items() if k != "rows"}
    hdr["n_rows"] = len(blob.get("rows", []))
    return hdr, rows


def last_fills(records: list) -> dict:
    """wallet -> {last_fill_utc, last_record_utc, n_ok, n_records} from
    the shadow sink (OK verdict + numeric fill = a recorded paper fill)."""
    out: dict = {}
    for r in records:
        a = str(r.get("trader", "")).lower()
        if not a:
            continue
        d = out.setdefault(a, {"last_fill": None, "last_record": None,
                               "n_ok": 0, "n_records": 0})
        try:
            ts = float(r.get("detect_ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        d["n_records"] += 1
        if ts > (d["last_record"] or 0):
            d["last_record"] = ts
        if (r.get("verdict") == "OK"
                and isinstance(r.get("shadow_fill"), (int, float))):
            d["n_ok"] += 1
            if ts > (d["last_fill"] or 0):
                d["last_fill"] = ts
    return {a: {"last_fill_utc": utc_iso(d["last_fill"]),
                "last_record_utc": utc_iso(d["last_record"]),
                "n_ok": d["n_ok"], "n_records": d["n_records"]}
            for a, d in out.items()}


def null_map(path: str) -> tuple[dict, dict]:
    if not os.path.exists(path):
        return {"note": "ABSENT - every wallet 'untested'"}, {}
    blob = json.load(open(path))
    hdr = {k: v for k, v in blob.items() if k != "wallets"}
    return hdr, {str(a).lower(): v for a, v in blob.get("wallets", {}).items()}


# ── eligibility (the only network read) ───────────────────────────────────
def fetch_first_trades(addr: str, timeout_s: float = 20.0) -> list:
    """ASC activity page: the wallet's FIRST trades (query shape proven
    2026-09-07 and 2026-09-08 against a known-old wallet)."""
    url = DATA_API + "?" + urllib.parse.urlencode(
        {"user": addr, "type": "TRADE", "limit": ELIG_MIN_TRADES,
         "sortBy": "TIMESTAMP", "sortDirection": "ASC"})
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        blob = json.load(r)
    return blob if isinstance(blob, list) else []


def eligibility_verdict(rows: list, now_ts: float) -> dict:
    """PURE. PASS iff >= ELIG_MIN_TRADES trade rows AND the earliest
    timestamp is >= ELIG_MIN_DAYS before now. Anything else FAIL with
    the reason. An empty page is FAIL ('no trades'), never PASS."""
    ts = []
    for r in rows:
        if str(r.get("type", "TRADE")).upper() != "TRADE":
            continue
        try:
            ts.append(float(r["timestamp"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not ts:
        return {"status": "FAIL", "first_trade_utc": None, "n_rows": 0,
                "reason": "no trades returned"}
    first = min(ts)
    age_d = (now_ts - first) / DAY_S
    if len(ts) < ELIG_MIN_TRADES:
        return {"status": "FAIL", "first_trade_utc": utc_iso(first),
                "n_rows": len(ts),
                "reason": f"{len(ts)} trades < {ELIG_MIN_TRADES}"}
    if age_d < ELIG_MIN_DAYS:
        return {"status": "FAIL", "first_trade_utc": utc_iso(first),
                "n_rows": len(ts),
                "reason": f"first trade {age_d:.1f}d ago < {ELIG_MIN_DAYS}d"}
    return {"status": "PASS", "first_trade_utc": utc_iso(first),
            "n_rows": len(ts),
            "reason": f">={ELIG_MIN_TRADES} trades, first {age_d:.0f}d ago"}


def load_cache(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return {}


def save_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def refresh_eligibility(wallets: list, cache: dict, net: bool,
                        now_ts: float, sleep_s: float = 0.3) -> dict:
    """PASS is permanent (a first trade never moves; counts only grow);
    FAIL / ERROR / missing are re-read every run when net is on."""
    for a in wallets:
        cur = cache.get(a) or {}
        if cur.get("status") == "PASS":
            continue
        if not net:
            if not cur:
                cache[a] = {"status": "UNREAD", "reason": "no-net run",
                            "read_utc": None}
            continue
        try:
            rows = fetch_first_trades(a)
            v = eligibility_verdict(rows, now_ts)
        except Exception as e:  # network - recorded, retried next run
            v = {"status": "ERROR", "first_trade_utc": None, "n_rows": None,
                 "reason": repr(e)[:120]}
        v["read_utc"] = now_iso()
        cache[a] = v
        time.sleep(sleep_s)
    return cache


# ── the list ──────────────────────────────────────────────────────────────
def tier_of(r: dict) -> tuple[str, list]:
    """PURE. Precedence: EXCLUDED (dive REJECT) > DROPPED (forward
    tripwire) > FLAGGED (cov<50 / conc>20) > VERIFIED (all green) >
    PENDING. Returns (tier, reasons) - every failed criterion named."""
    why = []
    dive = str(r.get("dive_verdict") or "")
    if dive.startswith("REJECT"):
        return "EXCLUDED", [f"dive REJECT ({r.get('dive_dir')})"]
    trip = str(r.get("fwd_tripwire") or "")
    if trip.startswith("DROPPED"):
        return "DROPPED", [f"forward tripwire {trip}"]
    conc = r.get("conc_pos")
    if conc is None:
        conc = r.get("conc_replay")
    cov = r.get("cov_pct")
    flagged = []
    if conc is not None and conc > CONC_BAR:
        flagged.append(f"conc {conc} > {CONC_BAR}")
    if cov is not None and cov < COV_BAR:
        flagged.append(f"cov {cov:.0f}% < {COV_BAR:.0f}%")
    if flagged:
        return "FLAGGED", flagged
    if r.get("elig_status") != "PASS":
        why.append(f"eligibility {r.get('elig_status') or 'UNREAD'}")
    if conc is None:
        why.append("concurrency unmeasured")
    if cov is None:
        why.append("coverage unmeasured (no board row)")
    lcb = r.get("ho_roi_lcb")
    if lcb is None or lcb <= 0.0:
        why.append("holdout ROI LCB not > 0")
    if not dive.startswith("ADMIT"):
        why.append(f"dive {dive or 'none'}")
    return ("VERIFIED" if not why else "PENDING"), why


def candidate_set(fh: dict, ro: dict, roster_groups: dict,
                  dossiers: dict, pipeline_dirs: set) -> set:
    """Who gets a row: post-conversion roster groups + every wallet with a
    dossier in a promotion-pipeline dir + every board row that
    QUALIFIES with a positive holdout $ (the discovery finds)."""
    out = {a for a, (g, adm) in roster_groups.items()
           if _after_basis(adm)}
    out |= {a for a, d in dossiers.items() if d["dir"] in pipeline_dirs}
    for board in (fh, ro):
        for a, r in board.items():
            if (str(r.get("verdict", "")).startswith("QUALIFIES")
                    and (r.get("ho_wk_net_lcb") or 0) > 0):
                out.add(a)
    return out


def _after_basis(admitted_utc: str) -> bool:
    try:
        return cq.parse_utc(admitted_utc) >= cq.BASIS_EPOCH
    except ValueError:
        return False


def build_rows(cands: set, fh: dict, ro: dict, dossiers: dict,
               elig: dict, clean: set, roster_groups: dict, conc: dict,
               fwd: dict, fills: dict, nulls: dict) -> list:
    rows = []
    for a in sorted(cands):
        b = fh.get(a) or ro.get(a) or {}
        src = "firehose" if a in fh else ("roster" if a in ro else None)
        alt = ro.get(a) if src == "firehose" else None
        d = dossiers.get(a) or {}
        e = elig.get(a) or {}
        f = fwd.get(a) or {}
        nl = nulls.get(a)
        g = roster_groups.get(a)
        r = {
            "wallet": a,
            "board_source": src,
            "board_verdict": b.get("verdict"),
            "ho_wk_net_lcb": b.get("ho_wk_net_lcb"),      # $ref100/wk
            "ho_wk_net_real": b.get("ho_wk_net_real"),
            "ho_wk_net_algo_lcb": b.get("ho_wk_net_algo_lcb"),  # P3
            "ho_wk_net_algo_real": b.get("ho_wk_net_algo_real"),
            "algo_stake_med": b.get("algo_stake_med"),
            "ho_roi_lcb": b.get("ho_roi_lcb"),
            "ho_roi_realized": b.get("ho_roi_realized"),
            "ho_n_holdout": b.get("ho_n_holdout"),
            "ho_wagers": b.get("ho_wagers"),
            "ho_holdout_days": b.get("ho_holdout_days"),
            "cov_pct": b.get("cov_pct"),
            "conc_replay": b.get("peak_conc_replay"),
            "conc_pos": conc.get(a),
            "roster_board_wk_lcb": (alt or {}).get("ho_wk_net_lcb"),
            "elig_status": e.get("status"),
            "elig_first_trade_utc": e.get("first_trade_utc"),
            "elig_reason": e.get("reason"),
            "elig_read_utc": e.get("read_utc"),
            "dive_verdict": d.get("verdict"),
            "dive_dir": d.get("dir"),
            "dive_utc": d.get("mtime_utc"),
            "dive_span_days": d.get("span_days"),
            "dive_n_markets": d.get("n_markets"),
            "dive_p": d.get("p"),
            "dive_reason": d.get("reason"),
            "on_roster": a in clean,
            "roster_group": g[0] if g else None,
            "roster_admitted_utc": g[1] if g else None,
            "selection_survives": (None if nl is None else bool(nl.get("survives"))),
            "selection_p": None if nl is None else nl.get("p"),
            "fwd_state": f.get("state"),
            "fwd_verdict": f.get("verdict"),
            "fwd_epoch_utc": f.get("epoch_utc"),
            "fwd_futility_utc": f.get("futility_utc"),
            "fwd_n_mkts": f.get("n_mkts"),
            "fwd_n_wagers": f.get("n_wagers"),
            "fwd_e": f.get("e"),
            "fwd_roi": f.get("roi"),
            "fwd_lcb": f.get("lcb"),
            "fwd_wk_ref100_lcb": f.get("wk_ref100_lcb"),
            "fwd_tripwire": f.get("tripwire"),
            "last_fill_utc": (fills.get(a) or {}).get("last_fill_utc"),
            "fwd_n_ok_fills": (fills.get(a) or {}).get("n_ok"),
            "fwd_n_records": (fills.get(a) or {}).get("n_records"),
        }
        r["tier"], r["tier_reasons"] = tier_of(r)
        rows.append(r)

    def money(x):
        v = x.get("ho_wk_net_algo_lcb")
        if v is None:
            v = x.get("ho_wk_net_lcb")
        return v if v is not None else -1e18
    rows.sort(key=lambda x: (TIER_ORDER.index(x["tier"]), -money(x),
                             x["wallet"]))
    return rows


def _fmt(v, spec, dash="-"):
    if v is None or (isinstance(v, float) and v != v):
        return dash
    try:
        return format(v, spec)
    except (TypeError, ValueError):
        return dash


def render_md(rows: list, prov: dict) -> str:
    L = []
    L.append("# MB TAILABLE LIST - verified tailable traders (generated; "
             "do not hand-edit)")
    L.append("")
    L.append(f"**Generated:** {prov['generated_utc']} by "
             f"`scripts/mb_tailable_list.py` @ {prov.get('code_rev')} | "
             f"**EVERY $ IS HYPOTHETICAL** (no real order exists; real money "
             f"only via docs/MB_GO_CHECKLIST.md).")
    L.append("")
    L.append("**Rulings applied:** backtest ADMITS to the list, forward "
             "watching = drop-off tripwire only (2026-09-08 #1); money "
             "headline = $algo/wk at OUR sizer stake per wager, as-if-approved "
             "(2026-09-08 #2; board field `ho_wk_net_algo_lcb`, P3), $ref100/wk "
             "= $100/wager comparison; VERIFIED = eligibility PASS AND conc<="
             f"{CONC_BAR} (position-level) AND cov>={COV_BAR:.0f}% AND holdout ROI "
             "LCB>0 AND dive ADMIT. Tiers are labels: nothing here removes a "
             "wallet from anything (report + ask).")
    L.append("")
    L.append("## Provenance (label vintage travels with every $)")
    L.append("")
    L.append("| input | vintage (mtime UTC) | size / count |")
    L.append("|---|---|---|")
    for k, v in prov["inputs"].items():
        cnt = v.get("count")
        L.append(f"| {k} | {v.get('mtime_utc') or 'ABSENT'} | "
                 f"{cnt if cnt is not None else v.get('bytes')} |")
    L.append("")
    L.append(f"- Label vintage: **{prov['label_vintage']['keys']} keys** in "
             f"gamma_resolutions.json @ {prov['label_vintage']['mtime_utc']}"
             f" - quote every holdout $ with this.")
    L.append(f"- Sizer foursome (mb_sizer.env via env): "
             f"{prov.get('sizer') or 'UNSET in this run'}; $algo columns "
             f"come from the board (P3) - "
             f"{'present' if prov['algo_present'] else 'NOT YET on the board (P3 pending): $ref100 shown as headline until then'}.")
    L.append(f"- Null replication: {prov['null']}")
    L.append(f"- Forward status: {prov['forward']}")
    L.append(f"- Candidate universe: {prov['universe']}")
    L.append("")
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in TIER_ORDER}
    L.append("## Summary: " + " | ".join(f"{t} {counts[t]}" for t in TIER_ORDER))
    L.append("")
    hdr = ("| # | wallet | $algo/wk LCB | $ref100/wk LCB | roi_lcb | n_ho | "
           "conc pos/replay | cov% | elig | dive | selection | roster | "
           "forward (grader) | tripwire | last fill |")
    sep = "|---|---|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|"
    for t in TIER_ORDER:
        sub = [r for r in rows if r["tier"] == t]
        L.append(f"## {t} ({len(sub)})")
        L.append("")
        if not sub:
            L.append("_none_")
            L.append("")
            continue
        L.append(hdr)
        L.append(sep)
        for i, r in enumerate(sub, 1):
            algo = (_fmt(r["ho_wk_net_algo_lcb"], "+,.0f")
                    if r["ho_wk_net_algo_lcb"] is not None else "P3")
            fwd = (f"{r['fwd_state']} n={r['fwd_n_mkts']} "
                   f"roi={_fmt(r['fwd_roi'], '+.3f')} "
                   f"lcb={_fmt(r['fwd_lcb'], '+.3f')}"
                   if r["fwd_state"] else "not registered")
            sel = ("untested" if r["selection_survives"] is None else
                   ("Y" if r["selection_survives"] else "no")
                   + f" p={_fmt(r['selection_p'], '.2f')}")
            L.append(
                f"| {i} | `{r['wallet'][:12]}` | {algo} | "
                f"{_fmt(r['ho_wk_net_lcb'], '+,.0f')} | "
                f"{_fmt(r['ho_roi_lcb'], '+.3f')} | {_fmt(r['ho_n_holdout'], 'd')} | "
                f"{_fmt(r['conc_pos'], 'd')}/{_fmt(r['conc_replay'], 'd')} | "
                f"{_fmt(r['cov_pct'], '.0f')} | {r['elig_status'] or 'UNREAD'} | "
                f"{(r['dive_verdict'] or 'none')[:6]} | {sel} | "
                f"{r['roster_group'] or ('yes' if r['on_roster'] else 'no')} | "
                f"{fwd} | {r['fwd_tripwire'] or '-'} | "
                f"{(r['last_fill_utc'] or '-')[:16]} |")
        L.append("")
        for r in sub:
            L.append(f"- `{r['wallet']}` - {t}: "
                     + ("; ".join(r["tier_reasons"]) if r["tier_reasons"]
                        else "all criteria green")
                     + f". Board {r['board_source']} verdict "
                     f"{r['board_verdict']}, holdout n={r['ho_n_holdout']} "
                     f"wagers={r['ho_wagers']} over {r['ho_holdout_days']}d, "
                     f"$ref100/wk real {_fmt(r['ho_wk_net_real'], '+,.0f')}"
                     + (f", roster-board $ref100/wk LCB "
                        f"{_fmt(r['roster_board_wk_lcb'], '+,.0f')}"
                        if r["roster_board_wk_lcb"] is not None else "")
                     + f". Eligibility {r['elig_status']} "
                     f"({r['elig_reason']}; read {r['elig_read_utc']}). "
                     f"Dive {r['dive_verdict']} {r['dive_dir']} "
                     f"{r['dive_utc']} span={r['dive_span_days']}d "
                     f"mkts={r['dive_n_markets']} P={_fmt(r['dive_p'], '.3f')}"
                     + (f" - {r['dive_reason']}" if r["dive_reason"] else "")
                     + (f". Forward epoch {r['fwd_epoch_utc']}, futility "
                        f"{r['fwd_futility_utc']}, {r['fwd_n_records']} "
                        f"records / {r['fwd_n_ok_fills']} paper fills."
                        if r["fwd_state"] else
                        ". Forward: not registered in the grader."))
        L.append("")
    L.append("## Legend")
    L.append("")
    L.append("- **$algo/wk LCB** - LCB net winnings per week at OUR sizer's "
             "stake per wager (bankroll x kelly_mult / conc at each wager's "
             "fill, per-bet cap, min-viable-to-zero), as-if-approved. "
             "HYPOTHETICAL. `P3` = the board does not carry it yet.")
    L.append("- **$ref100/wk LCB** - same at a flat $100/wager reference "
             "(comparison column only, ruling 2026-09-08 #2).")
    L.append("- **conc pos/replay** - position-level peak concurrency "
             "(firehose/peak_conc.jsonl, the screen's authority) / the "
             "board's replay measurement.")
    L.append("- **selection** - survives the 2026-09-08 null replication "
             "(Y/no + p); `untested` = not in that replication, never "
             "assumed.")
    L.append("- **forward / tripwire** - the grader's own row "
             "(cohort5_forward_status.json): WATCH / DEGRADED / DROPPED / "
             "PASSED. DROPPED moves the wallet to the DROPPED tier; roster "
             "removal is an operator ruling.")
    L.append("")
    return "\n".join(L) + "\n"


def code_rev() -> str:
    try:
        top = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return subprocess.run(["git", "-C", top, "rev-parse", "--short",
                               "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or "?"
    except Exception:
        return "?"


def run(args) -> int:
    now_ts = time.time()
    fh, ro = read_boards(args.firehose_board, args.roster_board)
    if not fh and not ro:
        print("[tailable] FATAL: both boards empty/absent - refusing to "
              "print an empty list as 'no traders'")
        return 2
    dossiers = newest_dossiers(args.admit_dirs)
    clean, roster_groups = roster_info(args.chain_audit)
    conc = peak_conc_map(args.peak_conc)
    fwd_hdr, fwd = forward_status_map(args.forward_status)
    fills = last_fills(az.load_records(args.shadow)) \
        if os.path.exists(args.shadow) else {}
    null_hdr, nulls = null_map(args.null)
    cands = candidate_set(fh, ro, roster_groups, dossiers,
                          set(os.path.basename(d.rstrip("/"))
                              for d in args.pipeline_dirs))
    os.makedirs(args.out_dir, exist_ok=True)
    cache = load_cache(args.elig_cache)
    cache = refresh_eligibility(sorted(cands), cache, not args.no_net,
                                now_ts)
    save_json(args.elig_cache, cache)
    rows = build_rows(cands, fh, ro, dossiers, cache, clean, roster_groups,
                      conc, fwd, fills, nulls)
    # label vintage: key count + mtime (a crawl moves $ with zero lookahead)
    lv = file_vintage(args.resolutions)
    try:
        lv["keys"] = len(json.load(open(args.resolutions)))
    except (OSError, ValueError):
        lv["keys"] = None
    algo_present = any(r.get("ho_wk_net_algo_lcb") is not None
                       for r in list(fh.values()) + list(ro.values()))
    sizer = {k: os.environ.get(k) for k in
             ("MB_SIZER_BANKROLL", "MB_SIZER_KELLY_MULT",
              "MB_SIZER_CONCURRENCY", "MB_SIZER_MIN_VIABLE")}
    prov = {
        "generated_utc": now_iso(), "code_rev": code_rev(),
        "inputs": {
            "leaderboard_firehose": dict(file_vintage(args.firehose_board), count=len(fh)),
            "leaderboard_roster": dict(file_vintage(args.roster_board), count=len(ro)),
            "chain_audit": dict(file_vintage(args.chain_audit), count=len(clean)),
            "peak_conc": dict(file_vintage(args.peak_conc), count=len(conc)),
            "forward_status": dict(file_vintage(args.forward_status), count=len(fwd)),
            "shadow_sink": dict(file_vintage(args.shadow), count=len(fills)),
            "dossiers": {"path": ";".join(args.admit_dirs), "mtime_utc": None,
                         "count": len(dossiers)},
            "eligibility_cache": dict(file_vintage(args.elig_cache), count=len(cache)),
            "null_replication": dict(file_vintage(args.null), count=len(nulls)),
        },
        "label_vintage": lv,
        "sizer": sizer if all(sizer.values()) else None,
        "algo_present": algo_present,
        "null": null_hdr.get("source") or null_hdr.get("note"),
        "forward": (f"{fwd_hdr.get('ts')} basis {fwd_hdr.get('basis')} "
                    f"rows {fwd_hdr.get('n_rows')}" if fwd_hdr
                    else "ABSENT (grader has not written it yet)"),
        "universe": (f"{len(cands)} wallets = post-conversion roster groups "
                     f"U pipeline dossiers U board QUALIFIES with $ref100/wk "
                     f"LCB>0 (firehose {len(fh)} rows, roster {len(ro)} rows)"),
        "rules": {"elig_min_days": ELIG_MIN_DAYS,
                  "elig_min_trades": ELIG_MIN_TRADES,
                  "conc_bar": CONC_BAR, "cov_bar": COV_BAR,
                  "tripwire": (fwd_hdr or {}).get("tripwire")},
    }
    out = {"generated_utc": prov["generated_utc"], "provenance": prov,
           "hypothetical": True, "rows": rows}
    save_json(os.path.join(args.out_dir, "tailable_list.json"), out)
    md = render_md(rows, prov)
    mdp = os.path.join(args.out_dir, "MB_TAILABLE_LIST.md")
    with open(mdp + ".tmp", "w", encoding="utf-8") as f:
        f.write(md)
    os.replace(mdp + ".tmp", mdp)
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in TIER_ORDER}
    print(f"===== {prov['generated_utc']} MB TAILABLE LIST (HYPOTHETICAL $; "
          f"labels {lv['keys']} keys @ {lv['mtime_utc']}) =====")
    print("[tailable] " + " | ".join(f"{t} {counts[t]}" for t in TIER_ORDER)
          + f" | universe {len(cands)} | eligibility reads "
          f"{'ON' if not args.no_net else 'OFF (cache only)'}")
    for r in rows:
        if r["tier"] in ("VERIFIED", "PENDING", "FLAGGED", "DROPPED"):
            algo = (_fmt(r["ho_wk_net_algo_lcb"], "+,.0f")
                    if r["ho_wk_net_algo_lcb"] is not None else "P3")
            print(f"  {r['wallet'][:12]}..  {r['tier']:<9} $algo/wk {algo:>8} "
                  f"$ref100/wk {_fmt(r['ho_wk_net_lcb'], '+,.0f'):>8} "
                  f"lcb {_fmt(r['ho_roi_lcb'], '+.3f')} n_ho {_fmt(r['ho_n_holdout'], 'd'):>4} "
                  f"conc {_fmt(r['conc_pos'], 'd')}/{_fmt(r['conc_replay'], 'd')} "
                  f"cov {_fmt(r['cov_pct'], '.0f')} elig {r['elig_status'] or 'UNREAD'} "
                  f"dive {(r['dive_verdict'] or 'none')[:6]} "
                  f"fwd {r['fwd_tripwire'] or 'unregistered'}"
                  + (f"  [{'; '.join(r['tier_reasons'])}]" if r["tier_reasons"] else ""))
    print(f"[tailable] wrote {mdp} + tailable_list.json")
    return 0


def _self_test() -> int:
    import tempfile
    print("SELF-TEST - mb_tailable_list (offline)\n")
    ok = True
    now = 1_800_000_000.0
    mk = lambda t0, n: [{"type": "TRADE", "timestamp": t0 + i * 60}
                        for i in range(n)]
    v1 = eligibility_verdict(mk(now - 40 * DAY_S, 25), now)
    v2 = eligibility_verdict(mk(now - 10 * DAY_S, 25), now)
    v3 = eligibility_verdict(mk(now - 40 * DAY_S, 10), now)
    v4 = eligibility_verdict([], now)
    v5 = eligibility_verdict(mk(now - 40 * DAY_S, 25)
                             + [{"type": "REDEEM", "timestamp": now - 400 * DAY_S}], now)
    ok1 = (v1["status"] == "PASS" and v2["status"] == "FAIL"
           and "< 30d" in v2["reason"] and v3["status"] == "FAIL"
           and "< 25" in v3["reason"] and v4["status"] == "FAIL"
           and v5["first_trade_utc"] == v1["first_trade_utc"])
    print(f"  [elig] PASS needs >=25 trades AND first >=30d ago; empty=FAIL;"
          f" non-TRADE rows ignored : {ok1}")
    ok &= ok1
    base = {"conc_pos": 10, "conc_replay": 12, "cov_pct": 90.0,
            "elig_status": "PASS", "ho_roi_lcb": 0.1, "dive_verdict": "ADMIT",
            "fwd_tripwire": "WATCH"}
    t = lambda **kw: tier_of(dict(base, **kw))[0]
    ok2 = (t() == "VERIFIED"
           and t(dive_verdict="REJECT") == "EXCLUDED"
           and t(fwd_tripwire="DROPPED (futility)") == "DROPPED"
           and t(cov_pct=30.0) == "FLAGGED"
           and t(conc_pos=25) == "FLAGGED"
           and t(conc_pos=None, conc_replay=25) == "FLAGGED"
           and t(conc_pos=18, conc_replay=35) == "VERIFIED"   # position-level rules
           and t(elig_status="FAIL") == "PENDING"
           and t(elig_status=None) == "PENDING"
           and t(ho_roi_lcb=0.0) == "PENDING"
           and t(ho_roi_lcb=None) == "PENDING"
           and t(dive_verdict="INSUFFICIENT-EVIDENCE") == "PENDING"
           and t(dive_verdict=None) == "PENDING"
           and t(fwd_tripwire="DEGRADED (roi<0 @ n>=5)") == "VERIFIED"
           and t(fwd_tripwire=None) == "VERIFIED"
           and t(dive_verdict="REJECT", fwd_tripwire="DROPPED (futility)") == "EXCLUDED"
           and t(fwd_tripwire="DROPPED (futility)", cov_pct=10.0) == "DROPPED")
    _, why = tier_of(dict(base, elig_status="FAIL", ho_roi_lcb=-0.2,
                          dive_verdict=None))
    ok2b = len(why) == 3 and any("eligibility" in w for w in why) \
        and any("LCB" in w for w in why) and any("dive" in w for w in why)
    print(f"  [tier] precedence EXCLUDED>DROPPED>FLAGGED>VERIFIED>PENDING; "
          f"position-level conc rules; every miss named : {ok2 and ok2b}")
    ok &= ok2 and ok2b
    with tempfile.TemporaryDirectory() as d:
        A, B, C, D, E = ("0x" + ch * 40 for ch in "abcde")
        post = datetime.fromtimestamp(cq.BASIS_EPOCH + DAY_S,
                                      timezone.utc).isoformat()
        j = lambda name, rows: (open(os.path.join(d, name), "w").write(
            "\n".join(json.dumps(r) for r in rows) + "\n"), os.path.join(d, name))[1]
        fhp = j("fh.jsonl", [
            {"w": A, "verdict": "QUALIFIES", "ho_wk_net_lcb": 500.0,
             "ho_roi_lcb": 0.2, "ho_n_holdout": 30, "cov_pct": 90.0,
             "peak_conc_replay": 12, "ho_wagers": 40, "ho_holdout_days": 6.7,
             "ho_wk_net_real": 900.0},
            {"w": B, "verdict": "QUALIFIES", "ho_wk_net_lcb": 100.0,
             "ho_roi_lcb": 0.1, "ho_n_holdout": 12, "cov_pct": 30.0,
             "peak_conc_replay": 5},
            {"w": C, "verdict": "NOT DEMONSTRATED (futility 1wk)",
             "ho_wk_net_lcb": 50.0, "ho_roi_lcb": 0.05, "cov_pct": 80.0,
             "peak_conc_replay": 3},   # not QUALIFIES -> not a candidate
            {"w": E, "verdict": "QUALIFIES", "ho_wk_net_lcb": -5.0,
             "cov_pct": 80.0, "peak_conc_replay": 3},   # $<=0 -> not a cand
        ])
        rop = j("ro.jsonl", [{"w": A, "verdict": "ACCRUING",
                              "ho_wk_net_lcb": 120.0}])
        pcp = j("pc.jsonl", [{"w": A, "peak_conc": 10}, {"w": B, "peak_conc": 5},
                             {"w": D, "peak_conc": 2}])
        dd = os.path.join(d, "deep_dive_pipeline")
        os.makedirs(dd)
        for a, v in ((A, "ADMIT"), (D, "REJECT")):
            json.dump({"address": a, "verdict": v, "span_days": 100,
                       "tier3_skill": {"n_markets": 50, "p": 0.99},
                       "reasons": ["r"]}, open(os.path.join(dd, a + ".json"), "w"))
        cap = os.path.join(d, "chain_audit.json")
        json.dump({"clean": [A, D], "cohortX": {"addresses": [A, D],
                                                "admitted_utc": post},
                   "cohortOld": {"addresses": [B], "admitted_utc":
                                 "2026-07-15T19:20:12+00:00"}}, open(cap, "w"))
        fsp = os.path.join(d, "fs.json")
        json.dump({"ts": "2026-09-09T11:40:00Z", "basis": "roi-netwin-20260906",
                   "tripwire": {"drop_lcb_n": 10, "degrade_n": 5},
                   "rows": [{"address": A, "lock_key": A, "state": "ACCRUING",
                             "n_mkts": 3, "roi": 0.2, "lcb": None,
                             "tripwire": "WATCH", "epoch_utc": post,
                             "futility_utc": "x"},
                            {"address": D, "lock_key": D, "state": "LOCKED",
                             "verdict": "NOT DEMONSTRATED (futility 1wk)",
                             "n_mkts": 0, "tripwire": "DROPPED (futility)"}]},
                  open(fsp, "w"))
        shp = j("shadow.jsonl", [
            {"trader": A, "detect_ts": now - 100, "verdict": "OK", "shadow_fill": 0.4},
            {"trader": A, "detect_ts": now - 50, "verdict": "PRICE_NO_UPSIDE",
             "shadow_fill": None}])
        nlp = os.path.join(d, "null.json")
        json.dump({"source": "test", "wallets": {A: {"p": 0.0, "survives": True}}},
                  open(nlp, "w"))
        elp = os.path.join(d, "elig.json")
        json.dump({A: {"status": "PASS", "first_trade_utc": "2026-01-01T00:00:00Z",
                       "reason": "ok", "read_utc": "2026-09-08T00:00:00Z"},
                   B: {"status": "FAIL", "reason": "young", "read_utc": "x"}},
                  open(elp, "w"))
        resp = os.path.join(d, "res.json")
        json.dump({"c1": {}, "c2": {}}, open(resp, "w"))
        from types import SimpleNamespace as NS
        args = NS(firehose_board=fhp, roster_board=rop, admit_dirs=[dd],
                  pipeline_dirs=[dd], chain_audit=cap, peak_conc=pcp,
                  forward_status=fsp, shadow=shp, null=nlp, elig_cache=elp,
                  resolutions=resp, out_dir=os.path.join(d, "out"),
                  no_net=True)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(args)
        out = json.load(open(os.path.join(d, "out", "tailable_list.json")))
        rows = {r["wallet"]: r for r in out["rows"]}
        ok3 = (rc == 0 and set(rows) == {A, B, D}
               and rows[A]["tier"] == "VERIFIED"
               and rows[B]["tier"] == "FLAGGED"
               and rows[D]["tier"] == "EXCLUDED"
               and rows[A]["conc_pos"] == 10 and rows[A]["conc_replay"] == 12
               and rows[A]["roster_board_wk_lcb"] == 120.0
               and rows[A]["selection_survives"] is True
               and rows[B]["selection_survives"] is None
               and rows[A]["fwd_tripwire"] == "WATCH"
               and rows[A]["last_fill_utc"] == utc_iso(now - 100)
               and rows[A]["fwd_n_records"] == 2 and rows[A]["fwd_n_ok_fills"] == 1
               and rows[A]["roster_group"] == "cohortX"
               and rows[B]["elig_status"] == "FAIL"
               and rows[A]["ho_wk_net_algo_lcb"] is None
               and out["hypothetical"] is True
               and out["provenance"]["label_vintage"]["keys"] == 2
               and out["provenance"]["algo_present"] is False)
        md = open(os.path.join(d, "out", "MB_TAILABLE_LIST.md"), encoding="utf-8").read()
        ok3b = ("HYPOTHETICAL" in md and "## Provenance" in md
                and "## VERIFIED (1)" in md and "## FLAGGED (1)" in md
                and "## EXCLUDED (1)" in md and "P3" in md
                and "2 keys" in md and A[:12] in md)
        cache = json.load(open(elp))
        ok3c = (cache[D]["status"] == "UNREAD" and cache[A]["status"] == "PASS"
                and cache[B]["status"] == "FAIL")
        print(f"  [build] universe = post-conv roster U pipeline dossiers U "
              f"QUALIFIES $>0; tiers/columns/vintage/md; no-net leaves "
              f"UNREAD : {ok3 and ok3b and ok3c}")
        ok &= ok3 and ok3b and ok3c
        # EMPTY boards -> refuse, never 'no traders'
        args2 = NS(**dict(vars(args), firehose_board=os.path.join(d, "none"),
                          roster_board=os.path.join(d, "none2")))
        with contextlib.redirect_stdout(io.StringIO()):
            rc2 = run(args2)
        print(f"  [guard] empty boards refuse (rc=2), never an empty list : "
              f"{rc2 == 2}")
        ok &= rc2 == 2
    import inspect
    src = inspect.getsource(run) + inspect.getsource(render_md)
    ok4 = ("HYPOTHETICAL" in src and "label_vintage" in src
           and "ho_wk_net_algo_lcb" in inspect.getsource(build_rows)
           and 'cq.parse_utc' in inspect.getsource(_after_basis)
           and "sortDirection" in inspect.getsource(fetch_first_trades)
           and '"ASC"' in inspect.getsource(fetch_first_trades))
    print(f"  [pins] HYPOTHETICAL + vintage in outputs; $algo read from the "
          f"board; roster clock via the grader's parser; ASC query : {ok4}")
    ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MB tailable-trader list "
                                             "(generated from artifacts)")
    ap.add_argument("--firehose-board", dest="firehose_board",
                    default=f"{BASE}/backtest/leaderboard_firehose.jsonl")
    ap.add_argument("--roster-board", dest="roster_board",
                    default=f"{BASE}/backtest/leaderboard_roster.jsonl")
    ap.add_argument("--admit-dirs", dest="admit_dirs", nargs="*",
                    default=cq.ADMIT_DIRS_DEFAULT)
    ap.add_argument("--pipeline-dirs", dest="pipeline_dirs", nargs="*",
                    default=[f"{BASE}/deep_dive_promo0907",
                             f"{BASE}/deep_dive_pipeline"],
                    help="promotion-pipeline dossier dirs: every wallet "
                         "with a dossier here gets a row")
    ap.add_argument("--chain-audit", dest="chain_audit",
                    default=cq.CHAIN_AUDIT_DEFAULT)
    ap.add_argument("--peak-conc", dest="peak_conc",
                    default=f"{BASE}/firehose/peak_conc.jsonl")
    ap.add_argument("--forward-status", dest="forward_status",
                    default=f"{BASE}/deep_dive/cohort5_forward_status.json")
    ap.add_argument("--shadow", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--resolutions",
                    default=f"{BASE}/copyable_cache/gamma_resolutions.json")
    ap.add_argument("--null", default=f"{BASE}/tailable/tailable_null.json")
    ap.add_argument("--elig-cache", dest="elig_cache",
                    default=f"{BASE}/tailable/tailable_elig.json")
    ap.add_argument("--out-dir", dest="out_dir", default=f"{BASE}/tailable")
    ap.add_argument("--no-net", dest="no_net", action="store_true",
                    help="no eligibility reads; cache only (UNREAD marked)")
    ap.add_argument("--self-test", dest="self_test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else run(a))
