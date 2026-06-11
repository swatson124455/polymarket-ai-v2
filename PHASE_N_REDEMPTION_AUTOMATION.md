# Phase-N Enhancement — CTF Redemption Automation

**Filed:** 2026-05-26 (MB session S230)
**Status:** Discovery complete; implementation deferred pending trigger condition
**Priority:** LOW until trigger condition met (see below)
**Estimated effort:** 4–8 hours engineering once ABI is in hand; 1–3 days if reverse-engineering is needed

## Problem statement

When a live-trading position resolves on-chain, the bot's resolution-backfill at `base_engine/data/database.py:3556` (Phase 4b) updates the internal DB records but does **NOT** call `CTF.redeemPositions(...)` to recover the actual collateral. The winning-side CTF outcome tokens remain in the deposit wallet until the operator manually redeems via the Polymarket UI.

For S230's specific case: 4 phantom-closed positions on 2026-05-24, 1 of which won on-chain. Recovery currently requires a UI click. That's fine at current volume (single-digit redemptions/week). It becomes a bottleneck if any of:

- Live trading scales to multi-position resolution cadence
- Operator availability becomes scarce
- Audit-trail completeness (programmatic tx hashes for ledger) becomes a requirement
- We want a closed-loop "open → close → resolve → redeem → reconcile" automated flow

## Where we're stuck

The Polymarket V2 deposit wallet is a smart-contract proxy, not an EOA. To call `CTF.redeemPositions(collateral, parentCollectionId, conditionId, indexSets)` *from* the deposit wallet (which holds the CTF tokens), the EOA must invoke an executor method on the proxy. We don't know that executor's function signature without the proxy implementation's ABI.

## What S230 discovered (anchor data for next attempt)

### Deposit wallet structure

- **Address:** `0xBB3988D74a853ddC16f22eEC52fa53E3Cedd2247` (per `DEPOSIT_WALLET_ADDRESS` in `/opt/pa2-shared/.env`)
- **Type:** Smart contract proxy (125-byte custom bytecode)
- **Proxy pattern:** EIP-1967 — implementation stored at canonical slot `0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc5`
- **Implementation contract:** `0x58ca52ebe0dadfdf531cde7062e76746de4db1eb` (Polygon, 13.8 KB bytecode)
- **Hardcoded owner-EOA:** `0xd6a5e2d75fae67739749af380c54b0544878627f` (verified by reading trailing bytes of proxy bytecode — last 20 bytes after leading zero-padding)
- **EIP-1271 confirmed working:** `isValidSignature(bytes32,bytes)` selector `0x1626ba7e` responds (returned `0xffffffff` for dummy input, expected) — this is the POLY_1271 wallet pattern referenced in `base_engine/execution/clob_adapter.py:46-47` for `signature_type=3` orders.

### What does NOT work (already probed)

eth_call from EOA against the proxy with each selector returns identical custom error `0x3c10b94e`:

| Function signature | Selector | Result |
|---|---|---|
| `proxy(address,uint256,bytes)` | `0x10b85a2c` | revert `0x3c10b94e` |
| `execTransaction(address,uint256,bytes,uint8)` | `0x468721a7` | revert `0x3c10b94e` |
| `exec(address,uint256,bytes)` | `0x9c46d2a4` | revert `0x3c10b94e` |
| `execute(address,uint256,bytes,uint8)` | `0xb61d27f6` | revert `0x3c10b94e` |
| `execute((address,uint256,bytes,uint8)[])` | `0xa6cb9b8b` | revert `0x3c10b94e` |

Same revert code across all 5 selectors suggests either (a) none of those signatures exist on the impl and the fallback reverts, or (b) all 5 hit the same authorization check that fires before dispatch.

### Decoding hint (`0x3c10b94e`)

The custom error `0x3c10b94e` is the keccak256-first-4-bytes of some error signature. Decoding this string would identify whether it's `NotAuthorized()`, `InvalidSelector()`, `Paused()`, or something Polymarket-specific. A Polygonscan/Etherscan API key would let you query the verified contract's error signatures and decode this directly.

### What IS available in the codebase

- `py_clob_client_v2/order_utils/abi/abis.py` contains `_EXCHANGE_V1_ABI_JSON` only — the Exchange contract, NOT the proxy wallet.
- The Exchange ABI constructor signature reveals the wallet split: `Exchange(collateral, ctf, proxyFactory, safeFactory)` — Polymarket V2 supports two wallet types: PolyProxy and Gnosis Safe. The deposit wallet here matches the PolyProxy pattern (custom 125-byte bytecode), not Gnosis Safe.

## ⚠ 2026-06-10 CANARY — factory route REDEEMS THE WRONG PROXY (ABI wall confirmed real)

Operator authorized a live canary to test programmatic redemption. Result: **the ABI wall stands; the "wall broken" claim was premature.**

- **Tried:** EOA → `ProxyWalletFactory(0xaB45…4052).proxy([{typeCode:1, to:CTF, value:0, data:redeemPositions(USDC.e,0,conditionId,[1,2])}])`. `eth_call` AND `estimate_gas` both passed (209,861 gas) — but **passing only means "no revert," NOT "redeems."**
- **Broadcast `0x1b25ebb284e8171ddb8442dbd36bc7f827d3c8d90d5b06176521d6beb10dca46`** (block 88273725, status=1, gasUsed 207,050, ~$0.01 gas). **PayoutRedemption fired but payout = 0.000002 (dust); no token burn, no USDC.e transfer.**
- **ROOT CAUSE (decoded):** `redeemer = 0xB9Bd3FAfF25F3a2b31A7e0b2f6ed56F64E989b7a` — the EOA's OWN ProxyWalletFactory proxy, which holds 0 of the winning token. Funds live in the Polymarket deposit wallet `0xBB39…2247` (separate relayer-provisioned proxy, impl `0x58ca…b1eb`); the factory route does NOT drive it. The EOA controls TWO proxies; `factory.proxy()` actuates the empty one.
- **No harm:** `0xBB39…2247` still holds 3.92 MIBR token + all $18.82 (re-verified post-tx). Dust + ~$0.01 gas only.
- **Corrected:** to redeem the deposit wallet's tokens programmatically, make `0xBB39…2247` itself the CTF caller → needs that proxy's executor ABI (impl `0x58ca…b1eb`) + decode custom error `0x3c10b94e`. The original wall. The bot can TRADE (CLOB orders settled by relayer via EIP-1271 sig_type=3) but has NO signed path for redemption.
- **Secondary bug — ⚠ CORRECTED 2026-06-11 (WI-24 verification):** the original claim here ("real collateral is USDC.e; `0xC011` is NOT the collateral; repoint the probe") was **REFUTED** by direct on-chain verification. `0xC011a7E1…82DFB` IS the V2 trading collateral: it is symbol=`pUSD` name="Polymarket USD" (6 decimals), and `getCollateral()` on BOTH V2 exchanges (Exchange `0xE111…996B` AND NegRiskExchange `0xe2222d279d…0F59`) returns `0xC011`. The USDC.e conclusion came from the V1/CTF layer: V1 Exchange `0x4bFb…982E` and the CTF position-ID math ARE USDC.e-denominated — so **redemptions pay USDC.e, but trading debits pUSD**. Two-token model, both halves true. History corroborates: the operator's 05-26 $20 UI deposit landed as pUSD and was consumed by live trades; deposit wallet today = $0.31782 pUSD / $0.00 USDC.e. **Corrected fix (shipped 2026-06-11):** keep `check_pusd_balance` as the buying-power probe; ADD a deposit-wallet USDC.e probe in `base_engine.start()` (`deposit_wallet_balance_usdce` log + system_kv + `redeemed_funds_awaiting_conversion` WARNING) so redeemed USDC.e is visible. **Open empirical question (test after first real redemption):** whether Polymarket's relayer auto-converts/credits deposit-wallet USDC.e as buying power, or a manual conversion step is needed — check CLOB `/balance-allowance` once USDC.e lands.

**Unblock now needs the impl ABI — Path 1 below (Polygonscan/Etherscan key). Until then, manual UI redemption is the only reliable recovery (`OPERATOR_GUARDIANS_REDEMPTION.md`).** Do NOT blind-fire more executor-signature guesses against a funds-holding proxy.

### 2026-06-10 impl bytecode decode (no key — partial)
`eth_getCode(0x58ca…b1eb)` = 13,804 B, ~70 PUSH4 candidates (some are string-data false positives). The impl is a **UUPS-upgradeable, Ownable, Pausable, EIP-1271 (`isValidSignature` 0x1626ba7e), ERC721/1155-receiver Polymarket proxy** — NOT a Gnosis Safe (no `execTransaction` 0x6a761202). Selectors resolved via 4byte (needs a browser `User-Agent`; default urllib UA is 403'd; openchain 500'd):
- `0x44004cc1 withdrawERC20(address,address,uint256)` ← the **post-redeem withdraw path** (move USDC.e out of the proxy after redemption)
- `0x7ae26773 revokeAllowance(address,address)`, `0x5325ad3d authorizedImplementation(address)`, `0xc987336c upgrade(address,bytes)`, `0xeef09bad timelockDelay()`, `0xaf640d0f id()`, `0x90b8ec18 TransferFailed()` (error)
- **~30 proprietary selectors UNRESOLVED** (not in 4byte/openchain) — the redeem/executor path is among these; bytecode-only naming is the multi-day route and unsafe to brute-force against a funds-holding proxy.
**CONCLUSION: the build is gated on Path 1 (the verified source via an API key).** With the key, `getsourcecode(0x58ca…b1eb)` gives exact function names → identify the redeem executor → build → ONE canary → batch. `withdrawERC20` is already identified for the conversion/withdraw step. **Operator action: get a free Etherscan V2 key, set `ETHERSCAN_API_KEY=` in `/opt/pa2-shared/.env`; then this build completes.**

## Three paths to unblock

### Path 1 — Get the impl ABI (fastest if it works)

1. Get a free Etherscan V2 API key from https://etherscan.io/myapikey
2. Set `ETHERSCAN_API_KEY=...` in `/opt/pa2-shared/.env`
3. Query: `GET https://api.etherscan.io/v2/api?chainid=137&module=contract&action=getsourcecode&address=0x58ca52ebe0dadfdf531cde7062e76746de4db1eb&apikey=...`
4. If verified, the response includes the full ABI. Identify the executor function (likely `proxy(...)` with a Polymarket-specific signature) and the error definitions (decode `0x3c10b94e`).
5. Build the redemption tx: ABI-encode `CTF.redeemPositions(0xPUSDcollateral, 0x00, conditionId, [1])` as `data`, wrap in proxy.executor(CTF_addr, 0, data), sign with EOA, submit.

**Likelihood of success:** High if Polymarket has verified their proxy impl. Most production proxy impls are verified.

### Path 2 — Polymarket GitHub for ABI

Check Polymarket's public repos for the proxy wallet ABI:
- `polymarket/poly-proxy-wallet` (if exists)
- `polymarket/exchange-fpmm`
- `polymarket/agents-py`
- Their developer docs at https://docs.polymarket.com

The ABI may be published in JSON form. Failing that, the Solidity source may be readable.

### Path 3 — Polymarket's gasless relayer endpoint

The Polymarket UI uses a gasless meta-transaction relayer behind the scenes. There's likely a REST endpoint (probably on `*.polymarket.com`) that accepts signed redemption payloads and submits them on-chain via their relayer's MATIC. Finding that endpoint requires:
- Polymarket developer docs
- Browser DevTools captured during a real UI redemption (network tab → see the POST request)

This is the cleanest production path since it matches Polymarket's intended developer flow.

## Decision criteria — when to actually build this

Build when ANY of:
- ≥5 redemptions per week for 2 consecutive weeks (volume threshold)
- Operator unavailable for 24h windows that would block redemption cycles
- Live trading P&L reconciliation requires programmatic tx-hash capture
- An audit requires complete closed-loop "open → resolve → redeem → ledger" traceability

Until then: continue manual UI redemption per `OPERATOR_GUARDIANS_REDEMPTION.md`-style docs. Each redemption: log into polymarket.com, click Redeem, drop tx hash into ledger.

## Suggested next-session entry point

1. Read this doc + `OPERATOR_GUARDIANS_REDEMPTION.md` + the relevant section of `WALLET_LEDGER.md`
2. Acquire Etherscan V2 API key (Path 1)
3. Decode the impl ABI, identify executor + error catalog
4. Write a small test script: dry-run a redemption via `eth_call` (no gas) against a known-resolved position to confirm the calldata structure works
5. Once dry-run succeeds, execute one live redemption with operator approval per WALLET_LEDGER protocol
6. Generalize into a `scripts/redeem_resolved_positions.py` that batches through `positions` where `status='closed' AND has_onchain_balance > 0`
7. Optional Phase-N+1: integrate into `database.py:Phase 4b` resolution-backfill so redemption fires automatically when a position resolves

## Why not just reverse-engineer the bytecode now

13.8 KB of unverified Solidity-compiled bytecode is well within reverse-engineering scope using tools like Panoramix or Etherscan's decompiler view, but doing so without verified source and signing real-money transactions against guessed function signatures is the kind of decision that should be made deliberately (not improvised mid-session) when the value-at-stake or the volume justifies the eng cost. We're not at that threshold.

## Related session context

S230 MB session (2026-05-25→26):
- Original task: unwind 4 phantom CTF positions
- Discovery: 3 of 4 had already resolved as losing tickets (zero recoverable), 1 of 4 (Cleveland Guardians position #187438) had resolved as winning ticket (recoverable via redemption)
- Operator approved programmatic redemption path
- Discovery walked through the layers above; hit ABI wall
- No on-chain transactions signed, no money moved this session
- Filed this doc + left the Guardians position pending UI redemption per `OPERATOR_GUARDIANS_REDEMPTION.md`

## References (for next session)

- This repo:
  - `WALLET_LEDGER.md` (master root) — money-movement ledger
  - `OPERATOR_GUARDIANS_REDEMPTION.md` (master root) — UI redemption instructions
  - `base_engine/execution/clob_adapter.py:46-47` — signature_type=3 / POLY_1271 reference
  - `base_engine/data/database.py:3556` — Phase 4b resolution-backfill (DB-only; doesn't redeem)
- External:
  - https://docs.polymarket.com — developer docs
  - https://etherscan.io/v2-migration — Etherscan V2 API onboarding
  - https://polygonscan.com/address/0x58ca52ebe0dadfdf531cde7062e76746de4db1eb — proxy impl (would show verified source if available)
