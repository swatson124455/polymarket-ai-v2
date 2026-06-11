# Redeem-and-Retrade Loop — Runbook

**What it does:** collects MirrorBot's resolved *winning* CTF tokens, converts the
USDC.e payout to pUSD (the V2 trading collateral), and refreshes the bot's
buying-power cache so it resumes trading. Closes the loop:
`open → resolve → redeem → convert → retrade`.

**Script:** [`scripts/redeem_and_retrade.py`](scripts/redeem_and_retrade.py)
**Timer units (recurring loop):** [`deploy/polymarket-redeem.service`](deploy/polymarket-redeem.service) + [`deploy/polymarket-redeem.timer`](deploy/polymarket-redeem.timer)

---

## The path (all on-chain-verified 2026-06-11, no Etherscan key needed)

S242 said this was blocked on an Etherscan API key to decode the deposit-wallet
proxy ABI. That was wrong — the contracts are **verified on Sourcify + Polygonscan**
(keyless):

| Piece | Address / fact |
|---|---|
| Deposit wallet (owner = our EOA) | `0xBB3988D74a853ddC16f22eEC52fa53E3Cedd2247`, impl `DepositWallet` `0x58ca…b1eb` |
| Owner / signer (our `PRIVATE_KEY`) | `0xd6a5e2d75fae67739749af380c54b0544878627f` |
| Factory (relayer entrypoint, `proxy()` is **OnlyOperator**) | `DepositWalletFactory` `0x00000000000Fb5C9ADea0298D729A0CB3823Cc07` |
| Relayer (the only execution path) | `POST https://relayer-v2.polymarket.com/submit` type `WALLET`, auth `RELAYER_API_KEY` (set) |
| CTF | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`, `redeemPositions(address,bytes32,bytes32,uint256[])` = `0x01b7037c` |
| Redeem collateral | **USDC.e** `0x2791…4174` (the 7 winners are USDC.e-collateralized — derived per-position via `getPositionId` match; pUSD would pay dust — the S242 canary's exact failure) |
| Convert | pUSD `CollateralToken` `0xC011…82DFB`, `wrap(address,address,uint256,address,bytes)` = `0xb97b57c7` |

**Why the relayer is the only path:** `factory.proxy()` reverts `OnlyOperator()`
(`0x27e1f1e5`) for any caller but the relayer. The EOA cannot broadcast the batch
itself even with MATIC. The relayer (an authorized operator) submits gaslessly.

**Signing:** EIP-712, domain `{name:"DepositWallet", version:"1", chainId:137,
verifyingContract: depositWallet}`, `Batch{wallet,nonce,deadline,calls[]}` /
`Call{target,value,data}`. Signed by the owner EOA. Verified: the signature
recovers to the owner, and `execute(batch,sig)` simulated from the factory returns
`0x` (success) for every winner.

---

## Manual operation

All commands run on the VPS (the `PRIVATE_KEY` lives only there). From a release dir:

```bash
REL=$(readlink -f /opt/polymarket-ai-v2); cd "$REL"
set -a; source "$REL/.env"; set +a
PY="$REL/venv/bin/python"; S="$REL/scripts/redeem_and_retrade.py"   # script ships in each release after deploy
```

**1. Dry-run (safe, no broadcast, simulates every redeem):**
```bash
PYTHONPATH="$REL" "$PY" "$S" --phase full          # default is dry-run
```
Look for `SIMULATION OK … all N redeems valid`.

**2. Execute the redeem (real money — collects your own winnings into your own wallet):**
```bash
PYTHONPATH="$REL" "$PY" "$S" --execute --phase redeem --min-usd 0.10
```
Watch for `relayer response: {transactionID…}` then `final: STATE_CONFIRMED`.
Verify on-chain: deposit-wallet USDC.e balance rises by the payout, CTF tokens burn to 0.

**3. Convert USDC.e → pUSD (run after redeem confirms; reads the landed balance):**
```bash
PYTHONPATH="$REL" "$PY" "$S" --execute --phase convert
```
If it prints "no USDC.e to convert" the redeem is still settling — wait ~30s and re-run.

**4. Full loop in one shot (redeem → convert → retrade refresh):**
```bash
PYTHONPATH="$REL" "$PY" "$S" --execute --phase full --min-usd 0.10
```

**Flags:** `--dry-run` (default) · `--execute` (broadcast) · `--phase {redeem,convert,full}` ·
`--min-usd N` (skip dust) · `--deadline-secs N` (batch deadline horizon).

---

## Recurring loop (the timer)

The committed units run the full loop every 6h (no-op when nothing has resolved-to-win).
They ship **INERT** — install + enable only with operator intent (enabling = standing
authorization for the recurring collection of your own winnings):

```bash
sudo cp /opt/polymarket-ai-v2/deploy/polymarket-redeem.service /etc/systemd/system/
sudo cp /opt/polymarket-ai-v2/deploy/polymarket-redeem.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-redeem.timer
systemctl list-timers polymarket-redeem.timer        # confirm scheduled
journalctl -u polymarket-redeem.service -f           # watch a cycle
```
Disable: `sudo systemctl disable --now polymarket-redeem.timer`.

---

## The one execute-time unknown

Whether `RELAYER_API_KEY` is authorized for `WALLET`-type batches (vs only CLOB
order relaying) can only be confirmed by submitting. The bot already uses this
relayer for CLOB orders, so it's very likely. If `/submit` returns 401/403, **no
money moves** (rejected before broadcast); the fallback is a Builder/Relayer key
scoped for WALLET txns, or manual UI redemption (`OPERATOR_GUARDIANS_REDEMPTION.md`).
Everything else (signature, collateral, calldata, redeem validity) is proven.

## Ledger

Every execute run that moves funds must be recorded in `WALLET_LEDGER.md`
(redemption tx hashes + new balances) per its money-movement rule.
