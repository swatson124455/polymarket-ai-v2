# MirrorBot Live P&L — On-Chain Reconciliation (Calibration Baseline)

**Date:** 2026-06-03 · **Source of record:** on-chain (CTF `balanceOf` on Polygon) + Polymarket CLOB resolution. **Independent of the broken `trade_events` ledger and of `bot_pnl.py`.**

> **Canonical-by-construction:** for live positions, the chain is ground truth; `positions`/`trade_events`/`bot_pnl.py` are *derived* data that has been proven to diverge. Verify against chain, then DB, then bot_pnl last.

Reproduce: `scripts/reconcile_live_onchain.py` (read-only) on the VPS — reads CLOB resolution + CTF balance per live position.

---

## 0. TL;DR
The live ledger has been **disconnected from reality for ~77% of live trades** (44 of 57 position-rows have no recorded cost basis). The trades are **real** (the wallet holds the tokens on-chain); the ledger simply never recorded them. **$18.82 in winning tokens sits unclaimed on-chain**, including the S234 "T1 winner" (~9 days unredeemed). A complete historical realized-P&L dollar figure is **unrecoverable from internal data** without on-chain USDC tx tracing (Polygonscan).

---

## 1. IMMEDIATE — Redemption list (7 winners, $18.82 redeemable NOW)

Deposit wallet holds these winning ERC1155 tokens, resolved, unredeemed. Redeem via Polymarket portfolio with the deposit wallet (see `OPERATOR_GUARDIANS_REDEMPTION.md`).

| # | Market (condition_id) | Side | Redeemable (pUSD) | Slug / market |
|---|---|---|---|---|
| 1 | `0x65683ee510831e9c8162925451f6de35c1dc89987297b25fe7f109330be38b7d` | NO | 3.92 | cs2-mibr-thunde-2026-06-02 (MIBR vs THUNDER) |
| 2 | `0x611444b486e45275ed18ab1c6206f6bc9f15fde753d9f4ae2b964c27dd54c7ef` | YES | 3.07 | nba-okc-sas-…-spread-home-8pt5 (Spurs -8.5) |
| 3 | `0x44c16e2267e59af61ab5f69d79cadd1d68fa26cc084c82101bac86e9d8c5aa13` | NO | 2.66 | cs2-tri9-justpl-2026-05-28 (Tricked vs Just Players) |
| 4 | `0xce92a0164dd224f2edf3f18764fe3a23ab6a80ce32674c0add500f9825fd3f06` | NO | 2.59 | cs2-kol-zerote-2026-05-28 (KOLESIE vs ex-Zero Tenacity) |
| 5 | `0x6869dd21e8ca7c8f61cfd1c24bb95bb9325b6b8147ad3b1bfc276e8184f11e65` | YES | 2.53 | atp-arnaldi-tsitsip-2026-05-28 (Arnaldi vs Tsitsipas) |
| 6 | `0x9bce195835d89ff1786d56e8760a7c739b5b7ff2f7c73e1e6a57a893b397bb12` | YES | 2.15 | lol-kt-t1-…-game-handicap-home-1pt5 (T1 -1.5 vs KT) — **S234 carryover, ~9d unredeemed** |
| 7 | `0x844f4f3fd12eccb5ffc809c4b22455378525e88773e0d5f7190b448049d24b57` | NO | 1.90 | mlb-phi-lad-2026-05-29 (Phillies vs Dodgers) |
| | | **Total** | **18.82** | |

token_ids (for direct CTF `redeemPositions`): 1=`75757…622643`, 2=`99977…918966`, 3=`48102…891693`, 4=`78626…974376`, 5=`67125…976870`, 6=`55362…520205`, 7=`9142…230823` (full IDs in §4).

---

## 2. The four structural proofs (DB-direct, definitive)

1. **`trade_events` RESOLUTION (MirrorBot): 7,301 paper / 0 live.** Zero live resolutions ever recorded — live resolution P&L is structurally unreadable.
2. **25 of 46 live position-markets have no ENTRY event.** Phantom positions: exist in `positions`, never emitted an ENTRY trade_event.
3. **9 resolved live markets contribute $0** to live realized P&L (because of #1).
4. **`entry_cost` cleared on close** — 45 closed live markets sum to ≈$0.85 entry_cost (essentially zeroed). Cost basis lost for closed positions.

Full event/mode matrix (MirrorBot `trade_events`): ENTRY live=13 / paper=11,768 · EXIT live=5 / paper=3,643 · RESOLUTION live=0 / paper=7,301.

---

## 3. Methodology (reproducible)
For each distinct live `(market_id, side)` in `positions` (`is_paper=false`, MirrorBot):
1. CLOB `markets/{condition_id}` → `closed?` + winner (numeric token prices / winner flag).
2. CTF `balanceOf(deposit_wallet, token_id)` via Polygon RPC (`clob_adapter.check_ctf_balance`).
3. Outcome: held side vs winner → WIN/LOSS; EXITED if a live EXIT event exists; OPEN if unresolved.
4. Cost basis: from live ENTRY `trade_event` (size×price) if present; else MISSING.

---

## 4. Full 57-position reconciliation

```
market           side stat   resolved winner outcome  onchain_bal cost_basis
0x053930d0a8a8fa NO  closed True  NO   WIN           0.000 MISSING
0x053930d0a8a8fa SELL closed True  NO   LOSS          0.000 MISSING
0x090297e9749a22 SELL closed True  YES  LOSS          0.000 MISSING
0x0a931d96b40bb6 YES closed True  NO   LOSS          8.690 MISSING
0x0e77b1ad3dc69c SELL closed False None OPEN          0.000 MISSING
0x120b15af9f5f44 SELL closed True  YES  LOSS          0.000 MISSING
0x13c91c7a00b48c YES closed True  NO   LOSS          1.520 MISSING
0x2340fa159af03d NO  closed False None OPEN          0.000 $1.013
0x2340fa159af03d SELL closed False None EXITED        0.000 MISSING
0x2bf0a2af5ad593 NO  closed True  YES  LOSS          1.800 MISSING
0x3381d22e66fc67 NO  closed True  YES  LOSS          2.980 $1.015
0x3869f115d4e8b7 NO  closed True  YES  LOSS          1.800 $1.009
0x44c16e2267e59a NO  closed True  NO   WIN           2.660 MISSING
0x462cea17704715 YES closed True  NO   LOSS          0.000 MISSING
0x481e603aa927b4 SELL closed False None OPEN          0.000 MISSING
0x4e4f77e7dbf4ca SELL closed False None OPEN          0.000 MISSING
0x50afaf865f377a SELL closed True  NO   EXITED        0.000 MISSING
0x50afaf865f377a YES closed True  NO   LOSS          0.000 $1.009
0x58551814773fbb YES closed True  NO   LOSS          1.270 MISSING
0x5c19f205507ce0 SELL closed False None EXITED        0.000 MISSING
0x5c19f205507ce0 YES closed False None OPEN          0.000 $1.010
0x611444b486e452 YES closed True  YES  WIN           3.070 MISSING
0x65683ee510831e NO  closed True  NO   WIN           3.920 $1.020
0x668c361e70bab3 SELL closed True  NO   LOSS          0.000 MISSING
0x668c361e70bab3 YES closed True  NO   LOSS          0.000 MISSING
0x6869dd21e8ca7c YES closed True  YES  WIN           2.530 MISSING
0x713641f745d71f NO  closed False None OPEN          1.390 MISSING
0x76ee74212caead YES closed True  NO   LOSS          3.770 MISSING
0x78a736e934d4e8 YES closed True  NO   LOSS          3.170 MISSING
0x7da2328022bf3b SELL closed True  NO   LOSS          0.000 MISSING
0x844f4f3fd12ecc NO  closed True  NO   WIN           1.900 $1.010
0x8b369e10358094 NO  closed True  NO   WIN           0.000 $1.000
0x9188ea717e5239 YES closed True  NO   LOSS          1.860 $1.009
0x94139bb20e5d6a NO  closed True  YES  LOSS          2.190 MISSING
0x9a73341dde87ce YES closed True  NO   LOSS          1.980 MISSING
0x9bce195835d89f YES closed True  YES  WIN           2.150 MISSING
0x9d40a76fee6bf5 NO  closed True  YES  LOSS          2.150 MISSING
0x9d5bcba0e0375a SELL closed True  YES  LOSS          0.000 MISSING
0xb13083a7590728 YES closed True  NO   LOSS          1.900 MISSING
0xbd1f142f4429d2 NO  closed True  NO   WIN           0.000 MISSING
0xbd1f142f4429d2 SELL closed True  NO   LOSS          0.000 MISSING
0xbe35053975d6bf NO  closed True  NO   WIN           0.000 MISSING
0xbf8a20565edd1c YES closed True  YES  WIN           0.000 MISSING
0xcca44537d5cbb2 NO  closed True  YES  LOSS          0.000 MISSING
0xcca44537d5cbb2 SELL closed True  YES  LOSS          0.000 MISSING
0xce92a0164dd224 NO  closed True  NO   WIN           2.590 MISSING
0xd49bf898535928 NO  closed True  YES  LOSS          2.150 $1.011
0xd86a816093fcd0 SELL closed False None EXITED        0.000 MISSING
0xd86a816093fcd0 YES closed False None OPEN          0.000 $1.030
0xdbe93b5a701f36 YES open   False None OPEN          0.000 $1.010
0xe35304decf0479 SELL closed True  NO   EXITED        0.000 MISSING
0xe35304decf0479 YES closed True  NO   LOSS          0.000 $1.009
0xfcef33fa13f427 YES closed True  NO   LOSS          2.530 MISSING
0xfe4fae85647837 NO  closed True  NO   WIN           0.000 MISSING
0xfe4fae85647837 SELL closed True  NO   LOSS          0.000 MISSING
0xff2108973744b9 NO  closed True  YES  LOSS          0.000 MISSING
0xff2108973744b9 SELL closed True  YES  LOSS          0.000 MISSING
```
Summary: 57 rows · EXITED=5 WIN=13 LOSS=31 OPEN=8 · cost-basis present=13 / MISSING=44 · on-chain redeemable=18.82.

---

## 5. Hard limit on complete historical P&L
44 of 57 rows have **no cost basis** in the DB (`entry_cost` cleared on close + no ENTRY event). Realized P&L for those is **not computable internally**. Recovery requires:
1. Polygonscan API key → enumerate USDC transfers from the deposit wallet.
2. Match each to a position entry (timestamp + amount + counterparty) → synthetic ENTRY.
3. Realized P&L = on-chain outcome (verified, §4) − reconstructed entry cost.
~200–400 LOC + the key (~$50/mo). **Operator decision:** outcome-level reconciliation (win/loss known, §4) may suffice for "is the bot positive-EV"; dollar-magnitude questions ("how much have I lost," "add capital?") require the reconstruction.

---

## 6. Integrity issues surfaced
- **18 of 57 rows are `side='SELL'`** (live MB). SELL is an order direction, not a position side — corrupted/duplicate rows inflating the count. Real YES/NO positions = 39 (YES=20, NO=19). WI-8 added 4 CHECK constraints but **no `chk_positions_side IN ('YES','NO')`** — add it + clean the SELL rows.
- **6 of 13 wins hold 0 tokens on-chain** (0x053930, 0x8b369e, 0xbd1f14, 0xbe3505, 0xbf8a20, 0xfe4fae). Either already-redeemed (good — who/when?) or recorded-but-never-filled ("Bug 22" class). Per-case verification needed; if never-filled, WI-11 audit must check on-chain backing at entry time.

---

## 7. Follow-up work items
1. **Phase-1 code (go-forward correctness):** RESOLUTION emitter sets `execution_mode` from `is_paper`; WI-6 `_close_position_terminal` emits a P&L-bearing event; `bot_pnl.py` cross-references CLOB resolution + flags DB/CLOB mismatch. *Ship before further go-live work.*
2. **Redemption alerting:** no surface signal exists for "wallet holds redeemable winning tokens." Add an alert (the $18.82 sat unflagged ~9 days).
3. **SELL-row cleanup + `chk_positions_side` constraint.**
4. **Investigate the 6-of-13 zero-balance wins** (redeemed vs phantom-filled).
5. **Polygonscan decision** (§5) — gated on the operator's go-live decision framework.

---

## 8. Standing rule (record)
**On-chain is canonical-by-construction; DB/`bot_pnl.py` are derived and must be verified against chain.** When checking live P&L: chain first → DB cross-reference → bot_pnl last. This inverts the prior "bot_pnl canonical-by-convention" framing. Until Phase-1 lands, treat all live P&L from bot_pnl as unverified against chain.
