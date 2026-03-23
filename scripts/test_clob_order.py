"""
S120: CLOB Connectivity Test — place a $1 limit order, verify, cancel.

Validates the full live order path: credentials → approval → CLOB POST → cancel.
Uses an unfillable price (1 cent) so no capital is at risk.

Required env vars:
  PRIVATE_KEY          - Ethereum private key (hex, with or without 0x prefix)
  CLOB_API_KEY         - Polymarket CLOB API key
  CLOB_SECRET          - Polymarket CLOB API secret
  CLOB_PASSPHRASE      - Polymarket CLOB API passphrase
  POLYMARKET_CLOB_API  - CLOB API base URL (default: https://clob.polymarket.com)

Usage:
  python scripts/test_clob_order.py --token-id TOKEN_ID [--dry-run]

If --token-id is not provided, the script queries the DB for an active market's YES token.
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    parser = argparse.ArgumentParser(description="Test CLOB order placement and cancellation")
    parser.add_argument("--token-id", help="Token ID for the test order (YES token hex)")
    parser.add_argument("--dry-run", action="store_true", help="Validate credentials without placing an order")
    args = parser.parse_args()

    from config.settings import settings

    # --- Step 1: Validate credentials ---
    missing = []
    if not settings.PRIVATE_KEY:
        missing.append("PRIVATE_KEY")
    if not settings.CLOB_API_KEY:
        missing.append("CLOB_API_KEY")
    if not settings.CLOB_SECRET:
        missing.append("CLOB_SECRET")
    if not settings.CLOB_PASSPHRASE:
        missing.append("CLOB_PASSPHRASE")
    if missing:
        print(f"FAIL: Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    print("OK: All CLOB credentials found")

    host = (getattr(settings, "POLYMARKET_CLOB_API", None) or "").rstrip("/")
    if not host:
        print("FAIL: POLYMARKET_CLOB_API not set")
        sys.exit(1)
    print(f"OK: CLOB API host = {host}")

    # --- Step 2: Check wallet balance ---
    from base_engine.execution.contract_manager import ContractManager

    cm = ContractManager(private_key=settings.PRIVATE_KEY)
    print(f"OK: Wallet address = {cm.account.address}")

    bal = await cm.get_usdce_balance()
    if bal.get("success"):
        print(f"OK: USDCe balance = ${bal['balance_usd']:.2f}")
        if bal["balance_usd"] < 1.0:
            print("WARN: Balance < $1 — order may be rejected by CLOB")
    else:
        print(f"WARN: Balance query failed: {bal.get('error')} (continuing anyway)")

    if args.dry_run:
        print("\n--- DRY RUN: credentials validated, skipping order placement ---")
        return

    # --- Step 3: Resolve token ID ---
    token_id = args.token_id
    if not token_id:
        print("Querying DB for an active market token ID...")
        try:
            from base_engine.data.database import Database

            db = Database()
            await db.initialize()
            from sqlalchemy import text as sa_text

            async with db.get_session() as session:
                row = await session.execute(sa_text(
                    "SELECT yes_token_id, condition_id FROM markets "
                    "WHERE active = true AND yes_token_id IS NOT NULL "
                    "ORDER BY volume DESC LIMIT 1"
                ))
                result = row.first()
                if result:
                    token_id = result[0]
                    print(f"OK: Using token_id={token_id[:20]}... (condition_id={result[1][:20]}...)")
                else:
                    print("FAIL: No active markets with token IDs in DB")
                    sys.exit(1)
        except Exception as e:
            print(f"FAIL: DB lookup failed: {e}")
            print("Provide --token-id manually")
            sys.exit(1)

    # --- Step 4: Place $1 limit order at 1 cent (unfillable) ---
    from base_engine.execution.clob_adapter import ClobAdapter

    adapter = ClobAdapter()
    if not adapter.available:
        print("FAIL: ClobAdapter not available (py-clob-client not configured)")
        sys.exit(1)

    test_price = 0.01  # 1 cent — practically unfillable
    test_size = 100.0  # 100 shares at 1 cent = $1
    print(f"\nPlacing test order: BUY {test_size} shares at ${test_price} (${test_size * test_price:.2f} total)")

    t0 = time.monotonic()
    result = await adapter.place_order(
        market_id="test",
        token_id=token_id,
        side="BUY",
        size=test_size,
        price=test_price,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    if not result.get("success"):
        print(f"FAIL: Order rejected: {result.get('error')}")
        sys.exit(1)

    order_id = result.get("order_id")
    print(f"OK: Order placed in {elapsed_ms:.0f}ms — order_id={order_id}")

    # --- Step 5: Cancel the order ---
    print(f"Cancelling order {order_id}...")
    t0 = time.monotonic()
    cancelled = await adapter.cancel_order(order_id)
    cancel_ms = (time.monotonic() - t0) * 1000

    if cancelled:
        print(f"OK: Order cancelled in {cancel_ms:.0f}ms")
    else:
        print(f"WARN: Cancel returned False (order may have already expired) — {cancel_ms:.0f}ms")

    # --- Summary ---
    print("\n=== CLOB CONNECTIVITY TEST PASSED ===")
    print(f"  Wallet:     {cm.account.address}")
    print(f"  Balance:    ${bal.get('balance_usd', '?')}")
    print(f"  Order:      {order_id}")
    print(f"  Place:      {elapsed_ms:.0f}ms")
    print(f"  Cancel:     {cancel_ms:.0f}ms")
    print(f"  Token ID:   {token_id[:30]}...")
    print("\nReady for CANARY_STAGE=1 deployment.")


if __name__ == "__main__":
    asyncio.run(main())
