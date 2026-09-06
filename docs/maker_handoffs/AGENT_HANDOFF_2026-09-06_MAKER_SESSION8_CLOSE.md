# MAKER SESSION 8 CLOSE — 2026-09-06 (session ran 07-29 → 09-01 → 09-06 across gaps)

**STATE IN ONE LINE:** paper arm HEALTHY on branch HEAD (Qmin accrual era started
09-06 16:31Z, md5(LF) `7e97417e`); wallet `0x9B24D25A514eE02b44DFF20F5e835585d35CE7b4`
EXISTS on-box with 5 POL gas + sanity FULL PASS; the ONLY blocker to the first
verified dollar is the operator's ~$98 USDC send (MetaMask walkthrough given);
two open operator decisions: paper-arm mark floor re-size (rec 750) + nothing else.

**LEDGER OF RECORD:** `docs/POLYMARKET_MAKER_RUNNING_TAB.md` — read ALL 09-01 and
09-06 rows plus §C.0 LANE RULES (operator-adopted, binding). Numbers discipline:
`docs/MAKER_NUMBERS_LEDGER.md`; venue rules: `docs/MAKER_POLY_REWARDS_RULES_CANON_2026-09-01.md`
(R1-R8 + OPEN-C1..C4). Plan: `MAKER_MASTER_PLAN.md` incl. the 09-01 amendment
(selection never open-loop past first receipt).

## WHAT S8 SHIPPED (all on `claude/maker-bot`, deployed to the paper arm)
- Two-arm halt (mark/realized, self-diagnosing kill messages) — realized arm never
  false-fired in 5 live days; mark arm DID fire 09-02 at −501.68 vs −300 (pure mark
  noise, settle_realized +6.15) → floor re-size decision open.
- OOM root fix: hourly state prune (8,921→138 entries, day_pnl-invariant, ledger-
  archived) + committed/resid gauge split (reconciled to the cent) + sweep 120/hr +
  MemoryMax 768M. No OOM recurrence in 5 days.
- Discovery hardening: truncation alarms (page-cap + budget-trip), 41-page horizon.
- Official rules canon (live fetch): one-sided scores ÷3 mid-range (REFUTES the old
  "de-risk earns zero" claim), $1/day payout floor, sampling/`b` open items C1-C3.
- Qmin adopted into the accrual model (shared combine own+comp; one-sided accrual
  gate opened). MODEL-ERA BREAK 09-06: prior acc/accday not comparable.
- Preflight fixed (balance probe needs explicit AssetType.COLLATERAL) + installed
  on box; sanity FULL PASS on the fresh EOA (L2 creds derived, no UI signup needed).
- Wallet created ON-BOX (key never left the VPS; env 600; root backup
  `/root/maker-wallet-backup-20260901`); 5.0 POL funded from system EOA
  (tx `0xf9635eee…437d`, WALLET_LEDGER.md entry).
- Lane rules codified (§C.0): numbers-level approval for live changes; absolute
  dollars only; no restarts ±60min of 00:00Z live; never `git add` from an aged
  temp worktree.
- Kalshi ports E-A..E-F (07-29) + failure-catalog triple-blind-verified; P-A..P-D
  candidates adversarially ruled fluff and SHELVED by operator (revive triggers in
  the 09-01 tab row).

## CRITICAL PATH (in order; nothing else gates the first verified dollar)
1. OPERATOR: send ~$98 USDC (Polygon) to the wallet — MetaMask guide in session
   transcript; verify on-chain immediately (publicnode RPC; polygon-rpc.com 401s).
2. Token check: if native USDC arrives, resolve collateral form EMPIRICALLY via
   balance-allowance (pUSD is V2 collateral per S244 canon — do NOT guess; OPEN).
3. `--stage scoring` from /opt/pa2-maker-live (cancel-shape probe + $3/$20
   `is_order_scoring` = settles OPEN-C4). SHOW OPERATOR before placing.
4. Fresh softness sample (ALL prior picks expired; perishable — same-day only).
5. One-page cutover sheet (Kalshi money-plan format: every value an approval
   line): staged env `deploy/maker-pilot-env.staged` (floors 10/10 per the
   operator's "lose max 10 in 24 hours" ruling, allowlist, 3 mkts, caps 30/30/60,
   worst case) → operator's explicit numbers-level GO.
6. Tiny live window (WS flags STAY OFF; finding-#2 order-rate ack still required
   before MAKER_WS_HOT=1 ever rides live) → next day `--stage receipts` = FIRST
   VERIFIED NUMBER → `maker_onchain_recon.py` (now prune-aware) → wire receipts
   into selection (master-plan amendment, binding).

## OPEN OPERATOR DECISIONS
- Mark floor re-size for the PAPER arm: −300 tripped on pure mark noise 09-02;
  rec 750 (paper guards no capital; realized 75 stays the real protection). Do
  NOT change without their number.
- (At cutover) the cutover sheet GO.

## DEFERRED ON OPERATOR ORDER (do not touch without their word)
Items 21+ of the 09-01 open-findings list: GAP-4 cap sizing (wakes on receipts +
capital number; design in elevations register), G2 paper over-credit,
unknown-sector gate hole, GAP-1/GAP-2/reconciler wiring/gate-policy lock, derisk
revisit (wakes on receipts). Plus the "come back" set: tape-starvation counter,
sweep-stall cutover note, torn-read one-time walk, report() doc line, banner
3-token completeness (offered, undecided). Plus shelved P-A..P-D (revive triggers
in tab).

## WATCHES (passive)
- RSS slope on the current process (systemd counters reset each restart — use
  journal age + MemoryCurrent trend). No OOM since the prune.
- Two-arm halt survival: if the mark arm keeps eating measurement days, that IS
  the floor-decision evidence.

## TRAPS (S8 paid for these — do not repay)
- **Aged temp worktrees get gutted by the OS cleaner**: `git add` of a
  missing-but-tracked file stages a DELETION (shipped twice, reverted twice).
  Fresh worktree every session; `git status` + file-exists before every add;
  never `git add -A`.
- CRLF everywhere: md5 via `tr -d '\r'`; patch scripts must normalize CRLF and
  use encoding='utf-8' (cp1252 mangles em-dashes); bash heredocs with CRLF in
  the command string die with "unexpected EOF" — Write the script to a file.
- Commit BEFORE mutation-testing loops (`git checkout` in the loop wiped
  uncommitted work once).
- Read the HALT file BEFORE removing it (journal KILL line is the backup).
- polygon-rpc.com returns 401; use polygon-bor-rpc.publicnode.com.
- Deploys: LF blob from `git show HEAD:`, venv py_compile first, backup first;
  boot re-arms HALT from persisted meta — remove at runtime for the resume path.
- Preflight/env run as service user: `sudo -u polymarket bash -c "set -a; . env; …"`.

## HARD RULES (unchanged): Maker ≠ MB ≠ MM; rewards basis only; NO TAKER; Kalshi
is a separate lane (read their branch read-only, never touch); `git branch
--show-current` before every repo write; hook-injected RULES ZERO-THIRTEEN bind.
