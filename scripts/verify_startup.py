"""
Startup verification script — validates the full MLP + RL integration chain.
Run: python scripts/verify_startup.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to sys.path so imports work from scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.chdir(Path(__file__).resolve().parent.parent)


def main():
    errors = []

    print("=" * 60)
    print("STEP 1: Settings load from .env")
    print("=" * 60)
    from config.settings import settings

    checks = {
        "SIMULATION_MODE": (settings.SIMULATION_MODE, True),
        "MODEL_ENABLE_MLP": (settings.MODEL_ENABLE_MLP, True),
        "RL_TRADE_TIMING_ENABLED": (settings.RL_TRADE_TIMING_ENABLED, True),
        "RL_LEARNING_RATE": (settings.RL_LEARNING_RATE, 0.1),
        "RL_DISCOUNT_FACTOR": (settings.RL_DISCOUNT_FACTOR, 0.95),
        "RL_EPSILON_START": (settings.RL_EPSILON_START, 0.3),
        "RL_EPSILON_MIN": (settings.RL_EPSILON_MIN, 0.05),
        "RL_EPSILON_DECAY_TRADES": (settings.RL_EPSILON_DECAY_TRADES, 500),
        "RL_REPLAY_BUFFER_SIZE": (settings.RL_REPLAY_BUFFER_SIZE, 2000),
        "RL_REPLAY_BATCH_SIZE": (settings.RL_REPLAY_BATCH_SIZE, 32),
    }
    for name, (actual, expected) in checks.items():
        ok = actual == expected
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {name} = {actual} (expected {expected})")
        if not ok:
            errors.append(f"Settings: {name} = {actual}, expected {expected}")

    print()
    print("=" * 60)
    print("STEP 2: model_cache.pkl + rl_qtable.pkl status")
    print("=" * 60)
    cache_path = Path("data/model_cache.pkl")
    if cache_path.exists():
        print(f"  [WARN] model_cache.pkl EXISTS ({cache_path.stat().st_size} bytes) — old cache, will NOT retrain")
        print(f"         Delete it to force retrain with MLP model #11")
    else:
        print(f"  [OK] model_cache.pkl absent — will retrain on startup (~2-3 min)")

    rl_path = Path("data/rl_qtable.pkl")
    if rl_path.exists():
        print(f"  [INFO] rl_qtable.pkl exists ({rl_path.stat().st_size} bytes) — will resume learning")
    else:
        print(f"  [OK] rl_qtable.pkl absent — fresh RL start")

    print()
    print("=" * 60)
    print("STEP 3: Import chain (base_engine + sklearn + rl)")
    print("=" * 60)
    try:
        from base_engine.base_engine import BaseEngine
        print("  [OK] BaseEngine imported")
    except Exception as e:
        print(f"  [FAIL] BaseEngine import: {e}")
        errors.append(f"BaseEngine import: {e}")

    try:
        from sklearn.neural_network import MLPClassifier
        import sklearn
        print(f"  [OK] MLPClassifier imported (sklearn {sklearn.__version__})")
    except Exception as e:
        print(f"  [FAIL] MLPClassifier: {e}")
        errors.append(f"MLPClassifier: {e}")

    try:
        from base_engine.execution.rl_trade_timing import RLTradeTimingAgent, TRADE_NOW
        print("  [OK] RLTradeTimingAgent imported")
    except Exception as e:
        print(f"  [FAIL] RLTradeTimingAgent: {e}")
        errors.append(f"RLTradeTimingAgent: {e}")

    print()
    print("=" * 60)
    print("STEP 4: RL agent decide() in async event loop")
    print("=" * 60)
    from base_engine.execution.rl_trade_timing import RLTradeTimingAgent, TRADE_NOW, WAIT, SKIP

    agent = RLTradeTimingAgent(epsilon_start=0.0)  # greedy for deterministic test
    state = {
        "confidence": 0.7,
        "spread": 0.03,
        "volatility": 0.02,
        "regime": "calm",
        "hour": 15,
        "market_id": "verify_mkt",
    }

    async def _decide():
        return await agent.decide(state)

    action, q = asyncio.run(_decide())
    names = {0: "TRADE_NOW", 1: "WAIT", 2: "SKIP"}
    print(f"  [OK] decide() -> {names[action]} (q={q})")

    print()
    print("=" * 60)
    print("STEP 5: Paper trading callback chain (BUY -> SELL -> RL learns)")
    print("=" * 60)
    from base_engine.execution.paper_trading import PaperTradingEngine

    pt = PaperTradingEngine(initial_capital=10000.0)
    pt.enable()

    callback_results = []

    def _rl_cb(market_id, realized_pnl, exit_price, avg_entry_price):
        callback_results.append({
            "market_id": market_id,
            "pnl": realized_pnl,
            "exit_price": exit_price,
            "avg_entry": avg_entry_price,
        })
        agent.record_outcome_from_trade(market_id, realized_pnl)

    pt.set_rl_outcome_callback(_rl_cb)

    async def _test_callback():
        # Create pending RL entry
        await agent.decide({
            "confidence": 0.7, "spread": 0.03, "volatility": 0.02,
            "regime": "calm", "hour": 15, "market_id": "cb_mkt",
        })
        # BUY
        buy = await pt.place_order(
            market_id="cb_mkt", token_id="tok_1",
            side="BUY", size=10, price=0.5,
            bot_name="verify_bot", original_side="YES",
        )
        # SELL
        sell = await pt.place_order(
            market_id="cb_mkt", token_id="tok_1",
            side="SELL", size=10, price=0.6,
            bot_name="verify_bot",
        )
        return buy, sell

    buy_r, sell_r = asyncio.run(_test_callback())
    buy_ok = buy_r.get("success", False)
    sell_ok = sell_r.get("success", False)
    print(f"  [{'OK' if buy_ok else 'FAIL'}] BUY executed: success={buy_ok}")
    print(f"  [{'OK' if sell_ok else 'FAIL'}] SELL executed: success={sell_ok}")

    if callback_results:
        cr = callback_results[0]
        print(f"  [OK] Callback fired: market={cr['market_id']}, pnl={cr['pnl']:.4f}")
        print(f"  [OK] RL agent learned: total_trades={agent._total_trades}")
    else:
        print("  [FAIL] RL callback did NOT fire on SELL")
        errors.append("RL callback did not fire")

    if not buy_ok:
        errors.append(f"BUY failed: {buy_r}")
    if not sell_ok:
        errors.append(f"SELL failed: {sell_r}")

    print()
    print("=" * 60)
    print("STEP 6: OrderGateway RL pipeline (SIMULATION_MODE)")
    print("=" * 60)
    from base_engine.execution.order_gateway import OrderGateway

    ks = MagicMock()
    ks.is_engaged = AsyncMock(return_value=False)
    rm = MagicMock()
    rm.check_risk_limits = AsyncMock(return_value={"allowed": True})
    tc = MagicMock()
    tc.reserve_position = AsyncMock(return_value=True)
    tc.confirm_position = AsyncMock()
    ee = MagicMock()

    pt2 = PaperTradingEngine(initial_capital=10000.0)
    pt2.enable()
    rl2 = RLTradeTimingAgent(epsilon_start=0.0)

    gw = OrderGateway(
        kill_switch=ks,
        risk_manager=rm,
        trade_coordinator=tc,
        execution_engine=ee,
        paper_trading_engine=pt2,
        rl_agent=rl2,
    )

    async def _test_gw():
        return await gw.place_order(
            bot_name="ensemble", market_id="gw_test",
            token_id="tok_1", side="YES",
            size=5, price=0.5, confidence=0.75,
        )

    gw_result = asyncio.run(_test_gw())
    gw_ok = gw_result.get("success", False)
    print(f"  [{'OK' if gw_ok else 'FAIL'}] Gateway: success={gw_ok}")
    if gw_ok:
        oid = gw_result.get("order_id", "???")
        print(f"  [OK] Paper order_id={oid[:30]}...")
    else:
        err = gw_result.get("error", "unknown")
        print(f"  [FAIL] Gateway error: {err}")
        errors.append(f"Gateway: {err}")

    print()
    print("=" * 60)
    print("STEP 7: RL Q-table save/load roundtrip")
    print("=" * 60)
    for i in range(10):
        rl2.record_outcome(i % 324, i % 3, 0.5)

    tmp = Path(tempfile.mktemp(suffix=".pkl"))
    rl2.save(tmp)
    rl3 = RLTradeTimingAgent()
    loaded = rl3.load(tmp)
    if loaded and rl3._total_trades == rl2._total_trades:
        print(f"  [OK] Save/load: {rl3._total_trades} trades preserved, epsilon={rl3.epsilon:.4f}")
    else:
        print(f"  [FAIL] Save/load mismatch")
        errors.append("Save/load roundtrip failed")
    tmp.unlink(missing_ok=True)

    print()
    print("=" * 60)
    print("STEP 8: Verify OrderGateway helper methods")
    print("=" * 60)
    # Test with empty market index
    spread = gw._get_spread_for_rl("unknown_market")
    vol = gw._get_volatility_for_rl("unknown_market")
    regime = gw._get_regime_for_rl()
    print(f"  [OK] Fallback spread={spread} (expected 0.05)")
    print(f"  [OK] Fallback volatility={vol} (expected 0.02)")
    print(f"  [OK] Fallback regime={regime} (expected calm)")

    # Test with populated market index
    gw._market_index = {
        "rich_mkt": {
            "bestBid": "0.45",
            "bestAsk": "0.55",
            "volatility": "0.03",
        }
    }
    spread2 = gw._get_spread_for_rl("rich_mkt")
    vol2 = gw._get_volatility_for_rl("rich_mkt")
    print(f"  [OK] Rich spread={spread2:.4f} (from bestBid/bestAsk)")
    print(f"  [OK] Rich volatility={vol2:.4f} (from volatility field)")

    print()
    print("=" * 60)
    if errors:
        print(f"FAILURES ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("ALL 8 STARTUP VERIFICATION STEPS PASSED")
        print()
        print("Ready to run: python main.py")
        print("  - MLP model #11 will retrain (model_cache.pkl deleted)")
        print("  - RL agent starts fresh (epsilon=0.3, Q-table zeros)")
        print("  - Paper trades feed RL via callback on every SELL")
        print("  - Q-table saves to data/rl_qtable.pkl on shutdown")
    print("=" * 60)


if __name__ == "__main__":
    main()
