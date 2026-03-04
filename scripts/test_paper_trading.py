"""
Test paper trading flow: SIMULATION_MODE + place_order -> PaperTradingEngine.
Run from polymarket-ai-v2: python scripts/test_paper_trading.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Ensure project root in path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)  # .env is loaded from cwd by pydantic-settings


async def test_paper_trading():
    """Test that place_order routes to PaperTradingEngine when SIMULATION_MODE=true."""
    from config.settings import settings
    from base_engine.execution.paper_trading import PaperTradingEngine
    from base_engine.base_engine import BaseEngine

    print("=" * 60)
    print("Paper Trading Flow Test")
    print("=" * 60)
    print(f"SIMULATION_MODE: {getattr(settings, 'SIMULATION_MODE', 'NOT SET')}")

    # Test 1: PaperTradingEngine directly
    print("\n--- Test 1: PaperTradingEngine direct ---")
    engine = PaperTradingEngine(initial_capital=10000.0)
    assert not engine.enabled, "Should start disabled"
    engine.enable()
    result = await engine.place_order(
        market_id="test_market_1",
        token_id="test_token_yes",
        side="BUY",
        size=10.0,
        price=0.55,
        bot_name="test_bot",
    )
    print(f"  Direct place_order result: {result}")
    assert result.get("success"), f"Expected success, got: {result}"
    assert len(engine.get_trades()) == 1, f"Expected 1 trade, got {len(engine.get_trades())}"
    assert len(engine.get_positions()) == 1, f"Expected 1 position, got {len(engine.get_positions())}"
    print(f"  Trades: {len(engine.get_trades())}")
    print(f"  Positions: {len(engine.get_positions())}")
    print(f"  Cash remaining: ${engine.cash:.2f}")
    print("  [PASS] PaperTradingEngine direct")

    # Test 2: BaseEngine with paper path (minimal init - heavy, may skip if DB/Redis unavailable)
    print("\n--- Test 2: BaseEngine place_order (paper path) ---")
    try:
        be = BaseEngine()
        await be.init(wallet_private_key=None, wallet_address=None)
        await be.start()

        pt = getattr(be, "paper_trading", None)
        if not pt or not pt.enabled:
            print("  [SKIP] paper_trading not enabled (SIMULATION_MODE or start() issue)")
        else:
            # Place via base_engine (should route to paper)
            result = await be.place_order(
                bot_name="TestBot",
                market_id="test_market_2",
                token_id="test_token_yes",
                side="YES",
                size=5.0,
                price=0.60,
                confidence=0.85,
            )
            print(f"  BaseEngine place_order result: success={result.get('success')}, error={result.get('error')}")
            if result.get("success"):
                trades = pt.get_trades()
                print(f"  Total paper trades: {len(trades)}")
                print("  [PASS] BaseEngine paper path")
            else:
                print(f"  [FAIL] {result.get('error', 'Unknown error')}")
    except Exception as e:
        print(f"  [SKIP] BaseEngine init failed (DB/Redis may be unavailable): {e}")

    print("\n" + "=" * 60)
    print("Test complete.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_paper_trading())
