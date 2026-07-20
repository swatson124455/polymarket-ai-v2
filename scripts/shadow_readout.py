#!/usr/bin/env python3
"""Durable shadow readout — FRESH DB labels + per-cohort split + trigger alert.

WHY (landmine 2026-07-15): `analyze_shadow.py --gamma-cache` reads a resolution
cache that goes STALE the moment new markets trade, so it silently reports
"0 resolved -> UNDERPOWERED" and MASKS the real edge signal. Caught 2026-07-15:
the readout claimed 0 resolved markets when the live DB already knew 10 (and the
early edge was NEGATIVE, which the stale cache hid). This runner rebuilds the
token->outcome map FRESH from the `markets` table every run, then produces the
pre-registered readout SEPARATELY for cohort-1 (all roster) and cohort-2 (the
deep-dive admits, own start epoch — never pooled), appends to a durable log, and
writes an ALERT file when a cohort crosses the power bar OR its edge is
convincingly negative before then.

READ-ONLY: DB reads + the shadow JSONL; appends to the durable readout log.
INVOCATION (VPS / cron):
    DATABASE_URL=... PYTHONPATH=<repo> venv/bin/python scripts/shadow_readout.py
    ... --self-test    # offline: cohort split + alert logic, no DB/log
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_shadow as az  # noqa: E402  (pure readout core, reused)

TRUST1 = 1783985376  # cohort-1: quote-fix redeploy epoch (2026-07-13 23:29 UTC)
TRUST2 = 1784143245  # cohort-2 fallback: watcher restart epoch (2026-07-15 19:20:45 UTC)


_COHORT_RE = re.compile(r"^cohort(\d+)$")


def _parse_epoch(key: str, blob: dict, strict: bool) -> float:
    """admitted_utc -> epoch seconds. strict: a group WITH addresses but an
    unparsable admitted_utc fails loud (a bad epoch silently WIDENS the trust
    window). Empty/absent group falls back to TRUST2 (unused — no members)."""
    try:
        return datetime.fromisoformat(str(blob.get("admitted_utc"))).timestamp()
    except (TypeError, ValueError):
        if blob.get("addresses") and strict:
            raise ValueError(
                f"roster group '{key}' has addresses but an unparsable "
                f"admitted_utc — fix the ledger before reading out")
        return TRUST2


def load_cohorts(roster: dict) -> list[tuple[str, list[str], float]]:
    """Ordered [(name, addrs, epoch), ...] from the LIVE roster JSON
    (chain_audit.json). Membership comes from the file, NEVER hardcoded
    (session-close review 2026-07-15 findings A/#9: an empty filter silently
    pooled cohort-2 into cohort-1's pre-registered readout). Groups:
      - cohort1  — from `cohort1_original`, epoch TRUST1 (the quote-fix
        redeploy), ALWAYS present, the pre-registered readout.
      - cohort2, cohort3, ... — keys `cohort<N>` (N>=2), each
        {addresses, admitted_utc}, own PARSED epoch, NEVER pooled. A missing/
        bad admitted_utc fails loud (cohort<N> generalization 2026-07-19 for
        wave promotions; supersedes the cohort2-only hardcode).
      - probe — optional, observation-only (e.g. 0xf705fa pre-graduation),
        own line, never pooled, never part of a cohort verdict.
    Fail-loud on inconsistency (empty required group, cross-group overlap,
    clean != union) — a wrong split must never produce a readout."""
    c1 = [str(a).lower() for a in roster.get("cohort1_original", [])]
    if not c1:
        raise ValueError("roster lacks a non-empty cohort1_original — refusing "
                         "a readout on an ambiguous cohort split")
    groups: list[tuple[str, list[str], float]] = [("cohort1", c1, TRUST1)]
    nums = []  # additional admitted cohorts: keys 'cohort<N>' with N>=2 only
    for k in roster:
        m = _COHORT_RE.match(k)
        if m and int(m.group(1)) >= 2:
            nums.append(int(m.group(1)))
    nums.sort()
    if not nums:
        raise ValueError("roster lacks a cohort2+ admitted group — refusing "
                         "a readout on an ambiguous cohort split")
    for n in nums:
        key = f"cohort{n}"
        blob = roster.get(key) or {}
        addrs = [str(a).lower() for a in blob.get("addresses", [])]
        if not addrs:
            # fail loud (restores HEAD's `if not c2: raise`): an empty admitted
            # cohort would reach cohort_readout with members="" -> filter_traders
            # treats "" as "all records" and the whole roster pools under this
            # label, able to fire a FALSE 'POWERED' go/no-go alert (the exact
            # silent-pooling class of 2026-07-15 finding A/#9). Verified defect,
            # adversarial review 2026-07-19.
            raise ValueError(
                f"roster group '{key}' present but has NO addresses — an empty "
                f"admitted cohort pools the whole roster under its label; "
                f"remove the key or populate its members")
        groups.append((key, addrs, _parse_epoch(key, blob, strict=True)))
    probe_blob = roster.get("probe") or {}
    probe = [str(a).lower() for a in probe_blob.get("addresses", [])]
    if probe:
        groups.append(("probe", probe,
                       _parse_epoch("probe", probe_blob, strict=True)))
    # BENCHED (2026-07-20): a trader pulled from a cohort into a TIME-OUT —
    # never pooled into any cohort verdict, own line, own FRESH epoch (the
    # bench start) so the line measures FORWARD re-evaluation only, not the
    # dragged history that got them benched. The pre-registered verdict that
    # INCLUDED them is already computed + locked in the durable log; benching
    # changes forward grouping, never that record. Reversible: when the
    # forward line clears the re-admission bar (operator-gated), the address
    # moves benched -> a new cohort. Structurally identical to probe (own
    # epoch, never pooled, counted in the ledger union) — mirror it exactly.
    bench_blob = roster.get("benched") or {}
    bench = [str(a).lower() for a in bench_blob.get("addresses", [])]
    if bench:
        groups.append(("benched", bench,
                       _parse_epoch("benched", bench_blob, strict=True)))
    for name, addrs, _ in groups:
        # intra-group duplicate (adversarial review 2026-07-19, finding #1):
        # a dup passes the cross-group set() checks but inflates the label
        # count AND breaks the leave-one-out `rest` (a duplicated top trader
        # leaves rest=[] -> filter_traders("") -> whole-roster pooling in the
        # LOO line). Fail loud — a dup is a ledger typo, not a valid split.
        if len(addrs) != len(set(addrs)):
            raise ValueError(f"roster group '{name}' has a DUPLICATE address — "
                             f"fix the ledger before reading out")
    memberships = [set(a) for _, a, _ in groups]
    union = set().union(*memberships)
    if sum(len(m) for m in memberships) != len(union):
        raise ValueError("cohort/probe group OVERLAP — an address in two groups "
                         "would be pooled into two readouts; fix the ledger "
                         "before reading out")
    clean = {str(a).lower() for a in roster.get("clean", [])}
    if clean != union:
        raise ValueError(f"roster clean ({len(clean)}) != the union of "
                         f"cohort1_original + cohort<N> + probe + benched "
                         f"({len(union)}) — a roster change was made without "
                         f"extending the ledger; fix chain_audit.json before "
                         f"reading out")
    return groups


async def fresh_outcomes(tokens: list[str]) -> dict[str, int]:
    """token_id -> 1 (won) / 0 (lost) from the markets table — FRESH, not a
    stale cache. Only definitively-resolved YES/NO markets contribute."""
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    try:
        async with db.get_session() as s:
            await s.execute(text("SET LOCAL statement_timeout='60s'"))
            rows = (await s.execute(text(
                "SELECT resolution, resolved, yes_token_id, no_token_id "
                "FROM markets WHERE yes_token_id = ANY(:t) OR no_token_id = ANY(:t)"),
                {"t": tokens})).fetchall()
    finally:
        await db.close()
    out: dict[str, int] = {}
    for r in rows:
        m = r._mapping
        if not (m["resolved"] and m["resolution"] in ("YES", "NO")):
            continue
        yt, nt = str(m["yes_token_id"]), str(m["no_token_id"])
        out[yt] = 1 if m["resolution"] == "YES" else 0
        out[nt] = 0 if m["resolution"] == "YES" else 1
    return out


def cohort_readout(records, outcomes, trust_after, traders, cfg) -> dict:
    # ROOT guard for the whole silent-pooling CLASS (root-cause audit
    # 2026-07-19): a per-group readout with an EMPTY member list must mean
    # ZERO records, never the whole roster. filter_traders treats "" as
    # "all records" (a legitimate CLI whole-roster feature), so an empty
    # `traders` here is the shared footgun that the load_cohorts empty-cohort
    # and intra-group-dup guards each only blocked ONE trigger-path of. This
    # disarms the MECHANISM at the single chokepoint every group flows through
    # (the group readout AND the leave-one-out `rest`), so any future path
    # that yields empty members is safe, not just the two we enumerated.
    recs = az.filter_traders(records, traders) if traders.strip() else []
    recs, _ = az.repair_records(recs, cfg.max_chase, cfg.max_spread, trust_after)
    return az.analyze(recs, outcomes, cfg.fee, cfg.econ_floor, cfg.p_min,
                      cfg.min_markets)


def per_trader_lines(recs, outcomes, trust, members, label, cfg) -> list[str]:
    """Per-trader DIAGNOSTIC breakdown for one cohort (opt-in `--per-trader`;
    the POWERED alert's second half asks for it alongside the leave-one-out).

    Runs the SAME canonical pipeline as the cohort line — cohort_readout ->
    filter_traders -> repair_records -> az.analyze — so a per-trader number can
    never drift from the money-gate readout's. A duplicated verdict statistic
    is exactly the defect `bdcfefb` fixed; never re-implement the stats here.

    DELIBERATELY never calls alerts_for(): one trader crossing the resolved bar
    must not fire a COHORT-level POWERED alert — that would manufacture a
    verdict out of a single arm (the silent-pooling class, inverted).

    Returns [] for a 1-member group: the per-trader line would just restate the
    cohort line, and an empty `members` must yield NO lines rather than a
    whole-roster line (cohort_readout's root guard also zeroes that path)."""
    if len(members) <= 1:
        return []
    # Bonferroni anchor: prose loses to a three-decimal number. An operator
    # trained on p_min=0.95 reads an arm at P=0.96 as a finding; among N arms it
    # is unremarkable. Give the warning a bar the reader can actually apply.
    adj = 1.0 - (1.0 - cfg.p_min) / len(members)
    out = [f"  --- {label} PER-TRADER (DIAGNOSTIC ONLY — {len(members)} post-hoc "
           f"arms => multiple comparisons: an arm needs P(>0) >= {adj:.4f} "
           f"(Bonferroni) to carry the weight P >= {cfg.p_min:.2f} carries on "
           f"the cohort line. Arms do NOT partition the cohort — a token traded "
           f"by two members is counted once in EACH arm, so arm 'resolved/N' is "
           f"not comparable to the cohort's. NOT a verdict, and NOT grounds to "
           f"re-cut a pre-registered cohort) ---"]
    per = []
    for addr in members:
        r1 = cohort_readout(recs, outcomes, trust, addr, cfg)
        per.append((r1.get("first_buys", 0), str(addr).lower(), r1))
    # deterministic order: most-influential first, address as tiebreak.
    # (sorted() compares ONLY key values, so the dict 3rd element is never
    # compared even on a full tie — verified 2026-07-20 against a review claim
    # that it would TypeError.)
    for fb, addr, r1 in sorted(per, key=lambda x: (-x[0], x[1])):
        if not fb:
            # an empty sample formats as `OK-rate=nan% ... lag_p50=nans` and
            # then advises "keep collecting" — garbage in a durable log, and
            # wrong guidance for a member who is simply dormant
            out.append(f"    [arm {addr[:10]}…] no first-buys in window")
            continue
        # label carries "arm" so a line QUOTED IN ISOLATION still announces what
        # it is — the documented workflow is a human pasting single lines into
        # chat, which strips the header warning above
        out.append("    " + fmt_line(f"arm {addr[:10]}…", r1, cfg.min_markets,
                                     diagnostic=True))
    return out


def concentration(res: dict) -> tuple[Optional[str], float]:
    """(dominant_trader, their share of first-buys). STANDING OPERATOR RULE
    (2026-07-15): every readout must disclose sample concentration BEFORE its
    aggregate is presented — the pooled cohort-1 edge turned out to be
    effectively ONE trader's edge (0x84dbb7 = 1,171 of 1,627 records), which a
    bare pooled number silently hides. Protocol-14 bucket-concentration applied
    to traders."""
    by = res.get("by_trader") or {}
    tot = sum(sum(c.values()) for c in by.values())
    if not tot:
        return None, 0.0
    top = max(by, key=lambda t: sum(by[t].values()))
    return top, sum(by[top].values()) / tot


_DIAG_POSTHOC = ("post-hoc cut — the pre-registered verdict is the cohort line "
                 "only; this cut can never 'survive'")
_DIAG_BENCH = ("benched trader in TIME-OUT — a forward re-evaluation, never a "
               "cohort verdict; re-admission is operator-gated")


def fmt_line(label: str, res: dict, min_markets: int,
             diagnostic: bool = False, diag_reason: str = _DIAG_POSTHOC) -> str:
    """One readout line. `diagnostic=True` marks a line that must NEVER print
    the pre-registered verdict vocabulary — a POST-HOC cut (leave-one-out,
    per-trader arm) or a benched observation line. `diag_reason` names why.

    A post-hoc cut must NEVER print the pre-registered verdict vocabulary.
    `az.analyze` mints "SURVIVES (pre-registered bars met)" for ANY cut that
    clears min_markets + p_min — but a cut CHOSEN AFTER SEEING THE DATA cannot
    satisfy a pre-registration it was never part of, and "UNDERPOWERED — keep
    collecting" on such a cut is an active instruction to collect until the
    post-hoc cut passes. Adversarial review 2026-07-20 (3 independent lenses,
    all converged): the LIVE cohort1-minus-top LOO sits at 28/30 resolved — TWO
    markets from printing that exact string on a trader dropped for looking bad.
    Default False keeps every pre-existing caller's output identical."""
    s = (f"[{label}] first-buys={res['first_buys']} OK-rate={res['ok_rate']:.1%} "
         f"tax_med={res['tax_p50']:+.4f} lag_p50={res['lag_p50']:.1f}s")
    top, share = concentration(res)
    # A single-trader DIAGNOSTIC arm is trivially 100% concentrated; 16 such
    # markers desensitise the reader to the one conc marker that carries
    # information (the cohort line's 40%). Concentration disclosure is a
    # standing operator rule — diluting it is a real cost, not cosmetic.
    # Scoped to `diagnostic` on purpose: a genuine 1-member COHORT (the probe)
    # keeps its marker, so this fix does not churn the live cron line.
    if top is not None and not (diagnostic
                                and len(res.get("by_trader") or {}) <= 1):
        s += f" conc={top[:10]}…{share:.0%}"
    if "shadow_edge" in res:
        s += (f" | resolved={res['resolved_mkts']}/{min_markets} "
              f"edge={res['shadow_edge']:+.4f} P(>0)={res['shadow_edge_p']:.3f}")
        s += (f" :: NO VERDICT ({diag_reason})"
              if diagnostic else f" :: {res['edge_verdict']}")
    return s


def alerts_for(label: str, res: dict, min_markets: int,
               neg_p_max: float = 0.10, neg_min_n: int = 10) -> list[str]:
    """Trigger: cohort crosses the power bar (>= min_markets resolved), OR its
    edge is convincingly NEGATIVE before then (P(>0) <= neg_p_max on >= neg_min_n).
    Every alert carries the concentration disclosure — a verdict must never be
    run blind to who dominates the sample (standing operator rule 2026-07-15)."""
    out = []
    if "shadow_edge" not in res:
        return out
    top, share = concentration(res)
    conc = (f"; CONCENTRATION {top[:10]}…={share:.0%} — verdict requires the "
            f"per-trader breakdown + leave-one-out" if top else "")
    n = res["resolved_mkts"]
    if n >= min_markets:
        out.append(f"{label}: resolved {n} >= {min_markets} — POWERED; run the "
                   f"pre-registered verdict (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.3f}){conc}")
    if (n >= neg_min_n and res["shadow_edge"] < 0
            and res["shadow_edge_p"] <= neg_p_max):
        out.append(f"{label}: edge NEGATIVE firming (edge={res['shadow_edge']:+.4f} "
                   f"P(>0)={res['shadow_edge_p']:.2f} on {n} mkts){conc}")
    return out


async def run(args) -> int:
    with open(args.roster) as f:
        cohorts = load_cohorts(json.load(f))
    recs = az.load_records(args.log)
    tokens = sorted({str(r["token_id"]) for r in recs if r.get("token_id")})
    outcomes = await fresh_outcomes(tokens)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    OBS = ("probe", "benched")  # observation-only groups: never a cohort count
    counts = "+".join(str(len(a)) for n, a, _ in cohorts if n not in OBS)
    for obs in OBS:
        obs_n = next((len(a) for n, a, _ in cohorts if n == obs), 0)
        if obs_n:
            counts += f"+{obs_n}{obs}"
    lines = [f"===== shadow readout {stamp}  (fresh DB labels: "
             f"{len(outcomes) // 2} resolved markets among {len(tokens)} shadow "
             f"tokens; cohorts from {os.path.basename(args.roster)}: "
             f"{counts}) ====="]
    all_alerts: list[str] = []
    for name, members, trust in cohorts:
        label = f"{name}({len(members)})"
        res = cohort_readout(recs, outcomes, trust, ",".join(members), args)
        # benched is an OBSERVATION line — a timed-out trader's FORWARD
        # re-evaluation. It is never a cohort verdict, so it prints diagnostic
        # (never "SURVIVES") and never fires a cohort POWERED/negative alert.
        is_bench = (name == "benched")
        if is_bench:
            lines.append(fmt_line(f"{name}({len(members)}) TIME-OUT", res,
                                  args.min_markets, diagnostic=True,
                                  diag_reason=_DIAG_BENCH))
        else:
            lines.append(fmt_line(label, res, args.min_markets))
            all_alerts += alerts_for(label, res, args.min_markets)
        # standing rule: when one trader dominates the sample, ALSO show the
        # cohort WITHOUT them — the pooled number alone is misleading
        top, share = concentration(res)
        if top and share >= args.conc_threshold and len(members) > 1:
            rest = [a for a in members if a.lower() != top.lower()]
            loo = cohort_readout(recs, outcomes, trust, ",".join(rest), args)
            # diagnostic=True: the LOO is a POST-HOC cut. Without this it prints
            # the pre-registered verdict vocabulary — including "SURVIVES
            # (pre-registered bars met)" — on a cohort re-cut by dropping the
            # trader who looked worst AFTER the data was seen. Live cohort1-
            # minus-top is at 28/30; two more resolved markets and the
            # un-fixed line asserts a survival that never was.
            lines.append("  " + fmt_line(f"{label} minus {top[:10]}… (LOO)",
                                         loo, args.min_markets,
                                         diagnostic=True))
        if args.per_trader:
            lines += per_trader_lines(recs, outcomes, trust, members, label, args)
    block = "\n".join(lines)
    print(block)
    if args.per_trader:
        # DIAGNOSTIC RUN — never mutate the durable record. Two verified hazards
        # (adversarial review 2026-07-20):
        #  (a) ALERT DESTRUCTION. alerts_for is recomputed from LIVE data and the
        #      negative-firming trigger is NOT monotonic — it un-fires as new
        #      records arrive. A steward running --per-trader to INVESTIGATE an
        #      alert could take the else-branch below and os.remove() the very
        #      alert file whose existence IS the operator's trigger signal.
        #  (b) LOG POLLUTION. ~30 arm lines per run push the cohort verdict
        #      lines, the concentration disclosure and the block header out of
        #      the documented `tail` view, so the next session reads single-arm
        #      extremes as the readout — the exact misreading the header warns
        #      about, with the warning itself scrolled off.
        print("\n(--per-trader: DIAGNOSTIC run — durable log NOT appended, ALERT "
              "file NOT touched. Re-run without the flag for the daily record.)")
        return 0
    with open(args.out, "a") as f:
        f.write(block + "\n")
    if all_alerts:
        with open(args.alert, "w") as f:
            f.write(stamp + "\n" + "\n".join(all_alerts) + "\n")
        print("*** ALERT:", "; ".join(all_alerts))
    else:
        # the ALERT file's EXISTENCE is the documented trigger signal — a
        # stale one from a prior day must not persist (review finding C)
        try:
            os.remove(args.alert)
        except FileNotFoundError:
            pass
        print("(no trigger — still accruing; a steward session should relay the "
              "line above to the operator)")
    return 0


def _self_test() -> int:
    print("SELF-TEST — shadow_readout cohort split + alerts (offline)\n")
    ok = True
    recs = [{"trader": "0xA", "token_id": "1", "verdict": "OK", "first_buy": True,
             "whale_price": 0.5, "shadow_fill": 0.5, "detect_lag_s": 3.0}]
    ok1 = (len(az.filter_traders(recs, "0xa")) == 1
           and az.filter_traders(recs, "0xB") == [])
    print(f"  [split] cohort filter case-insensitive : {ok1}"); ok &= ok1
    # powered trigger
    ok2 = any("POWERED" in a for a in alerts_for(
        "c", {"shadow_edge": 0.01, "shadow_edge_p": 0.9, "resolved_mkts": 35}, 30))
    print(f"  [alert] resolved >= min -> POWERED : {ok2}"); ok &= ok2
    # negative-firming trigger
    ok3 = any("NEGATIVE" in a for a in alerts_for(
        "c", {"shadow_edge": -0.05, "shadow_edge_p": 0.05, "resolved_mkts": 12}, 30))
    print(f"  [alert] negative firms before power bar : {ok3}"); ok &= ok3
    # no trigger while accruing positive-ish underpowered
    ok4 = alerts_for("c", {"shadow_edge": 0.01, "shadow_edge_p": 0.6,
                           "resolved_mkts": 12}, 30) == []
    print(f"  [alert] underpowered+noisy -> no trigger : {ok4}"); ok &= ok4
    # cohorts from the roster file, fail-loud on ledger drift
    def as_map(roster):
        return {n: (a, e) for n, a, e in load_cohorts(roster)}
    good = {"clean": ["0xA", "0xB", "0xC"], "cohort1_original": ["0xa", "0xb"],
            "cohort2": {"addresses": ["0xC"],
                        "admitted_utc": "2026-07-15T19:16:00+00:00"}}
    g = as_map(good)
    ok5 = (g["cohort1"][0] == ["0xa", "0xb"] and g["cohort2"][0] == ["0xc"]
           and g["cohort1"][1] == TRUST1 and g["cohort2"][1] > 1_784_000_000
           and "probe" not in g)
    print(f"  [cohorts] loaded from roster, cohort1=TRUST1, epoch parsed : {ok5}")
    ok &= ok5
    # probe group (2026-07-16): own membership+epoch, counted in the ledger
    withp = {"clean": ["0xA", "0xB", "0xC", "0xD"],
             "cohort1_original": ["0xa", "0xb"],
             "cohort2": {"addresses": ["0xC"],
                         "admitted_utc": "2026-07-15T19:16:00+00:00"},
             "probe": {"addresses": ["0xD"],
                       "admitted_utc": "2026-07-16T17:00:00+00:00"}}
    g = as_map(withp)
    ok5b = (g["probe"][0] == ["0xd"] and g["probe"][1] > g["cohort2"][1]
            and "0xd" not in g["cohort1"][0] and "0xd" not in g["cohort2"][0])
    print(f"  [cohorts] probe group parsed, disjoint from cohorts : {ok5b}")
    ok &= ok5b
    # cohort3 wave promotion (2026-07-19): Nth admitted cohort, own epoch,
    # ORDERED after cohort2, never pooled, probe stays separate + last
    wave = {"clean": ["0xA", "0xB", "0xC", "0xD", "0xE"],
            "cohort1_original": ["0xa", "0xb"],
            "cohort2": {"addresses": ["0xC"],
                        "admitted_utc": "2026-07-15T19:16:00+00:00"},
            "cohort3": {"addresses": ["0xD"],
                        "admitted_utc": "2026-07-19T13:00:00+00:00"},
            "probe": {"addresses": ["0xE"],
                      "admitted_utc": "2026-07-16T17:00:00+00:00"}}
    order = [n for n, _, _ in load_cohorts(wave)]
    g = as_map(wave)
    ok5c = (order == ["cohort1", "cohort2", "cohort3", "probe"]
            and g["cohort3"][0] == ["0xd"]
            and g["cohort3"][1] > g["cohort2"][1])
    print(f"  [cohorts] cohort3 wave: ordered, own epoch, not pooled : {ok5c}")
    ok &= ok5c
    # BENCHED group (2026-07-20): trader in time-out — own FRESH epoch, never
    # pooled, disjoint, counted in the ledger union. Realistic bench-only shape:
    # a cohort1 member (0xb) pulled out to benched, cohort1_original shrinks.
    benched_ledger = {"clean": ["0xA", "0xB", "0xC", "0xE"],
                      "cohort1_original": ["0xa"],  # 0xb removed -> benched
                      "cohort2": {"addresses": ["0xC"],
                                  "admitted_utc": "2026-07-15T19:16:00+00:00"},
                      "probe": {"addresses": ["0xE"],
                                "admitted_utc": "2026-07-16T17:00:00+00:00"},
                      "benched": {"addresses": ["0xB"],
                                  "admitted_utc": "2026-07-20T15:00:00+00:00"}}
    order = [n for n, _, _ in load_cohorts(benched_ledger)]
    g = as_map(benched_ledger)
    ok5d = (order == ["cohort1", "cohort2", "probe", "benched"]
            and g["benched"][0] == ["0xb"]
            and g["benched"][1] > g["cohort2"][1]  # fresh epoch, after cohort2
            and "0xb" not in g["cohort1"][0]        # not double-counted
            and "0xb" not in g["cohort2"][0])
    print(f"  [cohorts] benched: fresh epoch, disjoint, in union : {ok5d}")
    ok &= ok5d
    # benched line prints diagnostic (never SURVIVES) with its own reason.
    # Fixture is a would-be-passing result: proves the pass verdict is refused.
    _wonb = {"first_buys": 40, "ok_rate": 1.0, "tax_p50": 0.01, "lag_p50": 1.0,
             "by_trader": {"0xb": {"OK": 40}}, "shadow_edge": 0.21,
             "shadow_edge_p": 0.999, "resolved_mkts": 40,
             "edge_verdict": "SURVIVES (pre-registered bars met)"}
    bl = fmt_line("benched(1) TIME-OUT", _wonb, 30, diagnostic=True,
                  diag_reason=_DIAG_BENCH)
    ok5e = ("SURVIVES" not in bl and "TIME-OUT" in bl and "operator-gated" in bl
            and "NO VERDICT" in bl)
    print(f"  [cohorts] benched line: diagnostic, no verdict : {ok5e}")
    ok &= ok5e
    for bad in ({"clean": ["0xA"], "cohort1_original": [], "cohort2": {}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # admit w/o ledger
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"}},
                {"clean": ["0xA", "0xB"],  # NO cohort2+ admitted group
                 "cohort1_original": ["0xa", "0xb"]},
                {"clean": ["0xA", "0xB", "0xC"],  # EMPTY admitted cohort ->
                 "cohort1_original": ["0xa", "0xb"],  # would pool whole roster
                 "cohort2": {"addresses": [],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "cohort3": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-19T13:00:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC"],  # INTRA-GROUP DUPLICATE ->
                 "cohort1_original": ["0xa", "0xa", "0xb"],  # inflates+breaks LOO
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC"],  # OVERLAP: 0xC in two groups
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "probe": {"addresses": ["0xc"],
                           "admitted_utc": "2026-07-16T17:00:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # cohort3 BAD epoch
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "cohort3": {"addresses": ["0xD"], "admitted_utc": "garbage"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # probe w/ BAD epoch
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "probe": {"addresses": ["0xD"], "admitted_utc": "garbage"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # BENCHED w/ BAD epoch
                 "cohort1_original": ["0xa", "0xb"],
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "benched": {"addresses": ["0xD"], "admitted_utc": "garbage"}},
                {"clean": ["0xA", "0xB", "0xC"],  # OVERLAP: benched addr also
                 "cohort1_original": ["0xa", "0xb"],  # in cohort1 -> double count
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "benched": {"addresses": ["0xA"],
                             "admitted_utc": "2026-07-20T15:00:00+00:00"}},
                {"clean": ["0xA", "0xB", "0xC", "0xD"],  # benched NOT in clean
                 "cohort1_original": ["0xa", "0xb"],     # -> clean != union
                 "cohort2": {"addresses": ["0xC"],
                             "admitted_utc": "2026-07-15T19:16:00+00:00"},
                 "benched": {"addresses": ["0xZ"],
                             "admitted_utc": "2026-07-20T15:00:00+00:00"}}):
        try:
            load_cohorts(bad)
            ok6 = False
        except ValueError:
            ok6 = True
        print(f"  [cohorts] inconsistent ledger -> refuses readout : {ok6}")
        ok &= ok6
    # concentration disclosure (standing operator rule 2026-07-15)
    dom = {"by_trader": {"0xwhale": {"OK": 9}, "0xother": {"OK": 1}}}
    top, share = concentration(dom)
    ok7 = top == "0xwhale" and abs(share - 0.9) < 1e-9
    print(f"  [conc] dominant trader + share computed : {ok7}"); ok &= ok7
    ok8 = concentration({"by_trader": {}}) == (None, 0.0)
    print(f"  [conc] empty cohort -> no top : {ok8}"); ok &= ok8
    # concentration string reaches the line and the alert
    line = fmt_line("c", {**dom, "first_buys": 10, "ok_rate": 1.0,
                          "tax_p50": 0.01, "lag_p50": 2.0}, 30)
    al = alerts_for("c", {**dom, "shadow_edge": 0.03, "shadow_edge_p": 0.99,
                          "resolved_mkts": 35}, 30)
    ok9 = "conc=0xwhale" in line and any("CONCENTRATION" in a for a in al)
    print(f"  [conc] disclosed in line AND in every alert : {ok9}"); ok &= ok9
    # ROOT guard (root-cause audit 2026-07-19): empty members -> ZERO records,
    # NOT the whole roster, even if a ledger guard were bypassed. Disarms the
    # shared silent-pooling mechanism at the cohort_readout chokepoint.
    from types import SimpleNamespace
    _cfg = SimpleNamespace(max_chase=0.02, max_spread=0.05, fee=0.02,
                           econ_floor=0.02, p_min=0.95, min_markets=30)
    _recs = [{"trader": "0xX", "token_id": "t", "verdict": "OK",
              "first_buy": True, "whale_price": 0.5, "shadow_fill": 0.5,
              "detect_lag_s": 1.0, "best_ask": 0.5, "detect_ts": TRUST1 + 1}]
    ok10 = (cohort_readout(_recs, {}, TRUST1, "", _cfg)["first_buys"] == 0
            and cohort_readout(_recs, {}, TRUST1, "0xX", _cfg)["first_buys"] == 1)
    print(f"  [root] empty members -> 0 records not whole roster : {ok10}")
    ok &= ok10
    # ---- per-trader diagnostic breakdown (2026-07-20, POWERED-alert follow-up)
    _pt = [{"trader": "0xA", "token_id": "t1", "verdict": "OK", "first_buy": True,
            "whale_price": 0.5, "shadow_fill": 0.5, "detect_lag_s": 1.0,
            "best_ask": 0.5, "detect_ts": TRUST1 + 1},
           {"trader": "0xA", "token_id": "t2", "verdict": "OK", "first_buy": True,
            "whale_price": 0.5, "shadow_fill": 0.5, "detect_lag_s": 1.0,
            "best_ask": 0.5, "detect_ts": TRUST1 + 1},
           {"trader": "0xB", "token_id": "t3", "verdict": "OK", "first_buy": True,
            "whale_price": 0.5, "shadow_fill": 0.5, "detect_lag_s": 1.0,
            "best_ask": 0.5, "detect_ts": TRUST1 + 1}]
    pl = per_trader_lines(_pt, {}, TRUST1, ["0xA", "0xB"], "c(2)", _cfg)
    # one header + one line per member, most-influential FIRST (0xA has 2)
    ok11 = (len(pl) == 3 and "PER-TRADER" in pl[0]
            and "0xa" in pl[1] and "0xb" in pl[2])
    print(f"  [per-trader] one line per member, sorted by influence : {ok11}")
    ok &= ok11
    # each arm is filtered to ITS OWN records — never the pooled cohort
    ok12 = ("first-buys=2" in pl[1] and "first-buys=1" in pl[2])
    print(f"  [per-trader] arms isolated (2/1), not pooled : {ok12}"); ok &= ok12
    # a 1-member group adds NOTHING (would merely restate the cohort line)
    ok13 = (per_trader_lines(_pt, {}, TRUST1, ["0xA"], "p(1)", _cfg) == []
            and per_trader_lines(_pt, {}, TRUST1, [], "e(0)", _cfg) == [])
    print(f"  [per-trader] 1-member/empty group -> no lines : {ok13}"); ok &= ok13
    # DIAGNOSTIC framing must be present — the multiple-comparisons warning is
    # what stops a reader treating an extreme arm as a verdict
    ok14 = ("DIAGNOSTIC ONLY" in pl[0] and "multiple comparisons" in pl[0]
            and "NOT a verdict" in pl[0])
    print(f"  [per-trader] carries multiple-comparisons warning : {ok14}")
    ok &= ok14
    # deterministic across calls (no set/dict iteration order leakage)
    ok15 = (per_trader_lines(_pt, {}, TRUST1, ["0xB", "0xA"], "c(2)", _cfg) == pl)
    print(f"  [per-trader] order independent of input order : {ok15}"); ok &= ok15
    # ---- post-hoc cuts must NEVER borrow the pre-registered verdict vocabulary
    # (adversarial review 2026-07-20; the LIVE LOO is 2 markets from tripping it)
    _won = {"first_buys": 40, "ok_rate": 1.0, "tax_p50": 0.01, "lag_p50": 1.0,
            "by_trader": {"0xa": {"OK": 40}}, "shadow_edge": 0.21,
            "shadow_edge_p": 0.999, "resolved_mkts": 40,
            "edge_verdict": "SURVIVES (pre-registered bars met)"}
    pre, diag = (fmt_line("c", _won, 30),
                 fmt_line("c", _won, 30, diagnostic=True))
    ok16 = ("SURVIVES (pre-registered bars met)" in pre
            and "SURVIVES" not in diag and "NO VERDICT" in diag
            and "post-hoc" in diag)
    print(f"  [post-hoc] diagnostic cut refuses verdict vocabulary : {ok16}")
    ok &= ok16
    # the guard must be OPT-IN so every pre-existing caller is byte-identical
    ok17 = (fmt_line("c", _won, 30) == fmt_line("c", _won, 30, diagnostic=False))
    print(f"  [post-hoc] default OFF -> unchanged for old callers : {ok17}")
    ok &= ok17
    # arms carry it too, and stay self-describing when quoted in isolation
    _dompt = _pt + [{"trader": "0xA", "token_id": f"x{i}", "verdict": "OK",
                     "first_buy": True, "whale_price": 0.5, "shadow_fill": 0.5,
                     "detect_lag_s": 1.0, "best_ask": 0.5,
                     "detect_ts": TRUST1 + 1} for i in range(3)]
    apl = per_trader_lines(_dompt, {}, TRUST1, ["0xA", "0xB"], "c(2)", _cfg)
    ok18 = all("SURVIVES" not in ln for ln in apl) and "arm 0xa" in apl[1]
    print(f"  [post-hoc] arms: no verdict, label self-describing : {ok18}")
    ok &= ok18
    # multiplicity anchor is a NUMBER, not just prose (0.95 over 2 arms -> 0.975)
    ok19 = "0.9750" in apl[0] and "Bonferroni" in apl[0]
    print(f"  [per-trader] Bonferroni bar shown numerically : {ok19}"); ok &= ok19
    # a dormant member renders cleanly instead of nan-garbage
    dl = per_trader_lines(_pt, {}, TRUST1, ["0xA", "0xZZ"], "c(2)", _cfg)
    dormant = [ln for ln in dl if "0xzz" in ln]
    # NOTE scope: only the DORMANT arm is asserted nan-free. An arm with
    # first-buys but no RESOLVED markets still renders `edge=+nan P(>0)=nan` —
    # that is pre-existing cohort-line behaviour (the live probe line shows it
    # today), so it is deliberately NOT changed here; changing it would alter
    # live output for a cosmetic gain.
    ok20 = (len(dormant) == 1 and "no first-buys in window" in dormant[0]
            and "nan" not in dormant[0])
    print(f"  [per-trader] dormant member -> clean line, no nan : {ok20}")
    ok &= ok20
    # single-trader result: the trivially-100% conc marker is suppressed so it
    # cannot dilute the ONE conc disclosure that carries information
    ok21 = ("conc=" not in fmt_line("a", _won, 30, diagnostic=True)
            and "conc=0xwhale" in line
            # a genuine 1-member COHORT (the probe) is NOT diagnostic and KEEPS
            # its marker — locks the reduced blast radius on the live cron line
            and "conc=0xa" in fmt_line("probe(1)", _won, 30))
    print(f"  [conc] tautological arm 100% cut, cohort line kept : {ok21}")
    ok &= ok21
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Durable per-cohort shadow readout "
                                             "with fresh DB labels + alerts")
    ap.add_argument("--log", default="/opt/pa2-shared/mirror3_shadow.jsonl")
    ap.add_argument("--roster", default="/opt/pa2-shared/mb_copyable_data/chain_audit.json",
                    help="live roster JSON; cohort membership comes from its "
                         "cohort1_original/cohort2 keys (NEVER hardcoded)")
    # deep_dive/ is polymarket-owned (mb_copyable_data itself is root-owned —
    # the cron runs as polymarket and must be able to write here)
    ap.add_argument("--out", default="/opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt")
    ap.add_argument("--alert", default="/opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_ALERT.txt")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--econ-floor", type=float, default=0.02, dest="econ_floor")
    ap.add_argument("--p-min", type=float, default=0.95, dest="p_min")
    ap.add_argument("--min-markets", type=int, default=30, dest="min_markets")
    ap.add_argument("--max-chase", type=float, default=0.02, dest="max_chase")
    ap.add_argument("--max-spread", type=float, default=0.05, dest="max_spread")
    ap.add_argument("--conc-threshold", type=float, default=0.50,
                    dest="conc_threshold",
                    help="top-trader share of first-buys above which a leave-"
                         "one-out line is ALSO printed (standing operator rule)")
    ap.add_argument("--per-trader", action="store_true", dest="per_trader",
                    help="ALSO emit a per-trader diagnostic line per cohort "
                         "(same canonical pipeline; never alerts). Default OFF "
                         "so the daily cron output is unchanged.")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        raise SystemExit(_self_test())
    raise SystemExit(asyncio.run(run(args)))
