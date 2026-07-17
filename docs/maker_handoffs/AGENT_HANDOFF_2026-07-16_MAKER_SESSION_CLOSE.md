# MAKER LANE — SESSION CLOSE HANDOFF (2026-07-16)

**Read order:** this file → memory `project_mm_feasibility_study.md` (the full evidence
log) → check the Friday scheduled task (`maker-sim-readout`, fires 2026-07-18 ~10:00
local; if it already ran, its report + memory updates supersede parts of this).

## NAMING + LANGUAGE RULES (operator hard-coded, non-negotiable)
- The market-making initiative/bot = **"Maker"**. NEVER "MB" (= MirrorBot exclusively), never "MM".
- **NEVER use the word "sim"** — the three background processes are **RECORDER ARMS**
  (they observe real markets and record hypothetical-quote outcomes).
- Evidence standard: **real measured data first** (payment records, real trades, real
  resolutions, chain verification). Systematic over anecdotal — do NOT lead with
  individual-wallet stories; population stats and full-universe replays only.
- Numbers need sources (Protocol 11). This lane's trading-state numbers come from its own
  measured artifacts, not bot_pnl.py (Maker has no live bot yet — NOTHING trades real money).

## WHAT EXISTS (all built this session, all read-only/paper, real capital NOWHERE)
1. **Three recorder arms on the VPS** (each 100% separate; kernel-sandboxed; kill:
   `sudo touch <dir>/STOP` or disable the unit):
   - V1 control (naive): `/opt/pa2-maker-sim`, `polymarket-maker-sim.timer`, 5-min ticks,
     since 07-15 00:45Z. Quotes ALL rewarded markets, never pulls — operator: "let all
     items run, cut later, keep notes" — do NOT gate it.
   - V2 gated: `/opt/pa2-maker-sim-v2`, `...-v2.timer`, 2-min ticks, since 07-15 19:00Z.
     Gates: in_play / extreme_wx / last_hours(19Z) / vol_pull(>2pt per ~2min → 10min off);
     quote-width A/B by market-id parity.
   - V3 gated+fast: `/opt/pa2-maker-sim-v3`, `...-v3.service` (WS daemon, own venv),
     since 07-15 19:37Z (restarts 20:08Z, 23:43Z). Same gates as V2; ~0.6s requote
     latency (hb-logged). WATCH: hb `stale_books` briefly spiked to 204 at 07-16 00:38Z
     then self-healed to 0 — WS reconnect wave; check recurrence frequency.
   - DATA ERAS: fills/realized before 07-15 ~20:20Z are LOWER BOUNDS (fill-undercount
     bug: newest-first tape + in-loop cursor → ≤1 print/market/tick considered; fixed
     `96df6d2`). v1 unreal carries ≈ −$149 over-cap legacy (frozen-msz cap fix `2e44be4`).
2. **Code**: branch `claude/maker-paper-sim` (pushed): `scripts/maker_paper_sim{,_v2,_v3}.py`
   + `deploy/polymarket-maker-sim*.{service,timer}`. NOT merged to master (not needed —
   arms run from /opt dirs). Scratchpad artifacts (session b3b85ed5): `mm_maker_backtest.py`,
   `mm_real_cohort.py`, `mm_analysis.json`, `mm_maker_econ*.json`.
3. **Scheduled readout**: task `maker-sim-readout` (07-18 ~10:00). Reads BOTH legs —
   real-cohort refresh (PRIMARY) + recorder arms (SECONDARY) — and reconciles; real data
   wins conflicts. Prompt embeds data eras + v3-zero-fills adjudication.
4. **Related shipped infra** (earlier in session, other lane): orderbook worst-of-book
   fixes hotfixed + merged to master `55747a7`; `orderbook_snapshots` truncated 07-14
   23:47Z and now collecting CLEAN best-of-book (60s cadence, top-200 markets) — the
   growing dataset future backtests need. `scripts/verify_data_integrity.py` = read-only
   harness (run it after any data change; also on master).

## THE EVIDENCE (three independent methods, they agree — memory has full detail)
1. **Real cohort (30d, chain-verified)**: 69 flagged maker wallets: **$513K actual income**
   (REWARD $144K + MAKER_REBATE $370K — rebates 2.6× pools) + **+$380K trading realized**
   (sports +$484K dominant; crypto −$28K, esports −$36K legs). **72% of wallets
   net-positive; median +$683/30d** (= the no-nuance planning baseline; tops are
   sophisticated shops, not comparables). One REWARD payment verified on-chain to the cent.
2. **Systematic backtest (30d, 1,590 full-lifecycle resolved markets, real prints/
   resolutions, exact rebates, pools EXCLUDED = floor)**: naive+stale **−$198K**;
   gated+stale −$45K; naive+fast −$2.3K; **gated+fast +$1,657 — the ONLY positive**
   (sports +$1,620). Speed ≈ $196K/mo of loss-avoidance; gates flip the sign at speed.
   Limits: strictly-through fills (rebates floor-bounded), min-size only, weather(1)/
   politics(7) absent from historical tape, gate proxies crude.
3. **Recorder arms (live)**: same shape in real time — v1 bleeds where ungated (esports
   in-play), gates + freshness kill the toxic fills (v3 ~zero through-fills at 0.6s).

**Standing verdict:** vanilla gated+fast Maker floors ≈ breakeven; the measured subsidy
layer ($75.8K/day pools posted; $513K/30d real payments) is the profit engine; our unused
nuances (WB forecast single-sided weather, EB match-start exact gating, time-of-day
rhythm — weather share ~3× at 12–16 UTC, width/size optimization) are un-priced upside.

## NEXT SESSION, IN ORDER
1. **Step zero**: `git branch --show-current` before ANY repo write (shared checkout!).
   Read memory project file. Check whether `maker-sim-readout` ran; if yes, consume its
   report; if it failed/paused, run its steps manually (its prompt is the runbook).
2. **Arms health** (read-only): tick counts, v3 hb (`books=280, stale_books=0`,
   sub-second latency), disk. Investigate stale_books recurrence.
3. **After the Friday (or 7-day, ~07-22) readout**: assemble the OPERATOR DECISION
   PACKAGE for a real-capital pilot: config = gated + WS-fast + sports-led + min-size;
   include capital ask, expected floor (backtest), subsidy expectation (REAL cohort
   per-$-of-maker-volume rates, NOT recorder estimates), kill criteria, and which
   nuances to enable first. Pilot = propose-only; operator decides.
4. **Nuance experiments** (each 100% separate, guard-railed, recorder-style first):
   WB-forecast single-sided weather quoting is highest-value BUT touches WB models —
   coordinate via WB handoff, do NOT read/modify WB runtime yourself (lane rules).
   EB match-start times for exact in-play gating: EB pipeline has them (EB lane owns).
5. **Keep the notes ledger** (operator: cut later) — the readouts quantify each
   cut-candidate in $; maintain that table.

## CAUTIONS / OPEN ITEMS
- Sports maker-rebate rate: docs conflict 15% vs 25% (used 15% conservative everywhere).
- Third-party "rewards expire Sept 1" claim: UNVERIFIED against all primary sources;
  the true kernel = subsidy is discretionary ("may change over time") — keep builds $0-capex.
- Recorder fill model = strictly-through only (conservative by design; don't "fix" it
  into optimism). v3 zero-fills adjudication is a named readout item — don't skip.
- Do not touch other bots' code/runtime/data (operator: "stay in your own lane") —
  MirrorBot handoff items live in `AGENT_HANDOFF_2026-07-14_MB_ORDERBOOK_WORSTOFBOOK.md`;
  WB items in `WB_PROMPT_ingestion_midpoint_bug.md` / `WB_COORDINATION_*.md`.
- Everything here is PAPER. The word "live" is banned until an operator-approved pilot
  exists (RUNNING ≠ LIVE discipline).
