#!/usr/bin/env python3
"""Redeem resolved winning MirrorBot positions, convert the payout to pUSD, and
free it as buying power so the bot retrades — the closed loop.

Pipeline per run:
  1. RECONCILE  — read-only on-chain detection of resolved WINNING live positions
                  that still hold CTF tokens (same logic as reconcile_live_onchain.py).
  2. REDEEM     — CTF.redeemPositions(collateral, 0x0, conditionId, [indexSet]) for
                  each winner, wrapped in a DepositWallet EIP-712 Batch, submitted
                  gaslessly via the Polymarket relayer. Payout lands in the deposit
                  wallet AS THE COLLATERAL THE TOKEN WAS MINTED AGAINST.
                  NEG-RISK winners (minted against the NegRisk wrapped collateral —
                  vanilla CTF.redeemPositions can't redeem them, so they were silently
                  skipped and the loop recovered $0) instead go via
                  NegRiskAdapter.redeemPositions(conditionId, amounts), which redeems
                  AND unwraps the payout to USDC.e in one call (deposit wallet is
                  pre-approved as the adapter's ERC1155 operator).
  3. CONVERT    — if the redeemed collateral is USDC.e (older markets), approve +
                  pUSD.wrap(USDC.e, depositWallet, amount) so it becomes pUSD, the
                  V2 trading collateral. (pUSD-collateralized markets skip this.)
  4. RETRADE    — refresh the CLOB balance/allowance cache so MirrorBot sees the new
                  pUSD as buying power on its next scan. The bot is already live; it
                  resumes on its own once buying power exists.

SAFETY — this moves REAL MONEY when --execute is passed.
  * Default mode is --dry-run: builds calldata + the real EIP-712 signature, then
    SIMULATES the redeem via eth_call against the factory (no broadcast, no state
    change) and prints exactly what would happen. Safe to run anytime.
  * --execute actually POSTs to the relayer. Per WALLET_LEDGER.md money-movement
    rule, only run --execute with explicit operator approval. Redeeming your own
    confirmed-winning tokens is non-discretionary (collecting winnings), so the
    operator may pre-authorize the recurring loop; that authorization is theirs to
    give, not this script's to assume.

Collateral correctness (the S242 dust lesson): the CTF positionId is a function of
the collateral token. Redeeming with the wrong collateral computes a zero-balance
positionId and pays nothing. This script DERIVES the collateral per position by
matching the held token_id against getPositionId(collateral, collectionId) for both
pUSD and USDC.e — it never guesses.

Env (from /opt/pa2-shared/.env): PRIVATE_KEY, DEPOSIT_WALLET_ADDRESS, WALLET_ADDRESS,
POLYGON_RPC, RELAYER_API_KEY.
"""
import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")

from eth_abi import encode as abi_encode  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_utils import keccak, to_checksum_address  # noqa: E402

# --- Contract addresses (Polygon, chainid 137) ---
CTF = to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")          # ConditionalTokens
PUSD = to_checksum_address("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")         # CollateralToken (pUSD) — V2 trading collateral
USDCE = to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")        # USDC.e — CTF/redemption collateral
FACTORY = to_checksum_address("0x00000000000Fb5C9ADea0298D729A0CB3823Cc07")      # DepositWalletFactory (relayer entrypoint)
ONRAMP = to_checksum_address("0x93070a847efEf7F70739046A929D47a521F5B8ee")       # Permissionless Collateral Onramp (USDC.e -> pUSD)
NEGRISK_ADAPTER = to_checksum_address("0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296")  # NegRiskAdapter (redeems neg-risk CTF -> USDC.e)
NEGRISK_WCOL = to_checksum_address("0x3A3BD7bb9528E159577F7C2e685CC81A765002E2")   # NegRisk wrapped collateral (adapter.wcol(); col()=USDC.e)
ZERO_ADDR = "0x" + "0" * 40
ZERO_B32 = "0x" + "0" * 64

RELAYER = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137
HTTP_HDRS = {
    "Content-Type": "application/json",
    # Public Polygon RPCs 403 the default urllib UA; a browser UA is required.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
}

# --- Function selectors (keccak-4) ---
SEL_REDEEM = "0x" + keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4].hex()  # 0x01b7037c
SEL_APPROVE = "0x" + keccak(text="approve(address,uint256)")[:4].hex()                            # 0x095ea7b3
# Conversion goes through the Collateral Onramp's 3-arg wrap (0x62355638), NOT the pUSD
# token's own wrap — the deposit wallet isn't a direct pUSD wrapper, and the relayer blocks
# wrap() on the collateral token. The onramp IS an authorized wrapper and is permissionless.
SEL_ONRAMP_WRAP = "0x" + keccak(text="wrap(address,address,uint256)")[:4].hex()                   # 0x62355638
SEL_GETCOLLID = "0x" + keccak(text="getCollectionId(bytes32,bytes32,uint256)")[:4].hex()
SEL_GETPOSID = "0x" + keccak(text="getPositionId(address,bytes32)")[:4].hex()
SEL_BALANCEOF20 = "0x70a08231"  # ERC20 balanceOf(address)
SEL_NR_REDEEM = "0x" + keccak(text="redeemPositions(bytes32,uint256[])")[:4].hex()  # NegRiskAdapter.redeemPositions
SEL_BALANCEOF1155 = "0x" + keccak(text="balanceOf(address,uint256)")[:4].hex()      # CTF ERC1155 balanceOf

# DepositWallet EIP-712 typed-data (domain name "DepositWallet", version "1").
EIP712_TYPES = {
    "Call": [
        {"name": "target", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
    ],
    "Batch": [
        {"name": "wallet", "type": "address"},
        {"name": "nonce", "type": "uint256"},
        {"name": "deadline", "type": "uint256"},
        {"name": "calls", "type": "Call[]"},
    ],
}


def _rpc():
    return (os.environ.get("POLYGON_RPC") or os.environ.get("POLYGON_RPC_URL") or "").strip()


def eth_call(to, data):
    return eth_call_from(None, to, data)


def eth_call_from(frm, to, data):
    params = {"to": to, "data": data}
    if frm:
        params["from"] = to_checksum_address(frm)
    body = json.dumps({"jsonrpc": "2.0", "method": "eth_call",
                       "params": [params, "latest"], "id": 1}).encode()
    req = urllib.request.Request(_rpc(), data=body, headers=HTTP_HDRS)
    j = json.load(urllib.request.urlopen(req, timeout=20))
    if "error" in j:
        return {"error": j["error"]}
    return {"result": j.get("result", "0x")}


def _enc_b32(hexstr):
    return bytes.fromhex(hexstr.lower().replace("0x", "").rjust(64, "0"))


def derive_collateral(condition_id, index_set, held_token_id):
    """Return the collateral address whose positionId matches the held token, or None.

    Guards against the S242 dust failure: redeeming with the wrong collateral pays $0.
    """
    cid = eth_call(CTF, SEL_GETCOLLID + _enc_b32(ZERO_B32).hex()
                   + _enc_b32(condition_id).hex() + hex(index_set)[2:].rjust(64, "0"))
    if "error" in cid:
        return None
    coll_id = cid["result"]
    for coll in (USDCE, PUSD):
        pos = eth_call(CTF, SEL_GETPOSID + coll.lower().replace("0x", "").rjust(64, "0")
                       + coll_id.lower().replace("0x", "").rjust(64, "0"))
        if "error" in pos:
            continue
        if int(pos["result"], 16) == int(held_token_id):
            return coll
    return None


def redeem_calldata(collateral, condition_id, index_set):
    args = abi_encode(["address", "bytes32", "bytes32", "uint256[]"],
                      [collateral, bytes.fromhex(ZERO_B32[2:]), bytes.fromhex(condition_id[2:]), [index_set]])
    return SEL_REDEEM + args.hex()


def negrisk_redeem_calldata(condition_id, winning_index, raw_amount):
    """NegRiskAdapter.redeemPositions(conditionId, amounts) — amounts is per-OUTCOME,
    0-based (YES=0, NO=1). The adapter burns the held winning CTF tokens (minted
    against the NegRisk wrapped collateral) AND unwraps the payout to the underlying
    collateral (USDC.e) in one call. Vanilla CTF.redeemPositions cannot redeem these:
    their positionId derives from the wrapped collateral, not pUSD/USDC.e, so
    derive_collateral never matches and they were silently skipped (loop recovered $0)."""
    amounts = [0, 0]
    amounts[winning_index] = int(raw_amount)
    args = abi_encode(["bytes32", "uint256[]"],
                      [bytes.fromhex(condition_id[2:]), amounts])
    return SEL_NR_REDEEM + args.hex()


def ctf_balance_raw(token_id, wallet):
    """Raw ERC1155 CTF balance (integer, collateral-decimals) the wallet holds of
    token_id. NegRiskAdapter.redeemPositions needs the exact integer amount — there
    is no 'redeem all' variant as with CTF.redeemPositions(indexSets)."""
    r = eth_call(CTF, SEL_BALANCEOF1155 + wallet.lower().replace("0x", "").rjust(64, "0")
                 + hex(int(token_id))[2:].rjust(64, "0"))
    if "error" in r or r.get("result") in ("0x", "0x0", None):
        return 0
    return int(r["result"], 16)


def approve_calldata(spender, amount):
    return SEL_APPROVE + abi_encode(["address", "uint256"], [spender, amount]).hex()


def onramp_wrap_calldata(asset, to_wallet, amount):
    """Onramp.wrap(_asset, _to, _amount): pulls `amount` of `asset` from the caller
    (the deposit wallet, via approve) and mints pUSD to `to_wallet`."""
    return SEL_ONRAMP_WRAP + abi_encode(["address", "address", "uint256"],
                                        [asset, to_wallet, amount]).hex()


def usdce_balance(wallet):
    r = eth_call(USDCE, SEL_BALANCEOF20 + wallet.lower().replace("0x", "").rjust(64, "0"))
    if "error" in r or r["result"] in ("0x", "0x0"):
        return 0
    return int(r["result"], 16)


def relayer_nonce(owner):
    req = urllib.request.Request(f"{RELAYER}/nonce?address={owner}&type=WALLET", headers=HTTP_HDRS)
    return int(json.load(urllib.request.urlopen(req, timeout=15))["nonce"])


def sign_batch(deposit_wallet, nonce, deadline, calls, private_key):
    """EIP-712 sign a DepositWallet Batch. calls: list of {target,value,data(bytes)}."""
    domain = {"name": "DepositWallet", "version": "1", "chainId": CHAIN_ID,
              "verifyingContract": to_checksum_address(deposit_wallet)}
    message = {"wallet": to_checksum_address(deposit_wallet), "nonce": nonce, "deadline": deadline,
               "calls": [{"target": to_checksum_address(c["target"]), "value": c["value"],
                          "data": c["data"]} for c in calls]}
    full = {"types": EIP712_TYPES, "primaryType": "Batch", "domain": domain, "message": message}
    signed = Account.sign_typed_data(private_key, full_message=full)
    return "0x" + signed.signature.hex() if not signed.signature.hex().startswith("0x") else signed.signature.hex()


def simulate_execute(deposit_wallet, nonce, deadline, calls, signature):
    """Dry-run: eth_call the wallet's execute(batch,sig) with from=FACTORY.

    This is the faithful simulation of the relayer path: the relayer calls
    factory.proxy(...), which calls depositWallet.execute(batch,sig) (the
    onlyFactory gate). Overriding eth_call's `from` to the factory passes that
    gate, so the simulation exercises the real signature validation + the inner
    redeem calls. A `0x` return means it would not revert; a revert surfaces the
    contract's custom error before any real money moves.

    (Simulating factory.proxy() directly instead reverts with OnlyOperator()
    [0x27e1f1e5] — proxy() is gated to authorized operators, i.e. the Polymarket
    relayer. The EOA cannot broadcast the batch itself even with gas; the relayer
    is the only execution path. That's a caller-gate, not a problem with the batch.)
    """
    esel = "0x" + keccak(text="execute((address,uint256,uint256,(address,uint256,bytes)[]),bytes)")[:4].hex()
    call_tuples = [(to_checksum_address(c["target"]), c["value"], c["data"]) for c in calls]
    batch_tuple = (to_checksum_address(deposit_wallet), nonce, deadline, call_tuples)
    sig_bytes = bytes.fromhex(signature.replace("0x", ""))
    args = abi_encode(["(address,uint256,uint256,(address,uint256,bytes)[])", "bytes"],
                      [batch_tuple, sig_bytes])
    return eth_call_from(FACTORY, deposit_wallet, esel + args.hex())


def submit_relayer(owner, deposit_wallet, deadline, calls, signature, relayer_key):
    payload = {
        "type": "WALLET",
        "from": to_checksum_address(owner),
        "to": FACTORY,
        "nonce": str(relayer_nonce(owner)),
        "signature": signature,
        "depositWalletParams": {
            "depositWallet": to_checksum_address(deposit_wallet),
            "deadline": str(deadline),
            "calls": [{"target": to_checksum_address(c["target"]), "value": str(c["value"]),
                       "data": c["data_hex"]} for c in calls],
        },
    }
    hdrs = dict(HTTP_HDRS)
    hdrs["RELAYER_API_KEY"] = relayer_key
    hdrs["RELAYER_API_KEY_ADDRESS"] = to_checksum_address(owner)
    req = urllib.request.Request(f"{RELAYER}/submit", data=json.dumps(payload).encode(), headers=hdrs)
    return json.load(urllib.request.urlopen(req, timeout=30))


def poll_relayer(tx_id, timeout_s=120):
    """Poll the relayer for terminal state. The /transaction endpoint returns a
    list of records; the submit response itself already carries the executed
    state + on-chain tx hash, so this is confirmation-grade, not the source."""
    deadline = time.time() + timeout_s
    terminal = ("STATE_CONFIRMED", "STATE_EXECUTED", "STATE_MINED", "STATE_FAILED")
    while time.time() < deadline:
        req = urllib.request.Request(f"{RELAYER}/transaction?id={tx_id}", headers=HTTP_HDRS)
        try:
            j = json.load(urllib.request.urlopen(req, timeout=15))
            rec = j[0] if isinstance(j, list) and j else (j if isinstance(j, dict) else {})
            state = rec.get("state", "")
            if state:
                print(f"    relayer state: {state}")
            if state in terminal:
                return rec
        except Exception as e:
            print(f"    poll error: {e}")
        time.sleep(6)
    return {"state": "STATE_TIMEOUT"}


async def get_winners(min_tokens):
    """Resolved WINNING live MB positions still holding CTF tokens >= min_tokens."""
    from sqlalchemy import text
    from base_engine.data.database import Database
    from base_engine.data.resolution_backfill import _fetch_market_by_condition_id, _clob_to_market_format
    from base_engine.execution.clob_adapter import check_ctf_balance

    db = Database()
    await db.init()
    async with db.get_session() as s:
        rows = await s.execute(text("""
            SELECT DISTINCT ON (p.market_id, p.side)
                   p.market_id, p.side, p.token_id, p.status
            FROM positions p
            LEFT JOIN (SELECT market_id, side, COUNT(*) n FROM trade_events
                       WHERE bot_name='MirrorBot' AND event_type='EXIT' AND execution_mode='live'
                       GROUP BY market_id, side) ex
              ON ex.market_id=p.market_id AND ex.side=p.side
            WHERE COALESCE(p.source_bot,p.bot_id)='MirrorBot' AND p.is_paper=false
              AND ex.n IS NULL
            ORDER BY p.market_id, p.side, p.opened_at DESC
        """))
        positions = rows.fetchall()

    winners = []
    for market_id, side, token_id, status in positions:
        clob = await _fetch_market_by_condition_id(market_id)
        if not clob or not clob.get("closed"):
            continue
        res = _clob_to_market_format(clob, market_id).get("resolution")
        if res not in ("YES", "NO") or side != res:
            continue
        try:
            bal = await check_ctf_balance(token_id) if token_id else None
        except Exception:
            bal = None
        if not bal or bal < min_tokens:
            continue
        index_set = 1 if side == "YES" else 2  # binary: YES=outcome0=bit0=1, NO=outcome1=bit1=2
        winners.append({
            "condition_id": market_id, "side": side, "token_id": str(token_id),
            "balance": bal, "index_set": index_set,
            "neg_risk": bool(clob.get("neg_risk")),
            "question": (clob.get("question") or "")[:48],
        })
    await db.close()
    return winners


def main():
    ap = argparse.ArgumentParser(description="Redeem winning positions, convert to pUSD, free buying power.")
    ap.add_argument("--execute", action="store_true",
                    help="ACTUALLY broadcast via the relayer (moves real money). Default is dry-run simulation.")
    ap.add_argument("--phase", choices=["redeem", "convert", "full"], default="full",
                    help="redeem only, convert-USDC.e-to-pUSD only, or the full loop (default).")
    ap.add_argument("--min-usd", type=float, default=0.10,
                    help="skip winners holding fewer than this many tokens (default 0.10).")
    ap.add_argument("--deadline-secs", type=int, default=3600, help="batch deadline horizon (default 1h).")
    args = ap.parse_args()

    pk = (os.environ.get("PRIVATE_KEY") or "").strip()
    deposit_wallet = (os.environ.get("DEPOSIT_WALLET_ADDRESS") or "").strip()
    owner = (os.environ.get("WALLET_ADDRESS") or "").strip()
    relayer_key = (os.environ.get("RELAYER_API_KEY") or "").strip()
    if not pk or not deposit_wallet or not owner:
        print("FATAL: PRIVATE_KEY / DEPOSIT_WALLET_ADDRESS / WALLET_ADDRESS must be set in env.")
        return 2
    if not pk.startswith("0x"):
        pk = "0x" + pk
    derived_owner = Account.from_key(pk).address
    if derived_owner.lower() != owner.lower():
        print(f"WARNING: PRIVATE_KEY address {derived_owner} != WALLET_ADDRESS {owner}; using key address as signer.")
        owner = derived_owner

    mode = "EXECUTE (REAL MONEY)" if args.execute else "DRY-RUN (simulate, no broadcast)"
    print(f"=== redeem_and_retrade — mode: {mode} — phase: {args.phase} ===")
    print(f"deposit wallet: {deposit_wallet}   owner/signer: {owner}")

    winners = asyncio.run(get_winners(args.min_usd))
    print(f"\nResolved winning positions holding CTF tokens: {len(winners)}")
    redeem_calls = []
    total_tokens = 0.0
    for w in winners:
        # NegRisk winners (elections/tournaments/multi-outcome groups — in scope per
        # CLAUDE.md RULE TWO) mint CTF positions against the NegRisk wrapped collateral,
        # so vanilla derive_collateral (pUSD/USDC.e only) never matches and they were
        # silently SKIPPED — the loop recovered $0 from them. Redeem via the
        # NegRiskAdapter, which unwraps the payout to USDC.e for the convert phase.
        # (neg_risk read from the CLOB, which is authoritative; the local markets.neg_risk
        # flag is stale-false for these.)
        if w.get("neg_risk"):
            raw = ctf_balance_raw(w["token_id"], deposit_wallet)
            if raw <= 0:
                print(f"  SKIP {w['condition_id'][:14]}… {w['side']} — neg-risk but 0 on-chain balance")
                continue
            widx = 0 if w["side"] == "YES" else 1
            total_tokens += w["balance"]
            data_hex = negrisk_redeem_calldata(w["condition_id"], widx, raw)  # 0x-prefixed
            redeem_calls.append({"target": NEGRISK_ADAPTER, "value": 0, "data": bytes.fromhex(data_hex[2:]),
                                 "data_hex": data_hex, "collateral": None, "collateral_name": "NegRisk", "w": w})
            print(f"  REDEEM {w['side']:3} idx={widx} raw={raw} bal={w['balance']:.3f} coll=NegRisk "
                  f"{w['condition_id'][:16]}… {w['question']}")
            continue
        coll = derive_collateral(w["condition_id"], w["index_set"], w["token_id"])
        coll_name = {USDCE: "USDC.e", PUSD: "pUSD"}.get(coll, "UNKNOWN")
        if coll is None:
            print(f"  SKIP {w['condition_id'][:14]}… {w['side']} — collateral unresolved (token mismatch); not redeeming blind")
            continue
        total_tokens += w["balance"]
        data_hex = redeem_calldata(coll, w["condition_id"], w["index_set"])  # 0x-prefixed
        redeem_calls.append({"target": CTF, "value": 0, "data": bytes.fromhex(data_hex[2:]),
                             "data_hex": data_hex, "collateral": coll, "collateral_name": coll_name, "w": w})
        print(f"  REDEEM {w['side']:3} idx={w['index_set']} bal={w['balance']:.3f} coll={coll_name:6} "
              f"{w['condition_id'][:16]}… {w['question']}")

    deadline = int(time.time()) + args.deadline_secs

    # ---- REDEEM phase ----
    if args.phase in ("redeem", "full") and redeem_calls:
        nonce = relayer_nonce(owner)
        calls = [{"target": c["target"], "value": c["value"], "data": c["data"], "data_hex": c["data_hex"]}
                 for c in redeem_calls]
        sig = sign_batch(deposit_wallet, nonce, deadline, calls, pk)
        print(f"\n[REDEEM] batch: {len(calls)} calls, nonce={nonce}, deadline={deadline}")
        print(f"         signature={sig[:20]}…{sig[-8:]} (len {len(sig)})")
        if args.execute:
            print("         >>> BROADCASTING redeem batch via relayer …")
            resp = submit_relayer(owner, deposit_wallet, deadline, calls, sig, relayer_key)
            print(f"         relayer response: {resp}")
            txid = resp.get("transactionID")
            if txid:
                final = poll_relayer(txid)
                print(f"         final: {final.get('state')}")
        else:
            sim = simulate_execute(deposit_wallet, nonce, deadline, calls, sig)
            if "error" in sim:
                print(f"         SIMULATION REVERTED: {sim['error']}  (selector/collateral/signature issue — investigate before execute)")
            else:
                print(f"         SIMULATION OK (execute-from-factory eth_call returned '{sim['result']}') — "
                      f"all {len(calls)} redeems valid; would not revert via the relayer.")
    elif args.phase in ("redeem", "full") and not redeem_calls:
        print("\n[REDEEM] no resolved winning positions to redeem this cycle.")

    # ---- CONVERT phase (USDC.e -> pUSD) — standalone: converts whatever USDC.e the
    #      deposit wallet holds, independent of whether new winners were redeemed this run.
    if args.phase in ("convert", "full"):
        if args.execute:
            amt = usdce_balance(deposit_wallet)
            print(f"\n[CONVERT] deposit-wallet USDC.e balance now: {amt/1e6:.4f}")
            if amt > 0:
                approve_hex = approve_calldata(ONRAMP, amt)                     # approve USDC.e to the onramp
                wrap_hex = onramp_wrap_calldata(USDCE, deposit_wallet, amt)     # onramp wraps -> pUSD to deposit wallet
                conv_calls = [
                    {"target": USDCE, "value": 0, "data": bytes.fromhex(approve_hex[2:]), "data_hex": approve_hex},
                    {"target": ONRAMP, "value": 0, "data": bytes.fromhex(wrap_hex[2:]), "data_hex": wrap_hex},
                ]
                nonce2 = relayer_nonce(owner)
                sig2 = sign_batch(deposit_wallet, nonce2, deadline, conv_calls, pk)
                print(f"          approve+wrap {amt/1e6:.4f} USDC.e -> pUSD, nonce={nonce2}")
                resp2 = submit_relayer(owner, deposit_wallet, deadline, conv_calls, sig2, relayer_key)
                print(f"          relayer response: {resp2}")
                txid2 = resp2.get("transactionID")
                if txid2:
                    print(f"          final: {poll_relayer(txid2).get('state')}")
            else:
                print("          no USDC.e to convert (redeem may still be settling — re-run convert phase shortly).")
        else:
            amt = usdce_balance(deposit_wallet)
            print(f"\n[CONVERT] (dry-run) deposit-wallet USDC.e = {amt/1e6:.4f}. On execute, approves the Collateral "
                  f"Onramp ({ONRAMP}) to spend it and calls Onramp.wrap(USDC.e, depositWallet, amount) to mint pUSD.")

    # ---- RETRADE phase ----
    if args.phase == "full":
        if args.execute:
            try:
                from base_engine.execution.clob_adapter import _refresh_balance_allowance_sync
                ok = _refresh_balance_allowance_sync("COLLATERAL")
                print(f"\n[RETRADE] CLOB balance/allowance cache refresh: {'ok' if ok else 'failed (bot will retry on next scan)'}")
            except Exception as e:
                print(f"\n[RETRADE] refresh skipped: {e}")
        else:
            print("\n[RETRADE] (dry-run) on execute, refreshes the CLOB balance/allowance cache so MirrorBot "
                  "picks up the new pUSD as buying power on its next scan. The bot is already live; it resumes itself.")

    print("\n=== done ===")
    if not args.execute:
        print("This was a DRY-RUN. To actually redeem (moves real money), re-run with --execute "
              "AND explicit operator approval per WALLET_LEDGER.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
