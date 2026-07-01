# esports_silo — HARDCODED COMMANDMENTS

Non-negotiable. Every change and every claim in this silo is bound by these.

## 1. P&L IS NOT EVIDENCE
Never cite, rely on, or conclude from realized-dollar figures. Judge the system only
on forecasting skill vs the market (Brier, calibration, correlation, closing-line
agreement), live-CLOB liquidity, and verified data. A claim resting on P&L is not a claim.

## 2. DE-VIG DOES NOT EXIST
No Shin-devig, no overround-stripping, anywhere. Sharp-book odds are stored and used
**raw**. The prior `esports_v2/model/clv.py` Shin path is dead — do not call it.

## 3. SURGICAL CUT
When reusing anything from the prior bot:
- Extract only the **minimal shape/logic** needed, copied **self-contained** into the silo.
- **Never import** from the 15-bot system; the silo stays independent.
- **Never fabricate or guess data.**
- Any adaptation of borrowed logic is labelled a **DEVIATION** — never called a "port."
- A source behaviour that **guesses/contaminates data is surfaced (e.g. as NULL), never
  reproduced.** Faithfulness to the source stops where the source contaminates.
- Record provenance inline: `# from <file>: <what/why>`.

### Standing deviations (flagged under Commandment 3)
- **Winner resolution** (`scripts/import_from_prior_bot.py:map_winner`): the source
  (`esports_v2/data/normalizer.raw_to_match_result`) defaults unresolved/missing winners
  to team **'a'** — a silent guess that contaminates training labels. The silo returns
  **NULL** for those and reports the count, and substring-checks **both** teams (source
  checks only team_b). Same exact-match intent; deliberately safer on the unhandled case.

## 4. QUARANTINE BY DEFAULT (unsure = out)
Any data, table, column, feature, or signal whose quality **and** truth have not been
verified on real data (via `scripts/verify_data_quality.py`) is **QUARANTINED** —
excluded from training, features, and every decision — until proven clean. The burden is
on the data to prove itself; we never assume it innocent. "Not sure" = excluded.

**Consequence (stated, not hidden):** until the verification battery runs and passes on
the box, **all carried data is quarantined.** The silo may be built and wired, but it
trains on nothing and trades nothing until each asset clears the battery.
