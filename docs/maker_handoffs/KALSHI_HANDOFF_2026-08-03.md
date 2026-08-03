# KALSHI MAKER — HANDOFF 2026-08-03. BOT STILL HALTED. READ §0 FIRST.

Supersedes `KALSHI_MASTER_PLAN_2026-08-02.md` for everything below; that document remains the
source for the 08-02 halt post-mortem, the money history and the original 12-defect list.
Every figure here carries its source and denominator. All 13 hook-injected operator rules bind.

## 0. STEP ZERO — verify, trust nothing here

- Worktree `C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/5dfe0ebf-2821-475d-946c-72012db34c3b/scratchpad/kalshi-wt`,
  branch `claude/maker-kalshi-live`, HEAD `9de3d89`. The main checkout is ANOTHER LANE — never
  touch it or master. `git worktree list` + `git branch --show-current` before any write.
- **The bot has been HALTED since 2026-08-02T10:26:37Z and stays halted until the operator
  names a restart.** Verified 2026-08-03T16:47Z: STOP present (230 B, uid 0, mtime unchanged at
  2026-08-02 10:26:53.414658703Z), service `polymarket-maker-kalshi-ws.service` ACTIVE (it idles
  under STOP and rests maker EXIT offsets — canon-confirmed, not a violation).
- **NOTHING FROM THIS SESSION IS DEPLOYED.** Deployed quoter md5 `9bfac08f6c9251b57749e1c80ddc356a`
  vs HEAD blob `f4e967973e13475acc76e3c903a87f5f`; deployed recorder `9d842c41c12afc8de804cab4013bd2c2`
  vs HEAD `2ec0f5b4e33409d0e0d1941ef261b424`. The running bot still carries every defect fixed below.
- Test baseline at HEAD: **1112 passed / 2 xfailed** (`python -m pytest kalshi_live/ -q`), up from
  981/2 at session start. Worktree clean.
- Live knobs (read 2026-08-03T16:47Z): `MAX_TOTAL_CAPITAL=350`, `DAILY_LOSS_HALT_USD=40`,
  `DAILY_DOWN_HALT_USD=60`, `NETEV_GATE=0`, `MKT_DAY_LOSS_EXITONLY_USD=3`.
- Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`

## 1. OPERATOR RULINGS THIS SESSION (all binding, none may be re-litigated)

From 2026-08-02 (14 decision points) and 2026-08-03 (8-3 review + new defects):

| # | Ruling |
|---|---|
| Restart gate | ALL known defects root-fixed — **and any found later**. Defects 13 and 14 are inside the gate. |
| Horizon | ≤ 8 days out. `MAX_DAYS_TO_CLOSE` is ALREADY 8 — no change needed. |
| Halt arms | REMOVE the $60 cumulative-down ratchet (done in code); drawdown arm $40 → **$30** at deploy. |
| Net-EV | Full rebuild + arm. **Rebuild built; NOT armed — see §4.** |
| Directive-6 | Use the MEASURED separators (size, market age), not the volatility gate. |
| Ladder rungs | KEEP $3/$5 now, retune later (agent's reading of "keep retune", flagged; operator has not corrected). |
| STRIKES_OUT | LEAVE at 0. Gov-D6 folded into it — revisit only if armed. |
| Ramp | 5→10→25→50 ct, ≥10 min per rung. **NOT BUILT — Phase D.** |
| Unknown markets | Slow probe, data checkpoint at 5 min, reevaluate. **NOT BUILT — Phase D.** |
| Deny-list | Exact tickers only. |
| Selection scoring | Option A — everything in dollars. Fill-cost refresh 15 min. |
| Sweeper pts | Approved into ranking (Phase D). |
| Clean session | None — full go with the new settings once all fixes land. |
| Deploy | Agent's discretion, "whatever is cleanest". **Plan of record: ONE deploy after the fix program completes.** |

## 2. WHAT SHIPPED (33 commits, `00c3d93..9de3d89`)

Every item: tests + copy-based mutation + adversarial blind review, per THE NORM.

**Phase A** — A1 halt-meter torn-read root fix + $60 arm removed (`d811141`); A2 settlement-fee
double-subtraction (`3b131d8`); A3 always-emit counters + labelled denominators (`3ae9842`).

**Phase B** — read-budget leak in the STOP branch (`84932fd`); STOP flatten is diff-and-keep
(`ff6410b`); blocked exits are loud (`2461ec4`); rung 2 on the LIVE day-delta with a bounded
unwind allowance (`614dae1`); governor carry-forward (`8a22698`); settlement top-up (`01521bb`).

**Phase C** — live per-market inventory meter, measurement-only (`6078c58`); selection inputs +
pairedness split (`36fd117`); net-EV empty-table alarm (`7b6b367`); net-EV rebuild engine
(`f5a114b`, `9de3d89`).

**New defects found and fixed** — 13: cash recorder used a position-independent fill model
(`c1af032`); 14: fill-cost feed blind to every settled market (`207f481`).

### Measurements that are now canon (all ESTABLISHED, API reads dated inline)
- Settlement `fee_cost` == SUM(fills' fees) same ticker on **127/127**; the double-count was
  **$35.5619** lifetime (read 2026-08-02T21:27:34Z).
- `/portfolio/positions` returns **0 of 129** settled tickers under unfiltered,
  `total_traded`, or `position` (2026-08-03T12:39:55Z). Two live beliefs refuted by this.
- Settlement `revenue` is CENTS (127/127); `settled_time` is an ISO STRING; `min_ts` honoured
  (2026-08-03T02:30:57Z). ⚠ `yes/no_total_cost_dollars` are GROSS LIFETIME costs on both legs —
  **never a P&L basis**.
- Payout prediction HELD: KXTRUMPENDORSEMENTS-26AUG01 (closed 08-02T14:00Z) credited
  **2026-08-03T06:50:51Z**, inside the 05:17–07:49Z band. Lifetime **58 credits / $198.95**
  (12:40:11Z). ⚠ one event does NOT discriminate close+1 from program-window-end.

## 3. DEPLOY CHECKLIST (operator-gated; nothing below has been done)

1. `deploy.sh` ships the quoter + recorder + the two new modules.
2. live.env: **delete** `KALSHI_DAILY_DOWN_HALT_USD` (knob no longer exists; a leftover line is
   inert — `if k not in watch: continue`), set `KALSHI_DAILY_LOSS_HALT_USD=30`
   (**hot-reloaded**, no restart: `KALSHI_ENV_FILE` is set and `_refresh_safety_knobs()` is the
   first statement of every cycle).
3. ⚠ **THE LEDGER STEPS ONCE AT DEPLOY: `unexplained_todate_*` jumps +$330.0381** in a single
   5-minute interval (cum_fills −$365.6000, cum_settle +$35.5619). This module's own rule reads
   a positive unexplained step as a REWARD — it is a CONVENTION CHANGE. Tell: at the boundary
   d(cash), d(n_fills_todate) and d(n_settlements_todate) are all 0 while halted. Row markers
   `fills_cash_basis` / `settle_payout_basis` appear for the first time on the first post-deploy row.
4. First post-restart governor cycle pulls the whole settlement history once (`min_ts=None`);
   safe only because `mkt_exposure` is empty that cycle. **Do not hand-populate it.**
5. `KALSHI_NETEV_GATE` stays 0 until §4 is decided.

## 4. THE ONE OPEN DECISION — net-EV arming

The rebuild engine (`kalshi_netev_rebuild.py`) is built, tested (11 pins) and validated:
**KXAAAGASD-26JUL21 = −$5.2676 vs the canon −$5.27, to the cent** (36 fills / 5 markets, read
2026-08-03T15:00:40Z). It needs no CSV and no operator screenshots — `credit_history` gives
per-EVENT attribution and the position-aware fill model + settlement revenue gives trading P&L.

**No table file was written and `NETEV_GATE` is 0.** Reason: on the canon's window the engine
puts **gas at −2.74% where the CSV canon records +1.1%** (temp agrees: −7.58% vs −9.2%).

ROOT-CAUSED, not unexplained: the two engines measure different quantities. **121.62 gas
contracts were still open at the window edge** and 7 of 9 markets traded in-window settled
outside it. A cash model books an open position's full cost; the CSV summed venue REALIZED P&L,
which is 0 while open. The residual difference is SCOPE — canon scored in-window TRADES (99
trades, $214.85 notional, realized-only); this scores whole MARKETS touched in-window (118
fills, $539.82 notional, cash + settlement). Canon's own table flags gas `credit_lag=true`
(credits under-counted, net biased pessimistic), so its +1.1% is not obviously right either.

**Operator choice:** (a) accept the whole-market scope and arm; (b) arm with gas forced
`unproven` so it falls to the model fallback; (c) reconcile the two scopes first; (d) leave off.

Also unresolved and additive: **$107.20 of credits in the full-history window map to NO family**
(only gas and temp have rules), so a two-family gate is blind to where most reward income is.

## 5. OPEN ITEMS — nothing here may be dropped (RULE NINE)

- **Phase D, unstarted:** D1 score-coverage + sweeper pts into ranking; D2 reward feedback +
  fill cost + hours-to-close into the rank key; D3 size ramp (5→10→25→50 @10 min) + a
  dollars-at-risk sizing term. All specced in the master plan §6.
- **Restart (E1)** — operator-named, gated on all defects fixed.
- **8-3 re-review** — material delivered in-session; ladder rungs and Gov-D6 remain the
  operator's to rule on. Live strike state 2026-08-03T12:48:49Z: 24 tickers, **21 at one strike,
  3 at two**; 9 permanent bans, `mkt_out_backup.json` agrees exactly.
- **The $5.77–$9.30 residual** in the cash identity is still unexplained. It DRIFTED $1.50
  while the bot was halted (no trading, 4 settlements) — that drift points at the settlement
  leg and is the best lead. Deposits are now operator-stated **$640.00** ($565.00 venue-verified
  + $75 added 2026-08-03); the model implies $629.20 cash-form / $631.81 funded-form.
- **`KALSHI_MAX_TOTAL_CAPITAL=350`** predates the deposit — a cap the operator may want to revisit.
- **$0.1093 of finalized-NO dust**; **`KXRAIN-26AUG03-PHIL` self-closed** 08-02T18:57:33Z
  (−$0.62 ticker realized) — no longer open.

## 6. HOW THIS SESSION WORKED — keep doing this

Every money-path change: tests that fail before and pass after, **copy-based mutation in a
scratch dir (never the worktree)**, then an adversarial blind review whose findings are
refutation-tested before being believed.

That process caught, in this session alone: two defects in shipped Phase-B work (a wrong
PERMANENT BAN from a stale exposure basis, and 2× resting inventory), **three false claims in
the agent's own commit messages**, an alarm that was silently dead by `NameError`, a net-EV
alarm reading the wrong table shape, and — five separate times — **a pin that passed for the
wrong reason** because the fixture was built to match an assumption instead of the contract.

Two habits worth inheriting: commit BEFORE mutating (a `git checkout --` restore ate
uncommitted work once), and when a fix breaks a test, **check whether the test was asserting
the defect** before changing either — twice it was, and once the test was right and the code
was wrong.
