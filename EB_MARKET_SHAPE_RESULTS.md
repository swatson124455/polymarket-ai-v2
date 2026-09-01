# EB Market-Shape Probe — Results

## STATUS: ✅ RAN — real live data (2026-07-08)

**Source:** public Gamma API (`tag_id=64` "esports") + live CLOB ground-truth peek.
**How:** `python scripts/esports_market_shape_probe_public.py 6000 12` (DB-free variant;
egress to gamma-api + clob was enabled for this session). Read-only.
**Universe:** 2100 esports-tag markets, active & not closed.

---

## 1. Outcome-label distribution (all 2100, from Gamma `outcomes`)

| Outcome labels | Count | Meaning | Resolver action |
|---|---:|---|---|
| `['Odd','Even']` | 908 | total-kills parity **prop** | **ignore** (not a winner market) |
| `['Yes','No']` | 852 | shape-1 "Will \<team\> win …?" | parse SUBJECT team |
| `['Over','Under']` | 149 | totals **prop** | **ignore** |
| team-name pairs (e.g. `['G2 Esports','100 Thieves']`, `['DRX','DetonatioN FocusMe']`) | ~191 | **shape-2** head-to-head match/game winner | map YES-token outcome → team (authoritative) |

CLOB peek on the top-12 by volume confirmed the Gamma labels exactly (all shape-1
`Yes/No` season-winner markets, `neg_risk=True`). Gamma `outcomes` == CLOB ground truth
for the sample — Gamma is trustworthy for shape.

## 2. The two shapes, concretely

**Shape-1 (Yes/No), 852 markets — almost all FUTURES/OUTRIGHT, neg-risk:**
```
Will BRION win the LCK 2026 season playoffs?     outcomes ['Yes','No']  neg_risk=True
Will T1 win the LCK 2026 season playoffs?         outcomes ['Yes','No']  neg_risk=True
Will <team> win the EWC Dota 2 Tournament          (x23 teams)
Will <team> win the LPL 2026 season?               (x14 teams)
Will <team> win LCK CL 2026?  / MSI 2026?          (tournament outright)
```
These pair with **outright/futures** sharp odds, NOT match-winner odds.

**Shape-2 (team-name outcomes), ~191 markets — HEAD-TO-HEAD match/game winners:**
```
LoL: ZennIT vs The Bandits - Game 1 Winner   outcomes ['ZennIT','The Bandits']
G2 Esports vs 100 Thieves                      outcomes ['G2 Esports','100 Thieves']
DRX vs DetonatioN FocusMe                       outcomes ['DRX','DetonatioN FocusMe']
```
**These are what sharp MATCH-winner odds pair with.** The YES/token outcome string IS a
team name → `resolve_yes_is_team_a` maps it authoritatively (no fragile text parse).

## 3. Pollution in the esports tag (correct-or-absent MUST bail on these)

The tag is not clean — 440 questions matched none of the winner patterns, incl.:
```
Will MoistCr1TiKaL get a haircut in 2026?
Will <player> be on the cover of Madden NFL 27? / NBA 2K27?   (dozens)
Will any FaZe member come out as a furry by July 31?
Will Valve add first CS2 operation by August 31, 2026?
Will <hero> the Most Picked / Most Banned Hero at the Dota EWC 2026?
Will <team> win MSI Without Dropping a Series?                (conditional prop)
Games Total: O/U 2.5   /   Map 1: Odd/Even Total Kills?       (props)
```
Plus 908 Odd/Even + 149 Over/Under prop markets. **Market-type gating (winner-market
only) is the plumbing layer's job** (per `EB_SHARP_LINE_PLUMBING.md`), not the resolver
regex — but the resolver must still fail-to-None on all of the above, never a wrong bool.

## 4. Phrasing-pattern tally (esports-tag questions)

```
will_X_win        315   (Will <team> win the <league/event> …?)  -> shape-1 subject parse
will_X_beat_Y       4   (rare)
X_vs_Y            191   (mostly shape-2 team-name-outcome match markets)
map_round_prop   1325   (map/round/kills/handicap/score) -> PROP, ignore
unmatched-by-any  440   (pollution above + odd templates)
```

## 5. Implications for steps 2 & 3

- **The sharp MATCH-winner signal rides on shape-2** (team-name outcomes), which the
  resolver already handles authoritatively. That is the primary path and it is robust.
- **Shape-1 is mostly futures/outright** (season/tournament winners), a *different* odds
  type — lower priority for a match-odds signal, and the biggest source of ambiguous
  phrasings. Hardening it is worthwhile for correctness but is NOT on the critical path
  to a match-winner sharp signal.
- **Upstream market-type gating is essential**: only ~1043 of 2100 tag markets are
  winner markets at all (852 Yes/No + ~191 team-name); the other ~1057 are props.
- **Binding blocker unchanged:** `pinnacle_odds` is empty (B13). No sharp odds → the
  end-to-end backtest stays blocked regardless of parser coverage.

## 6. Reproduce

```bash
python scripts/esports_market_shape_probe_public.py 6000 12   # needs Polymarket egress; no VPS/DB
python scripts/esports_market_shape_probe.py 40 12            # DB-backed variant, on the VPS
```
