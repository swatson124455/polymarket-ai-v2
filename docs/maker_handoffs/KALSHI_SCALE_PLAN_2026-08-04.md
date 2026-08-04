# KALSHI MAKER — SCALE PLAN (CANON, operator-ratified 2026-08-04)

Operator instruction: "adjust and change as needed, full scope and make that plan canon."
This document is the forward plan of record. It does NOT supersede: the 12/14-defect record and
money history (`KALSHI_MASTER_PLAN_2026-08-02.md`), the current-state handoff
(`KALSHI_HANDOFF_2026-08-03_EOD.md`), the reconciliation canon, or any hook-injected rule.
Where the 08-02 master plan §6 and this document overlap (D-phase work), THIS document governs
sequencing; the D-item specs and proof criteria there remain binding.

## 0. THE VISION (operator-stated 2026-08-04, verbatim intent)

- **Target: $50–100/day NET profit**, as much as possible beyond that.
- **Thesis: follow the profit, safer the better.** "Ideally we take a modest cut and print
  money on markets that don't move." Drips are fine (standing 08-01 ruling).
- **Horizon: ≤ 8 days to close** (`MAX_DAYS_TO_CLOSE=8`, live). Supersedes the "2 days" phrase
  in the E1 directive — operator re-ruled 8 on 2026-08-04.
- **Risk: "no days we can't recover from in a few days on average; stay profitable without
  sticking our necks out. Pigs get fed, hogs get slaughtered."**
- **Capital: operator will scale SEVERAL THOUSAND dollars in, CONTINGENT on the bot proving it
  is a quality product at current scale first.** The proof gate is real and comes first.

## 1. MEASURED BASELINE (all sourced; the numbers the plan is built on)

- Gross reward rate: **$14.21/day average** over the 14-day span 2026-07-21→08-03 (credit_history,
  58 credits, $198.95 lifetime incl. $15 referral; 11 of 14 days credited; best day **$42.06**).
  Earned by the DEFECT-CARRYING bot — the fixed bot's rate is UNMEASURED until restart.
- Working capital: **$307.59 cash + $3.72 portfolio** (cash ledger 2026-08-04), cap
  `MAX_TOTAL_CAPITAL=350`.
- Implied gross yield: **~4–6%/day on committed capital** — INFERRED from the span average;
  linearity with capital is UNPROVEN (pools are fixed per market: $1,750–$10,470/day across 30
  series, venue_scan 2026-07-25).
- The only reliably profitable measured shape: **presence with zero fills — 5 series, +$7.51**
  (master plan §2). This is the thesis shape.
- Loss attribution stands per RULE SEVEN: on the −$122.57 basis, roughly 61–77% agent defects;
  structural maker cost −$28.68 was more than covered by rewards. All defect classes 1–14 are
  root-fixed at HEAD and deployed as of 2026-08-04.
- Live capital knobs (read 2026-08-04T19:33:16Z): `FUNDING_GATE=1`, `MAX_MARKET_CAPITAL=45`,
  `SERIES_MAX_USD=100`, `PER_SERIES_CAP=100`, `INV_SOFT_CT=15`, `INV_HARD_CT=50`, `JOIN_SIZE=0`.

**Scale math (INFERRED, to be re-measured at every rung):** at 4–6%/day gross, $50/day needs
roughly **$900–1,250 committed**; $100/day roughly **$1,800–2,500**. Because pools are fixed,
scale comes from BREADTH (more quiet markets held), not size-in-market — which is why D2/D3 are
prerequisites, not features.

## 2. THE PHASES (dependency-ordered; nothing below skips the NORM: failing-before tests,
copy-based scratch mutation, adversarial blind review on every money-path change)

### Phase P — PROVE (current scale, ~$350)
- **P0. Restart E1** — OPERATOR-NAMED ONLY. Bot is halted until then.
- **P1. First-restart checkpoint** (the one avenue unverifiable under STOP): live book-read →
  quote placement → telemetry, watched end-to-end on the first cycles.
- **P2. Clean measurement window** on the fixed bot: net $/day = credits (credit_history) +
  trading P&L (position-aware recorder, both bases live since 08-04). No defect-era data may be
  mixed in.
- **P3. Quality verdict — OPERATOR CALL** on the window's numbers. This is the capital gate.
- Success shape: net-positive days; drawdowns consistent with "recoverable in a few days";
  reward income visibly exceeding trading drag.

### Phase B — BUILD (may run during/alongside P; each item full-protocol)
- **B1 = D2. Follow-the-profit ranking** (highest leverage for the thesis): reward feedback per
  family/series from credit_history (exact per-event attribution), fill cost, hours-to-close
  into the rank key; lag exclusion keyed on PROGRAM `end_date` (close+1 held only 24 of 33;
  9 of 33 paid BEFORE close by 30.7–727.0 h). Proof: the 14 defensibly-never-paid series
  (−$127.10) rank below comparable payers; the 5 zero-fill earners (+$7.51) are NOT deranked.
- **B2 = D3. Size ramp + dollars-at-risk sizing**: 5→10→25→50 ct at ≥10 min per rung
  (operator-ruled 08-02). Proof: KXTEMPAUSH replay walks 5→50; dollar caps bind on some of the
  2,176 50-ct side quotes. **Prerequisite for any scale rung above ~$350.**
- **B3. Breadth-capacity study (NEW, scale-critical)**: how many concurrent qualifying quiet
  markets exist inside the 8-day horizon, and how much capital they absorb before share
  dilution kills the yield. Bounds how much of "several thousand" can work. Read-only study.
- **B4 = D1 clause 3. Widen measurement path**: score markets beyond the books each cycle
  already reads (sweeper as vehicle). Needed so B1 has coverage at breadth.
- **B5. Unknown-market slow probe** + 5-min data checkpoint (ruled BUILD 08-02).
- **B6. Recorder scalar fix**: `settlement_payout` → `return _f(s.get("revenue"))/100.0`
  (root-caused 2026-08-04, one row of 147, $0.0030). Full protocol; operator naming to deploy.
- **B7. `PYTHONUNBUFFERED=1` in the service unit** (journal lag up to ~57 min measured;
  observability for P1/P2). Needs a restart to take effect — bundle with P0.
- **B8. Post-restart net-EV table rebuild** on clean-window data only; re-ruling of
  `NETEV_MIN_MARGIN_PCT` on honest numbers. Until then the margin ladder measured on the
  defect-era table stands ready: 0.0 benches 6/6 receipt families; −4% keeps 1, −5% keeps 2,
  −6% keeps 5, −7% keeps all 6 — OPERATOR DECISION, open.

### Phase S — SCALE (each rung gated on the one before it)
- **Rungs: ~$350 → ~$1,000 → ~$2,500+.** At EVERY rung, in order:
  1. Operator deposits and re-states the deposit total (deposit-CHARGE convention: venue nets
     a charge; both gross and net figures are correct — recorded 2026-08-04).
  2. Re-set the risk envelope AS PERCENTAGES, operator-ratified in dollars:
     proposal — daily-loss halt ≈ **1–2% of total capital** (today's $30/$350 ≈ 8.6% shrinks to
     1% at $3k if left alone — the same dollars mean different protection), per-market exit-only
     ladder and `MAX_MARKET_CAPITAL`/`SERIES_MAX_USD` scaled to keep single-market exposure
     ≤ ~2–3% of capital. NUMBERS ARE PROPOSALS; operator sets each at each rung.
  3. Verify `FUNDING_GATE=1` behavior with the larger idle balance and raise
     `MAX_TOTAL_CAPITAL` to the operator's number.
  4. Run ≥ several clean days; **re-measure yield**; advance only if yield holds and drawdowns
     stay inside the envelope. Yield decay at a rung = stop, study dilution (B3 data), do not
     push size into the same markets. Pigs get fed; hogs get slaughtered.

## 3. STANDING GUARANTEES (unchanged by this plan)
De-risk is never blocked (pinned at every gate). One clock for credits and trading. The gate
trusts only explicit `receipt` + real number; everything else routes to the conservative model.
STOP is sacred. All 13 hook-injected rules bind. The 2026-07-27 session remains quarantined.

## 4. OPEN OPERATOR DECISIONS AT WRITE TIME
Restart E1 · `NETEV_MIN_MARGIN_PCT` (interacts with P2's window) · B6 deploy naming ·
per-rung envelope dollars (§2 Phase S) · the fail-open `thin`-negative direction (documented in
the quoter; keep or change).
