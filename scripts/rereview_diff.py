#!/usr/bin/env python3
"""Strict before/after verdict diff for the ADMIT re-review.

The re-review re-grades every ADMIT trader against COMPLETE resolution labels.
Its answer is only meaningful if EVERY trader on the roster was compared — and
the failure mode this lane keeps hitting is a comparison over zero (or a
partial) input set printing "FLIPPED: 0", which is indistinguishable from "all
verdicts survived" (empty-set false pass, tripped twice on 2026-07-22).

`admit_rereview3.sh` already aborts on a zero-JSON re-dive and on
`compared == 0`. This runner closes the two remaining holes:
  * a trader whose ORIGINAL json is missing/unparsable is SKIPPED there
    (`except Exception: continue`), so `compared` can silently undercount;
  * `compared` is never checked against the ROSTER SIZE, so a 3-of-20 compare
    prints a clean-looking "FLIPPED: 0".
Here, anything less than roster-complete is a FAILURE with the missing
addresses named, never a verdict.

    python scripts/rereview_diff.py --roster /tmp/admit_rereview_roster.txt \
        --before <deep_dive dir> --after <deep_dive_rereview dir>
    ... --self-test    # offline
Exit: 0 roster-complete compare (flips, if any, listed) | 4 incomplete | 5 empty
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys


def _load(path: str):
    try:
        with open(path) as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError) as e:
        return None, f"unreadable ({type(e).__name__})"


def family(verdict: str) -> str:
    """ADMIT / REJECT / INSUFFICIENT — the part a roster decision turns on."""
    return str(verdict or "?").split("-")[0].split()[0].upper()


def compare(roster: list[str], before: dict, after: dict) -> dict:
    """roster -> per-address {before, after, flipped} plus the gap lists.
    `before`/`after` map address -> verdict-bearing dict (or None if absent)."""
    rows, flips, missing_b, missing_a = [], [], [], []
    for addr in roster:
        b, a = before.get(addr), after.get(addr)
        if b is None:
            missing_b.append(addr)
        if a is None:
            missing_a.append(addr)
        if b is None or a is None:
            continue
        fb, fa = family(b.get("verdict")), family(a.get("verdict"))
        rows.append({"address": addr, "before": fb, "after": fa,
                     "flipped": fb != fa})
        if fb != fa:
            flips.append((addr, fb, fa))
    return {"rows": rows, "flips": flips, "missing_before": missing_b,
            "missing_after": missing_a, "compared": len(rows),
            "expected": len(roster)}


def _dir_index(d: str) -> dict:
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        b = os.path.basename(f)
        if b.startswith("_summary"):
            continue
        blob, err = _load(f)
        if blob is None:
            continue
        addr = str(blob.get("address") or b[:-5]).lower()
        out[addr] = blob
    return out


def main(args) -> int:
    roster = [l.strip().lower() for l in open(args.roster) if l.strip()]
    if not roster:
        print("FATAL: roster is EMPTY — nothing to compare. A 0-row diff is "
              "never evidence that verdicts survived.", file=sys.stderr)
        return 5
    before, after = _dir_index(args.before), _dir_index(args.after)
    r = compare(roster, before, after)
    print("=== ADMIT re-review: before (partial labels) -> after (complete) ===")
    for row in r["rows"]:
        b_s = (before[row["address"]].get("tier3_skill") or {})
        a_s = (after[row["address"]].get("tier3_skill") or {})
        print("%-14s %-12s -> %-12s  edge %+.4f -> %+.4f%s"
              % (row["address"][:12], row["before"], row["after"],
                 b_s.get("edge") or 0.0, a_s.get("edge") or 0.0,
                 "   <== FLIPPED" if row["flipped"] else ""))
    print(f"\ncompared={r['compared']} / roster={r['expected']}")
    if r["missing_before"]:
        print("MISSING original:", ", ".join(a[:12] for a in r["missing_before"]))
    if r["missing_after"]:
        print("MISSING re-review:", ", ".join(a[:12] for a in r["missing_after"]))
    if r["compared"] != r["expected"]:
        print(f"\nFATAL: {r['expected'] - r['compared']} of {r['expected']} "
              f"traders were NOT compared — this run proves NOTHING about the "
              f"uncompared ones. Do not read it as 'verdicts survived'.",
              file=sys.stderr)
        return 4
    if r["flips"]:
        print(f"\nFLIPPED: {len(r['flips'])} — "
              + "; ".join(f"{a[:12]} {b}->{c}" for a, b, c in r["flips"]))
    else:
        print("\nFLIPPED: 0 (roster-complete compare — this one means it)")
    print("Verdict changes are PROPOSALS. No roster change without operator go.")
    return 0


def _self_test() -> int:
    print("SELF-TEST — rereview_diff (offline)\n")
    ok = True
    mk = lambda v: {"verdict": v, "tier3_skill": {"edge": 0.01}}
    rost = ["0xa", "0xb", "0xc"]
    full = compare(rost, {a: mk("ADMIT — reasons") for a in rost},
                   {"0xa": mk("ADMIT — x"), "0xb": mk("REJECT — y"),
                    "0xc": mk("INSUFFICIENT — z")})
    ok1 = (full["compared"] == 3 and len(full["flips"]) == 2
           and full["flips"][0] == ("0xb", "ADMIT", "REJECT"))
    print(f"  [diff] flips detected on verdict FAMILY : {ok1}"); ok &= ok1
    # the whole point: a partial compare must NOT look like a clean pass
    part = compare(rost, {"0xa": mk("ADMIT")}, {"0xa": mk("ADMIT")})
    ok2 = (part["compared"] == 1 and part["expected"] == 3
           and part["flips"] == [] and part["missing_after"] == ["0xb", "0xc"])
    print(f"  [guard] partial compare surfaces the gap, flips==0 : {ok2}")
    ok &= ok2
    empty = compare(rost, {}, {})
    ok3 = (empty["compared"] == 0 and len(empty["missing_before"]) == 3)
    print(f"  [guard] empty dirs -> compared 0, all named missing : {ok3}")
    ok &= ok3
    ok4 = (family("ADMIT — chain skill clears") == "ADMIT"
           and family("INSUFFICIENT-EVIDENCE") == "INSUFFICIENT"
           and family(None) == "?")
    print(f"  [family] verdict family parsed from either form : {ok4}"); ok &= ok4
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="strict ADMIT re-review diff")
    ap.add_argument("--roster", default="/tmp/admit_rereview_roster.txt")
    ap.add_argument("--before",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive")
    ap.add_argument("--after",
                    default="/opt/pa2-shared/mb_copyable_data/deep_dive_rereview")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    raise SystemExit(_self_test() if a.self_test else main(a))
