# WORK PROGRAM — MirrorBot / Polymarket AI V2

**Canonical reference for the multi-item work program. Read this in §0 orientation. Update it like code (commit changes).**

Created 2026-05-29 (S234) to stop the program living in conversational memory only — the same "memory ≠ reality" drift that produced operator-memory-vs-code gaps (S231 Bug 17) and the WALLET_LEDGER paper-vs-live drift caught in S234.

## ⚠ Completeness status (S234) — THIS FILE IS A SCAFFOLD

The program is described as **"14 items."** The S234 conversation specified only items **2, 4, 6, 10, 12, 14** (plus the new **2b** and **15**). The remaining numbered slots — **1, 3, 5, 7, 8, 9, 11, 13** (8 items) — are implied by the "14-item" framing but were **not specified in-conversation and are not in the repo** (grep for "Work Item" found no prior file). They are listed below as `TBD — operator to supply` rather than invented (anti-fabrication). **Operator: fill these in (or point to their source) so this becomes a complete canonical reference.** Do not read a `TBD` slot as "no such item."

## Standing rules
- MB session is the VPS deploy channel; MB has priority on shared resources (CLAUDE.md SESSION PRIORITY).
- **No further deploys until WI-2 + WI-2b are resolved** — every deploy currently reinforces the consolidated-symlink drift.
- **Do not re-flip MB to live until the WI-14 gate is defined and met.**
- **Do not start WI-4 before WI-2 + WI-2b** (and this file) are in place.

## Items

| # | Item | Status | Depends on | Completion gate |
|---|---|---|---|---|
| 1 | `TBD — operator to supply` | unknown | — | — |
| 2 | **Splinter symlink topology verification.** Confirm whether WB/EB are meant to run their own splinter releases (`/opt/pa2-weather-current`, `/opt/pa2-esports-current`) or the consolidated master symlink. S233/S234 found all 4 services on the single `/opt/polymarket-ai-v2 → master` symlink; splinter symlinks absent. | OPEN | — | topology documented + matches intent; splinter symlinks restored **OR** splinter intent formally retired |
| 2b | **deploy.sh per-bot test gating** (or formal retirement of splinter intent). `deploy.sh` runs all of `tests/unit/`, so any bot's red tests block every bot's deploy (S234: EB test rot blocked the MB deploy — top-priority bot gated on EB test debt). Either gate per-bot, or accept the shared-test-debt model explicitly. | OPEN (new S234) | couples WI-2 | deploy gates MB on MB+shared tests only, **OR** a committed decision doc accepting shared-test gating |
| 3 | `TBD — operator to supply` | unknown | — | — |
| 4 | **bot_pnl.py per-bot segmentation.** | OPEN | WI-2, WI-2b, this file | (operator to define) |
| 5 | `TBD — operator to supply` | unknown | — | — |
| 6 | **Position lifecycle module.** Explicit state machine that checks position state (RESOLVED / DELISTED / wallet-imbalanced) **before** any exit attempt. **Design note (S234): the state-check MUST precede BOTH `place_order` AND the Bug 11C balance guard (`mirror_bot.py:1601`). RESOLVED/DELISTED handlers MUST NOT depend on the SELL path completing or the terminal classifier (`mirror_bot.py:1688`) running.** Retires the Bug 21 tactical family (terminal-reject classification) + the CB-OPEN-masking gap caught S234. | OPEN | — | resolved/delisted/imbalanced positions route to write-off/redemption handlers without doomed SELLs; Bug 21 tactical path removable |
| 7 | `TBD — operator to supply` | unknown | — | — |
| 8 | `TBD — operator to supply` | unknown | — | — |
| 9 | `TBD — operator to supply` | unknown | — | — |
| 10 | **Wallet-ledger auto-update from journalctl probes.** `WALLET_LEDGER.md` operational-state + balances auto-synced from bot probes. Rationale: manual maintenance fails silently — S234 caught the ledger still claiming paper mode ~3 days after the S232 live flip. | OPEN | — | ledger operational-state + balance table auto-refreshed from probes; no silent manual drift |
| 11 | `TBD — operator to supply` | unknown | — | — |
| 12 | **Review-checkpoint scheduling (calendar entries).** Operator-actionable. *(Partial — exact scope TBD; named as an operator action item in S234.)* | OPEN | — | review checkpoints scheduled |
| 13 | `TBD — operator to supply` | unknown | — | — |
| 14 | **Live re-flip gate.** The criteria that must be satisfied before re-flipping MB to live (capital, safety nets, verification). **Do not re-flip until defined + met.** | OPEN | — | gate criteria documented + satisfied; operator sign-off |
| 15 | **Resolution-backfill audit** (new S234). `markets.resolved` reads `false` for known-resolved markets (all 3 S234 positions + the 4 S230 positions, per WALLET_LEDGER line ~147). Quantify drift scope: count `markets.resolved=false` across live-mode positions, cross-reference authoritative CLOB/on-chain resolution, root-cause (Phase 4b backfill gap in `database.py`). If broad, any code path gating on `markets.resolved` is operating on stale data. | OPEN (new S234) | — | drift scope quantified; if structural, backfill fixed; code paths gating on `markets.resolved` audited |

## Change log
- **2026-05-29 (S234):** file created. Added WI-2b and WI-15. Added WI-6 design note (lifecycle check must precede the SELL path + Bug 11C guard). Flagged 8 unspecified slots (1, 3, 5, 7, 8, 9, 11, 13) as TBD pending operator input.
