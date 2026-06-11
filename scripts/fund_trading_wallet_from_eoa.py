#!/usr/bin/env python3
"""Move idle USDC.e from the EOA (signer wallet) into the deposit wallet (the bot's
trading wallet), so it can be wrapped to pUSD and become buying power.

The EOA holds USDC.e the bot can't trade (wrong wallet + wrong token). This does a
plain ERC20 transfer EOA -> deposit wallet, signed by the EOA, EOA pays gas. After
this, run `redeem_and_retrade.py --execute --phase convert` to wrap the landed
USDC.e into pUSD.

SAFETY: moves real money when --execute is passed. Default is dry-run (prints the
plan + simulates, no broadcast). Operator-approved per WALLET_LEDGER.md.
"""
import argparse
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_root / ".env")

from eth_account import Account  # noqa: E402
from eth_utils import to_checksum_address  # noqa: E402
from web3 import Web3  # noqa: E402

USDCE = to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "a", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "transfer", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="broadcast (real money). Default dry-run.")
    ap.add_argument("--amount-usdce", type=float, default=None,
                    help="USDC.e to move (default: entire EOA balance).")
    args = ap.parse_args()

    pk = (os.environ.get("PRIVATE_KEY") or "").strip()
    pk = pk if pk.startswith("0x") else "0x" + pk
    rpc = (os.environ.get("POLYGON_RPC") or os.environ.get("POLYGON_RPC_URL") or "").strip()
    deposit_wallet = to_checksum_address((os.environ.get("DEPOSIT_WALLET_ADDRESS") or "").strip())
    eoa = Account.from_key(pk).address

    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={
        "headers": {"User-Agent": "Mozilla/5.0 Chrome/124.0"}, "timeout": 30}))
    usdce = w3.eth.contract(address=USDCE, abi=ERC20_ABI)

    bal = usdce.functions.balanceOf(eoa).call()
    amt = bal if args.amount_usdce is None else int(round(args.amount_usdce * 1e6))
    matic = w3.eth.get_balance(eoa)

    print(f"=== fund trading wallet from EOA — {'EXECUTE (REAL MONEY)' if args.execute else 'DRY-RUN'} ===")
    print(f"EOA (from):     {eoa}  USDC.e={bal/1e6:.6f}  MATIC={matic/1e18:.4f}")
    print(f"deposit (to):   {deposit_wallet}")
    print(f"transfer amount: {amt/1e6:.6f} USDC.e")
    if amt <= 0:
        print("Nothing to transfer."); return 0
    if amt > bal:
        print(f"FATAL: requested {amt/1e6:.6f} > balance {bal/1e6:.6f}."); return 2

    tx = usdce.functions.transfer(deposit_wallet, amt).build_transaction({
        "from": eoa,
        "nonce": w3.eth.get_transaction_count(eoa),
        "chainId": 137,
        "maxFeePerGas": w3.eth.gas_price * 2,
        "maxPriorityFeePerGas": w3.to_wei(30, "gwei"),
    })
    try:
        tx["gas"] = int(w3.eth.estimate_gas({"from": eoa, "to": USDCE, "data": tx["data"]}) * 1.3)
    except Exception as e:
        print(f"gas estimate failed (would revert?): {e}"); return 2
    print(f"gas={tx['gas']}  maxFeePerGas={tx['maxFeePerGas']/1e9:.1f} gwei  "
          f"est-cost<= {tx['gas']*tx['maxFeePerGas']/1e18:.5f} MATIC")

    if not args.execute:
        print("\nDRY-RUN: transfer simulates clean (gas estimated OK). Re-run with --execute to broadcast.")
        return 0

    signed = w3.eth.account.sign_transaction(tx, pk)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    txh = w3.eth.send_raw_transaction(raw)
    print(f"\n>>> broadcast: {txh.hex()}")
    rcpt = w3.eth.wait_for_transaction_receipt(txh, timeout=180)
    print(f"status={rcpt.status} block={rcpt.blockNumber} gasUsed={rcpt.gasUsed}")
    print(f"EOA USDC.e now:     {usdce.functions.balanceOf(eoa).call()/1e6:.6f}")
    print(f"deposit USDC.e now: {usdce.functions.balanceOf(deposit_wallet).call()/1e6:.6f}")
    print("\nNext: redeem_and_retrade.py --execute --phase convert  (wrap the landed USDC.e -> pUSD)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
