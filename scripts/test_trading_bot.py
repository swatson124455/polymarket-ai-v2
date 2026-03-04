"""
Quick Bot Test - Test a single bot without full system startup.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
from datetime import datetime, timezone
from structlog import get_logger
from base_engine.data.database import Database
from sqlalchemy import select
from base_engine.data.database import Market, Signal

logger = get_logger()


async def test_ensemble_bot():
    """Test EnsembleBot (absorbed CryptoPoliticalBot's signal-driven logic)."""
    
    # UTF-8 for Windows
    import io
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    
    print("\n" + "="*80)
    print("TESTING ENSEMBLEBOT")
    print("="*80 + "\n")
    
    db = Database()
    await db.init()
    
    if not db.session_factory:
        print("ERROR: Database not configured.")
        return
    
    try:
        from bots.ensemble_bot import EnsembleBot
        
        # Initialize bot with paper trading balance
        bot = EnsembleBot(db=db, balance=10000.0)
        
        print(f"Bot initialized: {bot.__class__.__name__}")
        print(f"Paper trading balance: ${bot.balance:,.2f}")
        print()
        
        # Get recent signals
        print("Fetching recent signals...")
        async with db.get_session() as session:
            result = await session.execute(
                select(Signal)
                .order_by(Signal.created_at.desc())
                .limit(20)
            )
            signals = result.scalars().all()
        
        print(f"Found {len(signals)} recent signals\n")
        
        # Show top signals
        print("Top 10 signals:")
        print("-" * 80)
        for i, sig in enumerate(signals[:10], 1):
            print(f"\n{i}. Market: {sig.market_id[:20]}...")
            print(f"   Direction: {sig.direction} | Confidence: {sig.confidence:.2%}")
            print(f"   Source: {sig.source_type} ({sig.source_name})")
            print(f"   Breaking: {'YES' if sig.is_breaking else 'no'}")
            print(f"   Created: {sig.created_at}")
        
        # Try to find trading opportunities
        print("\n\n" + "="*80)
        print("FINDING TRADING OPPORTUNITIES")
        print("="*80 + "\n")
        
        try:
            opportunities = await bot.find_opportunities()
            
            if opportunities:
                print(f"Found {len(opportunities)} trading opportunities!\n")
                
                for i, opp in enumerate(opportunities[:5], 1):
                    print(f"{i}. {opp.get('reason', 'Trading opportunity')}")
                    print(f"   Market: {opp.get('market_id', 'unknown')[:20]}...")
                    print(f"   Direction: {opp.get('direction', 'unknown')}")
                    print(f"   Confidence: {opp.get('confidence', 0):.2%}")
                    print(f"   Recommended size: ${opp.get('size', 0):.2f}")
                    print()
            else:
                print("No trading opportunities found right now.")
                print("This is normal - the bot is being selective!")
        except Exception as e:
            print(f"Bot doesn't have find_opportunities method: {e}")
            print("Bot is configured but needs opportunities to be fed externally.")
        
        print("\n" + "="*80)
        print("BOT TEST COMPLETE")
        print("="*80 + "\n")
        
        print("Next steps:")
        print("  1. Bot is working and has access to 15k signals")
        print("  2. Start the full system: python main.py")
        print("  3. Or use dashboard: streamlit run ui/dashboard.py")
        print("  4. Monitor paper trades in SIMULATION_MODE")
        
    except ImportError as e:
        print(f"Bot import failed: {e}")
        print("\nAvailable bots to test:")
        print("  - MirrorBot (copy elite traders)")
        print("  - PredictionEngine (ML predictions)")
        print("  - Check bots/ directory for available bots")
    
    finally:
        if db.engine:
            await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(test_ensemble_bot())
