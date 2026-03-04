"""
Trading Readiness Analyzer - Check what you can trade and learn with RIGHT NOW.
"""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from base_engine.data.database import Database
from sqlalchemy import select, func, and_, or_
from base_engine.data.database import Market, Trade, MarketPrice, User, Signal, LearningPattern

logger = get_logger()


async def analyze_readiness():
    """Comprehensive analysis of what's ready to trade."""
    
    # Set UTF-8 encoding for Windows
    import sys
    import io
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    print("\n" + "="*80)
    print("POLYMARKET TRADING READINESS ANALYSIS")
    print("="*80 + "\n")
    
    db = Database()
    await db.init()
    
    if not db.session_factory:
        print("ERROR: Database not configured. Set DATABASE_URL in .env file.")
        print("\nCannot analyze trading readiness without database connection.")
        return
    
    try:
        async with db.get_session() as session:
            # ==================== MARKET DATA ====================
            print("📊 MARKET DATA")
            print("-" * 80)
            
            # Total markets
            result = await session.execute(select(func.count(Market.id)))
            total_markets = result.scalar() or 0
            print(f"  Total markets in DB: {total_markets}")
            
            # Active markets
            result = await session.execute(
                select(func.count(Market.id)).where(Market.active == True)
            )
            active_markets = result.scalar() or 0
            print(f"  Active markets: {active_markets}")
            
            # Resolved markets
            result = await session.execute(
                select(func.count(Market.id)).where(Market.resolved == True)
            )
            resolved_markets = result.scalar() or 0
            print(f"  Resolved markets: {resolved_markets}")
            
            # Markets by category
            result = await session.execute(
                select(Market.category, func.count(Market.id))
                .where(Market.active == True)
                .group_by(Market.category)
                .order_by(func.count(Market.id).desc())
                .limit(5)
            )
            categories = result.all()
            if categories:
                print(f"\n  Top active categories:")
                for cat, count in categories:
                    print(f"    • {cat or 'unknown'}: {count} markets")
            
            # ==================== TRADE DATA ====================
            print("\n💰 TRADE DATA")
            print("-" * 80)
            
            # Total trades
            result = await session.execute(select(func.count(Trade.id)))
            total_trades = result.scalar() or 0
            print(f"  Total trades in DB: {total_trades}")
            
            # Recent trades (last 30 days)
            cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
            result = await session.execute(
                select(func.count(Trade.id)).where(Trade.timestamp >= cutoff_30d)
            )
            recent_trades = result.scalar() or 0
            print(f"  Trades (last 30 days): {recent_trades}")
            
            # Trades with PnL
            result = await session.execute(
                select(func.count(Trade.id)).where(Trade.pnl.isnot(None))
            )
            trades_with_pnl = result.scalar() or 0
            print(f"  Trades with PnL data: {trades_with_pnl}")
            
            # ==================== ELITE TRADERS ====================
            print("\n👑 ELITE TRADER DATA")
            print("-" * 80)
            
            # Total users
            result = await session.execute(select(func.count(User.address)))
            total_users = result.scalar() or 0
            print(f"  Total users tracked: {total_users}")
            
            # Elite users
            result = await session.execute(
                select(func.count(User.address)).where(User.is_elite == True)
            )
            elite_count = result.scalar() or 0
            print(f"  Elite users flagged: {elite_count}")
            
            # Top performers (by win rate)
            result = await session.execute(
                select(User.address, User.total_trades, User.win_rate, User.total_profit, User.is_elite)
                .where(User.total_trades >= 5)
                .order_by(User.win_rate.desc())
                .limit(10)
            )
            top_users = result.all()
            if top_users:
                print(f"\n  Top 10 traders (by win rate):")
                for i, (addr, trades, wr, profit, is_elite) in enumerate(top_users, 1):
                    elite_marker = "👑" if is_elite else "  "
                    print(f"    {i:2d}. {elite_marker} {addr[:10]}... | Trades: {trades:4d} | Win Rate: {wr:6.2%} | Profit: ${profit:,.0f}")
            
            # ==================== PRICE HISTORY ====================
            print("\n📈 PRICE HISTORY DATA")
            print("-" * 80)
            
            # Total price records
            result = await session.execute(select(func.count(MarketPrice.id)))
            total_prices = result.scalar() or 0
            print(f"  Total price records: {total_prices}")
            
            # Markets with price history
            result = await session.execute(
                select(func.count(func.distinct(MarketPrice.market_id)))
            )
            markets_with_prices = result.scalar() or 0
            print(f"  Markets with price history: {markets_with_prices}")
            
            # Recent price updates
            result = await session.execute(
                select(func.count(MarketPrice.id)).where(MarketPrice.timestamp >= cutoff_30d)
            )
            recent_prices = result.scalar() or 0
            print(f"  Price updates (last 30 days): {recent_prices}")
            
            # Resolved markets with prices (for learning)
            result = await session.execute(
                select(func.count(func.distinct(MarketPrice.market_id)))
                .join(Market, MarketPrice.market_id == Market.id)
                .where(Market.resolved == True)
            )
            resolved_with_prices = result.scalar() or 0
            print(f"  Resolved markets with prices: {resolved_with_prices} (ready for learning)")
            
            # ==================== LEARNING PATTERNS ====================
            print("\n🎓 LEARNING PATTERNS")
            print("-" * 80)
            
            # Total patterns
            result = await session.execute(select(func.count(LearningPattern.id)))
            total_patterns = result.scalar() or 0
            print(f"  Total learning patterns: {total_patterns}")
            
            # Patterns by type
            result = await session.execute(
                select(LearningPattern.pattern_type, func.count(LearningPattern.id))
                .group_by(LearningPattern.pattern_type)
            )
            pattern_types = result.all()
            if pattern_types:
                print(f"\n  Patterns by type:")
                for ptype, count in pattern_types:
                    print(f"    • {ptype}: {count} patterns")
            
            # High-confidence patterns (>60% win rate)
            result = await session.execute(
                select(func.count(LearningPattern.id))
                .where(and_(LearningPattern.confidence > 0.6, LearningPattern.total >= 10))
            )
            confident_patterns = result.scalar() or 0
            print(f"\n  High-confidence patterns (>60%, n≥10): {confident_patterns}")
            
            # ==================== SIGNALS ====================
            print("\n📡 SIGNAL DATA")
            print("-" * 80)
            
            # Total signals
            result = await session.execute(select(func.count(Signal.id)))
            total_signals = result.scalar() or 0
            print(f"  Total signals: {total_signals}")
            
            if total_signals > 0:
                # Recent signals
                result = await session.execute(
                    select(func.count(Signal.id)).where(Signal.created_at >= cutoff_30d)
                )
                recent_signals = result.scalar() or 0
                print(f"  Signals (last 30 days): {recent_signals}")
                
                # Try to show signals by source if column exists
                try:
                    result = await session.execute(
                        select(Signal.signal_type, func.count(Signal.id))
                        .group_by(Signal.signal_type)
                        .order_by(func.count(Signal.id).desc())
                    )
                    signal_sources = result.all()
                    if signal_sources:
                        print(f"\n  Signals by type:")
                        for sig_type, count in signal_sources:
                            print(f"    • {sig_type or 'unknown'}: {count}")
                except AttributeError:
                    # Signal model might not have these columns
                    pass
            
            # ==================== BOT READINESS ====================
            print("\n\n" + "="*80)
            print("🤖 BOT READINESS ASSESSMENT")
            print("="*80 + "\n")
            
            ready_bots = []
            caution_bots = []
            not_ready_bots = []
            
            # MirrorBot
            if elite_count >= 5 and recent_trades >= 50:
                ready_bots.append(("MirrorBot", f"{elite_count} elite traders, {recent_trades} recent trades"))
            elif elite_count >= 1:
                caution_bots.append(("MirrorBot", f"Only {elite_count} elite traders (need 5+)"))
            else:
                not_ready_bots.append(("MirrorBot", "No elite traders detected"))
            
            # PredictionEngine
            if resolved_with_prices >= 100 and trades_with_pnl >= 100:
                ready_bots.append(("PredictionEngine", f"{resolved_with_prices} resolved markets, {trades_with_pnl} trades with PnL"))
            elif resolved_with_prices >= 20:
                caution_bots.append(("PredictionEngine", f"Only {resolved_with_prices} resolved markets (need 100+ for best accuracy)"))
            else:
                not_ready_bots.append(("PredictionEngine", "Need more historical data (20+ resolved markets minimum)"))
            
            # LearningEngine
            if total_patterns >= 50 and confident_patterns >= 10:
                ready_bots.append(("LearningEngine", f"{total_patterns} patterns, {confident_patterns} high-confidence"))
            elif total_patterns >= 10:
                caution_bots.append(("LearningEngine", f"Only {confident_patterns} high-confidence patterns (collect more data)"))
            else:
                not_ready_bots.append(("LearningEngine", "No learning patterns - run learning first"))
            
            # EnsembleBot (absorbed CryptoPoliticalBot's signal-driven logic)
            if total_signals >= 100 and recent_signals >= 20:
                ready_bots.append(("EnsembleBot", f"{total_signals} total signals, {recent_signals} recent"))
            elif total_signals > 0:
                caution_bots.append(("EnsembleBot", f"Only {total_signals} signals (need consistent signal generation)"))
            else:
                not_ready_bots.append(("EnsembleBot", "No signals available - enable signal collection"))
            
            # Print readiness
            if ready_bots:
                print("✅ READY TO TRADE NOW:")
                for bot, reason in ready_bots:
                    print(f"  • {bot}: {reason}")
            
            if caution_bots:
                print("\n⚠️  CAN TRADE (WITH CAUTION - Limited Data):")
                for bot, reason in caution_bots:
                    print(f"  • {bot}: {reason}")
            
            if not_ready_bots:
                print("\n❌ NOT READY (Need More Data):")
                for bot, reason in not_ready_bots:
                    print(f"  • {bot}: {reason}")
            
            # ==================== RECOMMENDATIONS ====================
            print("\n\n" + "="*80)
            print("💡 RECOMMENDATIONS")
            print("="*80 + "\n")
            
            if ready_bots:
                print("✅ YOU CAN START TRADING NOW!")
                print("\n  Recommended approach:")
                print("  1. Enable paper trading mode first (SIMULATION_MODE=true)")
                print("  2. Start with bots marked 'READY TO TRADE NOW'")
                print("  3. Monitor for 24-48 hours")
                print("  4. Switch to live trading with 10% capital allocation")
                print("  5. Scale up gradually based on performance")
                
                print("\n  Quick start commands:")
                print("  ```bash")
                print("  # Enable paper trading")
                print("  echo 'SIMULATION_MODE=true' >> .env")
                print("  ")
                print("  # Start system")
                print("  python main.py")
                print("  ```")
            
            elif caution_bots:
                print("⚠️  You have limited data - recommend collecting more first")
                print("\n  Options:")
                print("  1. Paper trade to collect data (2-4 weeks)")
                print("  2. Run backtests on historical data")
                print("  3. Import more historical market/trade data")
                
                print("\n  Data collection mode:")
                print("  ```bash")
                print("  echo 'SIMULATION_MODE=true' >> .env")
                print("  echo 'DATA_COLLECTION_MODE=true' >> .env")
                print("  python main.py")
                print("  ```")
            
            else:
                print("❌ Need to collect data before trading")
                print("\n  Immediate actions:")
                print("  1. Run data ingestion scripts")
                print("  2. Import historical market data")
                print("  3. Run backtests to generate learning patterns")
                
                print("\n  Setup commands:")
                print("  ```bash")
                print("  # Import historical data")
                print("  python scripts/import_poly_data_to_db.py")
                print("  ")
                print("  # Run backtests")
                print("  python scripts/run_backtest.py --days 90")
                print("  ")
                print("  # Generate learning patterns")
                print("  python -c 'from base_engine.learning.learning_engine import LearningEngine; from base_engine.data.database import Database; import asyncio; async def learn(): db = Database(); await db.connect(); le = LearningEngine(db); await le.init(); await le.learn_from_price_history(); await db.close(); asyncio.run(learn())'")
                print("  ```")
            
            # ==================== DATA QUALITY SCORE ====================
            print("\n\n" + "="*80)
            print("📊 DATA QUALITY SCORE")
            print("="*80 + "\n")
            
            score = 0
            max_score = 100
            
            # Markets (20 points)
            if active_markets >= 100:
                score += 20
                print("  Markets: 20/20 ✅")
            elif active_markets >= 50:
                score += 15
                print(f"  Markets: 15/20 ⚠️  (have {active_markets}, ideal 100+)")
            elif active_markets >= 20:
                score += 10
                print(f"  Markets: 10/20 ⚠️  (have {active_markets}, ideal 100+)")
            else:
                print(f"  Markets: 0/20 ❌ (have {active_markets}, need 20+)")
            
            # Resolved markets (20 points)
            if resolved_markets >= 100:
                score += 20
                print("  Resolved Markets: 20/20 ✅")
            elif resolved_markets >= 50:
                score += 15
                print(f"  Resolved Markets: 15/20 ⚠️  (have {resolved_markets}, ideal 100+)")
            elif resolved_markets >= 20:
                score += 10
                print(f"  Resolved Markets: 10/20 ⚠️  (have {resolved_markets}, ideal 100+)")
            else:
                print(f"  Resolved Markets: 0/20 ❌ (have {resolved_markets}, need 20+)")
            
            # Elite traders (20 points)
            if elite_count >= 10:
                score += 20
                print("  Elite Traders: 20/20 ✅")
            elif elite_count >= 5:
                score += 15
                print(f"  Elite Traders: 15/20 ⚠️  (have {elite_count}, ideal 10+)")
            elif elite_count >= 1:
                score += 10
                print(f"  Elite Traders: 10/20 ⚠️  (have {elite_count}, ideal 10+)")
            else:
                print(f"  Elite Traders: 0/20 ❌ (have {elite_count}, need 1+)")
            
            # Price history (20 points)
            if resolved_with_prices >= 100:
                score += 20
                print("  Price History: 20/20 ✅")
            elif resolved_with_prices >= 50:
                score += 15
                print(f"  Price History: 15/20 ⚠️  (have {resolved_with_prices}, ideal 100+)")
            elif resolved_with_prices >= 20:
                score += 10
                print(f"  Price History: 10/20 ⚠️  (have {resolved_with_prices}, ideal 100+)")
            else:
                print(f"  Price History: 0/20 ❌ (have {resolved_with_prices}, need 20+)")
            
            # Learning patterns (20 points)
            if confident_patterns >= 20:
                score += 20
                print("  Learning Patterns: 20/20 ✅")
            elif confident_patterns >= 10:
                score += 15
                print(f"  Learning Patterns: 15/20 ⚠️  (have {confident_patterns}, ideal 20+)")
            elif confident_patterns >= 5:
                score += 10
                print(f"  Learning Patterns: 10/20 ⚠️  (have {confident_patterns}, ideal 20+)")
            else:
                print(f"  Learning Patterns: 0/20 ❌ (have {confident_patterns}, need 5+)")
            
            print(f"\n  TOTAL SCORE: {score}/{max_score}")
            
            if score >= 80:
                print("  📈 EXCELLENT - Ready for live trading")
            elif score >= 60:
                print("  📊 GOOD - Ready for paper trading, can go live with caution")
            elif score >= 40:
                print("  ⚠️  FAIR - Collect more data before trading")
            else:
                print("  ❌ POOR - Need significant data collection first")
            
    finally:
        if db.engine:
            await db.engine.dispose()
    
    print("\n" + "="*80)
    print("Analysis complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    asyncio.run(analyze_readiness())
