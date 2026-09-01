#!/usr/bin/env python3
"""Stress-test the sharp-line pipeline end-to-end on SYNTHETIC ground truth.

WHY: the live backtest is gated on slow forward-collected data, but the
SOFTWARE can be validated now. This harness generates matches where the right
answer is KNOWN — true win probabilities, a planted sharp-vs-PM edge, known
winners, adversarial team names, injected garbage — writes real snapshot/result
JSONL files, runs the REAL production chain (iter_snapshots_jsonl ->
reduce_to_closing_lines -> load_bulk_results -> join_closing_lines_to_results ->
evaluate_sharp_line + edge_backtest_from_joined), and asserts the chain
recovers the planted truth:

  S1  label integrity     — every joined record's home_won matches ground truth
                            (ZERO mislabels tolerated; absences are fine)
  S2  no look-ahead       — the closing line is the last PRE-start snapshot,
                            never a post-start one
  S3  calibration recovery— favorite hit-rate ~= book mean favorite prob and
                            Brier ~= E[p(1-p)] on the synthetic truth
  S4  planted-edge recovery— with PM prices offset by a known edge, the edge
                            backtest fires and realizes ROI ~= planted edge
  S5a absent-orientation  — resolver returning None DROPS records; ROI on the
                            survivors still ~= planted edge (drops never corrupt)
  S5b flip blind spot     — a flipped resolver produces a SELF-CONSISTENT
                            backtest (it settles with the same wrong bool it
                            decided with) that shows phantom PROFIT: backtest
                            P&L is structurally BLIND to orientation corruption.
                            Locked here as a permanent demonstration of why
                            orientation must be verified independently against
                            the CLOB (clob_labels), never inferred from P&L.
  S5c flip real damage    — the same flipped DECISIONS settled against TRUTH
                            (what reality does) destroy the planted edge
  S6  sibling-roster veto — main-vs-academy fixtures on the same day never
                            cross-join (odds for "Org N" never take the result
                            or PM market of "Org N Academy")
  S7  garbage resilience  — malformed JSONL lines are skipped, never crash
  S8  scale               — a ~100K-line snapshot file reduces+joins in bounded
                            time

ALL NUMBERS PRINTED BY THIS SCRIPT ARE SYNTHETIC — they validate the pipeline
software, not the market edge. Deterministic under --seed.

Usage:
    python -m esports_v2.scripts.stress_sharp_pipeline            # full run
    python -m esports_v2.scripts.stress_sharp_pipeline --quick    # CI-sized
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from esports_v2.model.closing_line import iter_snapshots_jsonl, reduce_to_closing_lines
from esports_v2.model.results_join import join_closing_lines_to_results, load_bulk_results
from esports_v2.model.sharp_eval import (
    edge_backtest,
    edge_backtest_from_joined,
    evaluate_sharp_line,
)
from esports_v2.model.team_match import same_team as _stress_same_team
from esports_v2.data.pm_market_index import PMMarketRef, match_pm_ref

FEE = 0.02
MIN_EDGE = 0.05


# ── synthetic universe ───────────────────────────────────────────────────────


def _mk_names(i: int, rng: random.Random) -> Tuple[str, str]:
    """Odds-side and results-side spellings of the same team — exercises the
    matcher's decoration/diacritic paths with KNOWN-same-team pairs."""
    base = f"Org{i}"
    forms = [
        (base, base),                          # identical
        (f"{base} Esports", base),             # decoration on odds side
        (base, f"Team {base}"),                # decoration on results side
        (f"KRÜ {base}", f"KRU {base}"),        # diacritic fold
    ]
    return forms[rng.randrange(len(forms))]


def gen_universe(n_matches: int, seed: int, pm_frac: float, edge: float,
                 sibling_every: int = 10, garbage_every: int = 7):
    """Build snapshot lines, result lines, and the ground-truth table."""
    rng = random.Random(seed)
    snaps: List[str] = []
    results: List[str] = []
    truth: Dict[str, dict] = {}          # match_key -> ground truth
    orient_truth: Dict[str, bool] = {}   # condition_id -> yes_is_home

    for i in range(n_matches):
        day = 1 + (i % 25)
        starts = f"2026-08-{day:02d}T12:00:00Z"
        date = starts[:10]
        home_o, home_r = _mk_names(2 * i, rng)
        away_o, away_r = _mk_names(2 * i + 1, rng)

        true_p = rng.uniform(0.15, 0.90)          # P(home wins)
        vig = rng.uniform(1.03, 1.07)             # proportional overround
        odds_a = 1.0 / (true_p * vig)
        odds_b = 1.0 / ((1.0 - true_p) * vig)
        home_won = rng.random() < true_p
        winner_r = home_r if home_won else away_r

        # PM attachment on a fraction: YES token randomly on either side, price
        # offset from truth by the planted edge (under- OR over-priced).
        cid = tok = yes_out = None
        mp = None
        if rng.random() < pm_frac:
            cid, tok = f"0xsynth{i}", f"tk{i}"
            yes_is_home = rng.random() < 0.5
            orient_truth[cid] = yes_is_home
            yes_out = home_o if yes_is_home else away_o
            p_yes = true_p if yes_is_home else (1.0 - true_p)
            sign = 1 if rng.random() < 0.5 else -1   # under/over priced
            mp = min(0.97, max(0.03, p_yes - sign * edge + rng.gauss(0, 0.005)))

        mk = f"m{i}"
        # snapshot trail: opens off-true, converges to the closing value; plus a
        # post-start LOOK-AHEAD tick with poisoned odds that must be ignored.
        for h, (oa, ob) in [(30, (odds_a * 1.06, odds_b * 0.95)),
                            (10, (odds_a * 1.02, odds_b * 0.99)),
                            (1, (odds_a, odds_b))]:
            snaps.append(json.dumps({
                "captured_at": f"2026-08-{day:02d}T{12 - h if h < 12 else 0:02d}:"
                               f"{30 if h >= 12 else 0:02d}:00Z",
                "match_key": mk, "home": home_o, "away": away_o,
                "starts": starts, "league_name": "SynthLeague",
                "odds_a": round(oa, 4), "odds_b": round(ob, 4),
                "event_type": "prematch", "condition_id": cid,
                "yes_token_id": tok, "yes_outcome": yes_out,
                "market_price": mp,
            }))
        snaps.append(json.dumps({          # look-ahead poison (post-start)
            "captured_at": f"2026-08-{day:02d}T13:00:00Z", "match_key": mk,
            "home": home_o, "away": away_o, "starts": starts,
            "league_name": "SynthLeague", "odds_a": 99.0, "odds_b": 1.01,
            "event_type": "live", "condition_id": cid, "yes_token_id": tok,
            "yes_outcome": yes_out, "market_price": mp,
        }))

        results.append(json.dumps({
            "match_id": f"r{i}", "game": "synth", "team_a": home_r,
            "team_b": away_r, "winner": winner_r, "match_date": date,
            "source": "synth",
        }))

        truth[mk] = {"home_won": home_won, "true_p": true_p,
                     "closing": (round(odds_a, 4), round(odds_b, 4)),
                     "cid": cid}

        # sibling-roster trap: the SAME org's academy fixture, same day, with
        # the OPPOSITE outcome. If the veto ever regresses, S1/S6 catch the
        # cross-join because the academy winner contradicts the main truth.
        if i % sibling_every == 0:
            results.append(json.dumps({
                "match_id": f"racad{i}", "game": "synth",
                "team_a": f"{home_r} Academy", "team_b": f"{away_r} Academy",
                "winner": (away_r if home_won else home_r) + " Academy",
                "match_date": date, "source": "synth",
            }))

        if i % garbage_every == 0:      # garbage the reducer must survive
            snaps.append("{broken json")
            snaps.append(json.dumps({"match_key": mk, "odds_a": None,
                                     "odds_b": "x", "starts": starts}))
            snaps.append("")

    return snaps, results, truth, orient_truth


# ── scenario runner ──────────────────────────────────────────────────────────


def run(n_matches: int, seed: int, scale_lines: int) -> int:
    edge = 0.10
    pm_frac = 0.6
    fails: List[str] = []

    def check(name: str, ok: bool, detail: str):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            fails.append(name)

    snaps, results, truth, orient_truth = gen_universe(
        n_matches, seed, pm_frac, edge)

    with tempfile.TemporaryDirectory() as td:
        sp, rp = Path(td) / "snaps.jsonl", Path(td) / "results.jsonl"
        sp.write_text("\n".join(snaps) + "\n", encoding="utf-8")
        rp.write_text("\n".join(results) + "\n", encoding="utf-8")

        # S7 first: the real prod loaders must survive the garbage in-file.
        try:
            closing = reduce_to_closing_lines(iter_snapshots_jsonl(sp))
            res = load_bulk_results(rp)
            joined, stats = join_closing_lines_to_results(closing, res)
            check("S7 garbage-resilience", True,
                  f"loaders survived {len(snaps)} lines incl. malformed")
        except Exception as e:  # noqa: BLE001 — a crash IS the finding here
            check("S7 garbage-resilience", False, f"chain crashed: {e!r}")
            print("\nSTRESS RESULT: FAIL (chain crashed)")
            return 1

        # S2: closing line == planted last pre-start odds, look-ahead ignored.
        bad_close = [k for k, cl in closing.items()
                     if (round(cl.odds_a, 4), round(cl.odds_b, 4))
                     != truth[k]["closing"]]
        check("S2 no-look-ahead", not bad_close,
              f"{len(closing)} closing lines, {len(bad_close)} wrong "
              f"(post-start poison ignored)")

        # S1: zero mislabels among joined records.
        mislabeled = [r for r in joined
                      if truth[r.match_key]["home_won"] != r.home_won]
        check("S1 label-integrity",
              not mislabeled and stats.joined > 0.9 * n_matches,
              f"joined {stats.joined}/{n_matches}, mislabels {len(mislabeled)}, "
              f"ambiguous-dropped {stats.ambiguous_multi_winner}")

        # S6: no odds row consumed a sibling (academy) result or PM market.
        cross = [r for r in joined if "Academy" in (r.winner or "")]
        pm_refs = [PMMarketRef(condition_id="0xacad", yes_token_id="t",
                               yes_outcome="Org0 Academy", market_price=0.5,
                               question="Org0 Academy vs Org1 Academy",
                               game_start="2026-08-01 12:00:00+00",
                               team_a="Org0 Academy", team_b="Org1 Academy",
                               day="2026-08-01")]
        attach = match_pm_ref("Org0", "Org1", "2026-08-01T12:00:00Z", pm_refs)
        check("S6 sibling-veto", not cross and attach is None,
              f"academy-result cross-joins {len(cross)}; "
              f"main->academy PM attach {'NO' if attach is None else 'YES'}")

        # S3: calibration recovered on the synthetic truth.
        rep = evaluate_sharp_line(joined)
        exp_brier = (sum(t["true_p"] * (1 - t["true_p"])
                         for t in truth.values()) / len(truth))
        hit_gap = abs((rep.favorite_hit_rate or 0) - (rep.mean_favorite_prob or 0))
        brier_gap = abs((rep.brier_closing or 0) - exp_brier)
        check("S3 calibration-recovery", hit_gap < 0.03 and brier_gap < 0.02,
              f"fav hit {rep.favorite_hit_rate:.3f} vs book {rep.mean_favorite_prob:.3f} "
              f"(gap {hit_gap:.3f}); brier {rep.brier_closing:.4f} vs "
              f"theoretical {exp_brier:.4f} (gap {brier_gap:.4f})")

        # S4: planted-edge recovery through the REAL edge backtest, with the
        # orientation resolver answering from ground truth (stands in for CLOB).
        def resolver_truth(cid, tok, ta, tb):
            return orient_truth.get(cid)

        def roi_tol(n_bets: int) -> float:
            """4-sigma binomial tolerance on a flat-stakes ROI at this n."""
            return max(0.04, 4.0 * (0.25 / max(1, n_bets)) ** 0.5)

        eb = edge_backtest_from_joined(joined, resolve_orientation=resolver_truth,
                                       fee=FEE, min_edge=MIN_EDGE)
        # planted: |edge|=0.10 both directions; EV per flat bet = 0.10
        roi_ok = eb.roi is not None and abs(eb.roi - edge) < roi_tol(eb.n_bets)
        fired_ok = eb.n_bets > 0.25 * stats.joined  # ~half of PM'd records under-priced side clears
        check("S4 planted-edge-recovery", roi_ok and fired_ok,
              f"bets {eb.n_bets}, hit {eb.hit_rate:.3f}, "
              f"ROI {eb.roi:+.3f} vs planted +{edge:.2f} "
              f"(tol {roi_tol(eb.n_bets):.3f}), "
              f"buckets {[(b['lo'], b['n']) for b in eb.edge_buckets]}")

        # Orientation is now PRIMARY from the FROZEN yes_outcome (2026-07-14 fix);
        # the injected resolver is a VETO-ONLY cross-check. So the S5 scenarios
        # corrupt orientation at the FROZEN source (yes_outcome), not the resolver.
        _fmt = lambda x: "n/a" if x is None else f"{x:+.3f}"

        def _unrelated_yes(r):
            """Frozen orientation unresolvable: yes_outcome matches neither team."""
            return replace(r, yes_outcome="Nonexistent Nonteam XYZ")

        def _flip_yes(r):
            """Flip orientation at the frozen source: yes_outcome -> the WRONG team."""
            to_home = _stress_same_team(r.yes_outcome, r.home, None)
            return replace(r, yes_outcome=(r.away if to_home else r.home))

        # S5a: correct-or-absent DROP now triggers on an UNRESOLVABLE FROZEN name
        # (the authority post-fix). Half the records get an unrelated yes_outcome
        # -> dropped; ROI on the surviving half stays ~planted (drops never corrupt).
        joined_halfbad = [
            _unrelated_yes(r) if (r.condition_id
                                  and int(r.condition_id.replace("0xsynth", "")) % 2 == 0)
            else r
            for r in joined]
        eb_abs = edge_backtest_from_joined(
            joined_halfbad, resolve_orientation=resolver_truth,
            fee=FEE, min_edge=MIN_EDGE)
        s5a_ok = (eb_abs.n_bets < eb.n_bets and eb_abs.roi is not None
                  and abs(eb_abs.roi - edge) < roi_tol(eb_abs.n_bets))
        check("S5a absent-orientation-drops (unresolvable frozen)", s5a_ok,
              f"bets {eb.n_bets}->{eb_abs.n_bets}, ROI {_fmt(eb_abs.roi)} "
              f"(tol {roi_tol(eb_abs.n_bets):.3f}; drops never corrupt)")

        # S5b: BLIND SPOT (permanent demonstration). If the AUTHORITATIVE frozen
        # yes_outcome is itself flipped AND no live cross-check is available (the
        # market resolved/archived -> resolver None), the backtest settles with the
        # same wrong bool it decided with -> self-consistent phantom PROFIT. P&L
        # CANNOT detect it; only the live-CLOB cross-check (S5d) or an independent
        # label check (clob_labels) can.
        joined_flip = [_flip_yes(r) for r in joined]
        eb_flip = edge_backtest_from_joined(
            joined_flip, resolve_orientation=lambda *a: None,
            fee=FEE, min_edge=MIN_EDGE)
        s5b_ok = (eb_flip.roi is not None and eb_flip.roi > 0
                  and eb_flip.n_bets > 0)
        check("S5b flip-blind-spot (backtest P&L cannot detect a flip)", s5b_ok,
              f"flipped-frozen backtest 'shows' ROI {_fmt(eb_flip.roi)} on "
              f"{eb_flip.n_bets} bets — phantom profit, NOT a real edge")

        # S5d: the NEW protection (2026-07-14). The SAME frozen flip, but with the
        # live CLOB cross-check AVAILABLE and correct, is CAUGHT — frozen(flipped)
        # disagrees with the live truth -> every such record is dropped, never bet.
        # The flip cannot reach P&L when the market is still served.
        eb_caught = edge_backtest_from_joined(
            joined_flip, resolve_orientation=resolver_truth,
            fee=FEE, min_edge=MIN_EDGE)
        s5d_ok = eb_caught.n_bets == 0
        check("S5d flip-caught-by-live-crosscheck", s5d_ok,
              f"flipped frozen + correct live check -> {eb_caught.n_bets} bets "
              f"(0 = every flip vetoed before it reaches P&L)")

        # S5c: the REAL damage — same flipped decisions, settled against TRUTH
        # (edge_backtest honors an explicit yes_won over the orientation bool).
        recs_flip, odds_lu = [], {}
        for r in joined:
            if r.market_price is None or r.condition_id not in orient_truth:
                continue
            yes_is_home = orient_truth[r.condition_id]
            recs_flip.append({
                "match_key": r.match_key, "market_price": r.market_price,
                "yes_is_team_a": not yes_is_home,               # corrupted input
                "yes_won": bool(r.home_won) == yes_is_home,     # reality settles
            })
            odds_lu[r.match_key] = (r.odds_a, r.odds_b)
        eb_real = edge_backtest(recs_flip, odds_lu, fee=FEE, min_edge=MIN_EDGE)
        s5c_ok = eb_real.roi is not None and eb_real.roi < eb.roi - 0.05
        check("S5c flip-real-damage (truth-settled)", s5c_ok,
              f"clean ROI {eb.roi:+.3f} -> flipped-decisions-vs-reality "
              f"{eb_real.roi:+.3f} — the edge is destroyed; correct-or-absent "
              f"is what prevents this")

        # S8: scale — pad the snapshot file to ~scale_lines and re-reduce.
        if scale_lines > 0:
            reps = max(1, scale_lines // max(1, len(snaps)))
            big = Path(td) / "big.jsonl"
            with open(big, "w", encoding="utf-8") as f:
                for _ in range(reps):
                    f.write("\n".join(snaps) + "\n")
            n_lines = reps * len(snaps)
            t0 = time.monotonic()
            big_closing = reduce_to_closing_lines(iter_snapshots_jsonl(big))
            join_closing_lines_to_results(big_closing, res)
            dt = time.monotonic() - t0
            check("S8 scale", dt < 60.0 and len(big_closing) == len(closing),
                  f"{n_lines} lines reduced+joined in {dt:.1f}s "
                  f"({len(big_closing)} closing lines)")

    print(f"\nSTRESS RESULT: {'PASS' if not fails else 'FAIL ' + str(fails)}"
          f"  (synthetic data — validates software, not market edge)")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Sharp-line pipeline stress test")
    ap.add_argument("--matches", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20260710)
    ap.add_argument("--scale-lines", type=int, default=100_000)
    ap.add_argument("--quick", action="store_true",
                    help="CI-sized: 400 matches, no scale stage")
    args = ap.parse_args()
    if args.quick:
        return run(400, args.seed, 0)
    return run(args.matches, args.seed, args.scale_lines)


if __name__ == "__main__":
    sys.exit(main())
