# EB Sharp-Line — Orientation Plumbing Spec (`yes_is_team_a`)

**Purpose:** the offline sharp-line core (`esports_v2/model/sharp_reference.py`,
commit `f64daa7`) is built and tested. Its one hard input from the matcher is
`yes_is_team_a` — whether the Polymarket YES token resolves on the sharp book's
team_a. This doc specs where that value comes from and why it must be extracted
from the (dead) model path. Written by the EB startup session, 2026-07-08.

**Status of each claim:** ✅ = verified by reading the code this session; ⚠️ =
inferred / needs a live check before implementing.

---

## The gap (verified)

The sharp-line edge inverts if `yes_is_team_a` is wrong (see the
`test_enrich_orientation_inversion_flips_side` test). So this value must be
correct-or-absent, never guessed. Today it is neither produced nor plumbed:

- ✅ **The matcher doesn't record team↔outcome.** `find_markets_for_match`
  (`esports/markets/esports_market_scanner.py:231`) confirms *both* teams are in
  the question (`_both_teams_present`, the S195 two-team gate) but the returned
  `market_dict` (:352-369) carries only `token_id` / `yes_token_id` /
  `yes_price` / `condition_id` / `question` — **no field saying which team the
  YES outcome resolves on.**
- ✅ **The analyze path resolves the YES/NO *token*, not the *team*.** S152
  (`bots/esports_bot.py:2577-2591`) picks the YES token by its `outcome` label
  (`"YES"/"1"/"TRUE"`, else positional). That fixes token ordering, not team
  orientation.
- ✅/⚠️ **Team→YES alignment is entangled in the dead model.** `_get_model_prediction`
  (:3249) returns "the model's estimated probability for **YES** outcome"
  (:3261) — so the team_a→YES flip happens *inside* the Glicko/`_match_team_name`
  path (`_build_glicko2_game_state`, `_get_glicko2_prediction`). ⚠️ I did not
  fully trace the exact flip site inside `_build_glicko2_game_state`. When the
  dead ratings model (B12) is removed, this orientation logic is removed with
  it — the sharp path cannot depend on it.

**Conclusion:** orientation must be lifted OUT of the model path into a
standalone, model-independent resolver, then plumbed onto the matcher's
`market_dict` as `yes_is_team_a`.

---

## Spec: a standalone orientation resolver

New pure-ish function (proposed home: `esports/markets/` next to the matcher, or
a small `esports_v2/model/orientation.py`):

```
resolve_yes_is_team_a(market: dict, team_a: str, team_b: str, alias_map) -> Optional[bool]
```

Returns:
- `True`  — the YES token resolves on **team_a** (the sharp odds 'A' side)
- `False` — the YES token resolves on **team_b**
- `None`  — could not resolve with confidence → record stays UNCOVERED (the
  `enrich_with_sharp_prob` contract already drops `None`/non-bool safely)

Two market shapes it must handle (⚠️ confirm the live mix before coding):
1. **"Will \<team\> win?" Yes/No markets** (what S152 implies): the YES outcome
   is the question's *subject* team. Resolve = fuzzy-match the subject team
   against `{team_a, team_b}` via the existing alias resolver
   (`_match_team_name` + `esports_team_aliases`, 1,777 rows). `yes_is_team_a` =
   (subject matched team_a).
2. **Team-name-outcome markets** ("Team A vs Team B" with the two team names as
   outcomes): map the YES token's `outcome` string to `{team_a, team_b}` via the
   same resolver. `yes_is_team_a` = (YES outcome name matched team_a).

Reuse, do NOT duplicate: the alias resolver (`_match_team_name`, :6802) and
`esports_team_aliases` are the same machinery the matcher already trusts.

---

## Plumbing (offline path, buildable now once shape is confirmed)

1. `resolve_yes_is_team_a(...)` → set `market_dict["yes_is_team_a"]` inside (or
   just after) `find_markets_for_match`, alongside the team names it already has.
2. Backfill path for the historical backtest: same resolver over stored
   predictions joined to their matched market — attach `yes_is_team_a` +
   `match_key` (from `odds_loader.make_match_key(team_a, team_b, date)`), then
   `enrich_with_sharp_prob` fills `sharp_prob`/`sharp_edge`. All offline once an
   odds lookup exists.
3. Live path: the scan sets `yes_is_team_a`, looks up the live sharp line
   (populated dict), and `evaluate_edge` gates the trade — reusing
   `pipeline.compute_sizing` with `sharp_prob` in the `p_model` slot.

---

## Why this is NOT built yet (the honest blockers)

- ⚠️ **Market-shape mix is unverified.** Whether live esports markets are
  shape (1), (2), or both changes the resolver. Needs a live read of the
  Polymarket esports market outcomes — a paste-relay or live-CLOB session, not
  a blind offline edit.
- ⚠️ **Editing the matcher is a working-code change** (Rule 3/Rule 7): it must
  be verified against live market structure, not guessed.
- **The backtest that would validate orientation end-to-end needs sharp odds,
  and `pinnacle_odds` is empty (B13)** — so orientation correctness can't be
  proven on owned data until odds are forward-collected.

## Next actions (ordered)

1. [live] Read 5–10 live esports market objects → confirm outcome shape(s).
   Probe: `scripts/esports_market_shape_probe.py` (read-only; paste output back).
2. ✅ [done, offline] `resolve_yes_is_team_a` implemented + 27 tests
   (`esports_v2/model/orientation.py`). Handles BOTH shapes on a correct-or-
   absent contract (bool only when unambiguous, else None → uncovered). Shape-2
   (team-name outcomes) is exact; the shape-1 subject parse is conservative and
   still needs coverage-HARDENING against the real phrasings action #1 returns.

   **Self-review (2026-07-08, adversarial):** found + fixed a CONFIRMED
   sign-flip in the first version — "Will X be defeated?" oriented YES to X
   (blacklist keyed "defeated by", missed passive "be defeated"). Root cause:
   the parser validated the TEAM but never the PREDICATE. Root fix: an
   affirmative win-verb (win/beat/defeat) is now REQUIRED, inversion verbs
   broadened, and map/round/prop qualifiers bail (match-winner odds only).
   Unknown phrasings now fail to None instead of failing wrong.
   ⚠️ Residual for the plumbing layer (action #3): the resolver bails on
   sub-match qualifiers as defense-in-depth, but market_type gating (only pair
   MATCH-winner sharp odds with match-winner markets) is the plumbing layer's
   responsibility — do not rely on the resolver's regex for it.
3. [code, needs live verify] Plumb `yes_is_team_a` onto the matcher's
   `market_dict` (working-code edit) + wire the offline backfill enrichment.
4. [blocked on odds] Backtest the full sharp-line signal once odds exist (B13).

---

## Step-3 PREFLIGHT (mandatory before editing the matcher) — added 2026-07-08

The market-shape probe (action #1) ran; `EB_MARKET_SHAPE_RESULTS.md` has the data.
It confirms the **match-winner path is shape-2** (team-name outcomes; ~191 live
markets) — resolved authoritatively from the YES-token outcome LABEL, not text. So
the matcher edit is mostly "carry the outcome label + team order onto `market_dict`."
The edit site is `find_markets_for_match` → the `market_dict` literal at
`esports/markets/esports_market_scanner.py:352-369` (verified this session; it carries
`token_id`/`yes_token_id`/`no_token_id`/`condition_id`/`question` but **no team↔outcome
field**). Before writing the edit, a VPS/live session MUST verify TWO things — each is
a latent sign-flip (the S152/B2 loss class):

- **[CHECK 1 — ANSWERED from code 2026-07-08: the label is ABSENT, this is the real
  root of step 3.]** Shape-2 authoritative resolution needs the YES token's outcome
  STRING (the team name). Verified it is **nowhere in the DB path**:
  `esports_market_service.py:239-249` builds tokens with only `tokenId` +
  `outcomePrice` — **no `outcome` field** — and the DB `markets` SELECT (:176-179) has
  no outcome-label column at all. Consequence: the bot's own S152 YES-detector
  (`esports_bot.py:2580-2591`, which reads `_t.get("outcome")`) **silently no-ops to
  positional fallback** (`tokens[0]`) on the live path, because the label it looks for
  is never populated. So step 3 is NOT a `market_dict` passthrough — the label must be
  brought INTO the pipeline. The authoritative source is the **CLOB**
  (`/markets/{condition_id}` → `tokens[].outcome` == team name; proven by the probe).
  Options, in preference order: (a) an OFFLINE backfill that joins `condition_id` →
  CLOB outcomes (safe, no live-path change — do this first for the backtest); (b) add
  a CLOB label lookup in the live scan (working-code change: latency + rate-limit on
  the live bot — verify live before/after). Do NOT fabricate the label from question
  text.
- **[LIVE CHECK 2] Does the matcher's `team_names[0]` == the sharp odds' `team_a`?**
  `resolve_yes_is_team_a` returns a bool *relative to the team_a you pass it*. The
  matcher has `team_names` (PandaScore order); the sharp line has `team_a/team_b`
  (`odds_loader.make_match_key` order). If those orders can differ, computing
  `yes_is_team_a` against `team_names[0]` and consuming it against odds `team_a`
  **inverts the edge**. Either (a) compute + store orientation against the SAME order
  the odds use, or (b) store the YES team NAME (not a bool) and let `enrich_with_sharp_
  prob` derive the bool against its own team_a. Option (b) is flip-proof — prefer it.

Verify live before/after per CLAUDE.md ("Can't Fully Verify" rule): scan output before
the edit, the same after, plus a spot-check that a known shape-2 market gets the correct
`yes_is_team_a`. EB is halted — no deploy.

### LIVE MEASUREMENT (2026-07-09, prod DB + live CLOB) — de-risks step 3

Ran `scripts/esports_orientation_live_check.py` on the VPS against 36 live shape-2
team-vs-team markets. Result:

```
shape-2 markets checked:              36
agree (positional fallback == authoritative CLOB team): 36
FLIP  (positional fallback wrong team):                  0   <- zero live sign-flip
stored yes_token_id NOT among CLOB tokens:               0   <- yes_token_id reliable
clob fetch errors:                                       0
```

**Findings that change the step-3 plan:**
- **No active sign-flip.** The bot's current positional `tokens[0]` fallback matches
  the authoritative CLOB team on 36/36. So step 3 is a ROBUSTNESS upgrade (stop
  depending on token *position*), NOT an active-bug fix. Not urgent.
- **`yes_token_id` is a reliable authoritative key** (0/36 missing from the live CLOB
  market). So [CHECK 1]'s answer for the FIX is: don't add DB columns — at
  orientation time, map the stored `yes_token_id` → its CLOB `outcome` string = the
  authoritative YES team NAME. Store that name on `market_dict`; `enrich_with_sharp_
  prob` aligns it against the odds' team_a itself (flip-proof, sidesteps [CHECK 2]).
- **Recommendation:** since there is no active flip AND the signal can't be validated
  end-to-end until odds exist (B13), bundle the actual live-path wiring with the odds
  session rather than making a standalone live-scan change now. The offline backfill
  (option a) that the backtest needs will do the same `yes_token_id → CLOB label`
  lookup — build it there, once there is odds data to test the whole chain against.
