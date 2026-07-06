# Assumption Audit — old-bot bleed-over hunt (2026-07-06)

**Question asked (operator):** what assumptions/logic did we inherit from the dead
bot without noticing — "it had too many and it could have bled over even if we
tried not to."

**Method:** provenance-trace every dataset and constant the new stack (v3
collector, scoring engine, acceptance gate) rests on, and label each inherited
assumption VERIFIED (code-traced this session) or UNVERIFIED (needs VPS data).
The $100 whale floor (caught + removed earlier) was bleed-over class 1; this
audit hunts the rest. Nothing here blocks the currently-running Stage-1
validation; A2/A6 affect how much weight one run's verdict can carry.

---

## A1 — STRUCTURAL · The entire data universe is conditioned on the old bot's trader-selection hypothesis (VERIFIED)

Three independent code paths all encode "only wallets the old hypothesis
surfaced are observable":

- `trades` user-attributed rows (the scoring universe): written ONLY by
  `data_ingestion.ingest_elite_trader_activity` — per-trader activity pulls for
  the elite/top list (`TOP_TRADER_COUNT` / `get_elite_traders`). Verified.
- `mirror_rejected_signals` (the validation corpus): RTDS ingress fast-rejects
  any wallet not on the old watchlist before anything is logged. Verified.
- **v3 collector (MY new code): re-implements the same filter** — top-1000
  monthly-PnL leaderboard. I ported "watched wallets" as if it were transport,
  but it is the old bot's CORE HYPOTHESIS (recent PnL leaders are worth
  watching — a hot-hand selector prone to picking variance).

**Consequence:** the scoring engine can only rank *within* the pool the old
hypothesis surfaced. Skilled wallets outside the leaderboard are unobservable —
forever — and no gate can detect what was never recorded. The engine corrects
ranking within the pool; nothing corrects the pool boundary.

**Recommendation (operator decision):** widen v3 collection to wallet-agnostic
whale prints. RTDS is the platform-wide firehose; the watchlist filter is a
choice, not a data constraint. Cheapest robust option: keep the watched stream
AND log a deterministic sample of non-watchlist whale-size prints (e.g. hash of
tx mod N) as a control population with `metadata.stream="control"`. Storage is
the only cost; it converts A1 from unfalsifiable to measurable.

## A2 — HIGH (interpretive) · The legacy corpus contains only what old MB REJECTED (VERIFIED code-side; magnitude UNVERIFIED)

All 24 `_log_rejection` sites are rejections; copied signals never enter
`mirror_rejected_signals`. The legacy validation population therefore has a
gate-shaped hole: each trader's most old-bot-attractive signals are missing.
Both comparison groups draw from the same holed corpus, so the bias partially
cancels — but only if old-MB's gate criteria are independent of the new
ranking, which is not guaranteed (both correlate with price/size/liquidity).

Mitigations: (a) in the old bot's end-state it copied almost nothing (flat $1,
high gates, then paper pause), so the RECENT corpus is near-complete —
UNVERIFIED, quantifiable on the VPS via `trade_events` copy counts per window;
(b) the v3 stream has no strategy filter, so this hole closes going forward.
Read the current validate verdict with this caveat attached.

## A3 — HIGH (pre-Stage-2) · Tailability replays the DEAD bot's exit ladder as the only execution model (VERIFIED)

`exit_replay.py` faithfully ports the old bot's stop/take-profit ladder — a
stack of S99→S168 patches tuned while the strategy was dying, never itself
validated as +EV (the audit suspected exits destroyed edge). Stage-2's gate
number `l_net` is computed under that ladder only. Risk both ways: a good
strategy can be killed by a bad ladder; a bad one can be flattered by it.
`delta_exit` (ladder vs hold) is already reported per trader — good — but it
does not gate.

**Recommendation:** before Stage-2 is used for any go/no-go, run it under BOTH
the ladder and hold-to-resolution, and gate on the exit policy v3 actually
intends to ship (a strategy decision not yet made). Blocking for Stage-2, not
for the current Stage-1 run.

## A4 — MEDIUM · Old-bot constants carried as config defaults (VERIFIED list)

All governed (Tier-1/2) and mostly conservative, but none re-measured:
`DELTA_SECONDS=60` (the claimed old-bot detect+execute latency — source
anecdotal; v3 should measure its own), `FEE_ROUNDTRIP=0.02` (docstring itself
says UNVERIFIED), `PRICE_STALENESS_MAX_S=30`, `RHO_MIN=0.60`, `F_MIN=0.40`,
`MIN_EVENTS=12`, `MIN_TRADES_PER_TRADER=20`, watchlist size=1000 / MONTH / PNL
/ 6h TTL (old cadence), collector price bounds 0.01/0.99 vs gate 0.02/0.98
(collector wider — safe direction). Action: none urgent; re-measure
DELTA_SECONDS from v3's own pipeline once it runs (RTDS receipt vs event ts),
calibrate FEE_ROUNDTRIP from shadow_fills as its comment already demands.

## A5 — NAMED, NOT HIDDEN · "Whale wallets persist in skill" is still the core bet

The scoring engine is a better instrument pointed at the same hypothesis the
audit already found wanting. That is exactly what the kill criterion exists to
test, with a documented skeptical prior — so this is a gated assumption, not a
fallacy. But be explicit about the exit: if Stage-1 FAILs, the pivot is the
sharp-line reference lane (knowledge edges vs efficient outside prices), not
another round of wallet-ranking tweaks.

## A6 — MEDIUM (interpretive) · One cutoff = one draw

The running validation uses a single train/test cutoff (2026-05-25, inherited
from the earlier runbook — itself an arbitrary pick). A verdict at one cutoff
can be split-luck. Cheap fix: re-run at 2–3 cutoffs (e.g. 2026-04-25,
2026-05-25, 2026-06-05) and require the verdict to agree; add the placebo mode
(shuffled admitted labels must FAIL) for calibration-on-real-data.

## A7 — LOW · Minor inheritances/narrowings in my v3 code (VERIFIED)

- Watchlist fail-safe keeps the last good set forever if the leaderboard API
  dies permanently — silent staleness (log-only). Acceptable; note it.
- Composite dedup key (no txhash) can false-dedup identical re-buys inside the
  50k window — same behavior as old ingress; transport-level noise.
- Whale SELL prints are dropped entirely (estimand is entries) — deliberate,
  but it narrows future exit-model research data; revisit if an exit model
  becomes a workstream.

## A8 — SOUND · Label provenance

Resolution labels are backfilled with the temporal guard
(`m.resolved_at >= mrs.event_time`) — no hindsight assignment; CANCELLED /
INVALID markets excluded (standard resolved-market survivorship, hits both
comparison groups equally).

---

## Priority actions (operator to approve; none implemented)

1. **A1**: decide on wallet-agnostic/control-sample collection in v3 — the only
   fix for the deepest bleed-over, and it only helps if started EARLY (data
   accrues from the day it ships).
2. **A6**: after the current run returns, re-run validate at 2 more cutoffs +
   add `--placebo`. Interpretation hygiene, ~30 lines total.
3. **A2**: quantify the gate-hole (VPS: copied-trade counts per window) so the
   legacy-stream verdict carries a measured caveat instead of an unknown one.
4. **A3**: dual exit-policy tailability before any Stage-2 gating.
5. **A4**: measure DELTA_SECONDS and FEE_ROUNDTRIP from v3's own telemetry once
   the silo runs.
