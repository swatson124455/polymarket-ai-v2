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

## 5. What's next (ordered) + status

1. **✅ [DONE 2026-07-08] Market-shape probe ran on live data.** From a cloud session,
   via the NEW DB-free variant `scripts/esports_market_shape_probe_public.py` (Gamma
   `tag_id=64` discovery + live CLOB peek; needs Polymarket egress, no VPS/DB). Results
   in `EB_MARKET_SHAPE_RESULTS.md`. **Key finding:** of 2100 live esports-tag markets —
   852 shape-1 `Yes/No` (mostly **season/tournament FUTURES**, neg-risk), ~191 shape-2
   **team-name-outcome head-to-head match/game winners** (the match-odds pairing
   target), and ~1057 prop markets (Odd/Even 908, Over/Under 149) + 440 polluted
   non-winner questions (Madden covers, haircuts, "Most Picked Hero", etc.).
2. **✅ [DONE 2026-07-08 — no behavior change needed] Shape-1 parser verified vs the
   real corpus.** Swept the resolver over the full live set: **315/315** "Will X win"
   correct with **ZERO sign-flips**, **438/438** pollution questions bailed to None,
   **68/68** shape-2 pairs authoritative. The correct-or-absent contract already holds
   on real phrasings — per "fix only what's broken," `orientation.py` was NOT edited.
   Locked in with real-corpus regression tests (`tests/unit/test_esports_orientation_
   real_corpus.py`, +4). Residual note: the resolver returns a (correct-orientation)
   True on conditional-winner phrasings like "win MSI without dropping a series" —
   that's a market-TYPE mismatch, gated by the plumbing layer, not an orientation bug.
3. **[code, needs live verify — STILL PENDING] Root-fix orientation at the matcher.**
   Persist the authoritative token→team mapping onto the matcher's `market_dict`
   (`find_markets_for_match`, `esports_market_scanner.py:352`), which today records no
   team↔outcome field. The probe now answers the open question: the **match-winner path
   is shape-2** (team-name outcomes), which the resolver maps AUTHORITATIVELY from the
   outcome label — so the matcher edit mainly needs to carry the YES-token outcome
   string + team names onto `market_dict`. Shape-1 Yes/No are mostly futures (a
   different odds type). This is a working-code edit to a LIVE matcher on a HALTED bot;
   the plumbing spec + CLAUDE.md require live before/after verification, which a cloud
   session cannot do — deferred to a VPS-capable session. NOT attempted blind.
4. **[ODDS SOURCE NOW LIVE — 2026-07-09] Provider switched OddsPapi → PinnOdds.**
   `esports_v2/data/pinnodds_loader.py` (`PinnOddsLoader`) fetches CURRENT Pinnacle
   esports match-winner odds and returns the same `match_key → (odds_a, odds_b)`
   contract. **Verified live on the VPS: 36 match-winner keys** across CS2 / Dota 2 /
   Valorant / Rainbow Six with clean decimal odds (e.g. `big||pvision||2026-07-10 →
   (2.3, 1.632)`). Key in `/opt/pa2-shared/.env` as `PINNACLE_ODDS_API_KEY`; endpoint
   `https://pinnodds.com/kit/v1/markets?sport_id=11&event_type=live|prematch`, header
   `x-portal-apikey`, browser UA required (WAF 403s python-requests — fixed). OddsPapi
   loader kept as a sibling. 7 unit tests from real captured shapes.
   **Forward-collection is LIVE (2026-07-09).** No cheap historical Pinnacle esports
   source exists (checked OddsPapi=no esports, Betting Is Cool=paid/unconfirmed depth,
   OddsPortal=ToS/scrape, The Odds API=$99+thin) — operator decision: collect forward.
   A cron on the VPS runs `collect_pinnodds_standalone.py` **every 15 min**, appending
   PinnOdds match-winner snapshots to `/home/ubuntu/eb-odds/pinnodds_snapshots.jsonl`
   (first run: 33 lines). Canonical code in repo: `esports_v2/scripts/collect_pinnodds.py`
   + `pinnodds_loader.fetch_rows()`. The standalone VPS copy is a bootstrap (non-git
   deploy dir) — reduce/replace when EB is properly deployed.
   **Still TODO to backtest (circle back once history has built up):** (a) reduce
   snapshots → CLOSING line per match (last snapshot with captured_at <= starts);
   (b) join to the free match RESULTS we already have (bulk jsonl / PandaScore) by
   match_key/alias; (c) finish Step 3 orientation plumbing; (d) run `enrich_with_sharp_
   prob → evaluate_edge`; (e) operator picks de-vig method (simple no-vig vs Shin).
   Parser coverage (#2) is DONE and no longer the constraint.
   **Revisit later:** a paid historical source (Betting Is Cool free trial) would give
   an instant backtest instead of waiting for forward data to accumulate.

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
