# WALLET LEDGER — MirrorBot Real-Money Movements

**Owning bot:** MirrorBot (only live-trading bot on Polymarket V2)
**EOA wallet:** `0xd6a5e2d75fae67739749af380c54b0544878627f`
**Deposit wallet (V2 proxy):** `0xBB3988D74a853ddC16f22eEC52fa53E3Cedd2247`
**Chain:** Polygon (chainid 137)

## Rule of approval

**Every entry in the "Money Movement" section below was either:**
1. Operator-initiated and operator-approved (deposits, withdrawals)
2. Bot-initiated for trade execution, opening/closing a recorded position
3. Operator-approved one-time on-chain reconciliation

**NO money is moved by an MB session without explicit operator approval naming the specific source, destination, and amount.** This applies to:
- Manual SELLs of stuck CTF positions
- Wallet-to-wallet transfers
- Approval grants (ERC20 / CTF approvals)
- pUSD ↔ USDC.e conversions
- Withdrawals from Polymarket back to L1/L2

If an MB session believes a money move is needed, the session **proposes it in writing** (this file or a session handoff), the operator approves, then the session executes. No "while I'm in here" approvals.

---

## Bot operational state (updated S235 2026-05-31)

**LIVE.** `SIMULATION_MODE=false`. Active release `20260529_154845` (Bug 21 + EB test fix). First live position opened 2026-05-27 21:27 UTC (position 189394, earlier than the S234 "18:44 UTC S232" timestamp which was the WB deploy, not the MB live flip). 8 positions currently open. Most recent balance probe: **$4.26409 pUSD** (from journalctl; not yet in system_kv — system_kv write path added S235/WI-11, will populate on next probe after deploy).

## Current state (last verified: S244 2026-06-11)

### On-chain balances

**Post redeem-and-retrade loop (2026-06-11 ~19:31 UTC):**

| Wallet | Asset | Balance | Verification source |
|---|---|---|---|
| DEPOSIT `0xBB39…2247` | MATIC | 0.0000 | on-chain RPC, S235 2026-05-31 (not re-checked S244) |
| DEPOSIT `0xBB39…2247` | **pUSD** | **$34.1502** | eth_call 2026-06-11 final (= $18.82 redeemed + $15.9957 swept from EOA, both wrapped to pUSD, minus the bot's first post-funding trade). **= bot buying power** (CLOB `balance-allowance` COLLATERAL = 34150200) |
| DEPOSIT `0xBB39…2247` | USDC.e (`0x2791…4174`) | **$0.0000** | eth_call 2026-06-11 (all wrapped to pUSD via the onramp) |
| DEPOSIT `0xBB39…2247` | USDC native (`0x3c49…3359`) | $0.0000 | eth_call 2026-06-11 |
| EOA `0xd6a5…627f` | USDC.e | **$0.0000** | eth_call 2026-06-11 — swept into the trading wallet (was $15.99566; tx `0x2e76de09…`) |
| EOA `0xd6a5…627f` | pUSD | $0.0000 | eth_call 2026-06-11 |
| EOA `0xd6a5…627f` | MATIC | ~9.30 | eth_call 2026-06-11 (gas; spent ~0.06 on the USDC.e sweep) |
| Factory proxy `0xB9Bd…9b7a` (EOA's 2nd proxy, see S242 canary) | USDC.e / pUSD | $0.0000 / $0.0000 | eth_call 2026-06-11 (checked to rule out "ledger read the wrong proxy") |

**Intermediate snapshot (pre-loop, 2026-06-11 ~18:00 UTC):** deposit USDC.e $0.0000, pUSD $0.31782 — see the $20 discrepancy note below, which is independent of the redemption (the redeemed $18.82 USDC.e landed AND was converted between this snapshot and the post-loop one above).

**✅ $20 USDC.e DISCREPANCY — RESOLVED 2026-06-11 (on-chain trace, keyless): the $20-USDC.e-at-deposit row was a PHANTOM; no money is/was missing.** Pulled the deposit wallet's COMPLETE USDC.e `Transfer` history via `eth_getLogs` (topic-filtered on the deposit wallet, 24-day window block 87351784→88335784, covering the wallet's entire life — first funded 05-21). Result: **the ONLY USDC.e ever to enter the deposit wallet is (a) today's 7 redemption payouts from CTF `0x4d97dcd9` totaling $18.82 and (b) today's $15.9957 sweep from the EOA. ZERO other inbound, ZERO outbound, ever.** So **$20 USDC.e never sat at the deposit wallet** — the S235 (05-31) "$20.0000 USDC.e" row was an erroneous/misattributed reading (hypothesis #1 confirmed; the operator's real 05-26 $20 deposit arrived/was-held as pUSD and was traded down, consistent with the pUSD balance history). The "idle $20 USDC.e deposit-wallet" footnote in the wallet-cash table is **withdrawn as a phantom**, not suspended. (The Etherscan key was never needed for this either — same false premise as the redemption "ABI wall.")
**Lesson logged:** an S235 session "on-chain RPC, this session" balance reading was simply wrong and propagated as a discrepancy for 11 days; the authoritative source is the `Transfer` event log, which is keyless via `eth_getLogs`.

### CTF token positions held on-chain (deposit wallet)

These are real on-chain CTF outcome tokens (ERC-1155). All 4 markets resolved on-chain between 2026-05-24 and 2026-05-26 (verified S230 via direct `CTF.payoutDenominator()` RPC query). Tokens remain in wallet — CTF standard does not auto-burn on resolution; redemption is an explicit holder action.

| DB id | Market | Our outcome | Result | On-chain CTF size | Token id | Redeemable pUSD |
|---|---|---|---|---|---|---:|
| 187436 | New York Mets vs. Miami Marlins | NY Mets (= YES) | **LOST** (Marlins won) | 1.900000 | `47092932352188404469…007860903` | $0.00 |
| 187437 | Roland Garros: Mpetshi Perricard vs. Djokovic | Mpetshi Perricard (= YES) | **LOST** (Djokovic won) | 8.690000 | `86177088704252466819…549646201` | $0.00 |
| 187438 | Cleveland Guardians vs. Phillies | Guardians (= YES) | **WON — REDEEMED 2026-05-26** | 0 (burned via redemption) | `77366692795049180022…243200775` | $1.83 received |
| 187439 | Will Lecce vs. Genoa end in a draw? | "Yes" / draw (= YES) | **LOST** (No-draw won) | 3.770000 | `115354460904010933622…094742405488` | $0.00 |
| **Total** | | | | | | **$1.83** |

**Reconciliation status:**
- 3 losing positions: economically zero. Tokens remain dormant in wallet (no monetary loss from leaving; redemption to-burn would cost gas and recover $0). Bot's DB has them as `status='closed'` (Bug 12 phantom-closed) which incidentally matches the on-chain economic reality.
- 1 winning position (#187438): **$1.83 pUSD recoverable via UI redemption** — operator-approved 2026-05-26 (S230), pending operator UI action. See `OPERATOR_GUARDIANS_REDEMPTION.md`.

**Cash-flow gap on the 2026-05-24 outflow:** $3.68 pUSD left the deposit wallet; $1.83 is recoverable as winning-CTF asset value at on-chain payout ratio; gap not coming back = $1.85. (This is a wallet-cash arithmetic — not bot-recorded trading P&L. bot_pnl.py records the bot's paper-simulated outcome for these positions, which diverges from on-chain reality due to Bug 12.)

Bug 11A (deployed 2026-05-25) prevents future phantom-close occurrences. Bug 12 root cause documented in `feedback_mb_top_priority.md`.

---

## Money Movement Log

### Inbound — operator deposits

| Date (UTC) | Asset | Amount | From | To | Tx hash | Approved by | Notes |
|---|---|---|---|---|---|---|---|
| ≤ 2026-05-21 20:22 | pUSD | $5.00 | operator | DEPOSIT `0xBB39…2247` | (older than RPC retention; first bot probe 2026-05-21 20:22 UTC) | operator | **Trace target — RESOLVED.** First observed in bot logs at 2026-05-21 20:22 UTC. Balance stayed at exactly $5.00 across all probes through 2026-05-24 16:43 UTC (last probe before the live BUYs fired). After the 4 live BUY orders on 2026-05-24 17:27–19:02 UTC, balance dropped to **$1.31993** ($5.00 - $3.68 = $1.32 ✓). Only TWO distinct values ever observed in the journal: 5.0 and 1.31993. |
| (unknown) | USDC.e | $20.00 (cumulative) | (unknown) | DEPOSIT `0xBB39…2247` | (older than RPC retention) | (unknown) | Source/origin not yet traced. **Bot does NOT use USDC.e for bankroll — only pUSD.** This $20 USDC.e is sitting idle from the bot's perspective. Polygonscan API key needed for full history. |
| (unknown) | USDC.e | $15.99 | (unknown) | EOA `0xd6a5…627f` | (older than RPC retention) | (unknown) | Source/origin not yet traced. EOA is not the V2 trading wallet — also sitting idle. |

### pUSD balance history (bot probe, full retention)

| Observed (UTC) | Balance | Note |
|---|---|---|
| 2026-05-21 20:22:04 | $5.0000 | First observed (operator deposit completed by this point) |
| through 2026-05-24 16:43:24 | $5.0000 | Stable across all probes — no trades touched it |
| 2026-05-25 18:17:59 | **$1.31993** | Post-deploy restart. Reflects net consumption of $3.68 by the 4 live BUYs on 2026-05-24 |
| 2026-05-26 ~16:50 (Polymarket UI) | **~$3.15** | Post Guardians redemption. |
| 2026-05-26 ~17:25 | **$23.14993** | After operator $20 deposit (verified on-chain RPC, S230). |
| 2026-05-27 00:47 – 21:17 (7 probes) | **$23.14993** | Stable through end of 05-27. Mirror running live; first live positions opened 21:27 UTC (189394/189396/189397). |
| 2026-05-28 13:54 (first probe after gap) | **$6.2903** | Drop of $16.86 from $23.14993. Window: 05-27 21:17 → 05-28 13:54. Matches ~16 live ENTRY positions opened in this window (each ~$1.01 capital) minus 2 SELL receipts (~$1.71 back). On-chain trace (phase b) not yet done — flagged as unresolvable without Polygonscan API key. |
| 2026-05-28 13:54 – 18:45 (4 probes) | **$6.2903** | Stable. Additional live positions closed (SELLs) + opened. |
| 2026-05-29 (2 probes) | **$5.26911** | Drop of ~$1.02. Consistent with ~1 net new position open. |
| 2026-05-30 – 05-31 (2 probes) | **$4.26409** | Drop of ~$1.00. Consistent with 1 new position (208932 opened 05-31 18:55). |

**Current capital deployed (8 open live positions):**

| DB id | Side | Entry price | Size | Capital (~pUSD) | Opened |
|---|---|---|---|---|---|
| 189394 | NO | 0.47 | 2.15 | ~$1.01 | 2026-05-27 21:27 |
| 190637 | YES | 0.79 | 1.27 | ~$1.01 | 2026-05-28 10:36 |
| 190638 | YES | 0.40 | 2.53 | ~$1.01 | 2026-05-28 10:37 |
| 190641 | YES | 0.40 | 2.53 | ~$1.01 | 2026-05-28 11:15 |
| 190677 | YES | 0.51 | 1.98 | ~$1.01 | 2026-05-28 12:30 |
| 196944 | NO | 0.20 | 5.00 | ~$1.00 | 2026-05-29 16:10 |
| 196945 | NO | 0.53 | 1.90 | ~$1.01 | 2026-05-29 16:10 |
| 208932 | NO | 0.47 | 2.15 | ~$1.01 | 2026-05-31 18:55 |
| **Total** | | | | **~$8.07** | |

**Estimated remaining liquid balance:** $4.26 (probed) — but capital in open positions is ~$8.07. These numbers don't add up — $4.26 + $8.07 > $23.15 starting balance. Explanation: (1) the probe balance ($4.26) is what's *left* in the deposit wallet after deploying capital, (2) the deployed capital ($8.07) is in CTF tokens, not in the deposit wallet. The total accounts are: $4.26 liquid + ~$8.07 deployed = $12.33, vs $23.15 starting — net consumed ~$10.82. This matches: ~17 total ENTRY positions × ~$1.01 = $17.17, minus ~6 SELL receipts × ~$1.00 = $6.00, net out = $11.17 (close enough given rounding and fee differences). Gap from $10.82 vs $11.17 estimate: ~$0.35 — within expected fee/slippage variance. **VERDICT: balance history is internally consistent; no unaccounted movements detected.**

### Redemptions — winnings collected on-chain (programmatic, S244)

**2026-06-11 — first programmatic redemption (the redeem-and-retrade loop's maiden run).**
Approved by operator ("create a loop to redeem and retrade … do it"). Source: the deposit
wallet's own 7 resolved winning CTF tokens. Destination: the same deposit wallet (collecting
winnings — no funds left operator control). Gas: relayer-paid (gasless WALLET batch).

| Item | Value |
|---|---|
| Tx hash | `0xa46cdf55a69929871ad5056ae3e6bdac53538634830a4aa8605f497c8574498d` |
| Block / status | 88333681 / **1 (success)**, gasUsed 576158, 30 logs |
| Route | `scripts/redeem_and_retrade.py --execute --phase redeem` → 7× `CTF.redeemPositions(USDC.e, 0x0, conditionId, [indexSet])` in one DepositWallet EIP-712 Batch → relayer-v2 `/submit` type WALLET (relayer txID `019eb81f-1204-71f2-a0b7-7e6949c5670f`, state STATE_EXECUTED) |
| Collateral | USDC.e (`0x2791…4174`) — all 7 winners were USDC.e-collateralized (derived per-position via `getPositionId` match) |
| Result | deposit-wallet **USDC.e $0.00 → $18.8200**; all **7 winning CTF tokens burned (7/7 → 0)**; pUSD unchanged at $0.31782 |
| Markets redeemed | 3× CS2 (Tricked/MIBR/KOLESIE), Spurs spread, Roland Garros (Arnaldi), Phillies/Dodgers, T1/KT handicap |

**Conversion (USDC.e → pUSD), same session:**

| Item | Value |
|---|---|
| Tx hash | `0x19eeb09d5b51a1e43b89e8d2608ce638c1dbeff28e405de9a867993559da972c` |
| Block / status | 88334134 / **1 (success)** |
| Route | `--phase convert` → `USDC.e.approve(Onramp, amt)` + `Onramp.wrap(USDC.e, depositWallet, amt)` on the **Permissionless Collateral Onramp `0x93070a847efEf7F70739046A929D47a521F5B8ee`** (3-arg `wrap` `0x62355638`) → one DepositWallet WALLET batch (relayer txID `019eb829-7201-7a42-8381-0522719fe021`, STATE_EXECUTED) |
| Why the onramp | the relayer blocks `wrap()` on the pUSD token directly ("unknown method on collateral token"), and the deposit wallet isn't a direct pUSD wrapper (revert `0x3204506f`); the onramp IS the authorized, permissionless wrapper |
| Result | deposit-wallet **pUSD $0.31782 → $19.1378**; **USDC.e $18.8200 → $0.0000**; CLOB COLLATERAL buying power $0.31782 → **$19.1378**; MirrorBot bankroll capital refreshed **$0.32 → $19.14** (`bankroll_wallet_material_change`, 19:31:18) → capital guard lifted, bot retrades |

**Closes the S230 redemption gap AND the retrade loop.** The $18.82 in winning tokens
(unredeemed since ~05-24, flagged across S230→S242) is recovered, converted to tradeable pUSD,
and recognized as bot buying power — end to end. The S242 "ABI wall / Etherscan-key-gated"
conclusion was wrong: the `DepositWallet` + `DepositWalletFactory` + `CollateralOnramp` are all
Sourcify/Polygonscan-verified; the existing `RELAYER_API_KEY` authorizes WALLET batches. The
loop is automated via `scripts/redeem_and_retrade.py` + the 6h `polymarket-redeem.timer`
(enabled 2026-06-11). Runbook: `REDEEM_AND_RETRADE_RUNBOOK.md`.

**Note — the $20 USDC.e discrepancy is SEPARATE and now RESOLVED** (✅ note in Current state
above): on-chain `eth_getLogs` trace shows no $20 USDC.e ever entered the deposit wallet — a
phantom S235 reading, no money missing.

### EOA → trading-wallet sweep + convert (2026-06-11, operator-approved)

Operator: "review ledger … xfer to be in the active trading wallet." The EOA signer wallet held
**$15.9957 idle USDC.e** the bot can't trade (wrong wallet + wrong token). Moved it into the
trading wallet and converted to pUSD:

| Step | Tx | Result |
|---|---|---|
| Transfer EOA → deposit | `0x2e76de09dd8336947206ab0aecd9b9a6f208c5c143e696db3416e2ad1f15ef72` (block 88335725, status 1) | EOA USDC.e $15.9957 → $0.00; deposit USDC.e → $15.9957. Plain ERC20 transfer, EOA-signed, EOA gas (~0.06 MATIC). `scripts/fund_trading_wallet_from_eoa.py`. |
| Convert (onramp) | `0x8e8be0777b127e2d49baacd3393da5144225264a77064fcb4c02161c715b42f0` (STATE_EXECUTED) | deposit USDC.e $15.9957 → $0.00; deposit pUSD → **$34.1502 total**; CLOB buying power = $34.1502. `--phase convert`. |

The EOA is now swept clean; all liquid capital is consolidated as pUSD in the trading wallet
and recognized as bot buying power.

### Outbound — bot-initiated CTF acquisitions (committed to live positions)

| Date (UTC) | Asset | Amount | Direction | Counterparty | Tx hash | Approved by | Trade context |
|---|---|---|---|---|---|---|---|
| 2026-05-24 17:27:16 | pUSD → CTF | ~$1.01 | DEPOSIT → CLOB → CTF token | Polymarket CTF `0x4D97DCd9…6045` | (in py-clob-client logs as order_id) | operator (live mode active at the time) | Bought 1.9048 YES tokens on market `0xb13083a7…` at $0.53. DB position #187436. |
| 2026-05-24 17:36:18 | pUSD → CTF | ~$1.04 | DEPOSIT → CLOB → CTF token | Polymarket CTF | order_id `0x3f0dd18bcd60f801bd793d38eea86c05002f61db3b063b72d711203a4929fe89` | operator | Bought 8.6957 YES on `0x0a931d96…` at $0.12. DB position #187437. |
| 2026-05-24 17:52:45 | pUSD → CTF | ~$1.01 | DEPOSIT → CLOB → CTF token | Polymarket CTF | order_id `0xbd69a552dc82ccc59b1e1f08135a877d4aaa73dd32e8b917ed176556f0e30e1d` | operator | Bought 1.8349 YES on `0xbf8a2056…` at $0.55. DB position #187438. |
| 2026-05-24 19:02:40 | pUSD → CTF | ~$1.02 | DEPOSIT → CLOB → CTF token | Polymarket CTF | order_id `0xe25f0e571f28a0ef0407c2c6f0ac8e2782c850ae89b7c2c1270a7d76fb09d805` | operator | Bought 3.7736 YES on `0x76ee7421…` at $0.27. DB position #187439. |

### Outbound — failed BUY attempts (rejected by CLOB, no money moved)

| Date (UTC) | Outcome | Notes |
|---|---|---|
| 2026-05-24 19:36:06 | HTTP 400 `balance: 0` | NO @ $0.52 size 1.94 on `0x8abddb00…`. Bug 11B (BUY pUSD capital guard) now in deployed code would have blocked these before CLOB. |
| 2026-05-24 19:38:25 | HTTP 400 `balance: 0` | NO @ $0.73 size 1.37 on `0x48763eee…` |
| 2026-05-24 19:40:30 | HTTP 400 `balance: 0` | NO @ $0.49 size 2.02 on `0x70c07d82…` |
| 2026-05-24 19:42:29 | HTTP 400 `balance: 0` | NO @ $0.56 size 1.80 on `0xed9f16ab…` |
| 2026-05-24 19:43:30 | HTTP 400 `balance: 0` | NO @ $0.56 size 1.79 on `0x04cbfd3b…` |

### Outbound — guard rejections (Bug 11C SELL guard, no money moved)

| Date (UTC) | Outcome | Notes |
|---|---|---|
| 2026-05-24 18:48:13 | guard reject | Attempted SELL size 8.6957 on token `0x0a931d96…` (position #187437) but on-chain balance was 8.690000 → guard rejected as insufficient. Bug 11C epsilon fix (committed `3d280d9`, deployed 2026-05-25) would have allowed this exit (gap < 0.01 token). |

### Resolved — losing tickets, dormant

| DB id | Market | On-chain CTF size | Result | Action |
|---|---|---|---|---|
| 187436 | NY Mets vs. Marlins | 1.900000 | LOST (Marlins won, payoutNumerator=0) | None — tokens worthless, gas to redeem-and-burn > $0 recovered. Leave dormant. |
| 187437 | Mpetshi Perricard vs. Djokovic | 8.690000 | LOST (Djokovic won) | None — leave dormant. |
| 187439 | Lecce vs. Genoa "draw" | 3.770000 | LOST (no-draw resolved) | None — leave dormant. |

These 3 token holdings will sit at zero economic value in the deposit wallet indefinitely. ERC-1155 standard doesn't auto-burn on resolution. Polymarket UI may show them as resolved-and-lost portfolio entries.

### Pending — proposed money moves awaiting operator action

*(none currently — Guardians redemption executed 2026-05-26, see Inbound section below)*

### Inbound — redemption proceeds

| Date (UTC) | Asset | Amount | From | To | Method | Approved by | Notes |
|---|---|---|---|---|---|---|---|
| 2026-05-26 ~16:50 | pUSD | $1.83 | CTF redemption of winning Guardians outcome | DEPOSIT `0xBB39…2247` | Polymarket UI Redeem button (gasless relayer-signed `CTF.redeemPositions` per Polymarket POLY_1271 proxy flow) | Operator (S230 session) | Position #187438 (Cleveland Guardians vs. Phillies, 1.83 shares). Polymarket UI showed: Cost $0.88 (filled), Cashed Out $1.83. No on-chain tx hash captured in UI (would require Polygonscan API key to trace). Cash balance on Polymarket UI body went from $1.32 → $3.15 immediately post-redemption (verified screenshot). |
| 2026-05-26 ~17:20 | pUSD | ~$20.00 | Operator deposit | DEPOSIT `0xBB39…2247` | Polymarket UI deposit flow (operator confirmed via "Confirm pending deposit" banner click) | Operator (S230 session, pre-smoke-test funding) | Brings deposit wallet pUSD to $23.14993 (verified via `check_pusd_balance` on-chain RPC helper, 2026-05-26 ~17:25 UTC). Funds the smoke test of live trading post-Bug-12 deploy. |

---

## S234 update (2026-05-29) — S232 live-window positions reconciled

Three positions opened in the S232 live window (2026-05-28). All three markets **resolved on-chain**. Verified this session via Polymarket **CLOB API** per-token `winner`/`price` + `check_ctf_balance` (CTF `balanceOf`). The local DB `markets.resolved` flag reads **false** for all three — same resolution-backfill drift as line 147 (now a work-program item: resolution-backfill audit, `WORK_PROGRAM.md` WI-15).

| DB id | Match (market) | Our outcome (YES) | Result | DB size | On-chain held | Redeemable |
|---|---|---|---|---:|---:|---|
| 190635 | T1 vs KT Rolster (`0x9bce195835…`) | **T1** | **WON** | 2.150538 | ~2.15 | **YES — pending operator on-chain redemption** |
| 190636 | Agamenone vs Coppejans (`0x13c91c7a00…`) | Coppejans | **LOST** | 1.526718 | ~1.52 | No ($0, losing outcome) |
| 190643 | T1 vs KT Rolster (`0x78a736e934…`) | KT Rolster | **LOST** | 3.174603 | ~3.17 | No ($0, losing outcome) |

**Disposition:**
- **190635 (winner):** `status=closed` via Bug 21 terminal-close (fired 2026-05-29 20:01 UTC; logged `mirror_redemption_pending`). The ~2.15 winning T1 tokens are **redeemable on-chain** — operator action pending per `OPERATOR_GUARDIANS_REDEMPTION.md`. No bot money movement.
- **190636 + 190643 (losers):** `status='closed'` set manually S234 (`UPDATE positions SET status='closed' WHERE id IN (190636,190643) AND is_paper=false AND coalesce(source_bot,bot_id)='MirrorBot'` → `UPDATE 2`) after on-chain verification confirmed both held tokens are losing outcomes (CLOB `winner=False, price=0`). Bug 21 could not auto-close them: the `polymarket_client.CircuitBreaker` masked their terminal CLOB rejections as "inconclusive", so the terminal classifier (`mirror_bot.py:1688`) never ran. Worthless losing tokens remain dormant on-chain (same disposition as the S230 losers above; no redemption value). No money moved.

**Structural fix** for the auto-close gap (a lifecycle state-check that runs *before* the SELL path AND the Bug 11C balance guard) is tracked in `WORK_PROGRAM.md` — position lifecycle module + `polymarket_client.CircuitBreaker` fix.

---

## 2026-06-10 update — gap window 06-01→06-02 closed + fresh on-chain verification

**Balance trail extension (journalctl probes, mirror):**

| Observed (UTC) | Balance | Note |
|---|---|---|
| 2026-06-01 18:57 – 06-02 05:21 | $4.26 → $0.32 | **5 live BUYs + 2 live SELLs** (order log below). Net out ≈ $3.95. |
| 2026-06-02 23:57 | **$0.31782** | Last system_kv write (`deposit_wallet_balance_pusd`). |
| 2026-06-10 14:17 (journal probe, live) | **$0.31782** | UNCHANGED for 8 days — zero trading activity since 06-02 05:19 (confirmed by `bot_pnl.py --mode live 168`: 0 entries/exits/resolutions). Balance is current, not stale; the system_kv WRITE path stopped updating after 06-02 (probe itself alive in journal — minor WI-11 follow-up). |

**Live orders 06-01→06-02 (journalctl "Order placed", live mode; fills evidenced by "matched orders can't be canceled" / recon EXITED status):**

| UTC | Side | Size@Price | ~pUSD | Market | Outcome today (recon 06-10) |
|---|---|---|---|---|---|
| 06-01 18:57 | BUY YES | 1.98@0.51 | −1.01 | `0x5c19f2…` | exited 06-02 (SELL below) |
| 06-01 19:12 | BUY NO | 3.92@0.26 | −1.02 | `0x65683e…` | **WON — $3.92 redeemable (item #1 of the $18.82)** |
| 06-01 19:22 | BUY YES | 1.87@0.54 | −1.01 | `0x9188ea…` | LOST |
| 06-02 03:40 | SELL | 6.06@0.15 | +0.91 | `0xd86a81…` | exited |
| 06-02 04:08 | BUY YES | 5.05@0.20 | −1.01 | `0xdbe93b…` | **the 1 currently-open position** |
| 06-02 04:56 | SELL | 1.98@0.46 | +0.91 | `0x5c19f2…` | exited |
| 06-02 05:19 | BUY NO | 2.53@0.40 | −1.01 | `0x2340fa…` | LOST (resolved after 06-03 baseline) |

Order-log net ≈ −$3.24 vs probe drop −$3.95: residual ~$0.71 is fill-price/fee variance + possibly the 05-31 208932 entry landing after the last 05-31 probe — exact per-fill amounts need CLOB fill records or Polygonscan (the standing phase-b gap). Within the same tolerance the 05-28 window was accepted at.

**Fresh on-chain reconciliation (2026-06-10, `reconcile_live_onchain.py`, read-only):** 57 live positions (unchanged — ZERO new since 06-03). Outcomes: WIN=13 LOSS=32 (was 31; `0x2340fa` NO resolved against us) EXITED=5 OPEN=7. **$18.82 in winning tokens STILL unredeemed on-chain — identical per-token balances to the 06-03 baseline. The operator redemption (recon §1) has NOT been done; the S234 T1 winner is now ~16 days unredeemed.**

**Positions-row note:** the 06-01/06-02 entries have live ENTRY trade_events (cost basis in recon) but `positions` shows no new rows with `created_at > 05-31` — row-provenance gap of the known S238 phantom/lifecycle family; does not affect the wallet arithmetic above (probes + order log + chain are the money truth).

### Wallet-cash accounting as of 2026-06-10 (source-cited per line)

**This is WALLET-CASH ARITHMETIC, NOT bot-recorded trading P&L.** `bot_pnl.py` cannot produce these figures: the live trade_events ledger is structurally incomplete for historical live data (0 live RESOLUTION events ever; cost basis cleared on close — `LIVE_ONCHAIN_RECONCILIATION_2026-06-03.md` §2). Per the standing rule (recon doc §8), chain is canonical for live positions; this table is the doc-of-record for these figures, each line carrying its own source.

| Line | Amount | Source |
|---|---|---|
| Total pUSD in (deposits + redemption) | $26.83 = 5.00 + 20.00 + 1.83 | Money Movement Log above (operator deposits + 05-26 redemption row) |
| Liquid pUSD now | $0.31782 | journal probe 2026-06-10 14:17 UTC (= system_kv 06-02 value, unchanged) |
| Winning tokens on-chain, unredeemed | $18.82 | `reconcile_live_onchain.py` 2026-06-10 run (CTF `balanceOf` + CLOB winner; identical to 06-03 baseline §1) |
| Open-position cost | ~$1.01 | `bot_pnl.py MirrorBot --mode live` 2026-06-10 (1 open, $1.01 cost) — matches recon `0xdbe93b` cost basis |
| **Recoverable today** | **$20.15** = 0.32 + 18.82 + 1.01 | arithmetic over the three sourced lines above |
| **Net wallet-cash drawdown** | **≈ −$6.68** = 26.83 − 20.15 | arithmetic; pending the open position's outcome; excludes idle USDC.e ($20.00 deposit-wallet + $15.99 EOA, never bot bankroll) |
| Outcome tallies (57 live position-rows) | WIN=13 LOSS=32 EXITED=5 OPEN=7 | `reconcile_live_onchain.py` 2026-06-10 run (chain+CLOB outcome per row; NOT a bot_pnl.py figure — bot_pnl's live ledger cannot compute these, see header note) |

Known residuals: ~$0.71 fill-price/fee variance in the 06-01→06-02 window (needs CLOB fill records or Polygonscan); 44/57 rows have no internal cost basis (recon §5) so per-position dollar P&L is not internally computable — the wallet-cash view above is the reliable aggregate.

## How this ledger is maintained

1. **Every MB session** that touches the deposit wallet, the EOA, or any pUSD/USDC.e/CTF amount must add a row to the relevant section.
2. **Inbound entries** require: tx hash (or "older than retention" with first-observed timestamp), source label, approver.
3. **Outbound entries** require: tx hash or CLOB order_id, destination, approver, trade context.
4. **Pending entries** stay in the "Pending" section until either approved-and-executed (move to Outbound) or rejected (annotate inline).
5. **On-chain probes** done as part of session verification are recorded in the "Current state" section, replacing the previous snapshot.
6. **The bot's own probe** (`deposit_wallet_balance_pusd` log event) is the canonical pUSD balance signal until/unless the operator funds a direct on-chain query path with an API key.

## Trace gaps to close (operator-actionable)

1. **Polygonscan / Etherscan V2 API key** in `/opt/pa2-shared/.env` as `POLYGONSCAN_API_KEY=...` — unlocks unlimited historical tx pulls. Free key from https://polygonscan.com/myapikey. Adding this lets MB sessions trace deposits older than ~28h on demand.
2. **Tx hashes for the original $20 USDC.e and $16 USDC.e deposits** would close the "(unknown) origin" rows above. Operator can find these in Polymarket UI deposit history or in their own wallet's tx log.
3. **Guardians redemption** — operator redeems via polymarket.com UI per `OPERATOR_GUARDIANS_REDEMPTION.md`. After redemption, MB session updates this ledger with redemption tx hash + new pUSD balance.
4. **$20 USDC.e discrepancy (S244 2026-06-11)** — S235 snapshot read $20 USDC.e at the deposit wallet; today it reads $0.00 (two RPCs) with no corresponding pUSD rise. Leading hypothesis: double-count with the 05-26 $20 deposit (the "Confirm pending deposit" click converted it). Resolve via deposit-wallet tx history (needs API key, gap #1) or operator's Polymarket UI deposit/withdrawal history. Full note in "Current state" above.

## DB resolution-backfill drift (separate concern)

As of 2026-05-26, the `markets` table in the local DB shows `resolved=false` for all 4 condition_ids, but `CTF.payoutDenominator()` returns 1 (resolved) for all 4 on-chain. The bot's resolution backfill (`base_engine.data.database.py` Phase 4b) has not picked up these resolutions yet — separate from the wallet-ledger concern, but worth flagging for next code session. The 4 positions also show `status='closed'` in `positions` table because of Bug 12 paper-simulation, which incidentally matches outcome for the 3 losers but mis-reports the Guardians position by an amount equal to the payout differential.
