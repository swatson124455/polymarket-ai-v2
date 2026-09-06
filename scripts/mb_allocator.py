#!/usr/bin/env python3
"""MB CROSS-TRADER ALLOCATOR — build 2 of the 2026-09-06 mandate
(operator "build": envelope layer closing the VERIFIED GAP that
trader_funnel/display_stake hands the FULL bankroll to every trader
independently — no cross-trader split existed; safe today only because
all stakes are $0 with no executor).

THE LAYER (graduated trust, down-only at every level):

    bankroll
      -> per-TIER budgets       (bankroll x operator tier fraction)
      -> per-TRADER envelopes   (tier budget / traders in tier)
      -> per-WAGER stake        (mb_sizer inside the envelope — the
                                 existing rule, bankroll:=envelope)

Nothing here can raise a stake: envelopes never exceed the bankroll,
tier fractions must sum to <= 1, and mb_sizer's stake is monotone
non-decreasing in bankroll, so enveloped stake <= un-enveloped stake
always (property-tested). min_viable-to-zero still applies inside the
envelope — a shrunken stake below minimum goes to $0, never up.

TIERS ARE OPERATOR NUMBERS (no defaults in code — the mb_sizer idiom):
the caller supplies {tier_name: fraction}. Tier names are labels the
CALLER maps from the deployed machine's states (e.g. PASSED->"proven",
TRIAL->"confirming"); this module hardcodes no state names. A trader
whose tier is NOT in the fractions dict gets a $0 envelope and a
flagged_unknown_tier=True marker — unknown is the alarm
(feedback_class_not_instance), never a silent pass-through.

Env sourcing (for the funnel/executor wiring): MB_ALLOC_TIER_FRACS,
format "tier:frac,tier:frac" (e.g. "proven:0.6,confirming:0.1").
Absent -> allocator OFF, caller keeps legacy behavior and SAYS SO.

PURE MODULE - no I/O, no network, no state. Offline-testable.
"""
from __future__ import annotations


def parse_tier_fracs(spec: str) -> dict[str, float]:
    """Parse the operator's MB_ALLOC_TIER_FRACS env string. Strict: a
    malformed spec is a hard error, never a silently-empty allocator."""
    if not spec or not spec.strip():
        raise ValueError("empty tier-fracs spec (operator numbers required)")
    out: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"malformed tier spec segment: {part!r}")
        name, frac = part.split(":", 1)
        name = name.strip()
        if not name or name in out:
            raise ValueError(f"empty or duplicate tier name in {part!r}")
        out[name] = float(frac)
    if not out:
        raise ValueError("no tiers parsed (operator numbers required)")
    return out


def allocate_envelopes(bankroll: float, traders: list[dict],
                       tier_fracs: dict[str, float]) -> dict[str, dict]:
    """{trader_key: {envelope, tier, flagged_unknown_tier}}.

    traders: [{"key": str, "tier": str}]. tier_fracs: operator-supplied
    {tier: fraction of bankroll}; fractions must each be in [0, 1] and
    SUM to <= 1.0 (the whole point — envelopes can never over-commit
    the bankroll). Within a tier the budget splits EQUALLY across its
    traders (no performance weighting here: ranking-based weights would
    re-import the winner's-curse the LCB machinery exists to kill;
    differentiation happens across tiers, an operator decision)."""
    if bankroll <= 0.0:
        raise ValueError("bankroll must be > 0")
    if not tier_fracs:
        raise ValueError("tier_fracs required (operator numbers, no defaults)")
    for t, f in tier_fracs.items():
        if not (0.0 <= f <= 1.0):
            raise ValueError(f"tier {t!r} fraction {f} outside [0, 1]")
    total = sum(tier_fracs.values())
    if total > 1.0 + 1e-9:
        raise ValueError(f"tier fractions sum to {total:.4f} > 1.0 — "
                         f"envelopes would over-commit the bankroll")
    counts: dict[str, int] = {}
    for tr in traders:
        tier = str(tr.get("tier"))
        counts[tier] = counts.get(tier, 0) + 1
    out: dict[str, dict] = {}
    for tr in traders:
        key = str(tr.get("key"))
        tier = str(tr.get("tier"))
        if key in out:
            raise ValueError(f"duplicate trader key {key!r}")
        if tier in tier_fracs:
            env = bankroll * tier_fracs[tier] / counts[tier]
            out[key] = {"envelope": env, "tier": tier,
                        "flagged_unknown_tier": False}
        else:
            # unknown tier = the alarm state: $0, flagged, never silent
            out[key] = {"envelope": 0.0, "tier": tier,
                        "flagged_unknown_tier": True}
    return out


def stake_in_envelope(envelope: float, lcb, fill: float,
                      fee_per_share: float, *, kelly_mult: float,
                      concurrency: int, book_depth_usd: float,
                      min_viable: float, **kw) -> dict:
    """Per-wager sizing INSIDE a trader's envelope: the existing
    mb_sizer rule with bankroll := envelope. One implementation — this
    is a delegate, not a re-derivation. envelope == 0 (unproven /
    unknown tier / empty fraction) -> structural $0 without invoking
    the sizer (whose bankroll>0 guard would refuse)."""
    import mb_sizer as msz
    if envelope < 0.0:
        raise ValueError("negative envelope")
    if envelope == 0.0:
        return {"stake": 0.0, "lcb": lcb, "k_full": 0.0, "raw_stake": 0.0,
                "caps_applied": ["zero_envelope"],
                "reason": "envelope $0 (tier budget/proof) -> $0"}
    return msz.recommend_stake_from_lcb(
        lcb, fill, fee_per_share, bankroll=envelope, kelly_mult=kelly_mult,
        concurrency=concurrency, book_depth_usd=book_depth_usd,
        min_viable=min_viable, **kw)


def _self_test() -> int:
    print("SELF-TEST — mb_allocator (offline)\n")
    import sys
    sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
    import band_tracker as bt
    import mb_sizer as msz
    ok = True
    fr = parse_tier_fracs("proven:0.6, confirming:0.1")
    ok1 = fr == {"proven": 0.6, "confirming": 0.1}
    for bad in ("", "proven", "proven:0.6,proven:0.1", ":0.5"):
        try:
            parse_tier_fracs(bad)
            ok1 = False
        except ValueError:
            pass
    print(f"  [parse] operator spec strict; malformed raises : {ok1}")
    ok &= ok1
    traders = [{"key": "a", "tier": "proven"}, {"key": "b", "tier": "proven"},
               {"key": "c", "tier": "confirming"},
               {"key": "d", "tier": "weird"}]
    env = allocate_envelopes(500.0, traders, fr)
    ok2 = (abs(env["a"]["envelope"] - 150.0) < 1e-9
           and abs(env["b"]["envelope"] - 150.0) < 1e-9
           and abs(env["c"]["envelope"] - 50.0) < 1e-9
           and env["d"]["envelope"] == 0.0
           and env["d"]["flagged_unknown_tier"] is True)
    print(f"  [alloc] equal split per tier; unknown tier $0+flag : {ok2}")
    ok &= ok2
    ok3 = sum(e["envelope"] for e in env.values()) <= 500.0 + 1e-9
    try:
        allocate_envelopes(500.0, traders, {"a": 0.7, "b": 0.4})
        ok3 = False
    except ValueError:
        pass
    try:
        allocate_envelopes(500.0, traders, {"a": -0.1})
        ok3 = False
    except ValueError:
        pass
    print(f"  [alloc] never over-commits; frac>1-sum/negative raise : {ok3}")
    ok &= ok3
    common = dict(kelly_mult=0.25, concurrency=6, book_depth_usd=1e12,
                  min_viable=1.0, e_value_fn=None)
    common.pop("e_value_fn")
    full = msz.recommend_stake_from_lcb(0.10, 0.50, 0.01, bankroll=500.0,
                                        **common)
    part = stake_in_envelope(150.0, 0.10, 0.50, 0.01, **common)
    zero = stake_in_envelope(0.0, 0.10, 0.50, 0.01, **common)
    ok4 = (part["stake"] <= full["stake"] + 1e-12 and part["stake"] > 0.0
           and zero["stake"] == 0.0
           and "zero_envelope" in zero["caps_applied"])
    print(f"  [downonly] enveloped stake <= full-bankroll stake; "
          f"$0 envelope structural : {ok4}")
    ok &= ok4
    tiny = stake_in_envelope(3.0, 0.10, 0.50, 0.01, **common)
    ok5 = tiny["stake"] == 0.0   # railed below min_viable -> $0 never up
    print(f"  [downonly] shrunken below min_viable -> $0 never up : {ok5}")
    ok &= ok5
    lcb0 = stake_in_envelope(150.0, -0.05, 0.50, 0.01, **common)
    ok6 = lcb0["stake"] == 0.0   # unproven stays $0 inside any envelope
    print(f"  [sizer] unproven LCB<=0 -> $0 inside envelope : {ok6}")
    ok &= ok6
    _ = bt  # canon import present for parity with sibling modules
    print("\n  RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="cross-trader allocator (pure)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        raise SystemExit(_self_test())
    ap.print_help()
