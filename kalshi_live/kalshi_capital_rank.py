#!/usr/bin/env python3
"""CAPITAL-AWARE RANKING (operator-ordered 2026-07-29, telemetry-first) — rank on $reward/day
per $COMMITTED net of measured fill cost, not on reward alone.

WHAT THE CURRENT RANK IS BLIND TO (handoff 2026-07-29 "TASK #1"):
1. CAPITAL EFFICIENCY. Selection ranks by capture (share x pool, kalshi_market_scores) or the
   pool prior — pure $/day. But two markets with identical capture can consume very different
   capital: at JOIN_SIZE=0 the per-side count is min(INV_HARD_CT, (MAX_MARKET_CAPITAL/2)/price),
   so a mid-priced book (~0.50/0.50) commits up to the full per-market cap while a skewed book
   (0.10/0.90) is contract-capped on the cheap side and commits far less. NOTE the truth is
   symmetric in ref <-> 1-ref because we quote BOTH sides: "a 0.90 book costs ~9x a 0.10 book"
   holds per contract on one side, but the paired commitment is what the account actually funds
   — `est_commit_usd` models THAT, byte-aligned with the quoter's `_capped_join` sizing.
2. FILL COST. Adverse-selection losses differ wildly by book (the excluded index family bled
   ~2c/ct; quiet political books cost ~0). The venue's own per-market attribution
   (`realized_pnl_dollars` on /portfolio/positions) is a receipt, not a model — it enters as a
   $/day penalty via a feed file (kalshi_fill_costs.py writes it, this module only reads it).
3. Correlation/family concentration: OUT OF SCOPE here (D3, tabled by operator).

SCORE = (capture_usd_day x calib − fill_cost_usd_day) / max(commit_usd, floor)
i.e. net $reward per $ committed per day. `calib` starts at 1.0 and gets set from the first
real receipt (Thu 2026-07-31 ballot-market window close) — receipts > models.

TELEMETRY-FIRST: this module never touches selection. The quoter logs the would-be ranking
alongside the actual one (caprank-YYYYMMDD.jsonl) for operator review; the ranking itself flips
only on an explicit operator approval, as a separate, named change.

Pure functions, no I/O except the fail-open feed loader — same discipline as
kalshi_market_scores (a ranking fault must never stop a trading cycle).
"""
import json

SCHEMA = 1
COMMIT_FLOOR_USD = 1.0      # denominator floor: a ~$0 commitment must not make the score explode


def load_fill_costs(path):
    """Fail-OPEN to {} -> every market costed at $0/day -> the shadow rank degrades to
    capture-per-dollar with no penalty term. Same contract as kalshi_market_scores.load."""
    try:
        with open(path) as fh:
            d = json.load(fh)
        if d.get("schema") != SCHEMA:
            return {}
        m = d.get("markets")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def load_prospective(path):
    """Fail-OPEN to {} -> no offline sweep data; unmeasured markets fall back to the pool prior.
    File written by kalshi_capture_sweep.py; same schema discipline as the fill-cost feed."""
    try:
        with open(path) as fh:
            d = json.load(fh)
        if d.get("schema") != SCHEMA:
            return {}
        m = d.get("markets")
        return m if isinstance(m, dict) else {}
    except Exception:
        return {}


def est_commit_usd(ref_yes, market_cap_usd, inv_hard_ct, quantum=5):
    """Estimate the dollars BOTH resting sides commit for one market, from the cached reference
    price. Mirrors the quoter's `_capped_join` at JOIN_SIZE=0 exactly (dollar cap per side,
    INV_HARD_CT contract ceiling, round DOWN to a multiple of `quantum` at >=5, floor 1) so the
    estimate and the live sizing can never drift in shape — only in the ref snapshot's age.
    Unknown ref -> 0.50/0.50, the maximum-commitment case (conservative: an unknown market is
    charged the largest plausible denominator, never flattered)."""
    try:
        p_yes = float(ref_yes) if ref_yes is not None else 0.5
    except (TypeError, ValueError):
        p_yes = 0.5
    # clamp to the venue's price grid: outside (0,1) the book is degenerate, not cheap/expensive
    p_yes = min(0.99, max(0.01, p_yes))
    per_side = float(market_cap_usd) / 2.0
    total = 0.0
    for p in (p_yes, 1.0 - p_yes):
        n = int(per_side / p)
        if inv_hard_ct and inv_hard_ct > 0:
            n = min(int(inv_hard_ct), n)
        if n >= 5:
            n = (n // quantum) * quantum
        n = max(1, n)
        total += n * p
    return total


def shadow_rank(rows, scores, fill_costs, market_cap_usd, inv_hard_ct, now,
                calib=1.0, swing_penalty=1.0, unknown_bonus=1.0,
                prospective=None, risk_lambda=1.0, prospective_haircut=1.0,
                unknown_haircut=1.0):
    """Order `rows` best-first by capital-aware score, WITHOUT mutating them. Returns a NEW list
    of component dicts (ticker + every term) so the telemetry row is self-explanatory and the
    operator can audit each ranking decision from the log alone.

    BASE TERM by evidence quality, best available wins:
      "scored"      — live measured capture (kalshi_market_scores.score() verbatim: decay,
                      swing penalty, pool-prior blending). Full weight.
      "prospective" — offline sweep measurement (kalshi_capture_sweep.py): the book WAS read,
                      but our join is hypothetical. Used when the live score is absent/stale.
                      x prospective_haircut.
      "unknown"/"stale" with no sweep data — pool prior only. x unknown_haircut.

    RISK-AVERSION KNOBS (operator ask 2026-07-29; every default 1.0 = exactly the prior
    behavior, provable no-op):
      risk_lambda         >1 punishes measured fill-cost harder: cost enters as lambda x cost.
      prospective_haircut <1 discounts model-joined (not-yet-lived-in) measurements.
      unknown_haircut     <1 discounts pure pool guesses, so unmeasured markets cannot
                          outrank receipts — the cycle-1 failure shape."""
    import kalshi_market_scores as ks
    prospective = prospective or {}
    out = []
    for r in rows:
        t = r.get("ticker")
        base, kind = ks.score(scores, t, r.get("usd_day"), now, swing_penalty, unknown_bonus)
        ref = (scores.get(t) or {}).get("ref")
        if kind != "scored":
            p = prospective.get(t)
            if isinstance(p, dict) and p.get("capture") is not None:
                try:
                    base = float(p["capture"]) * prospective_haircut
                    kind = "prospective"
                    if ref is None:
                        ref = p.get("ref")
                except (TypeError, ValueError):
                    pass                        # malformed sweep row -> keep the pool prior
        if kind in ("unknown", "stale"):
            base *= unknown_haircut
        commit = est_commit_usd(ref, market_cap_usd, inv_hard_ct)
        fc = 0.0
        try:
            fc = float((fill_costs.get(t) or {}).get("cost_usd_day") or 0.0)
        except (TypeError, ValueError, AttributeError):
            pass    # a malformed feed row costs $0, it never blocks the rank
        cap_score = (base * calib - risk_lambda * fc) / max(commit, COMMIT_FLOOR_USD)
        out.append({"ticker": t, "kind": kind, "base_usd_day": round(base, 4),
                    "ref": ref, "commit_usd": round(commit, 2),
                    "cost_usd_day": round(fc, 4), "cap_score": round(cap_score, 6)})
    out.sort(key=lambda d: (-d["cap_score"], str(d["ticker"])))
    return out
