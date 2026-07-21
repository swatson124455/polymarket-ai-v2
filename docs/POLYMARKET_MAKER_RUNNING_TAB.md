# POLYMARKET MAKER — RUNNING TAB (living ledger; append, never overwrite history)

Started 2026-07-21, adopted from the Kalshi lane's ledger practice after the
operator-directed review of their arc (their tab made a losing week fully
auditable; ours starts BEFORE first live money so the whole pilot is on the
record). This file is the chronological ledger of record for the Polymarket
Maker lane. Definitions/quote-rules live in `docs/MAKER_NUMBERS_LEDGER.md`
(the definition of record) — this file records EVENTS.

Rules for every session (binding):
- APPEND dated entries; never silently revise an old row — post a CORRECTION
  row that names what it corrects.
- Every number carries source + method + date. Tiers per
  `MAKER_NUMBERS_LEDGER.md`: CONFIG / MEASURED / MODEL / NOISE / BANNED.
- REWARDS BASIS ONLY (`MAKER_MASTER_PLAN.md` §0a); no derived EV/return until
  a real on-chain reward receipt anchors it (§0b). Trading marks: band only.
- Concentration-check pooled numbers before quoting (Protocol 14).
- md5 comparisons across machines: state the normalization
  (`tr -d '\r' | md5sum`) — CRLF working trees hash differently.

---

## A. LEDGER (chronological)

| date | event | verdict / state | source |
|---|---|---|---|
| 07-20 | Session 5B close: operator GO (staged, min-size, DEDICATED wallet, $75 floor); hardened engine DEPLOYED paper+halted; venv fixed; NO-TAKER hard rule; numbers discipline rebuilt (rewards basis only) | blocked ONLY on operator wallet | `AGENT_HANDOFF_2026-07-20_MAKER_SESSION5B_CLOSE.md` |
| 07-21 | Session 6 health check: engine md5 `1961f4b9…` VPS == branch HEAD `3531d83` (LF-normalized); unit active since 07-20 16:11:24Z, zero restarts; heartbeats clean (zombies/lmiss/feedfail/anom all 0); HALT from 07-20 07:00Z day-floor kill persisting by design; env has no wallet keys | paper arm HEALTHY; wallet still the only blocker | MEASURED — ssh journalctl/systemctl/md5sum 07-21 ~20:10Z |
| 07-21 | Kalshi-lane review (operator-directed, read-only): their live arc lost ~$45/$100 (their ledger) to execution microstructure the model never priced — taker fire-sales on flatten, unmanaged inventory accumulation, wrong-markets selection. Three lessons adopted here (rows below) | lessons imported BEFORE our first live dollar | their `KALSHI_RUNNING_TAB.md` + 07-21 handoff, read from `origin/claude/maker-kalshi-live` |
| 07-21 | ADOPTED #1 — `MAKER_SECTOR_ALLOWLIST` (CONFIG, `maker_live_engine.py` load_config/discover): non-empty ⇒ only listed sectors survive discovery, applied BEFORE ranking so a shrunk `MAKER_MAX_MARKETS` cannot admit an off-list market; overlap with excluded sectors fails loud; empty ⇒ behavior unchanged. Closes the Kalshi wrong-markets shape for our tiny footprint; fail-closes "unknown"-sector markets out of the pilot | built on branch; NOT deployed | this session's commit |
| 07-21 | ADOPTED #2 — cancel-SHAPE probe in `maker_preflight.py --stage scoring`: raw `cancel_orders` response captured, printed, and run through the ENGINE'S own `_cancel_shortfall` (imported, never copied). An unanticipated shape now surfaces at scoring, not at the first live kill | built on branch; NOT deployed | this session's commit |
| 07-21 | ADOPTED #3 — this running tab | active | this file |
| 07-21 | CLASSIFIER BUG caught by the allowlist's first-output cross-check on live gamma: weather KW `heat-` matched "miami-heat" NBA slugs — "Will LeBron James play for the Miami Heat in 2026-27?" classified WEATHER (would have entered the weather pilot slice; the exact Kalshi mis-labelled-market shape). Engine-only fix: `heat-` → heat-wave/heatwave/heat-index/heat-advisory/extreme-heat (+lowest-temp); LeBron → "unknown" → fail-closed out of any allowlist. ⚠ family recorder arms v1–v6 + census + research scripts keep the OLD pattern mid-era (measurement attribution only — census/canon "weather" sector stats may include team-name matches); family sync = PROPOSE-ONLY, own era stamp | fixed in engine; post-fix live cross-check = 8/8 genuine temp markets | MEASURED — discover() run vs live gamma 07-21, pre/post diff |

| 07-21 | INDEPENDENT ADVERSARIAL REVIEW of the session's changes → SHIP-WITH-FIXES, all fixed same session: (1) MAJOR — pre-existing partial-discovery guard's 40-market floor would have FROZEN any allowlisted (by-design-small) universe after first adoption; every refresh logged "PARTIAL" + discarded; daily-churn weather slice stale within a day. Fixed: `discovery_suspect()` extracted+testable, floor waived when allowlisted, relative half-shrink guard retained. (2) MINOR — heat-regex fix orphaned real heat-event slugs (heat-dome/heat-warning/heat-emergency/record-heat/excessive-heat) → restored. (3) MINOR — typo'd allowlist drops everything silently (universe.json write is gated on non-empty picked) → loud "matched ZERO" journal line added. (4) banner now prints allowlist. (7) preflight cancel-shape probe validates the SIBLING engine file → runbook rule: run preflight from /opt/pa2-maker-live. 126/126 tests | discipline held: reviewer found what the author's own tests missed | reviewer report, this session |

## B. CANONICAL STATE (latest-good; supersede by appending to §A with date)

| thing | state | as-of / source |
|---|---|---|
| Live pilot | APPROVED (staged, min-size first, dedicated wallet, $75 day-floor) — NOT started | operator decision, 5B handoff §1 |
| Blocker | operator-provisioned dedicated wallet (`MAKER_PK` + `MAKER_FUNDER` → `/opt/pa2-maker-live/env`) | 5B handoff §1.2 |
| Engine on VPS | `1961f4b9…` = branch `claude/maker-bot` HEAD `3531d83`; paper; HALTED (07-20 day-floor) | MEASURED 07-21 |
| Real money traded | NONE, ever | — |
| Verified income numbers | NONE — first one arrives at `--stage receipts` after the first live 00:00Z window | `MAKER_NUMBERS_LEDGER.md` |
| Live path | sanity → scoring (incl. cancel-shape probe) → tiny footprint (allowlist-pinned, weather-led) → receipts → on-chain recon | 5B handoff §6 + this session |
| Parked (propose-only) | cap redesign (flat $150 gross leaves 30/140 unquotable); "unknown"-sector in-play gate hole; gate-policy lock (≥3 clean post-cliff days; v5-report ranks WORST first — rank on canon only); reconciler→engine wiring | 5B handoff §6.6 |

## C. STANDING CAVEATS

1. Every return-shaped figure anywhere in this lane is MODEL until the first
   receipt; the receipt-vs-model divergence check is the promotion gate.
2. The paper arm's dayPnL/marks are NOISE-tier — never quote as points.
3. Kalshi-lane numbers cited in §A are THEIR ledger's figures (their
   citations), recorded here only as review context — never blend them into
   Polymarket expectations.
