# EsportsBot Sharp-Line — Session State / Handoff

**Branch:** `claude/esports-session-startup-k7bdep` (all work pushed to GitHub)
**Last session:** 2026-07-08 (cloud/sandboxed — no VPS/DB/Polymarket-API reach)
**Read first:** `CLAUDE.md` (binding directives), `EB_REBUILD_CARRYFORWARD.md` (postmortem + guardrails), `EB_SHARP_LINE_PLUMBING.md` (orientation spec), then this file.

---

## 1. One-paragraph state

The dead ratings model (B12: no measured edge) is being replaced by an **external
sharp-line signal**: strip the vig off a sharp book (Pinnacle via OddsPapi) to a
fair prob, align it to the Polymarket YES outcome, and bet the side the sharp line
says Polymarket underprices (`edge = sharp_prob − price − fee`). The full signal is
**built and unit-tested OFFLINE** — everything except the live odds fetch. EB stays
**HALTED / paper**. Nothing deployed. The remaining work is gated on two external
things (live market shapes, and odds data) — neither reachable from a cloud session.

## 2. What was built this session (6 commits, all pushed)

| Commit | What |
|---|---|
| `f64daa7` | `esports_v2/model/sharp_reference.py` — no-vig core, point-in-time line pick, two-sided edge rule, orientation guard, offline `enrich_with_sharp_prob`. +32 tests. |
| `a06c694` | `EB_SHARP_LINE_PLUMBING.md` — spec for where `yes_is_team_a` must come from. |
| `106d291` | `scripts/esports_market_shape_probe.py` — read-only probe: is a market shape-1 (Yes/No) or shape-2 (team-name outcomes)? |
| `be29d5a` | `esports_v2/model/orientation.py` — model-independent `resolve_yes_is_team_a`. +21 tests. |
| `275fed0` | **Bug fix (adversarial self-review):** resolver had a CONFIRMED sign-flip ("Will X be defeated?" → YES on X). Root fix: require an affirmative win-verb; unknown phrasings fail to None, never wrong. +6 regression tests. |
| `7cbc3bf` | probe made schema-adaptive (the `markets` table shape drifts across DBs). |

**Tests:** 59/59 green for `test_esports_sharp_reference.py` + `test_esports_orientation.py`.

## 3. The design principle (do not weaken)

**Correct-or-absent.** Every layer returns `None`/skip on ANY doubt rather than a
confident-but-wrong signal. Rationale: a flipped orientation doesn't miss a trade —
it *inverts* the edge, so you systematically bet the side the sharp book says is
overpriced (negative EV). That's the S152/B2 failure class. So: bad odds → None,
uncertain team↔YES → None, malformed price → None. `enrich_with_sharp_prob` requires
`yes_is_team_a` to be an actual `bool` (a truthy team-name string is rejected).

## 4. The seam (how the live odds wire in later)

`esports_v2/data/odds_loader.py` already fetches OddsPapi/Pinnacle into a
`match_key → (odds_a, odds_b)` lookup dict. Everything built this session consumes
exactly that shape. So the paid-tier wiring is: populate that dict → `enrich_with_
sharp_prob` does the rest. Nothing in the new modules changes.

## 5. What's next (ordered) + why each is blocked

1. **[live] Run the market-shape probe → confirm shape.** `scripts/esports_market_
   shape_probe.py` on a machine with the PROD DB + internet (the VPS — the local dev
   DB is stale: 0 esports rows). Commit its output (e.g. `EB_MARKET_SHAPE_RESULTS.md`)
   so any session can read it. *Blocked from cloud sessions (no VPS/DB/API).*
2. **[code, needs #1] Harden the shape-1 question parser** in `orientation.py`
   against the real phrasings the probe returns. Currently conservative (fails safe).
3. **[code, needs #1 + live verify] Root-fix orientation at the matcher.** The real
   fix is NOT the text parser — it's persisting the authoritative token→team mapping
   onto the matcher's `market_dict` at match time (`find_markets_for_match`,
   `esports_market_scanner.py:352`), which today records no team↔outcome field. See
   `EB_SHARP_LINE_PLUMBING.md`. Shape-2 (team-name outcome) is already authoritative;
   shape-1 needs the probe to say whether the subject team is a structured field or
   text-only. Working-code edit → verify live.
4. **[blocked on odds] Backtest the full signal end-to-end.** `pinnacle_odds` is
   EMPTY (B13) — zero sharp odds, live or historical. Until forward-collected (needs
   the OddsPapi paid tier), the signal cannot be validated or traded. **This is the
   real binding constraint** — parser coverage (#2) is moot until odds exist.

## 6. Landmines / gotchas

- **Odds data is the binding blocker,** not the parser. Don't over-invest in coverage
  before `pinnacle_odds` has data.
- **De-vig method is an OPEN operator decision.** Default is simple no-vig (matches
  MB's fleet `sharp_reference.py`); a Shin variant sits in `clv.py:odds_to_implied`.
  One call-site swap. Don't silently pick.
- **Market-type gating is the plumbing layer's job**, not the resolver's regex. The
  resolver bailing on "map/round" qualifiers is defense-in-depth only.
- **EB owns the OddsPapi/esports vendor integration** (per `docs/MB_STATE.md` §6); MB
  has priority on all shared resources. Don't touch shared modules / MB state.
- **Do NOT deploy** — the code isn't wired into the live bot and EB is halted.
- **`*_HANDOFF.md` is gitignored** — that's why this file is `_STATE.md`.

## 7. To continue from a local (VPS-capable) session

`git checkout claude/esports-session-startup-k7bdep`, then run action #1 (the probe)
and commit its output. That unblocks #2. #4 stays blocked until odds data exists.
