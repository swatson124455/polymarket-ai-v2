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
2. [code, post-#1] Implement `resolve_yes_is_team_a` + unit tests against the
   confirmed shapes (offline).
3. [code, post-#2] Plumb `yes_is_team_a` onto `market_dict`; wire the offline
   backfill enrichment.
4. [blocked on odds] Backtest the full sharp-line signal once odds exist.
