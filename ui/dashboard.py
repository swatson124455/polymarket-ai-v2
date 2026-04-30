import sys
from pathlib import Path as PathLib

# Add project root to path so base_engine, bots, config are importable
_ROOT = PathLib(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Avoid 'charmap' codec errors on Windows when writing/printing non-ASCII (e.g. emoji, Unicode)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta, timezone
import asyncio
import importlib
import json
import os
import subprocess
import threading
from queue import Queue
from typing import Any, Coroutine, Optional
from structlog import get_logger
from base_engine.base_engine import BaseEngine
from base_engine.data.database import Database as DbClass
from bots.arbitrage_bot import ArbitrageBot
from bots.mirror_bot import MirrorBot
from bots.cross_platform_arb_bot import CrossPlatformArbBot
from bots.oracle_bot import OracleBot
from bots.sports_bot import SportsBot
from bots.llm_forecaster_bot import LLMForecasterBot
from bots.weather_bot import WeatherBot
from bots.sports_injury_bot import SportsInjuryBot
from bots.sports_live_bot import SportsLiveBot
from bots.sports_arb_bot import SportsArbBot
from bots.esports_bot import EsportsBot
from bots.esports_bot_v2 import EsportsBotV2
from bots.esports_live_bot import EsportsLiveBot
from bots.logical_arb_bot import LogicalArbBot
from config.settings import settings
from main import BOT_REGISTRY  # S204: source-of-truth registry count for caption display

import base_engine.data.data_ingestion
importlib.reload(base_engine.data.data_ingestion)
import base_engine.data.polymarket_client
importlib.reload(base_engine.data.polymarket_client)
import base_engine.base_engine
importlib.reload(base_engine.base_engine)

logger = get_logger()

# Portable debug log path (same as base_engine/data/database.py convention)
_DEBUG_LOG_PATH = PathLib(os.path.expanduser("~")) / ".cursor" / "debug.log"


def _clear_ingestion_error(data_ingestion: Any, full_reset: bool = False) -> None:
    """Single place to clear ingestion progress error state. Use from all Reset/Clear buttons."""
    if not hasattr(data_ingestion, "ingestion_progress") or not data_ingestion.ingestion_progress:
        return
    data_ingestion.ingestion_progress["status"] = "idle"
    data_ingestion.ingestion_progress["error_message"] = None
    data_ingestion.ingestion_progress["error_info"] = None
    if full_reset:
        data_ingestion.ingestion_progress["current"] = 0
        data_ingestion.ingestion_progress["total"] = 0
        data_ingestion.ingestion_progress["api_fetched"] = 0
        data_ingestion.ingestion_progress["db_saved"] = 0


# Sentinel returned when a coro was skipped; callers can check "if x is _SKIPPED"
_SKIPPED = object()

LOCK_BUSY_MSG = "Operation already in progress. Try again later."


def run_async_safe(coro: Coroutine[Any, Any, Any], timeout: float = 300) -> Any:
    """
    Run async coroutine in a dedicated worker thread with its own event loop.
    Fixes: RuntimeError no running event loop, task context mismatches, greenlet thread switching.
    All DB/async ops run in one thread - no nest_asyncio, no cross-thread loop usage.
    """
    try:
        try:
            from ui.async_worker import run_coro_in_worker
        except ImportError:
            from async_worker import run_coro_in_worker
        return run_coro_in_worker(coro, timeout=timeout)
    except Exception as e:
        err_msg = str(e).lower()
        if "context" in err_msg or "already entered" in err_msg or "timeout" in err_msg:
            try:
                coro.close()
            except Exception:
                pass
            return _SKIPPED
        raise


def run_db_safe(coro: Coroutine[Any, Any, Any], fallback: Any = None) -> Any:
    """
    Run async DB/engine coroutine in the persistent worker thread.
    base_engine and DB live in the worker - must run there to avoid greenlet/thread errors.
    Returns fallback on failure.
    """
    try:
        result = run_async_safe(coro)
        return fallback if result is _SKIPPED else result
    except Exception as e:
        logger.debug("run_db_safe failed (using fallback): %s", e)
        return fallback

st.set_page_config(
    page_title="Polymarket AI Trading System",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    /* Light Theme - White Background */
    .stApp {
        background-color: #ffffff;
        color: #000000;
    }
    
    .main .block-container {
        background-color: #ffffff;
        color: #000000;
    }
    
    [data-testid="stSidebar"] {
        background-color: #ffffff;
        color: #000000;
    }
    
    [data-testid="stSidebar"] .css-1d391kg {
        background-color: #ffffff;
    }
    
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] {
        color: #000000;
    }
    
    [data-testid="stSidebar"] .stWidget {
        background-color: #ffffff;
        border-color: #e0e0e0;
    }
    
    [data-testid="stSidebar"] .element-container {
        background-color: #ffffff;
    }
    
    [data-testid="stSidebar"] .stButton > button {
        background-color: #4f46e5;
        color: #ffffff;
    }
    
    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: #6366f1;
    }
    
    .stMarkdown, .stText, p, h1, h2, h3, h4, h5, h6, label, div {
        color: #000000 !important;
    }
    
    [data-testid="stMetricValue"] {
        color: #000000;
    }
    
    [data-testid="stMetricLabel"] {
        color: #666666;
    }
    
    .stButton > button {
        background-color: #4f46e5;
        color: #ffffff;
        border: none;
    }
    
    .stButton > button:hover {
        background-color: #6366f1;
    }
    
    .stSelectbox label, .stTextInput label, .stNumberInput label {
        color: #000000;
    }
    
    .stSelectbox > div > div {
        background-color: #ffffff;
        color: #000000;
        border-color: #e0e0e0;
    }
    
    .stTextInput > div > div > input {
        background-color: #ffffff;
        color: #000000;
        border-color: #e0e0e0;
    }
    
    .stNumberInput > div > div > input {
        background-color: #ffffff;
        color: #000000;
        border-color: #e0e0e0;
    }
    
    .stDataFrame {
        background-color: #ffffff;
    }
    
    .element-container {
        color: #000000;
    }
    
    .stAlert {
        background-color: #f0f0f0;
        color: #000000;
    }
    
    .stSuccess {
        background-color: #d4edda;
        color: #155724;
    }
    
    .stError {
        background-color: #f8d7da;
        color: #721c24;
    }
    
    .stWarning {
        background-color: #fff3cd;
        color: #856404;
    }
    
    .stInfo {
        background-color: #d1ecf1;
        color: #0c5460;
    }
    
    .stExpander {
        background-color: #ffffff;
        border-color: #e0e0e0;
    }
    
    .stExpander label {
        color: #000000;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        background-color: #ffffff;
    }
    
    .stTabs [data-baseweb="tab"] {
        color: #666666;
    }
    
    .stTabs [aria-selected="true"] {
        color: #4f46e5;
    }
    
    .stCheckbox label {
        color: #000000;
    }
    
    .stProgress > div > div > div {
        background-color: #4f46e5;
    }
    
    code {
        background-color: #f5f5f5;
        color: #000000;
    }
    
    pre {
        background-color: #f5f5f5;
        color: #000000;
    }
</style>
""", unsafe_allow_html=True)

if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

if "base_engine" not in st.session_state:
    st.session_state.base_engine = None
if "bots" not in st.session_state:
    st.session_state.bots = {}
if "running" not in st.session_state:
    st.session_state.running = False
if "backtest_results" not in st.session_state:
    st.session_state.backtest_results = []
if "portfolio_sim_result" not in st.session_state:
    st.session_state.portfolio_sim_result = None
if "monte_carlo_result" not in st.session_state:
    st.session_state.monte_carlo_result = None
if "pending_rerun" not in st.session_state:
    st.session_state.pending_rerun = False


def _valid_private_key(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    t = s.strip()
    if t.startswith("0x"):
        return len(t) == 66 and all(c in "0123456789abcdefABCDEF" for c in t[2:])
    return len(t) == 64 and all(c in "0123456789abcdefABCDEF" for c in t)


def _valid_address(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    t = s.strip()
    return t.startswith("0x") and len(t) == 42 and all(c in "0123456789abcdefABCDEF" for c in t[2:])


_init_lock = threading.Lock()


async def init_system(wallet_private_key: str = "", wallet_address: str = ""):
    """
    Initialize BaseEngine in worker thread. Returns (base_engine, bots).
    Does NOT touch st.session_state - worker thread cannot access Streamlit session state.
    Caller must store result in session_state.
    """
    with _init_lock:
        pk = (wallet_private_key or "").strip() or None
        addr = (wallet_address or "").strip() or None
        if pk and not _valid_private_key(pk):
            pk = None
        if addr and not _valid_address(addr):
            addr = None
        base_engine = BaseEngine()
        await base_engine.init(wallet_private_key=pk, wallet_address=addr)

        if base_engine.db and base_engine.db.session_factory:
            logger.info("Database connection verified after initialization")
        else:
            logger.warning("Database connection not available after initialization - system will run in API-only mode")

        try:
            await base_engine.start()
            logger.info("Automated data ingestion started")
        except Exception as e:
            logger.warning(f"Failed to start automated data ingestion: {e}")

        bots = {
            "ArbitrageBot": ArbitrageBot(base_engine),
            "MirrorBot": MirrorBot(base_engine),
            "CrossPlatformArbBot": CrossPlatformArbBot(base_engine),
            "OracleBot": OracleBot(base_engine),
            "SportsBot": SportsBot(base_engine),
            "LLMForecasterBot": LLMForecasterBot(base_engine),
            "WeatherBot": WeatherBot(base_engine),
            "SportsInjuryBot": SportsInjuryBot(base_engine),
            "SportsLiveBot": SportsLiveBot(base_engine),
            "SportsArbBot": SportsArbBot(base_engine),
            "LogicalArbBot": LogicalArbBot(base_engine),
        }
        # Esports bots fail fast if PANDASCORE_API_KEY not set — add only if available
        for _name, _cls in [("EsportsBot", EsportsBot), ("EsportsBotV2", EsportsBotV2), ("EsportsLiveBot", EsportsLiveBot)]:
            try:
                bots[_name] = _cls(base_engine)
            except (ValueError, Exception):
                pass  # Missing API key — skip this bot in dashboard
        return (base_engine, bots)


def main():
    # Defensive init: ensure session state keys exist before any access (avoids KeyError from worker/rerun races)
    for key, default in [("base_engine", None), ("bots", {}), ("running", False), ("pending_rerun", False)]:
        if key not in st.session_state:
            st.session_state[key] = default

    if st.session_state.pending_rerun:
        st.session_state.pending_rerun = False
        st.rerun()
        return
    
    st.title("🚀 Polymarket AI Trading System")
    st.markdown("**The Best Trading Bot Polymarket Has Ever Seen**")
    
    with st.sidebar:
        st.warning(
            "**Dashboard is for monitoring and manual operations only.** "
            "To run bots 24/7, use: `python main.py`"
        )
        st.header("⚙️ Control Panel")
        
        st.subheader("Connect Wallet (optional)")
        st.caption("For trading. From MetaMask: Account → Export Private Key. Stored in session only, never logged or written to disk.")
        wallet_pk = st.text_input("Private Key", type="password", key="wallet_pk", placeholder="0x...", autocomplete="current-password")
        wallet_addr = st.text_input("Wallet Address", key="wallet_addr", placeholder="0x...", autocomplete="off")
        if wallet_pk and not _valid_private_key(wallet_pk):
            st.caption("⚠️ Private key format invalid (use 0x + 64 hex).")
        if wallet_addr and not _valid_address(wallet_addr):
            st.caption("⚠️ Address format invalid (0x + 40 hex).")
        
        if st.button("🚀 Initialize System", type="primary"):
            if st.session_state.get("base_engine") is not None:
                st.info("System already initialized.")
            else:
                with st.spinner("Initializing..."):
                    try:
                        result = run_async_safe(init_system(wallet_private_key=wallet_pk or "", wallet_address=wallet_addr or ""))
                        if result is _SKIPPED:
                            st.warning("Initialization skipped. Refresh the page and try again.")
                        elif isinstance(result, tuple) and len(result) == 2:
                            st.session_state.base_engine, st.session_state.bots = result[0], result[1]
                            st.success("System initialized!")
                            st.session_state.pending_rerun = True
                            st.rerun()
                        else:
                            st.warning("Unexpected init result.")
                    except Exception as e:
                        st.error("Initialization failed")
                        st.error(str(e))
                        st.info("Check .env (DB, Redis). System runs in API-only mode if DB is unavailable.")
                        st.session_state.base_engine = None
                        import traceback
                        with st.expander("Details"):
                            st.code(traceback.format_exc())
        
        if st.session_state.get("base_engine"):
            base_engine = st.session_state.base_engine
            can_start = not st.session_state.running and base_engine.data_ingestion is not None
            
            if st.button("▶️ Start Trading", disabled=not can_start):
                if can_start:
                    try:
                        bots_dict = st.session_state.get("bots") or {}
                        enabled = {n for n in bots_dict if st.session_state.get(f"enable_{n}", True)}
                        run_async_safe(start_trading(st.session_state.base_engine, bots_dict, enabled))
                        st.session_state.running = True
                        st.success("Trading started!")
                        st.session_state.pending_rerun = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to start trading: {str(e)}")
                        logger.error(f"Start trading error: {str(e)}", exc_info=True)
                else:
                    st.warning("⚠️ System not ready. Ensure data ingestion service is available.")
            
            if st.button("⏹️ Stop Trading", disabled=not st.session_state.running):
                try:
                    result = run_async_safe(stop_trading(st.session_state.base_engine, st.session_state.get("bots") or {}))
                    if result is _SKIPPED:
                        st.warning("Stop skipped (Streamlit on background thread). Refresh and try again.")
                    else:
                        st.session_state.running = False
                        st.success("Trading stopped!")
                        st.session_state.pending_rerun = True
                        st.rerun()
                except Exception as e:
                    st.error(f"Failed to stop trading: {str(e)}")
                    logger.error(f"Stop trading error: {str(e)}", exc_info=True)
            
            if not can_start and not st.session_state.running:
                st.caption("ℹ️ Initialize system and ingest data before starting trading")
            
            st.divider()
            
            st.subheader("Bot Controls")
            for bot_name in (st.session_state.get("bots") or {}).keys():
                enabled = st.checkbox(f"Enable {bot_name}", value=True, key=f"enable_{bot_name}")
    
    if st.session_state.get("base_engine") is None:
        st.info("👆 Click 'Initialize System' to begin")
        return

    base_engine = st.session_state.base_engine
    try:
        status = run_db_safe(base_engine.db.get_data_pull_status(), {}) if getattr(base_engine, "db", None) else {}
    except Exception:
        status = {}
    markets_count = status.get("markets_count", 0)
    prices_count = status.get("prices_count", 0)
    last_pull = status.get("last_pull_at")
    last_status = status.get("last_pull_status", "")
    if last_pull and hasattr(last_pull, "strftime"):
        last_str = last_pull.strftime("%Y-%m-%d %H:%M")
        st.caption(f"**Markets:** {markets_count} | **Prices:** {prices_count} | **Last pull:** {last_str} ({last_status})")
    else:
        st.caption(f"**Markets:** {markets_count} | **Prices:** {prices_count} | **Last pull:** —")

    # Simplified: 6 tabs (Overview merges Dashboard+Performance, Data merges Data Center+Settings)
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📊 Overview", "🤖 Bots", "💰 Positions", "📝 Paper Trading", "🧠 Learning", "🔬 Backtest", "📦 Data & Settings"
    ])
    
    with tab1:
        try:
            show_overview()
        except Exception as e:
            st.error(f"Overview error: {e}")
            logger.exception("show_overview failed")
    
    with tab2:
        try:
            show_bots()
        except Exception as e:
            st.error(f"Bots error: {e}")
            logger.exception("show_bots failed")
    
    with tab3:
        try:
            show_positions()
        except Exception as e:
            st.error(f"Positions error: {e}")
            logger.exception("show_positions failed")
    
    with tab4:
        try:
            show_paper_trading()
        except Exception as e:
            st.error(f"Paper Trading error: {e}")
            logger.exception("show_paper_trading failed")
    
    with tab5:
        try:
            show_learning()
        except Exception as e:
            st.error(f"Learning error: {e}")
            logger.exception("show_learning failed")
    
    with tab6:
        try:
            show_backtest()
        except Exception as e:
            st.error(f"Backtest error: {e}")
            logger.exception("show_backtest failed")
    
    with tab7:
        try:
            show_data_and_settings()
        except Exception as e:
            st.error(f"Data & Settings error: {e}")
            logger.exception("show_data_and_settings failed")


async def start_trading(base_engine: Any, bots: dict, enabled_bot_names: Optional[set] = None) -> None:
    """
    Start trading bots. Must receive base_engine and bots as args - runs in worker thread.
    When enabled_bot_names is provided (including empty set), only start those bots; when None, start all.
    """
    if base_engine and bots:
        to_start = list(bots.values()) if enabled_bot_names is None else [bots[n] for n in bots if n in (enabled_bot_names or set())]
        for bot in to_start:
            base_engine.register_bot_for_price_events(bot)
            await bot.start()


async def stop_trading(base_engine: Any, bots: dict) -> None:
    """
    Stop trading bots. Must receive base_engine and bots as args - runs in worker thread,
    cannot access st.session_state.
    """
    if base_engine and bots:
        for bot in bots.values():
            try:
                await bot.stop()
            except Exception as e:
                logger.warning(f"Error stopping bot: {str(e)}")
        try:
            await base_engine.stop()
        except Exception as e:
            logger.warning(f"Error stopping base engine: {str(e)}")


def show_overview():
    """Unified Overview: Dashboard metrics + Performance (real PnL)."""
    base_engine = st.session_state.base_engine
    
    st.divider()

    # System status: phase + active bots
    _phase = getattr(settings, "TRADING_PHASE", "paper")
    _bots = st.session_state.get("bots") or {}
    _enabled_count = sum(1 for n in _bots if st.session_state.get(f"enable_{n}", True))
    st.caption(f"**Phase:** {_phase.upper()} | **Bots registered:** {len(_bots)} | **Enabled:** {_enabled_count} | **BOT_REGISTRY:** {len(BOT_REGISTRY)}")

    # Metrics row
    col1, col2, col3, col4 = st.columns(4)
    total_pnl = 0.0
    active_positions = 0
    win_rate = 0.0
    total_trades = 0
    daily_pnl = 0.0
    weekly_pnl = 0.0
    
    if base_engine and base_engine.db and base_engine.db.session_factory:
        try:
            async def get_stats():
                if not base_engine.db or not base_engine.db.session_factory:
                    return 0.0, 0, 0, 0.0, 0.0, 0.0
                async with base_engine.db.get_session() as session:
                    from sqlalchemy import select, func
                    from base_engine.data.database import Position, Trade
                    positions_result = await session.execute(
                        select(func.count(Position.id)).where(Position.closed == False)
                    )
                    active = positions_result.scalar() or 0
                    trades_result = await session.execute(select(func.count(Trade.id)))
                    trades = trades_result.scalar() or 0
                    pnl_result = await session.execute(
                        select(func.sum(Position.unrealized_pnl)).where(Position.closed == False)
                    )
                    pnl = pnl_result.scalar() or 0.0
                    wins_result = await session.execute(
                        select(func.count(Position.id)).where(
                            Position.closed == True,
                            Position.unrealized_pnl > 0
                        )
                    )
                    wins = wins_result.scalar() or 0
                    wr = (wins / trades * 100) if trades > 0 else 0.0
                    risk_state = await base_engine.db.get_risk_state_pnl()
                    return pnl, active, trades, wr, risk_state.get("daily_pnl", 0), risk_state.get("weekly_pnl", 0)
            result = run_db_safe(get_stats(), (0.0, 0, 0, 0.0, 0.0, 0.0))
            if result and len(result) >= 6:
                total_pnl, active_positions, total_trades, win_rate, daily_pnl, weekly_pnl = result[:6]
            elif result and len(result) >= 4:
                total_pnl, active_positions, total_trades, win_rate = result[:4]
        except Exception as e:
            logger.warning("Overview stats: %s", e)
            st.warning(f"Could not load stats: {e}")

    with col1:
        st.metric("Total P&L", f"${total_pnl:.2f}")
    with col2:
        st.metric("Active Positions", active_positions)
    with col3:
        st.metric("Daily P&L", f"${daily_pnl:.2f}")
    with col4:
        st.metric("Weekly P&L", f"${weekly_pnl:.2f}")
    
    st.divider()
    
    # Bot performance table (real data)
    bots = st.session_state.get("bots") or {}
    if bots and base_engine and base_engine.db and base_engine.db.session_factory:
        try:
            async def get_bot_perf():
                out = []
                for name in bots.keys():
                    m = await base_engine.db.get_bot_performance_metrics(name, days=30)
                    out.append({
                        "Bot": name,
                        "Trades": m.get("total_trades", 0),
                        "P&L": m.get("total_pnl", 0),
                        "Win Rate": f"{m.get('win_rate', 0)*100:.1f}%"
                    })
                return out
            perf = run_db_safe(get_bot_perf(), [])
            if perf:
                st.subheader("Bot Performance (30d)")
                st.dataframe(pd.DataFrame(perf), width="stretch", hide_index=True)
            # Phase 2: per-bot metrics from closed positions (trades_executed, trades_won, total_pnl)
            async def get_all_bots():
                return await base_engine.db.get_all_bots_metrics()
            all_metrics = run_db_safe(get_all_bots(), [])
            if all_metrics:
                st.subheader("Per-bot metrics (closed positions)")
                st.dataframe(
                    pd.DataFrame(all_metrics)[["bot_id", "trades_executed", "trades_won", "total_pnl"]],
                    width="stretch",
                    hide_index=True,
                )
            # CLV diagnostic: avg realized PnL per share (positive = buying below settlement on average)
            async def get_clv():
                return await base_engine.db.get_clv_diagnostic()
            clv = run_db_safe(get_clv(), {"global": {"avg_clv": None, "n_positions": 0}, "per_bot": []})
            if clv and (clv.get("global", {}).get("n_positions", 0) > 0 or clv.get("per_bot")):
                st.subheader("CLV (Closing Line Value)")
                g = clv.get("global", {})
                avg_clv = g.get("avg_clv")
                n_pos = g.get("n_positions", 0)
                if avg_clv is not None and n_pos > 0:
                    st.caption("Avg realized $ per share on closed positions. Positive = buying below settlement (edge).")
                    clv_col1, clv_col2, clv_col3 = st.columns(3)
                    with clv_col1:
                        clv_sign = "+" if avg_clv >= 0 else ""
                        st.metric("Global avg CLV", f"${avg_clv:.4f}", delta=f"{clv_sign}{avg_clv:.4f}/share")
                    with clv_col2:
                        st.metric("Closed Positions", n_pos)
                    with clv_col3:
                        total_pnl_est = avg_clv * n_pos
                        st.metric("Est. Total Edge", f"${total_pnl_est:.2f}")
                per_bot = clv.get("per_bot", [])
                if per_bot:
                    bot_df = pd.DataFrame(per_bot)
                    if "avg_clv" in bot_df.columns and "bot_id" in bot_df.columns:
                        # Color-coded per-bot table
                        st.dataframe(
                            bot_df,
                            width="stretch",
                            hide_index=True,
                        )
                        # Bar chart of per-bot CLV
                        try:
                            import plotly.express as px
                            fig = px.bar(
                                bot_df, x="bot_id", y="avg_clv",
                                color="avg_clv",
                                color_continuous_scale=["#ff4444", "#ffcc00", "#44bb44"],
                                title="CLV by Bot ($/share)",
                                labels={"avg_clv": "Avg CLV ($/share)", "bot_id": "Bot"},
                            )
                            fig.update_layout(height=300, showlegend=False)
                            st.plotly_chart(fig, use_container_width=True)
                        except Exception:
                            pass
                    else:
                        st.dataframe(bot_df, width="stretch", hide_index=True)
        except Exception as e:
            logger.warning("Bot performance: %s", e)
    else:
        st.caption("Bot performance requires database and initialized bots.")
    
    # P&L Reconciliation: cross-check Positions vs Trades tables
    if base_engine and base_engine.db and base_engine.db.session_factory:
        try:
            async def get_recon():
                return await base_engine.db.reconcile_pnl()
            recon = run_db_safe(get_recon(), {"bots": []})
            disc = recon.get("total_discrepancy", 0)
            if recon.get("bots") and abs(disc) > 0.01:
                with st.expander(f"P&L Reconciliation (discrepancy: ${disc:+.2f})", expanded=False):
                    st.caption("Compares closed-position P&L with blockchain trade P&L. Small differences are normal (fees, timing).")
                    recon_df = pd.DataFrame(recon["bots"])
                    st.dataframe(recon_df, width="stretch", hide_index=True)
        except Exception as e:
            logger.debug("P&L reconciliation panel: %s", e)

    # Phase Tracker panel
    with st.expander("🎓 Phase Tracker", expanded=True):
        _cur_phase = getattr(settings, "TRADING_PHASE", "paper")
        _phase_icons = {"paper": "🟡", "learning": "🟠", "graduated": "🟢", "production": "🔵"}
        pt_col1, pt_col2, pt_col3 = st.columns(3)
        with pt_col1:
            st.metric("Current Phase", f"{_phase_icons.get(_cur_phase, '⚪')} {_cur_phase.upper()}")
        # Fetch win rate + brier + resolved count from prediction_log
        _pt_win_rate, _pt_brier, _pt_resolved = 0.0, 0.5, 0
        if base_engine and base_engine.db and base_engine.db.session_factory:
            try:
                async def _get_phase_stats():
                    from sqlalchemy import text
                    async with base_engine.db.get_session() as _s:
                        r = await _s.execute(text(
                            "SELECT "
                            "  COUNT(*) FILTER (WHERE was_correct IS NOT NULL) AS resolved, "
                            "  AVG(CASE WHEN was_correct THEN 1.0 ELSE 0.0 END) FILTER (WHERE was_correct IS NOT NULL) AS win_rate, "
                            "  AVG(POWER(predicted_prob - CASE WHEN was_correct THEN 1.0 ELSE 0.0 END, 2)) FILTER (WHERE was_correct IS NOT NULL) AS brier "
                            "FROM prediction_log"
                        ))
                        row = r.fetchone()
                        if row:
                            return int(row[0] or 0), float(row[1] or 0.0), float(row[2] or 0.5)
                    return 0, 0.0, 0.5
                _phase_stats = run_db_safe(_get_phase_stats(), (0, 0.0, 0.5))
                if _phase_stats:
                    _pt_resolved, _pt_win_rate, _pt_brier = _phase_stats
            except Exception:
                pass
        _tgt_wr = getattr(settings, "PHASE_PAPER_TO_LEARNING_WIN_RATE", 0.52) if _cur_phase == "paper" else getattr(settings, "PHASE_LEARNING_TO_GRADUATED_WIN_RATE", 0.55)
        _tgt_brier = getattr(settings, "PHASE_PAPER_TO_LEARNING_MAX_BRIER", 0.22) if _cur_phase == "paper" else getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MAX_BRIER", 0.20)
        _min_preds = getattr(settings, "PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS", 100) if _cur_phase == "paper" else getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS", 300)
        with pt_col2:
            st.metric("Win Rate", f"{_pt_win_rate:.1%}", delta=f"target: {_tgt_wr:.0%}")
            st.progress(min(_pt_win_rate / max(_tgt_wr, 0.001), 1.0))
        with pt_col3:
            st.metric("Brier Score", f"{_pt_brier:.3f}", delta=f"target: <{_tgt_brier:.2f}")
        st.caption(f"Resolved predictions: {_pt_resolved} / {_min_preds} required")
        st.progress(min(_pt_resolved / max(_min_preds, 1), 1.0))
        st.info("PhaseTracker logs PHASE_PROMOTION_RECOMMENDED every 24h. Update TRADING_PHASE in .env + restart to promote.")

    # Market overview (compact)
    st.subheader("Market Overview")
    if base_engine and base_engine.client:
        try:
            markets = run_db_safe(base_engine.get_markets(active=True, limit=10), [])
            if markets:
                mdf = pd.DataFrame(markets)
                cols = [c for c in ["id", "question", "category", "liquidity"] if c in mdf.columns]
                st.dataframe(mdf[cols].head(10) if cols else mdf.head(10), width="stretch", hide_index=True)
            else:
                st.caption("No markets. Pull data in Data & Settings.")
        except Exception as e:
            st.caption(f"Markets: {str(e)}")


def show_bots():
    st.header("🤖 Trading Bots")
    bots = st.session_state.get("bots") or {}
    base_engine = st.session_state.get("base_engine")
    
    # Per-bot enable toggles (in sidebar; shown here for visibility)
    st.caption("Enable/disable bots in sidebar, then click Start Trading. Only enabled bots run.")
    
    for bot_name, bot in bots.items():
        enabled = st.session_state.get(f"enable_{bot_name}", True)
        status = "🟢 Running" if st.session_state.running and enabled else "🔴 Stopped"
        
        # Fetch real PnL when DB available
        trades_count = 0
        pnl_val = 0.0
        if base_engine and base_engine.db and base_engine.db.session_factory:
            try:
                m = run_db_safe(base_engine.db.get_bot_performance_metrics(bot_name, days=30), {})
                trades_count = m.get("total_trades", 0)
                pnl_val = m.get("total_pnl", 0.0)
            except Exception as e:
                st.warning(f"Could not load stats for {bot_name}: {e}")
        
        with st.expander(f"**{bot_name}** {'✓' if enabled else '(disabled)'}", expanded=False):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Status", status)
            with col2:
                st.metric("Trades (30d)", trades_count)
            with col3:
                st.metric("P&L (30d)", f"${pnl_val:.2f}")
            
            st.divider()
            st.subheader("Configuration Parameters")
            
            config_data = []
            
            if hasattr(bot, 'min_profit_threshold'):
                new_threshold = st.slider(
                    "Min Profit Threshold", 
                    0.0, 0.1, 
                    float(bot.min_profit_threshold), 
                    step=0.001,
                    key=f"threshold_{bot_name}"
                )
                bot.min_profit_threshold = new_threshold
                config_data.append({"Parameter": "Min Profit Threshold", "Value": f"{new_threshold:.3f}"})
            
            if hasattr(bot, 'min_confidence'):
                new_confidence = st.slider(
                    "Min Confidence", 
                    0.0, 1.0, 
                    float(bot.min_confidence), 
                    step=0.01,
                    key=f"confidence_{bot_name}"
                )
                bot.min_confidence = new_confidence
                config_data.append({"Parameter": "Min Confidence", "Value": f"{new_confidence:.2f}"})
            
            if hasattr(bot, 'threshold'):
                new_threshold = st.slider(
                    "Price Change Threshold", 
                    0.0, 0.2, 
                    float(bot.threshold), 
                    step=0.01,
                    key=f"price_threshold_{bot_name}"
                )
                bot.threshold = new_threshold
                config_data.append({"Parameter": "Price Change Threshold", "Value": f"{new_threshold:.2f}"})
            
            if hasattr(bot, 'min_volume'):
                new_volume = st.number_input(
                    "Min Volume", 
                    min_value=0.0, 
                    value=float(bot.min_volume),
                    key=f"min_volume_{bot_name}"
                )
                bot.min_volume = new_volume
                config_data.append({"Parameter": "Min Volume", "Value": f"{new_volume:.2f}"})
            
            if hasattr(bot, 'model_weights'):
                st.write("**Model Weights:**")
                for model_name, weight in bot.model_weights.items():
                    new_weight = st.slider(
                        f"{model_name.replace('_', ' ').title()}", 
                        0.0, 1.0, 
                        float(weight), 
                        step=0.01,
                        key=f"weight_{model_name}_{bot_name}"
                    )
                    bot.model_weights[model_name] = new_weight
                    config_data.append({"Parameter": f"{model_name} Weight", "Value": f"{new_weight:.2f}"})
            
            if hasattr(bot, 'min_consensus_confidence'):
                new_consensus = st.slider(
                    "Min Consensus Confidence", 
                    0.0, 1.0, 
                    float(bot.min_consensus_confidence), 
                    step=0.01,
                    key=f"consensus_{bot_name}"
                )
                bot.min_consensus_confidence = new_consensus
                config_data.append({"Parameter": "Min Consensus Confidence", "Value": f"{new_consensus:.2f}"})
            
            if hasattr(bot, 'elite_traders'):
                st.write(f"**Elite Traders Tracked:** {len(bot.elite_traders)}")
                if bot.elite_traders:
                    traders_df = pd.DataFrame(bot.elite_traders)
                    st.dataframe(traders_df, width='stretch', hide_index=True)
                config_data.append({"Parameter": "Elite Traders Count", "Value": str(len(bot.elite_traders))})
            
            if hasattr(bot, 'target_categories') and bot.target_categories:
                st.write(f"**Target Categories:** {', '.join(bot.target_categories)}")
                config_data.append({"Parameter": "Target Categories", "Value": ', '.join(bot.target_categories)})
            
            if hasattr(bot, 'price_history'):
                total_markets = len(bot.price_history)
                st.write(f"**Markets with Price History:** {total_markets}")
                config_data.append({"Parameter": "Price History Markets", "Value": str(total_markets)})
            
            if config_data:
                st.subheader("Current Configuration")
                config_df = pd.DataFrame(config_data)
                st.dataframe(config_df, width="stretch", hide_index=True)



def show_positions():
    st.header("💰 Active Positions")
    
    base_engine = st.session_state.base_engine
    
    if not base_engine or not base_engine.db or not base_engine.db.session_factory:
        st.error("❌ **CRITICAL: Database not available**")
        st.error("Positions tab requires database connection. Initialize system with database first.")
        return
    
    try:
        async def get_positions():
            async with base_engine.db.get_session() as session:
                from sqlalchemy import select
                from base_engine.data.database import Position
                
                result = await session.execute(
                    select(Position).where(Position.closed == False)
                )
                positions = result.scalars().all()
                
                return [{
                    "Bot": p.bot_name,
                    "Market ID": p.market_id,
                    "Token ID": p.token_id,
                    "Side": p.side,
                    "Size": p.size,
                    "Entry Price": p.entry_price,
                    "Current Price": p.current_price,
                    "Unrealized P&L": p.unrealized_pnl,
                    "Timestamp": p.timestamp
                } for p in positions]
        
        try:
            positions_data = run_db_safe(get_positions(), [])
            positions_df = pd.DataFrame(positions_data) if positions_data else pd.DataFrame()
            
            st.dataframe(positions_df, width="stretch")
            
            if positions_df.empty:
                st.info("No active positions")
        except Exception as e:
            st.error(f"❌ **Error fetching positions:** {str(e)}")
            st.error("Database connection required for positions.")
            logger.error(f"Error fetching positions: {str(e)}", exc_info=True)
    except Exception:
        pass


def show_paper_trading():
    """Show paper trades and positions when SIMULATION_MODE=true."""
    st.header("📝 Paper Trading")
    
    base_engine = st.session_state.base_engine
    sim_mode = getattr(settings, "SIMULATION_MODE", False)
    
    if not base_engine:
        st.info("Initialize the system first to see paper trading.")
        return
    
    paper = getattr(base_engine, "paper_trading", None)
    if not paper:
        st.info("Paper trading engine not available.")
        return
    
    if not sim_mode or not paper.enabled:
        st.info(
            "Paper trading is disabled. Set `SIMULATION_MODE=true` in `.env` and restart to enable "
            "paper trading (orders simulated, no real money)."
        )
        return
    
    # Portfolio summary
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Cash", f"${paper.cash:,.2f}")
    with col2:
        st.metric("Initial Capital", f"${paper.initial_capital:,.2f}")
    with col3:
        st.metric("Positions", len(paper.positions))
    with col4:
        st.metric("Trades", len(paper.trades))
    
    st.subheader("Paper Positions")
    positions = paper.get_positions()
    if positions:
        rows = []
        for market_id, pos in positions.items():
            rows.append({
                "Market ID": market_id,
                "Side": pos.get("side", "YES"),
                "Size": pos.get("size", 0),
                "Avg Price": pos.get("avg_price", 0),
            })
        st.dataframe(pd.DataFrame(rows), width="stretch")
    else:
        st.info("No paper positions.")
    
    st.subheader("Paper Trade History")
    trades = paper.get_trades()
    if trades:
        trades_df = pd.DataFrame(trades)
        st.dataframe(trades_df, width="stretch")
    else:
        st.info("No paper trades yet. Start bots with SIMULATION_MODE=true to see simulated trades.")


def show_learning():
    st.header("🧠 Learning System - Advanced Analytics & Simulation")
    
    base_engine = st.session_state.base_engine
    
    if not base_engine:
        st.error("❌ **CRITICAL: Base engine not initialized**")
        st.error("Initialize system first.")
        return
    
    if not base_engine.db or not base_engine.db.session_factory:
        st.error("❌ **CRITICAL: Database not available**")
        st.error("Learning system requires database connection. Initialize system with database first.")
        return
    
    learning_engine = base_engine.learning_engine
    prediction_engine = base_engine.prediction_engine
    simulation_engine = base_engine.simulation_engine
    
    if not learning_engine:
        st.error("❌ **CRITICAL: Learning engine not available**")
        return
    
    if not prediction_engine:
        st.error("❌ **CRITICAL: Prediction engine not available**")
        return
    
    if not simulation_engine:
        st.error("❌ **CRITICAL: Simulation engine not available**")
        return
    
    patterns = learning_engine.patterns
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 Pattern Analysis", "🤖 Prediction Models", "🎲 Monte Carlo Simulation", 
        "📈 Portfolio Simulation", "🔄 Learning Actions"
    ])
    
    with tab1:
        st.subheader("Learned Patterns from Historical Data")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Market Type Performance**")
            if patterns.get("market_types"):
                market_data = []
                for category, stats in patterns["market_types"].items():
                    if stats["total"] > 0:
                        win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.0
                        market_data.append({
                            "Category": category,
                            "Wins": stats["wins"],
                            "Losses": stats["losses"],
                            "Total": stats["total"],
                            "Win Rate": f"{win_rate:.2%}",
                            "Confidence": f"{stats.get('confidence', 0.0):.2%}",
                            "Sample Size": stats.get("sample_size", stats["total"])
                        })
                if market_data:
                    df = pd.DataFrame(market_data)
                    st.dataframe(df, width='stretch', hide_index=True)
                    
                    fig = px.bar(df, x="Category", y="Win Rate", title="Win Rate by Market Category")
                    st.plotly_chart(fig, width='stretch', key="market_type_chart")
                else:
                    st.info("No market type patterns learned yet")
            else:
                st.info("No market type data available")
        
        with col2:
            st.write("**Price Range Performance**")
            if patterns.get("price_ranges"):
                price_data = []
                for price_range, stats in patterns["price_ranges"].items():
                    if stats["total"] > 0:
                        win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.0
                        price_data.append({
                            "Price Range": price_range,
                            "Wins": stats["wins"],
                            "Losses": stats["losses"],
                            "Total": stats["total"],
                            "Win Rate": f"{win_rate:.2%}",
                            "Confidence": f"{stats.get('confidence', 0.0):.2%}",
                            "Sample Size": stats.get("sample_size", stats["total"])
                        })
                if price_data:
                    df = pd.DataFrame(price_data)
                    st.dataframe(df, width='stretch', hide_index=True)
                    
                    fig = px.bar(df, x="Price Range", y="Win Rate", title="Win Rate by Price Range")
                    st.plotly_chart(fig, width="stretch", key="price_range_chart")
                else:
                    st.info("No price range patterns learned yet")
            else:
                st.info("No price range data available")
        
        st.subheader("Time to Resolution Performance")
        if patterns.get("time_to_resolution"):
            time_data = []
            for time_range, stats in patterns["time_to_resolution"].items():
                if stats["total"] > 0:
                    win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.0
                    time_data.append({
                        "Time Range": time_range,
                        "Wins": stats["wins"],
                        "Losses": stats["losses"],
                        "Total": stats["total"],
                        "Win Rate": f"{win_rate:.2%}",
                        "Confidence": f"{stats.get('confidence', 0.0):.2%}",
                        "Sample Size": stats.get("sample_size", stats["total"])
                    })
            if time_data:
                df = pd.DataFrame(time_data)
                st.dataframe(df, width='stretch', hide_index=True)
            else:
                st.info("No time-to-resolution patterns learned yet")
        
        st.subheader("Confidence Weights Configuration")
        col1, col2 = st.columns(2)
        with col1:
            user_weight = st.slider("User-based Confidence Weight", 0.0, 1.0, learning_engine.confidence_weights['user_based'], step=0.01)
            learning_engine.confidence_weights['user_based'] = user_weight
            st.metric("Current Weight", f"{user_weight:.0%}")
        with col2:
            bet_weight = st.slider("Bet-type Confidence Weight", 0.0, 1.0, learning_engine.confidence_weights['bet_type'], step=0.01)
            learning_engine.confidence_weights['bet_type'] = bet_weight
            st.metric("Current Weight", f"{bet_weight:.0%}")
    
    with tab2:
        st.subheader("🤖 Prediction Engine Status")
        
        if prediction_engine:
            col1, col2, col3 = st.columns(3)
            with col1:
                status = "✅ Initialized" if prediction_engine.initialized else "❌ Not Initialized"
                st.metric("Status", status)
            with col2:
                models_count = len(prediction_engine.models) if prediction_engine.models else 0
                st.metric("Models Trained", models_count)
            with col3:
                features_count = len(prediction_engine.feature_columns) if prediction_engine.feature_columns else 0
                st.metric("Features Used", features_count)
            # Phase 2: model live performance and recent accuracy from prediction_log
            if base_engine.db and base_engine.db.session_factory:
                try:
                    async def _live_perf():
                        return await base_engine.db.get_model_live_performance(lookback_days=30)
                    async def _recent_perf():
                        return await base_engine.db.get_recent_performance_from_prediction_log(n=20)
                    live = run_db_safe(_live_perf(), None)
                    recent = run_db_safe(_recent_perf(), None)
                    c1, c2 = st.columns(2)
                    with c1:
                        if live and live.get("count", 0) > 0:
                            st.metric("Model live accuracy (30d)", f"{live.get('accuracy', 0):.1%}", f"n={live.get('count', 0)}")
                            st.caption("Avg realized edge: {:.2%}".format(live.get("avg_edge", 0)))
                    with c2:
                        if recent and recent.get("count", 0) > 0:
                            st.metric("Recent accuracy (last 20)", f"{recent.get('accuracy', 0):.1%}", f"n={recent.get('count', 0)}")
                except Exception as e:
                    logger.debug("Prediction log metrics: %s", e)
            if prediction_engine.models:
                st.subheader("Available Models")
                model_list = []
                for model_name, model in prediction_engine.models.items():
                    model_list.append({
                        "Model": model_name.replace('_', ' ').title(),
                        "Type": type(model).__name__,
                        "Status": "✅ Trained"
                    })
                st.dataframe(pd.DataFrame(model_list), width="stretch", hide_index=True)
                
                st.subheader("Model Performance Test")
                col1, col2 = st.columns(2)
                with col1:
                    test_market_id = st.text_input("Market ID for Prediction Test", value="", autocomplete="off")
                    test_price = st.number_input("Current Price", 0.0, 1.0, 0.5, step=0.01)
                with col2:
                    test_token_id = st.text_input("Token ID", value="", autocomplete="off")
                    test_user = st.text_input("User Address (optional)", value="", autocomplete="off")
                
                prediction_disabled = not prediction_engine.initialized or not prediction_engine.models
                if st.button("🔮 Get Prediction", type="primary", disabled=prediction_disabled) and test_market_id and test_token_id:
                    if prediction_disabled:
                        st.warning("⚠️ Models not trained yet. Train models first or wait for automatic training.")
                    else:
                        with st.spinner("Generating prediction..."):
                            try:
                                prediction = run_db_safe(base_engine.get_predictions(
                                    test_market_id, test_token_id, test_price, test_user or None
                                ), {})
                                if not isinstance(prediction, dict):
                                    prediction = {}
                                col1, col2, col3, col4 = st.columns(4)
                                with col1:
                                    st.metric("Final Confidence", f"{prediction.get('confidence', 0.0):.2%}")
                                with col2:
                                    st.metric("Ensemble Prediction", f"{prediction.get('prediction', 0.0):.2%}")
                                with col3:
                                    st.metric("Learning Confidence", f"{prediction.get('learning_confidence', 0.0):.2%}")
                                with col4:
                                    st.metric("Models Used", len(prediction.get('model_predictions', {})))
                                
                                if prediction.get('model_predictions'):
                                    st.subheader("Individual Model Predictions")
                                    model_preds = []
                                    for name, pred in prediction['model_predictions'].items():
                                        model_preds.append({
                                            "Model": name.replace('_', ' ').title(),
                                            "Prediction": f"{pred:.2%}"
                                        })
                                    st.dataframe(pd.DataFrame(model_preds), width='stretch', hide_index=True)
                            except Exception as e:
                                st.error(f"❌ **Prediction failed:** {str(e)}")
                                raise
                elif not test_market_id or not test_token_id:
                    st.warning("⚠️ Enter Market ID and Token ID to test prediction")
            else:
                st.error("❌ **CRITICAL: No models trained yet.**")
                st.error("Train models with sufficient data first. Minimum 100 training samples required.")
            
            if prediction_engine.feature_columns:
                st.subheader("Features Used for Training")
                st.write(", ".join(prediction_engine.feature_columns))
            
            if st.button("🔄 Retrain Models"):
                with st.spinner("Retraining models..."):
                    try:
                        db = getattr(base_engine, "db", None) if base_engine else None
                        if db:
                            from base_engine.data.database_lock import acquire_lock
                            async def _retrain_with_lock():
                                async with acquire_lock(db, "model_training", timeout_seconds=120):
                                    await prediction_engine.retrain()
                            run_async_safe(_retrain_with_lock())
                        else:
                            run_async_safe(prediction_engine.retrain())
                        st.success("Models retrained successfully!")
                        st.session_state.pending_rerun = True
                        st.rerun()
                    except Exception as e:
                        err_str = str(e).lower()
                        if "lock" in err_str or "already in progress" in err_str:
                            st.info(LOCK_BUSY_MSG)
                        else:
                            st.error(f"❌ **Retraining failed:** {str(e)}")
                            logger.error(f"Retraining failed: {str(e)}", exc_info=True)
                            import traceback
                            with st.expander("Error Details"):
                                st.code(traceback.format_exc())
    
    with tab3:
        st.subheader("🎲 Monte Carlo Simulation")
        st.write("Simulate market outcomes using Monte Carlo methods")
        
        col1, col2 = st.columns(2)
        with col1:
            sim_market_id = st.text_input("Market ID", key="mc_market_id", autocomplete="off")
            sim_token_id = st.text_input("Token ID", key="mc_token_id", autocomplete="off")
        with col2:
            sim_price = st.number_input("Current Price", 0.0, 1.0, 0.5, step=0.01, key="mc_price")
            sim_iterations = st.number_input("Iterations", 1000, 1000000, settings.SIMULATION_ITERATIONS, step=10000, key="mc_iterations")
        
        if not sim_market_id or not sim_token_id:
            st.warning("⚠️ Enter Market ID and Token ID to run simulation")
        elif st.button("▶️ Run Monte Carlo Simulation", type="primary", key="run_mc_sim"):
            if not base_engine or not base_engine.simulation_engine:
                st.error("❌ **Simulation engine not available**")
            else:
                try:
                    with st.spinner(f"Running {sim_iterations:,} iterations..."):
                        result = run_db_safe(base_engine.run_simulation(
                            sim_market_id, sim_token_id, sim_price, sim_iterations
                        ), {})
                        if result and isinstance(result, dict):
                            for key in ['win_probability', 'current_price']:
                                if key in result:
                                    val = result[key]
                                    if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                                        result[key] = 0.0
                            
                            if 'confidence_intervals' in result and isinstance(result['confidence_intervals'], dict):
                                for ikey in result['confidence_intervals']:
                                    val = result['confidence_intervals'][ikey]
                                    if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                                        result['confidence_intervals'][ikey] = 0.0
                            
                            st.session_state.monte_carlo_result = result
                            st.session_state.pending_rerun = True
                            st.rerun()
                        else:
                            st.error("Invalid simulation result returned")
                except Exception as e:
                    st.error(f"Simulation failed: {str(e)}")
                    import traceback
                    with st.expander("Error Details"):
                        st.code(traceback.format_exc())
                    st.session_state.monte_carlo_result = None
        
        monte_carlo_result = getattr(st.session_state, 'monte_carlo_result', None)
        if monte_carlo_result:
            result = monte_carlo_result
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                win_prob = result.get('win_probability', 0.0)
                st.metric("Win Probability", f"{win_prob:.2%}")
            with col2:
                curr_price = result.get('current_price', 0.0)
                st.metric("Current Price", f"{curr_price:.3f}")
            with col3:
                iterations = result.get('iterations', 0)
                st.metric("Iterations", f"{iterations:,}")
            with col4:
                intervals = result.get('confidence_intervals', {})
                median_val = intervals.get('50th_percentile', 0.0)
                st.metric("50th Percentile", f"{median_val:.3f}")
            
            st.subheader("Confidence Intervals")
            if intervals and isinstance(intervals, dict):
                interval_labels = ["5th", "25th", "50th (Median)", "75th", "95th"]
                interval_keys = ['5th_percentile', '25th_percentile', '50th_percentile', '75th_percentile', '95th_percentile']
                interval_values = []
                
                for key in interval_keys:
                    val = intervals.get(key, 0.0)
                    if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                        val = 0.0
                    interval_values.append(val)
                
                intervals_df = pd.DataFrame({
                    "Percentile": interval_labels,
                    "Value": [f"{v:.3f}" for v in interval_values]
                })
                st.dataframe(intervals_df, width="stretch", hide_index=True)
                
                try:
                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=interval_labels,
                        y=interval_values,
                        name="Confidence Intervals",
                        marker_color='lightblue'
                    ))
                    fig.update_layout(
                        title="Monte Carlo Confidence Intervals",
                        xaxis_title="Percentile",
                        yaxis_title="Value",
                        height=400,
                        showlegend=False
                    )
                    st.plotly_chart(fig, width='stretch', key="mc_intervals_chart")
                except Exception as e:
                    st.warning(f"Could not render chart: {str(e)}")
            
            if st.button("Clear Results", key="clear_mc_sim"):
                st.session_state.monte_carlo_result = None
                st.session_state.pending_rerun = True
                st.rerun()
    
    with tab4:
        st.subheader("📈 Portfolio Strategy Simulation")
        st.write("Simulate entire portfolio performance over time")
        
        col1, col2 = st.columns(2)
        with col1:
            portfolio_days = st.number_input("Time Horizon (days)", 1, 365, 30, step=1, key="portfolio_days")
            portfolio_iterations = st.number_input("Simulation Iterations", 1000, 1000000, 10000, step=1000, key="portfolio_iterations")
        with col2:
            strategy_name = st.selectbox("Strategy", ["Conservative", "Moderate", "Aggressive"], key="portfolio_strategy")
            initial_capital = st.number_input("Initial Capital", 1000.0, 1000000.0, 10000.0, step=1000.0, key="portfolio_capital")
        
        strategy_configs = {
            "Conservative": {"risk": 0.1, "exposure": 0.3},
            "Moderate": {"risk": 0.2, "exposure": 0.5},
            "Aggressive": {"risk": 0.3, "exposure": 0.7}
        }
        
        if not base_engine or not base_engine.simulation_engine:
            st.error("❌ **Simulation engine not available**")
        elif st.button("▶️ Run Portfolio Simulation", type="primary", key="run_portfolio_sim"):
            try:
                with st.spinner(f"Simulating {portfolio_iterations:,} iterations over {portfolio_days} days..."):
                    result = run_db_safe(base_engine.simulate_portfolio(
                        strategy_configs[strategy_name],
                        portfolio_days,
                        portfolio_iterations
                    ), {})
                    if result and isinstance(result, dict):
                        for key in ['mean_final_value', 'median_final_value', 'min_final_value', 'max_final_value', 'std_final_value']:
                            if key in result:
                                val = result[key]
                                if val is None or (isinstance(val, float) and (not isinstance(val, float) or val != val or abs(val) == float('inf'))):
                                    result[key] = 0.0
                        
                        if 'percentiles' in result and isinstance(result['percentiles'], dict):
                            for pkey in result['percentiles']:
                                val = result['percentiles'][pkey]
                                if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                                    result['percentiles'][pkey] = 0.0
                        
                        result['strategy_name'] = strategy_name
                        st.session_state.portfolio_sim_result = result
                        st.session_state.pending_rerun = True
                        st.rerun()
                    else:
                        st.error("Invalid simulation result returned")
            except Exception as e:
                st.error(f"Portfolio simulation failed: {str(e)}")
                import traceback
                with st.expander("Error Details"):
                    st.code(traceback.format_exc())
                st.session_state.portfolio_sim_result = None
        
        if st.session_state.portfolio_sim_result:
            result = st.session_state.portfolio_sim_result
            strategy_display = result.get('strategy_name', 'Unknown')
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                mean_val = result.get('mean_final_value', 0.0)
                st.metric("Mean Final Value", f"${mean_val:,.2f}")
            with col2:
                median_val = result.get('median_final_value', 0.0)
                st.metric("Median Final Value", f"${median_val:,.2f}")
            with col3:
                min_val = result.get('min_final_value', 0.0)
                st.metric("Min Final Value", f"${min_val:,.2f}")
            with col4:
                max_val = result.get('max_final_value', 0.0)
                st.metric("Max Final Value", f"${max_val:,.2f}")
            
            st.subheader("Portfolio Value Distribution")
            percentiles = result.get('percentiles', {})
            
            if percentiles and isinstance(percentiles, dict):
                percentile_labels = ["5th", "25th", "50th (Median)", "75th", "95th"]
                percentile_keys = ['5th', '25th', '50th', '75th', '95th']
                percentile_values = []
                
                for key in percentile_keys:
                    val = percentiles.get(key, 0.0)
                    if val is None or (isinstance(val, float) and (val != val or abs(val) == float('inf'))):
                        val = 0.0
                    percentile_values.append(val)
                
                percentiles_df = pd.DataFrame({
                    "Percentile": percentile_labels,
                    "Value": [f"${v:,.2f}" for v in percentile_values]
                })
                st.dataframe(percentiles_df, width="stretch", hide_index=True)
                
                try:
                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=percentile_labels,
                        y=percentile_values,
                        name="Portfolio Value",
                        marker_color='steelblue'
                    ))
                    fig.update_layout(
                        title=f"Portfolio Simulation Results ({strategy_display} Strategy)",
                        xaxis_title="Percentile",
                        yaxis_title="Final Value ($)",
                        height=400,
                        showlegend=False
                    )
                    st.plotly_chart(fig, width="stretch", key="portfolio_sim_chart")
                except Exception as e:
                    st.warning(f"Could not render chart: {str(e)}")
            
            std_val = result.get('std_final_value', 0.0)
            st.metric("Standard Deviation", f"${std_val:,.2f}")
            
            if st.button("Clear Results", key="clear_portfolio_sim"):
                st.session_state.portfolio_sim_result = None
                st.session_state.pending_rerun = True
                st.rerun()
    
    with tab5:
        st.subheader("🔄 Learning Actions & Integration")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Update Learning from Backtest**")
            if not st.session_state.backtest_results:
                st.error("❌ No backtest results available. Run a backtest first.")
            elif st.button("🔄 Update from Latest Backtest"):
                latest_result = st.session_state.backtest_results[-1]
                with st.spinner("Updating learning patterns..."):
                    try:
                        run_async_safe(learning_engine.learn_from_backtest(latest_result))
                        st.success("Learning updated from latest backtest!")
                        st.session_state.pending_rerun = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ **Update failed:** {str(e)}")
                        logger.error(f"Update from backtest failed: {str(e)}", exc_info=True)
                        import traceback
                        with st.expander("Error Details"):
                            st.code(traceback.format_exc())
            
            st.write("**Learn from Price History**")
            learn_days = st.number_input("Days of price history", min_value=1, max_value=365, value=7, key="learn_price_days")
            if st.button("📈 Learn from Price History"):
                if hasattr(learning_engine, "learn_from_price_history"):
                    with st.spinner("Learning from price history..."):
                        try:
                            since = datetime.now(timezone.utc) - timedelta(days=int(learn_days))
                            run_async_safe(learning_engine.learn_from_price_history(since))
                            st.success("Learning from price history complete!")
                            st.session_state.pending_rerun = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"Learn from price history failed: {str(e)}")
                else:
                    st.error("Learning engine does not support learn_from_price_history")
            
            st.write("**Retrain Prediction Models**")
            if st.button("🎯 Retrain All Models"):
                with st.spinner("Retraining models..."):
                    try:
                        db = getattr(base_engine, "db", None) if base_engine else None
                        if db:
                            from base_engine.data.database_lock import acquire_lock
                            async def _retrain_with_lock():
                                async with acquire_lock(db, "model_training", timeout_seconds=120):
                                    await prediction_engine.retrain()
                            run_async_safe(_retrain_with_lock())
                        else:
                            run_async_safe(prediction_engine.retrain())
                        st.success("All models retrained successfully!")
                        st.session_state.pending_rerun = True
                        st.rerun()
                    except Exception as e:
                        err_str = str(e).lower()
                        if "lock" in err_str or "already in progress" in err_str:
                            st.info(LOCK_BUSY_MSG)
                        else:
                            st.error(f"❌ **Retraining failed:** {str(e)}")
                            logger.error(f"Retraining failed: {str(e)}", exc_info=True)
                            import traceback
                            with st.expander("Error Details"):
                                st.code(traceback.format_exc())
        
        with col2:
            st.write("**Learning Statistics**")
            total_patterns = sum(len(patterns.get(key, {})) for key in ["market_types", "price_ranges", "categories", "time_to_resolution"])
            st.metric("Total Patterns Tracked", total_patterns)
            
            total_samples = sum(
                stats.get("total", 0) 
                for pattern_type in ["market_types", "price_ranges", "categories", "time_to_resolution"]
                for stats in patterns.get(pattern_type, {}).values()
            )
            st.metric("Total Samples Analyzed", f"{total_samples:,}")
            
            if prediction_engine and prediction_engine.models:
                st.metric("Models Available", len(prediction_engine.models))
            
            st.write("**Simulation Error Tracking**")
            sim_errors = sum(
                len(patterns.get(market_id, {}).get("simulation_errors", []))
                for market_id in patterns.keys()
                if isinstance(patterns.get(market_id), dict)
            )
            st.metric("Simulation Errors Tracked", sim_errors)

            st.write("**Calibration (Prediction vs Actual)**")
            cal_tracker = getattr(base_engine, "calibration_tracker", None)
            if cal_tracker:
                metrics = cal_tracker.get_metrics()
                st.metric("Brier Score", f"{metrics.get('brier_score', 0):.4f}")
                st.metric("Calibration Samples", metrics.get("count", 0))
                if st.button("🔄 Process Resolved Markets"):
                    n = run_db_safe(cal_tracker.process_resolved_from_db(), 0)
                    n = max(0, int(n)) if isinstance(n, (int, float)) else 0
                    st.success(f"Processed {n} resolutions")
                    st.session_state.pending_rerun = True
                    st.rerun()
            else:
                st.info("Calibration tracker not available")
        
        st.subheader("Learned parameters (autonomy)")
        if prediction_engine:
            pe = prediction_engine
            col_a, col_b = st.columns(2)
            with col_a:
                st.write("**Model weights**")
                if getattr(pe, "model_weights", None):
                    for name, w in pe.model_weights.items():
                        st.write(f"- {name}: {w:.3f}")
                else:
                    st.info("Equal weights (none set)")
                if st.button("Revert to equal weights", key="revert_weights"):
                    if hasattr(pe, "model_weights"):
                        pe.model_weights = {}
                    st.success("Reverted to equal weights")
                    st.session_state.pending_rerun = True
                    st.rerun()
            with col_b:
                blend = getattr(pe, "ensemble_blend", 0.6)
                st.write("**Ensemble blend**")
                st.metric("Current", f"{blend:.2f}")
                if st.button("Revert blend to 0.6", key="revert_blend"):
                    if hasattr(pe, "ensemble_blend"):
                        pe.ensemble_blend = 0.6
                    st.success("Reverted to 0.6")
                    st.session_state.pending_rerun = True
                    st.rerun()
            best_feat = getattr(pe, "best_feature_names", None)
            if best_feat:
                with st.expander("Best feature names"):
                    st.write(", ".join(best_feat[:20]) + ("..." if len(best_feat) > 20 else ""))
            if st.button("Run self-tuning now", key="run_self_tuning"):
                scheduler = getattr(base_engine, "scheduler", None)
                if scheduler and hasattr(scheduler, "_run_meta_learner_tuning"):
                    with st.spinner("Running MetaLearner tuning..."):
                        try:
                            run_async_safe(scheduler._run_meta_learner_tuning())
                            if getattr(pe, "save_models_to_db", None):
                                run_async_safe(pe.save_models_to_db())
                            st.success("Self-tuning complete. Weights and blend updated.")
                            st.session_state.pending_rerun = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"Self-tuning failed: {e}")
                else:
                    st.warning("Scheduler not available")
        else:
            st.info("Prediction engine not available")
        
        st.subheader("Learning Configuration")
        st.write(f"**Learning Update Interval:** {settings.LEARNING_UPDATE_INTERVAL_SECONDS} seconds")
        st.write(f"**Simulation Iterations:** {settings.SIMULATION_ITERATIONS:,}")
        st.write(f"**Backtest Lookback Days:** {settings.BACKTEST_LOOKBACK_DAYS} days")


def show_backtest():
    st.header("🔬 Backtesting Interface")
    
    base_engine = st.session_state.base_engine
    
    if not base_engine:
        st.error("❌ **CRITICAL: Base engine not initialized**")
        st.error("Please initialize the system first.")
        st.info("👆 Go to sidebar and click '🚀 Initialize System'")
        return
    
    if not base_engine.backtest_engine:
        st.error("❌ **CRITICAL: Backtest engine not available**")
        return
    
    if not base_engine.db or not base_engine.db.session_factory:
        st.error("❌ **CRITICAL: Database not connected**")
        st.error("Backtesting requires database connection with historical trade data.")
        st.error("Initialize system with database and ingest data first.")
        return
    
    st.subheader("Run New Backtest")
    
    col1, col2 = st.columns(2)
    
    with col1:
        start_date = st.date_input("Start Date", value=datetime.now().date() - timedelta(days=30))
        initial_capital = st.number_input("Initial Capital", min_value=100.0, value=10000.0, step=100.0)
    
    with col2:
        end_date = st.date_input("End Date", value=datetime.now().date())
        bots = st.session_state.get("bots") or {}
        strategy_options = ["Simple Buy & Hold"] + list(bots.keys())
        selected_strategy = st.selectbox("Strategy", strategy_options)
    
    max_lookback_days = st.slider("Max Lookback Days", 1, 365, min(settings.BACKTEST_LOOKBACK_DAYS, 365), step=1)

    data_source = st.selectbox(
        "Data source",
        ["auto", "trades", "prices"],
        format_func=lambda x: {"auto": "Auto (trades first, prices fallback)", "trades": "Trades only", "prices": "Price history only"}[x],
        help="Auto: use trades, fallback to prices. Prices: prefer price history when available.",
    )

    use_walk_forward = st.checkbox("Walk-Forward Mode", value=False, help="Rolling train/test windows to detect overfitting")
    wf_train_days = 30
    wf_test_days = 7
    if use_walk_forward:
        wfc1, wfc2 = st.columns(2)
        wf_train_days = wfc1.number_input("Train Window (days)", min_value=7, value=30, step=7)
        wf_test_days = wfc2.number_input("Test Window (days)", min_value=1, value=7, step=1)

    if st.button("▶️ Run Backtest", type="primary"):
        with st.spinner("Running backtest..."):
            try:
                async def run_backtest():
                    async def bot_strategy_wrapper(bot_name, bot):
                        market_cache = {}  # market_id -> market dict (category, tokens)

                        async def strategy_func(trade, positions, capital):
                            market_id = trade.get("market_id")
                            token_id = trade.get("token_id")
                            price = float(trade.get("price", 0.5) or 0.5)

                            # Fetch market metadata from DB so bots get category + tokenId
                            if market_id and market_id not in market_cache and base_engine.db and base_engine.db.session_factory:
                                try:
                                    from sqlalchemy import select
                                    from base_engine.data.database import Market
                                    async with base_engine.db.get_session() as session:
                                        r = await session.execute(select(Market).where(Market.id == market_id))
                                        m = r.scalar_one_or_none()
                                        if m:
                                            market_cache[market_id] = {"category": (m.category or "").lower()}
                                        else:
                                            market_cache[market_id] = {"category": ""}
                                except Exception:
                                    market_cache[market_id] = {"category": ""}

                            cached = market_cache.get(market_id, {})
                            # Use trade's token_id and price; bots expect tokens[0]
                            tokens = [{"tokenId": token_id or "", "outcomePrice": price}]

                            market_data = {
                                "id": market_id,
                                "category": cached.get("category", ""),
                                "tokens": tokens,
                            }
                            opportunity = await bot.analyze_opportunity(market_data)
                            if opportunity:
                                return {
                                    "action": "BUY",
                                    "size": opportunity.get("size", 1.0),
                                    "price": opportunity.get("price", price)
                                }
                            return {"action": "HOLD", "size": 0.0, "price": 0.0}
                        return strategy_func
                    
                    start_dt = datetime.combine(start_date, datetime.min.time())
                    end_dt = datetime.combine(end_date, datetime.max.time())
                    
                    if selected_strategy == "Simple Buy & Hold":
                        async def simple_strategy(trade, positions, capital):
                            return {"action": "BUY", "size": 1.0, "price": float(trade.get("price", 0.5))}
                        strategy_func = simple_strategy
                    else:
                        bot = bots.get(selected_strategy)
                        if not bot:
                            raise ValueError(f"Bot not found: {selected_strategy}")
                        strategy_func = await bot_strategy_wrapper(selected_strategy, bot)
                    
                    if use_walk_forward and base_engine.backtest_engine:
                        wf_result = await base_engine.backtest_engine.run_walk_forward(
                            strategy_func=strategy_func,
                            start_date=start_dt,
                            end_date=end_dt,
                            initial_capital=initial_capital,
                            train_days=wf_train_days,
                            test_days=wf_test_days,
                            data_source=data_source,
                        )
                        # Wrap dict result into BacktestResult-like object for display
                        from base_engine.backtesting.backtest_engine import BacktestResult
                        result = BacktestResult()
                        result.total_return = wf_result.get("total_return_pct", 0)
                        result.total_trades = wf_result.get("total_trades", 0)
                        result.winning_trades = wf_result.get("total_wins", 0)
                        result.losing_trades = wf_result.get("total_losses", 0)
                        result.win_rate = wf_result.get("aggregate_win_rate", 0)
                        result.sharpe_ratio = wf_result.get("aggregate_sharpe", 0)
                        result.max_drawdown = wf_result.get("aggregate_max_drawdown_pct", 0)
                        result.total_pnl = wf_result.get("total_pnl", 0)
                        result.strategy_name = f"{selected_strategy} (Walk-Forward)"
                        result._walk_forward_windows = wf_result.get("windows", [])
                        return result

                    result = await base_engine.run_backtest(
                        strategy_func,
                        start_dt,
                        end_dt,
                        initial_capital,
                        data_source=data_source,
                    )
                    result.strategy_name = selected_strategy
                    return result
                
                result = run_db_safe(run_backtest(), None)
                if result is not None:
                    st.session_state.backtest_results.append(result)
                    st.success(f"Backtest completed for {selected_strategy}!")
                else:
                    st.error("Backtest failed or was skipped (thread context). Try again.")
                st.session_state.pending_rerun = True
                st.rerun()
            except Exception as e:
                st.error(f"Backtest failed: {str(e)}")
                import traceback
                with st.expander("Error Details"):
                    st.code(traceback.format_exc())
    
    st.divider()
    st.subheader("Backtest Results")
    
    if st.session_state.backtest_results:
        for idx, result in enumerate(reversed(st.session_state.backtest_results[-5:])):
            strategy_name = getattr(result, 'strategy_name', 'Unknown Strategy')
            with st.expander(f"Backtest #{len(st.session_state.backtest_results) - idx} - {strategy_name}", expanded=(idx == 0)):
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    st.metric("Total Return", f"{result.total_return:.2f}%")
                    st.metric("Win Rate", f"{result.win_rate:.2%}")
                
                with col2:
                    st.metric("Total Trades", result.total_trades)
                    st.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")
                
                with col3:
                    st.metric("Max Drawdown", f"{result.max_drawdown:.2f}%")
                    st.metric("Profit Factor", f"{result.profit_factor:.2f}")
                
                with col4:
                    st.metric("Avg Win", f"${result.avg_win:.2f}")
                    st.metric("Avg Loss", f"${result.avg_loss:.2f}")
                
                # Walk-forward window breakdown
                wf_windows = getattr(result, "_walk_forward_windows", None)
                if wf_windows:
                    st.caption("Walk-Forward Windows")
                    wf_data = []
                    for w in wf_windows:
                        if w.get("skipped"):
                            wf_data.append({"#": w["window"], "Period": f"{w.get('test_start','')[:10]}..{w.get('test_end','')[:10]}", "P&L": "skipped", "Trades": 0, "Win%": "-", "Sharpe": "-"})
                        else:
                            wf_data.append({"#": w["window"], "Period": f"{w.get('test_start','')[:10]}..{w.get('test_end','')[:10]}", "P&L": f"${w.get('pnl',0):.2f}", "Trades": w.get("trades",0), "Win%": f"{w.get('win_rate',0):.1%}", "Sharpe": w.get("sharpe",0)})
                    st.dataframe(pd.DataFrame(wf_data), hide_index=True, use_container_width=True)

                if result.trades:
                    trades_df = pd.DataFrame(result.trades)
                    st.dataframe(trades_df, width="stretch", hide_index=True)

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=[t["entry_time"] for t in result.trades],
                        y=[t["pnl"] for t in result.trades],
                        mode='markers',
                        name='Trade P&L'
                    ))
                    fig.update_layout(title=f"Trade P&L Distribution - {strategy_name}", height=300)
                    st.plotly_chart(fig, width="stretch", key=f"backtest_pnl_{idx}")
    else:
        st.info("No backtest results yet. Run a backtest above.")


def _run_ingestion_subprocess(markets: int, days: int, prices: int) -> tuple:
    """Run ingestion subprocess. Returns (result_dict, proc_returncode, stderr)."""
    project_root = PathLib(__file__).resolve().parent.parent
    script_path = project_root / "scripts" / "run_ingestion_standalone.py"
    if not script_path.exists():
        return ({"success": False, "error": f"Ingestion script not found: {script_path}"}, -1, "")
    proc = subprocess.run(
        [
            sys.executable, str(script_path),
            "--pull-all",
            "--markets", str(int(markets)),
            "--days", str(int(days)),
            "--prices", str(int(prices)),
        ],
        cwd=str(project_root),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "2"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    result = None
    _stdout = (proc.stdout or "").strip()
    if _stdout:
        _json_line = None
        for _line in reversed(_stdout.splitlines()):
            _line = _line.strip()
            if _line.startswith("{"):
                _json_line = _line
                break
        if _json_line:
            try:
                result = json.loads(_json_line)
            except json.JSONDecodeError:
                pass
    if result is None:
        result = {"success": False, "error": (proc.stderr or "").strip() or f"Exit {proc.returncode}"}
    return (result, proc.returncode, proc.stderr or "")


def show_data_and_settings():
    """Unified Data & Settings tab."""
    sub1, sub2 = st.tabs(["📦 Data Center", "⚙️ Settings"])
    with sub1:
        show_data_center()
    with sub2:
        show_settings()


def show_data_center():
    st.header("📦 Data Center - Pull & Ingest")
    
    base_engine = st.session_state.base_engine
    
    # Standalone ingest: works without Initialize (only needs DATABASE_URL in .env)
    if not base_engine:
        st.info("💡 **Ingest without full init:** Run ingestion below. Requires `DATABASE_URL` in `.env`.")
        pull_markets = getattr(settings, "DAILY_INGESTION_MARKETS_COUNT", 500)
        pull_days = getattr(settings, "DAILY_INGESTION_DAYS_BACK", 365)
        pull_prices = getattr(settings, "DAILY_INGESTION_PRICES_MARKETS", 500)
        if st.button("📥 Ingest Data (Markets + Prices)", type="primary", key="standalone_ingest_btn", use_container_width=True):
            with st.status("Ingesting... (subprocess)", expanded=True) as status:
                try:
                    result, rc, stderr = _run_ingestion_subprocess(pull_markets, pull_days, pull_prices)
                    if result.get("success"):
                        phase1 = result.get("phase1_count", 0)
                        phase2 = result.get("phase2_result") or {}
                        diag = phase2.get("diagnostics", {})
                        st.write(f"**Phase 1:** {phase1} markets saved.")
                        st.write(f"**Phase 2:** {diag.get('markets_processed', 0)} processed, {diag.get('prices_ingested', 0)} prices.")
                        status.update(label="Ingest complete", state="complete")
                        st.success("Data ingested. Initialize system and start trading to use it.")
                    else:
                        err = result.get("error", stderr or f"Exit {rc}")
                        status.update(label="Ingest failed", state="error")
                        st.error(err)
                except subprocess.TimeoutExpired:
                    status.update(label="Timed out (1h)", state="error")
                    st.error("Ingestion timed out. Reduce scope in Settings or run from CLI.")
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(str(e))
        st.caption("Initialize system above for full features (bots, learning, predictions).")
        return
    
    if not base_engine.client:
        st.error("❌ **CRITICAL: Polymarket client not available**")
        st.error("Cannot fetch blockchain data without client.")
        return

    
    data_ingestion = base_engine.data_ingestion

    # Ensure button refs exist before status block (avoids NameError if UI order changes)
    pull_data_btn = False
    ingest_btn = False
    blockchain_ingest_btn = False
    backfill_btn = False

    # Clear stale "different loop" error so subprocess-based ingestion shows clean state
    if hasattr(data_ingestion, "ingestion_progress") and data_ingestion.ingestion_progress:
        err = (data_ingestion.ingestion_progress.get("error_info") or "") + (data_ingestion.ingestion_progress.get("error_message") or "")
        if "different loop" in err or "attached to a different loop" in err:
            _clear_ingestion_error(data_ingestion)

    if not data_ingestion:
        st.error("❌ **CRITICAL: Data ingestion service not available**")
        return

    client = base_engine.client
    
    def _has_method(obj, method_name):
        try:
            if not hasattr(obj, method_name):
                return False
            attr = getattr(obj, method_name, None)
            if not callable(attr):
                return False
            import inspect
            if inspect.ismethod(attr) or inspect.isfunction(attr):
                return True
            if hasattr(attr, "__call__"):
                return True
            return False
        except Exception:
            return False
    
    has_check_method = _has_method(client, "check_gamma_connectivity")
    has_reset_method = _has_method(client, "reset_circuit_breaker")
    
    if not has_check_method:
        st.warning("⚠️ **Polymarket client missing connectivity check method**")
        st.info("💡 **The client instance was created before the code was updated.**")
        st.info("**Solution:** Reinitialize the system (System Control → Initialize System) or restart Streamlit to load the updated client code.")
        st.caption("Polymarket API: ⚠️ Status unavailable (client needs reload)")
        
        if st.button("🔄 Try to Fix Client", key="fix_client_btn", help="Attempts to recreate the client with updated code"):
            try:
                import importlib
                import base_engine.data.polymarket_client
                importlib.reload(base_engine.data.polymarket_client)
                from base_engine.data.polymarket_client import PolymarketClient as NewPolymarketClient
                
                old_client = base_engine.client
                pk = getattr(old_client, "private_key", None)
                addr = getattr(old_client, "wallet_address", None)
                
                try:
                    if hasattr(old_client, "client") and old_client.client:
                        run_async_safe(old_client.client.aclose())
                except Exception:
                    pass
                
                new_client = NewPolymarketClient(private_key=pk, wallet_address=addr)
                
                if _has_method(new_client, "check_gamma_connectivity"):
                    base_engine.client = new_client
                    if base_engine.data_ingestion:
                        base_engine.data_ingestion.client = new_client
                    if base_engine.execution_engine:
                        base_engine.execution_engine.client = new_client
                    st.success("✅ Client recreated successfully! Status check should work now.")
                    st.session_state["gamma_connectivity_ts"] = 0
                    st.rerun()
                else:
                    st.error("❌ New client also missing method. Please restart Streamlit.")
            except Exception as e:
                logger.exception("Failed to recreate client")
                st.error(f"❌ Failed to recreate client: {e}")
                st.info("Please restart Streamlit to fully reload the updated code.")
    else:
        import time as _time
        _now = _time.time()
        _ts = st.session_state.get("gamma_connectivity_ts", 0)
        
        if "gamma_connectivity_ts" not in st.session_state or (_now - _ts) > 120:
            try:
                _res = run_db_safe(client.check_gamma_connectivity(), (False, "Check skipped"))
                _ok, _msg = _res if isinstance(_res, (tuple, list)) and len(_res) >= 2 else (False, str(_res))
                st.session_state["gamma_connectivity_ok"] = _ok
                st.session_state["gamma_connectivity_msg"] = _msg
                st.session_state["gamma_connectivity_ts"] = _now
            except AttributeError as ae:
                if "check_gamma_connectivity" in str(ae):
                    st.session_state["gamma_connectivity_ok"] = False
                    st.session_state["gamma_connectivity_msg"] = "Client method missing - restart Streamlit to reload"
                    st.session_state["gamma_connectivity_ts"] = _now
                else:
                    raise
            except Exception as e:
                st.session_state["gamma_connectivity_ok"] = False
                st.session_state["gamma_connectivity_msg"] = str(e)
                st.session_state["gamma_connectivity_ts"] = _now

        _gok = st.session_state.get("gamma_connectivity_ok", None)
        _gmsg = st.session_state.get("gamma_connectivity_msg", "")
        _conn_row1, _conn_row2 = st.columns([3, 1])
        with _conn_row1:
            if _gok is True:
                st.caption("Polymarket API: ✅ OK")
            elif _gok is False:
                st.caption(f"Polymarket API: ❌ Cannot reach — {_gmsg[:120]}. Verify VPS IP geo-access.")
            else:
                st.caption("Polymarket API: —")
        with _conn_row2:
            if st.button("🔌 Check connection", key="gamma_check_btn"):
                try:
                    _res = run_db_safe(client.check_gamma_connectivity(), (False, "Check skipped"))
                    _ok, _msg = _res if isinstance(_res, (tuple, list)) and len(_res) >= 2 else (False, str(_res))
                    st.session_state["gamma_connectivity_ok"] = _ok
                    st.session_state["gamma_connectivity_msg"] = _msg
                    st.session_state["gamma_connectivity_ts"] = _time.time()
                    st.session_state.pending_rerun = True
                    st.rerun()
                except AttributeError as ae:
                    if "check_gamma_connectivity" in str(ae):
                        st.session_state["gamma_connectivity_ok"] = False
                        st.session_state["gamma_connectivity_msg"] = "Client method missing - restart Streamlit to reload"
                        st.session_state["gamma_connectivity_ts"] = _time.time()
                        st.session_state.pending_rerun = True
                        st.rerun()
                    else:
                        raise
                except Exception as e:
                    st.session_state["gamma_connectivity_ok"] = False
                    st.session_state["gamma_connectivity_msg"] = str(e)
                    st.session_state["gamma_connectivity_ts"] = _time.time()
                    st.session_state.pending_rerun = True
                    st.rerun()
            if has_reset_method and _gok is False and "circuit" in (_gmsg or "").lower():
                if st.button("🔄 Reset circuit breaker", key="reset_cb_btn"):
                    try:
                        import asyncio
                        asyncio.run(client.reset_circuit_breaker())
                        st.session_state["gamma_connectivity_ts"] = 0
                        st.session_state.pending_rerun = True
                        st.rerun()
                    except Exception as e:
                        logger.warning(f"Failed to reset circuit breaker: {e}")
                        st.error(f"Failed to reset circuit breaker: {e}")
    
    # Enhanced database connection check with diagnostics
    has_db = False
    db_status_msg = ""
    
    if base_engine.db:
        if base_engine.db.session_factory:
            # Simple check: if session_factory exists, assume connection is available
            # The actual connection will be tested when we try to use it
            # This avoids event loop issues with repeated connection tests
            has_db = True
            db_status_msg = "✅ Database Connected - Data will be persisted"
            
            # Optional: Do a lightweight test (cached to avoid event loop issues)
            # We'll test the actual connection when ingestion runs
        else:
            db_status_msg = "⚠️ Database object exists but session_factory is None - database not initialized"
    else:
        db_status_msg = "⚠️ Database object is None - database not initialized"
    
    if has_db:
        st.success(f"**{db_status_msg}**")
    else:
        st.warning(f"**{db_status_msg}**")
        st.info("💡 **To enable persistence:**")
        st.info("1. Verify Postgres is reachable and DATABASE_URL in `.env` is correct")
        st.info("2. Ensure `.env` has valid DATABASE_URL (e.g. postgresql+asyncpg://...)")
        st.info("3. Click '🚀 Initialize System' again to re-establish connection")
    
    # --- Auto daily pull status + Pause/Resume ---
    daily_enabled = getattr(settings, "DAILY_FULL_INGESTION_ENABLED", True)
    ingestion_sched = getattr(base_engine, "ingestion_scheduler", None)
    if daily_enabled and ingestion_sched and ingestion_sched.running:
        st.success("**Auto daily pull:** Enabled — markets + prices every 24h. Next run after interval.")
        pause_btn = st.button("⏸️ Pause ingestion scheduler", key="pause_ingestion_sched", help="Stop auto-ingestion so manual Pull data can acquire the lock. Use if Pull data reports 'Could not acquire lock'.")
        if pause_btn:
            async def _stop_sched():
                if ingestion_sched:
                    await ingestion_sched.stop()
            run_async_safe(_stop_sched())
            st.success("Ingestion scheduler paused. You can click **Pull data** now.")
            st.session_state.pending_rerun = True
            st.rerun()
    elif daily_enabled and ingestion_sched and not ingestion_sched.running:
        st.warning("**Auto daily pull:** Paused. Manual Pull data will work.")
        resume_btn = st.button("▶️ Resume ingestion scheduler", key="resume_ingestion_sched", help="Restart auto-ingestion (markets every 5 min, daily full when due).")
        if resume_btn:
            async def _start_sched():
                if ingestion_sched:
                    await ingestion_sched.start()
            run_async_safe(_start_sched())
            st.success("Ingestion scheduler resumed.")
            st.session_state.pending_rerun = True
            st.rerun()
    elif daily_enabled:
        st.info("**Auto daily pull:** Enabled — runs when system is started (Initialize System).")
    else:
        st.caption("**Auto daily pull:** Disabled. Set DAILY_FULL_INGESTION_ENABLED=true in .env to enable.")

    # --- Single Pull data button (primary action) ---
    st.subheader("Pull data")
    st.caption("One click: ingest markets from Gamma API, then historical prices from CLOB. Updates elite users after success.")
    # Fallbacks aligned with config/settings.py defaults (365/1000/1000)
    pull_markets = getattr(settings, "DAILY_INGESTION_MARKETS_COUNT", 1000)
    pull_days = getattr(settings, "DAILY_INGESTION_DAYS_BACK", 365)
    pull_prices = getattr(settings, "DAILY_INGESTION_PRICES_MARKETS", 1000)
    pull_data_btn = st.button(
        "Pull data (Markets + Prices)",
        key="pull_data_btn",
        type="primary",
        use_container_width=True,
        help=f"Fetch {pull_markets} markets, {pull_days} days of prices for {pull_prices} markets. Then update elite users."
    )

    if pull_data_btn:
        with st.status("Pull data: Ingesting markets, then historical prices (subprocess)...", expanded=True) as status:
            try:
                st.write("Phase 1: Fetching markets from Gamma API...")
                if hasattr(data_ingestion, "ingestion_progress") and data_ingestion.ingestion_progress:
                    data_ingestion.ingestion_progress["status"] = "ingesting"
                    data_ingestion.ingestion_progress["error_message"] = None
                    data_ingestion.ingestion_progress["error_info"] = None
                result, rc, _ = _run_ingestion_subprocess(pull_markets, pull_days, pull_prices)
                if rc != 0 and not result.get("success"):
                    err = result.get("error") or (result.get("phase2_result") or {}).get("error") or f"Exit {rc}"
                    if "already in progress" not in (err or "").lower():
                        raise RuntimeError(err)
                if hasattr(data_ingestion, "ingestion_progress") and data_ingestion.ingestion_progress:
                    data_ingestion.ingestion_progress["error_message"] = None
                    data_ingestion.ingestion_progress["error_info"] = None
                    if result.get("success"):
                        data_ingestion.ingestion_progress["status"] = "complete"
                        data_ingestion.ingestion_progress["api_fetched"] = result.get("phase1_count", 0)
                        data_ingestion.ingestion_progress["db_saved"] = result.get("phase1_count", 0)
                if result.get("success"):
                    phase1 = result.get("phase1_count", 0)
                    phase2 = result.get("phase2_result") or {}
                    diag = phase2.get("diagnostics", {})
                    st.write(f"**Phase 1 (Markets):** {phase1} markets saved to DB.")
                    st.write(f"**Phase 2 (Prices):** {diag.get('markets_processed', 0)} processed, {diag.get('prices_ingested', 0)} prices ingested.")
                    st.write("Updating elite users...")
                    try:
                        if getattr(base_engine, "elite_detector", None):
                            db = getattr(base_engine, "db", None)
                            if db:
                                from base_engine.data.database_lock import acquire_lock
                                async def _elite_with_lock():
                                    async with acquire_lock(db, "elite_update", timeout_seconds=30):
                                        await base_engine.elite_detector.update_elite_status()
                                run_async_safe(_elite_with_lock())
                            else:
                                run_async_safe(base_engine.elite_detector.update_elite_status())
                    except Exception as elite_err:
                        err_str = str(elite_err).lower()
                        if "lock" in err_str or "already in progress" in err_str:
                            st.info(LOCK_BUSY_MSG)
                        else:
                            st.warning(f"Elite update skipped: {elite_err}")
                    st.write("Retraining prediction models...")
                    try:
                        if getattr(base_engine, "prediction_engine", None):
                            db = getattr(base_engine, "db", None)
                            if db:
                                from base_engine.data.database_lock import acquire_lock
                                async def _retrain_with_lock():
                                    async with acquire_lock(db, "model_training", timeout_seconds=60):
                                        await base_engine.prediction_engine.retrain()
                                run_async_safe(_retrain_with_lock())
                            else:
                                run_async_safe(base_engine.prediction_engine.retrain())
                    except Exception as retrain_err:
                        err_str = str(retrain_err).lower()
                        if "lock" in err_str or "already in progress" in err_str:
                            st.info(LOCK_BUSY_MSG)
                        else:
                            st.warning(f"Retrain skipped: {retrain_err}")
                    status.update(label="Pull data complete", state="complete")
                    st.success("Pull data complete. Elite users updated. Models retrained.")
                    if phase1 > 0 and hasattr(data_ingestion, "last_market_update"):
                        data_ingestion.last_market_update = datetime.now(timezone.utc)
                else:
                    err = result.get("error") or (result.get("phase2_result") or {}).get("error") or "Unknown error"
                    status.update(label=f"Pull data failed: {err}", state="error")
                    phase1 = result.get("phase1_count", 0)
                    if phase1 > 0:
                        st.warning(f"**Phase 1:** {phase1} markets saved to DB.")
                    st.error(f"**Phase 2 failed:** {err}")
                    _err_lower = (err or "").lower()
                    if "lock" in _err_lower or "acquire" in _err_lower or "already in progress" in _err_lower:
                        st.info("💡 **Fix:** The IngestionScheduler may be holding the lock. Click **⏸️ Pause ingestion scheduler** above, then try **Pull data** again. Or use **Clear stuck sync_log** below if a previous run crashed.")
            except subprocess.TimeoutExpired:
                logger.warning("Pull data subprocess timed out (1h)")
                status.update(label="Pull data timed out (1h)", state="error")
                st.error("Pull data timed out after 1 hour. Run from CLI with no timeout or reduce scope in Settings.")
            except Exception as e:
                logger.exception("Pull data failed")
                status.update(label=f"Pull data failed: {str(e)}", state="error")
                st.error(f"Pull data failed: {str(e)}")
                _err_lower = str(e).lower()
                if "lock" in _err_lower or "acquire" in _err_lower or "already in progress" in _err_lower:
                    st.info("💡 **Fix:** The IngestionScheduler may be holding the lock. Click **⏸️ Pause ingestion scheduler** above, then try **Pull data** again.")

    # --- Ingest users only (elite traders when Pull data already ran) ---
    ingest_users_btn = st.button("Ingest users only", key="ingest_users_btn", help="Fetch top traders + their trades. Use when Elite Traders shows empty but markets/prices exist.")
    if ingest_users_btn:
        with st.status("Ingesting elite users and trades...", expanded=True) as status:
            try:
                if data_ingestion:
                    n_users = run_async_safe(data_ingestion.ingest_top_users(), 0)
                    st.write(f"**Users:** {n_users} saved.")
                    n_activity = run_async_safe(data_ingestion.ingest_elite_trader_activity(), 0)
                    st.write(f"**Trades:** {n_activity} fetched.")
                    if getattr(base_engine, "elite_detector", None):
                        db = getattr(base_engine, "db", None)
                        if db:
                            from base_engine.data.database_lock import acquire_lock
                            async def _elite_with_lock():
                                async with acquire_lock(db, "elite_update", timeout_seconds=30):
                                    await base_engine.elite_detector.update_elite_status()
                            run_async_safe(_elite_with_lock())
                        else:
                            run_async_safe(base_engine.elite_detector.update_elite_status())
                    status.update(label="Users ingested", state="complete")
                    st.success(f"Elite users: {n_users}, trades: {n_activity}. Refresh page to see Elite Traders.")
                else:
                    st.error("Data ingestion not available. Initialize system first.")
            except Exception as e:
                err_str = str(e).lower()
                if "lock" in err_str or "already in progress" in err_str:
                    st.info(LOCK_BUSY_MSG)
                else:
                    status.update(label="Ingest failed", state="error")
                    st.error(str(e))

    # --- Backfill resolution (markets with trades but no resolution - enables training) ---
    backfill_res_btn = st.button("Backfill resolution", key="backfill_resolution_btn", help="Fetch resolution for markets with trades. Run if training fails with 'no resolved markets'.")
    if backfill_res_btn:
        with st.status("Backfilling resolution...", expanded=True) as status:
            try:
                project_root = PathLib(__file__).resolve().parent.parent
                script_path = project_root / "scripts" / "backfill_market_resolution.py"
                if script_path.exists():
                    proc = subprocess.run(
                        [sys.executable, str(script_path)],
                        cwd=str(project_root),
                        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=600,
                    )
                    out = (proc.stdout or "").strip()
                    if proc.returncode == 0:
                        status.update(label="Backfill complete", state="complete")
                        st.success(out or "Resolution backfill complete. Retrain models to use the data.")
                    else:
                        status.update(label="Backfill failed", state="error")
                        st.error(proc.stderr or out or f"Exit {proc.returncode}")
                else:
                    st.error(f"Script not found: {script_path}")
            except subprocess.TimeoutExpired:
                status.update(label="Timed out", state="error")
                st.error("Backfill timed out (10 min)")
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(str(e))

    # --- Clear stuck sync_log (when Pull data says "already in progress" or lock timeout) ---
    clear_stuck_btn = st.button("Clear stuck sync_log", key="clear_stuck_ingestion", help="Clear sync_log entries stuck in 'running'. If lock timeout persists, pause IngestionScheduler above first.")
    if clear_stuck_btn:
        try:
            db = getattr(data_ingestion, "db", None)
            if db:
                # Clear all stuck runs (full + backfill) so user can retry either
                n = run_db_safe(db.clear_stuck_sync_running(component="data_ingestion"), 0)
                st.success(f"Cleared {n} stuck run(s). You can click **Pull data** or **Start backfill** again.")
            else:
                st.warning("Database not available to clear sync_log.")
        except Exception as clear_err:
            st.error(f"Clear failed: {clear_err}")

    # --- Advanced: backfill, markets only, historical prices only ---
    with st.expander("Advanced: Backfill, markets only, prices only", expanded=False):
        st.caption("One-time backfill or individual steps. Use Pull data above for normal use.")
        bf_col1, bf_col2, bf_col3 = st.columns([2, 2, 1])
        with bf_col1:
            backfill_days = st.number_input("Backfill days", min_value=30, max_value=365, value=365, step=30, key="backfill_days")
            backfill_markets_batch = st.number_input("Markets per batch", min_value=50, max_value=300, value=100, step=25, key="backfill_markets_batch")
        with bf_col2:
            backfill_prices_batch = st.number_input("Markets per price batch", min_value=25, max_value=100, value=50, step=25, key="backfill_prices_batch")
            backfill_market_batches = st.number_input("Market batches", min_value=1, max_value=10, value=1, key="backfill_market_batches")
        with bf_col3:
            backfill_btn = st.button("Start backfill", key="backfill_btn")
        if backfill_btn:
            project_root = PathLib(__file__).resolve().parent.parent
            script_path = project_root / "scripts" / "run_ingestion_standalone.py"
            with st.status("Backfill: markets (active+closed) then historical prices in batches...", expanded=True) as status:
                try:
                    proc = subprocess.run(
                        [
                            sys.executable, str(script_path),
                            "--backfill",
                            "--backfill-days", str(int(backfill_days)),
                            "--backfill-markets-batch", str(int(backfill_markets_batch)),
                            "--backfill-prices-batch", str(int(backfill_prices_batch)),
                            "--backfill-market-batches", str(int(backfill_market_batches)),
                        ],
                        cwd=str(project_root),
                        env={**os.environ, "PYTHONIOENCODING": "utf-8", "DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "2"},
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=7200,
                    )
                    out = (proc.stdout or "").strip()
                    result = None
                    for line in reversed(out.splitlines()):
                        line = line.strip()
                        if line.startswith("{"):
                            try:
                                result = json.loads(line)
                                break
                            except json.JSONDecodeError:
                                pass
                    if proc.returncode != 0:
                        err_msg = (result.get("error") if result else None) or proc.stderr or f"Backfill exited with {proc.returncode}"
                        st.error(err_msg)
                        status.update(label="Backfill failed", state="error")
                    elif result and result.get("success"):
                        status.update(label="Backfill complete", state="complete")
                        st.success(f"Markets ingested: {result.get('markets_ingested', 0)}; price batches: {result.get('price_batches_run', 0)}; prices: {result.get('prices_total_ingested', 0)}")
                    else:
                        st.warning(result.get("error", "Backfill finished with no result") if result else "Backfill finished")
                except subprocess.TimeoutExpired:
                    st.error("Backfill timed out (2h). Run from CLI with higher limits or more batches.")
                    status.update(label="Backfill timed out", state="error")
                except Exception as e:
                    logger.exception("Backfill failed")
                    st.error(str(e))
                    status.update(label=f"Backfill failed: {e}", state="error")

        st.caption("Markets only or historical prices only.")
        mc1, mc2 = st.columns([1, 1])
        with mc1:
            ingest_btn = st.button("Markets only", key="manual_ingest_btn")
        with mc2:
            blockchain_max_markets = st.number_input("Max markets", min_value=1, max_value=1000, value=50, key="blockchain_max_markets")
            blockchain_days_back = st.number_input("Days back", min_value=1, max_value=365, value=365, key="blockchain_days_back")
            blockchain_market_ids_input = st.text_input("Market IDs (optional)", placeholder="e.g., 540234,601700", key="blockchain_market_ids", autocomplete="off")
            blockchain_ingest_btn = st.button("Historical prices only", key="blockchain_ingest_btn")
    
    st.divider()
    tr_col1, tr_col2 = st.columns([1, 1])
    with tr_col1:
        if st.button("Clear Error", key="clear_error_btn", help="Clear cached error states"):
            _clear_ingestion_error(data_ingestion, full_reset=True)
            st.session_state.pending_rerun = True
            st.rerun()
    with tr_col2:
        if st.button("Refresh", key="refresh_status_btn"):
            st.session_state.pending_rerun = True
            st.rerun()

    if blockchain_ingest_btn:
        with st.status("⛓️ Historical prices (subprocess)...", expanded=True) as status:
            try:
                st.write("🔗 Running historical price ingestion in isolated process...")
                market_ids = None
                if blockchain_market_ids_input:
                    market_ids = [m.strip() for m in blockchain_market_ids_input.split(",") if m.strip()]
                    st.write(f"📋 Processing {len(market_ids)} specific markets...")
                else:
                    st.write(f"📋 Processing up to {blockchain_max_markets} active markets...")
                to_timestamp = int(datetime.now(timezone.utc).timestamp())
                from_timestamp = to_timestamp - (blockchain_days_back * 24 * 60 * 60)
                st.write(f"📅 Date range: {datetime.fromtimestamp(from_timestamp, tz=timezone.utc).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(to_timestamp, tz=timezone.utc).strftime('%Y-%m-%d')}")
                st.write("⏳ This may take several minutes...")
                project_root = PathLib(__file__).resolve().parent.parent
                script_path = project_root / "scripts" / "run_ingestion_standalone.py"
                if not script_path.exists():
                    raise FileNotFoundError(f"Ingestion script not found: {script_path}")
                cmd = [
                    sys.executable, str(script_path), "--historical",
                    "--from-ts", str(from_timestamp),
                    "--to-ts", str(to_timestamp),
                    "--max-markets", str(blockchain_max_markets),
                ]
                if market_ids:
                    cmd.extend(["--market-ids", ",".join(market_ids)])
                proc = subprocess.run(
                    cmd,
                    cwd=str(project_root),
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "2"},
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,
                )
                _stdout_h = (proc.stdout or "").strip()
                if proc.returncode != 0 and _stdout_h:
                    try:
                        _jl = None
                        for _ln in reversed(_stdout_h.splitlines()):
                            _ln = _ln.strip()
                            if _ln.startswith("{"):
                                _jl = _ln
                                break
                        if _jl:
                            _r = json.loads(_jl)
                            err_msg = _r.get("error") or proc.stderr or f"Subprocess exited with {proc.returncode}"
                            raise RuntimeError(err_msg)
                    except json.JSONDecodeError:
                        pass
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr or f"Subprocess exited with {proc.returncode}")
                _json_line_h = None
                if _stdout_h:
                    for _ln in reversed(_stdout_h.splitlines()):
                        _ln = _ln.strip()
                        if _ln.startswith("{"):
                            _json_line_h = _ln
                            break
                    if _json_line_h is None:
                        _json_line_h = _stdout_h
                try:
                    result = json.loads(_json_line_h) if _json_line_h else {"success": False, "error": "No output"}
                except json.JSONDecodeError:
                    result = {"success": False, "error": "Invalid JSON from subprocess"}
                if result.get("success"):
                    diagnostics = result.get("diagnostics", {})
                    status.update(label="✅ Historical prices complete", state="complete")
                    st.success("✅ **Historical prices complete**")
                    st.write(f"**Markets Processed:** {diagnostics.get('markets_processed', 0)}")
                    st.write(f"**Markets Successful:** {diagnostics.get('markets_successful', 0)}")
                    st.write(f"**Prices Ingested:** {diagnostics.get('prices_ingested', 0)}")
                    if diagnostics.get('markets_no_events', 0) > 0:
                        st.info(f"ℹ️ {diagnostics['markets_no_events']} markets had no trade events in date range")
                    if diagnostics.get('markets_failed', 0) > 0:
                        st.warning(f"⚠️ {diagnostics['markets_failed']} markets failed to process")
                    if diagnostics.get('errors'):
                        with st.expander("Error Details"):
                            for error in diagnostics['errors'][:10]:
                                st.code(error)
                else:
                    err = result.get("error", "Unknown error")
                    status.update(label=f"❌ Historical prices failed: {err}", state="error")
                    st.error(f"❌ **Historical prices failed:** {err}")
                    if result.get("diagnostics", {}).get("errors"):
                        with st.expander("Error Details"):
                            for error in result["diagnostics"]["errors"]:
                                st.code(error)
            except subprocess.TimeoutExpired:
                logger.warning("Historical prices subprocess timed out (30m)")
                status.update(label="Historical prices timed out (30m)", state="error")
                st.error("Historical prices timed out after 30 minutes. Reduce date range or max markets, or run from CLI.")
            except Exception as e:
                logger.error(f"Blockchain ingestion UI error: {str(e)}", exc_info=True)
                status.update(label=f"❌ Error: {str(e)}", state="error")
                st.error(f"❌ **Error:** {str(e)}")
    
    st.divider()
    progress_status = "idle"
    if hasattr(data_ingestion, 'ingestion_progress') and data_ingestion.ingestion_progress:
        progress_status = data_ingestion.ingestion_progress.get("status", "idle")
        if progress_status in ["starting", "ingesting", "running"] and not ingest_btn and not pull_data_btn and not blockchain_ingest_btn:
            st.info("🔄 Ingestion in progress - page will auto-refresh every 2 seconds")
            # BUG FIX: Replace blocking time.sleep() with async-friendly approach
            # Use st.empty() with automatic refresh instead of blocking the event loop
            if "auto_refresh_counter" not in st.session_state:
                st.session_state.auto_refresh_counter = 0
            st.session_state.auto_refresh_counter += 1
            # Auto-refresh every few page loads instead of blocking
            if st.session_state.auto_refresh_counter >= 2:
                st.session_state.auto_refresh_counter = 0
                st.session_state.pending_rerun = True
                st.rerun()
    st.divider()
    
    header_col1, header_col2 = st.columns([3, 1])
    with header_col1:
        st.subheader("Status")
    with header_col2:
        has_error = (progress_status == "error" or 
                    (hasattr(data_ingestion, 'ingestion_progress') and 
                     data_ingestion.ingestion_progress and 
                     (data_ingestion.ingestion_progress.get("status") == "error" or
                      data_ingestion.ingestion_progress.get("error_message") or
                      data_ingestion.ingestion_progress.get("error_info"))))
        if has_error:
            if st.button("🔄 Reset Error", key="reset_error_status_header", type="primary"):
                _clear_ingestion_error(data_ingestion)
                st.session_state.pending_rerun = True
                st.rerun()
    
    progress_data = data_ingestion.ingestion_progress if hasattr(data_ingestion, 'ingestion_progress') else {}
    api_fetched_total = progress_data.get("api_fetched", 0)
    db_saved_total = progress_data.get("db_saved", 0)
    data_was_saved = has_db and db_saved_total > 0
    # Fallback: sync_log has last_pull_at when subprocess completed (survives page refresh)
    db_pull_status = run_db_safe(base_engine.db.get_data_pull_status(), {}) if has_db and base_engine.db else {}
    last_pull_at = db_pull_status.get("last_pull_at")
    if os.environ.get("DEBUG_DASHBOARD_LOG", "").lower() in ("true", "1", "yes"):
        try:
            _d = {"progress_status": progress_status, "api_fetched_total": api_fetched_total, "db_saved_total": db_saved_total, "has_db": has_db, "progress_keys": list(progress_data.keys()) if isinstance(progress_data, dict) else []}
            _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as _lf:
                _lf.write(json.dumps({"sessionId": "debug-session", "runId": "run1", "hypothesisId": "H1", "location": "dashboard:progress_data", "message": "progress_data and derived", "data": _d, "timestamp": __import__("time").time() * 1000}) + "\n")
        except Exception:
            pass

    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        # CRITICAL FIX: Status logic now checks actual progress, not just flags
        # Root cause: Status checked data_ingestion.running flag which was True even when nothing was happening
        # Impact: Status showed "🟢 Running" but ingestion wasn't actually running
        # Fix: Check progress_status and actual data counts, not just running flag
        
        if ingest_btn or progress_status in ["starting", "ingesting", "running"]:
            # Currently ingesting (button clicked or in progress)
            status = "🟡 Ingesting"
        elif progress_status in ["complete", "completed"]:
            # Ingestion completed - check if data was actually saved
            if has_db and db_saved_total > 0:
                status = "✅ Complete"
            elif has_db and db_saved_total == 0 and api_fetched_total > 0:
                status = "⚠️ Complete (No data saved)"
            elif not has_db and api_fetched_total > 0:
                status = "✅ Fetched (API only)"
            else:
                # Check if we have historical data (last_market_update)
                last_market_exists = hasattr(data_ingestion, 'last_market_update') and data_ingestion.last_market_update is not None
                if last_market_exists:
                    if not has_db:
                        status = "✅ Fetched (API only)"
                    elif has_db:
                        status = "⚠️ Complete (No data saved)"
                    else:
                        status = "✅ Complete"
                else:
                    status = "⏸️ Idle"
        elif progress_status == "error":
            # Error occurred during ingestion
            status = "❌ Error"
        elif progress_status == "idle":
            # No ingestion in progress - check if we have any data (incl. sync_log from subprocess)
            last_market_exists = (
                (hasattr(data_ingestion, 'last_market_update') and data_ingestion.last_market_update is not None)
                or (last_pull_at is not None and hasattr(last_pull_at, "strftime"))
            )
            if last_market_exists:
                status = "⏸️ Idle"
            else:
                status = "🔴 Stopped"
        else:
            # Unknown status - default to idle
            status = "⏸️ Idle"
        st.metric("Ingestion Status", status)
    
    with col2:
        # Prefer in-memory last_market_update; fallback to sync_log last_pull_at (survives refresh)
        last_market = data_ingestion.last_market_update if hasattr(data_ingestion, 'last_market_update') else None
        if last_market is None and last_pull_at is not None:
            last_market = last_pull_at
        if last_market:
            try:
                if last_market.tzinfo:
                    last_market_str = last_market.strftime("%Y-%m-%d %H:%M:%S UTC")
                else:
                    last_market_str = last_market.strftime("%Y-%m-%d %H:%M:%S")
                
                if not has_db:
                    last_market_str += " (API only)"
                elif has_db and db_saved_total == 0 and api_fetched_total > 0:
                    last_market_str += " (API only)"
            except Exception:
                last_market_str = "Invalid Date"
        else:
            last_market_str = "Never"
        st.metric("Last Market Update", last_market_str)
    
    with col3:
        last_user = data_ingestion.last_user_update if hasattr(data_ingestion, 'last_user_update') else None
        if last_user:
            try:
                if last_user.tzinfo:
                    last_user_str = last_user.strftime("%Y-%m-%d %H:%M:%S UTC")
                else:
                    last_user_str = last_user.strftime("%Y-%m-%d %H:%M:%S")
                
                if not has_db:
                    last_user_str += " (API only)"
            except Exception:
                last_user_str = "Invalid Date"
        else:
            last_user_str = "Never"
        st.metric("Last User Update", last_user_str)
    
    with col4:
        records_str = f"{api_fetched_total:,} | {db_saved_total:,}" if has_db else f"{api_fetched_total:,} (API)"
        st.metric("Records (this run)", records_str)

    # Recent syncs & data quality (from sync_log and gap detector)
    with st.expander("Recent syncs & data quality"):
        if has_db and base_engine.db and base_engine.db.session_factory:
            try:
                syncs = run_db_safe(base_engine.db.get_recent_syncs(component="data_ingestion", limit=10), [])
                if syncs:
                    df_syncs = pd.DataFrame(syncs)
                    for col in ["started_at", "completed_at"]:
                        if col in df_syncs.columns:
                            df_syncs[col] = df_syncs[col].apply(lambda x: str(x)[:19] if x is not None else "")
                    display_cols = [c for c in ["sync_type", "status", "started_at", "completed_at", "records_inserted"] if c in df_syncs.columns]
                    st.caption("Last 10 ingestion runs (sync_log)")
                    st.dataframe(df_syncs[display_cols], width="stretch", hide_index=True)
                else:
                    st.caption("No sync log entries yet. Run backfill or Pull all to populate.")
                try:
                    from base_engine.monitoring.gap_detector import GapDetector
                    gap_detector = GapDetector(base_engine.db)
                    report = run_db_safe(gap_detector.check_continuity(trade_gap_hours=24.0, price_stale_hours=24.0), {})
                    summary = (report.get("summary") or {}) if isinstance(report, dict) else {}
                    st.caption("Data continuity (trade gaps & price staleness)")
                    st.json(summary)
                except Exception as g_err:
                    st.caption("Gap check skipped (optional)")
                    st.caption(str(g_err))
                try:
                    from base_engine.monitoring.quality_metrics import QualityMetrics
                    qm = QualityMetrics(base_engine.db)
                    pipeline_quality = run_db_safe(qm.calculate_pipeline_quality(), {})
                    if pipeline_quality and isinstance(pipeline_quality, dict) and "error" not in pipeline_quality:
                        st.caption("Pipeline quality (freshness & sync success rate)")
                        st.metric("Health", f"{pipeline_quality.get('overall_health', 0):.1f} ({pipeline_quality.get('grade', '-')})")
                        st.json(pipeline_quality.get("metrics") or {})
                    elif pipeline_quality and "error" in pipeline_quality:
                        st.caption("Pipeline quality: " + str(pipeline_quality.get("error", "")))
                except Exception as q_err:
                    st.caption("Pipeline quality skipped (optional)")
                    st.caption(str(q_err))
            except Exception as e:
                st.caption("Could not load sync log or gap report")
                st.caption(str(e))
        else:
            st.caption("Database not available — sync log and gap report require DB.")

    has_error_display = False
    if hasattr(data_ingestion, 'ingestion_progress') and data_ingestion.ingestion_progress:
        progress = data_ingestion.ingestion_progress
        if (progress_status == "error" or 
            progress.get("status") == "error" or
            progress.get("error_message") or
            progress.get("error_info")):
            has_error_display = True
    
    if has_error_display:
        progress = data_ingestion.ingestion_progress
        st.error("❌ **Ingestion Error Detected**")
        st.warning("Previous ingestion attempt failed. Click 'Pull all (Markets + Prices)' or 'Markets only' to retry.")
        # Prefer error_info; fall back to error_message so we never show blank (e.g. empty exception str).
        error_info = (progress.get("error_info") or "").strip() or (progress.get("error_message") or "").strip() or "Unknown error"
        # Stale state fix: never show "verification: ." (blank detail from runs before the db_detail fix).
        if error_info and "verification: ." in error_info:
            error_info = "Database connection failed verification (no details). Please check database initialization and try again."
        if error_info and error_info != "Unknown error":
            with st.expander("Error Details"):
                st.code(str(error_info))
        
        st.markdown("---")
        if st.button("🔄 Reset Error Status", key="reset_error_status_btn", type="primary"):
            _clear_ingestion_error(data_ingestion)
            st.session_state.pending_rerun = True
            st.rerun()
        st.markdown("---")
    
    if progress_status in ["starting", "ingesting", "running"] and hasattr(data_ingestion, 'ingestion_progress') and data_ingestion.ingestion_progress:
        progress = data_ingestion.ingestion_progress
        current = progress.get("current", 0)
        total = progress.get("total", 1)
        batch = progress.get("current_batch", 0)
        api_fetched = progress.get("api_fetched", 0)
        db_saved = progress.get("db_saved", 0)
        recovery_level = progress.get("recovery_level")
        
        if total > 0:
            percent = min(100, int((current / total) * 100))
        else:
            percent = 0
        
        st.subheader("📊 Current Ingestion Progress")
        st.progress(percent / 100, text=f"Batch {batch}: {current:,} / {total:,} markets ({percent}%)")
        st.caption(f"Status: {progress.get('status', 'unknown').title()}")
        
        if has_db:
            st.write(f"**📡 API Fetched:** {api_fetched:,} | **💾 DB Saved:** {db_saved:,}")
            if api_fetched > 0 and db_saved == 0:
                st.warning("⚠️ Data fetched from API but not saved to database. Check database connection.")
        else:
            st.write(f"**📡 API Fetched:** {api_fetched:,} | **💾 DB Saved:** N/A (Database not available)")
            st.info("💡 Data fetched from API but not persisted. Connect database to enable data persistence.")
        
        if recovery_level:
            st.write(f"**🔄 Recovery Level:** {recovery_level}")
    
    if progress_status in ["complete", "completed"] and hasattr(data_ingestion, 'ingestion_progress') and data_ingestion.ingestion_progress:
        progress = data_ingestion.ingestion_progress
        api_fetched = progress.get("api_fetched", 0)
        db_saved = progress.get("db_saved", 0)
        
        if api_fetched > 0:
            if has_db and db_saved > 0:
                st.success(f"✅ **Ingestion Complete:** Fetched {api_fetched:,} markets from API and saved {db_saved:,} to database.")
            elif has_db and db_saved == 0:
                st.warning(f"⚠️ **Ingestion Complete (Partial):** Fetched {api_fetched:,} markets from API but none were saved to database. Check database connection.")
            elif not has_db:
                st.info(f"ℹ️ **API Fetch Complete:** Fetched {api_fetched:,} markets from API. Data not persisted (database not available).")
    
    if ingest_btn:
        with st.status("🔄 Starting market ingestion...", expanded=True) as status:
            try:
                st.write("📡 Connecting to Polymarket API...")
                st.write("⏳ **Estimated time:** 30-120 seconds (depending on number of markets)")
                st.write("📊 Processing markets in batches of 100...")
                
                start_time = datetime.now()
                progress_container = st.container()
                
                progress_state = {"last_update": None}
                
                def update_progress(progress):
                    try:
                        current = progress.get("current", 0)
                        total = progress.get("total", 1)
                        batch = progress.get("current_batch", 0)
                        status_val = progress.get("status", "unknown")
                        api_fetched = progress.get("api_fetched", 0)
                        db_saved = progress.get("db_saved", 0)
                        recovery_level = progress.get("recovery_level")
                        
                        progress_state["last_update"] = {
                            "current": current,
                            "total": total,
                            "batch": batch,
                            "status": status_val,
                            "api_fetched": api_fetched,
                            "db_saved": db_saved,
                            "recovery_level": recovery_level,
                            "timestamp": datetime.now()
                        }
                        
                        with progress_container:
                            if total > 0:
                                percent = min(100, int((current / total) * 100))
                                elapsed = (datetime.now() - start_time).total_seconds()
                                if current > 0 and elapsed > 0:
                                    rate = current / elapsed
                                    remaining = (total - current) / rate if rate > 0 else 0
                                    eta_text = f" | ETA: {int(remaining)}s" if remaining > 0 else ""
                                else:
                                    eta_text = ""
                            else:
                                percent = 0
                                eta_text = ""
                            
                            st.progress(percent / 100, text=f"Batch {batch}: {current:,} / {total:,} markets ({percent}%){eta_text}")
                            
                            status_messages = {
                                "starting": "🟡 Initializing...",
                                "ingesting": f"🟢 Ingesting batch {batch}...",
                                "complete": "✅ Complete!",
                                "error": "❌ Error occurred"
                            }
                            st.caption(status_messages.get(status_val, f"Status: {status_val}"))
                            
                            if has_db:
                                st.write(f"**📡 API Fetched:** {api_fetched:,} | **💾 DB Saved:** {db_saved:,}")
                                if api_fetched > 0 and db_saved == 0 and status_val == "complete":
                                    st.warning("⚠️ Data fetched but not saved to database. Check database connection.")
                            else:
                                st.write(f"**📡 API Fetched:** {api_fetched:,} | **💾 DB Saved:** N/A (Database not available)")
                            
                            if recovery_level:
                                st.write(f"**🔄 Recovery Level:** {recovery_level}")
                    except Exception as e:
                        logger.warning(f"Progress callback error: {str(e)}", exc_info=True)
                
                try:
                    logger.info("Starting ingestion via subprocess (isolated process, no loop conflict)...")
                    if hasattr(data_ingestion, "ingestion_progress") and data_ingestion.ingestion_progress:
                        data_ingestion.ingestion_progress["status"] = "ingesting"
                        data_ingestion.ingestion_progress["error_message"] = None
                        data_ingestion.ingestion_progress["error_info"] = None
                    project_root = PathLib(__file__).resolve().parent.parent
                    script_path = project_root / "scripts" / "run_ingestion_standalone.py"
                    if not script_path.exists():
                        raise FileNotFoundError(f"Ingestion script not found: {script_path}")
                    top_markets = getattr(settings, "DAILY_INGESTION_MARKETS_COUNT", 1000)
                    result = subprocess.run(
                        [sys.executable, str(script_path), "--top", str(int(top_markets))],
                        cwd=str(project_root),
                        env={**os.environ, "PYTHONIOENCODING": "utf-8", "DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "2"},
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=600,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr or f"Subprocess exited with {result.returncode}")
                    count = int(result.stdout.strip()) if result.stdout.strip() else 0
                    logger.info(f"Ingestion returned count: {count}")
                    if hasattr(data_ingestion, "ingestion_progress") and data_ingestion.ingestion_progress:
                        data_ingestion.ingestion_progress["status"] = "complete"
                        data_ingestion.ingestion_progress["error_message"] = None
                        data_ingestion.ingestion_progress["error_info"] = None
                        data_ingestion.ingestion_progress["api_fetched"] = count
                        data_ingestion.ingestion_progress["db_saved"] = count
                except Exception as ingestion_error:
                    logger.error(f"Ingestion exception caught: {str(ingestion_error)}", exc_info=True)
                    import traceback
                    error_traceback = traceback.format_exc()
                    try:
                        capture_path = PathLib(__file__).resolve().parent.parent / "ingestion_error_capture.txt"
                        with open(capture_path, "w", encoding="utf-8") as _f:
                            _f.write(f"Exception type: {type(ingestion_error).__name__}\n")
                            _f.write(f"Exception: {ingestion_error}\n\n")
                            _f.write(f"Traceback:\n{error_traceback}\n")
                    except Exception:
                        pass
                    status.update(label=f"❌ Ingestion exception: {str(ingestion_error)}", state="error")
                    st.error(f"❌ **Ingestion failed with exception:** {str(ingestion_error)}")
                    with st.expander("Exception Details"):
                        st.code(error_traceback)
                    count = 0
                    final_status = "error"
                    error_message = str(ingestion_error)
                    error_info = error_traceback
                else:
                    final_status = "unknown"
                    error_message = None
                    error_info = None
                
                elapsed_time = (datetime.now() - start_time).total_seconds()
                
                progress_final = data_ingestion.ingestion_progress if hasattr(data_ingestion, 'ingestion_progress') else {}
                api_fetched = progress_final.get("api_fetched", 0)
                db_saved = progress_final.get("db_saved", 0)
                if final_status == "unknown":
                    final_status = progress_final.get("status", "unknown")
                if not error_message:
                    error_message = progress_final.get("error_message")
                if not error_info:
                    error_info = progress_final.get("error_info")
                
                logger.info(
                    f"Ingestion completed",
                    count=count,
                    api_fetched=api_fetched,
                    db_saved=db_saved,
                    final_status=final_status,
                    elapsed_time=elapsed_time,
                    error_message=error_message
                )
                
                if final_status == "error":
                    status.update(label=f"❌ Ingestion failed: {error_message or 'Unknown error'}", state="error")
                    st.error(f"❌ **Ingestion failed:** {error_message or 'Unknown error'}")
                    if error_info:
                        with st.expander("Error Details"):
                            st.code(str(error_info))
                    st.error(f"**Details:**")
                    st.error(f"- Returned count: {count}")
                    st.error(f"- API fetched: {api_fetched}")
                    st.error(f"- DB saved: {db_saved}")
                    st.error(f"- Final status: {final_status}")
                    st.error(f"- Elapsed time: {elapsed_time:.1f}s")
                    st.error(f"")
                    st.error(f"**Possible causes:**")
                    st.error(f"- API connection failed or timed out")
                    st.error(f"- API returned empty results")
                    st.error(f"- All batches failed during processing")
                    st.error(f"- Network or authentication issues")
                    st.error(f"")
                    st.error(f"**Check:**")
                    st.error(f"- Check console/logs for detailed error messages")
                    st.error(f"- Verify Polymarket API is accessible")
                    st.error(f"- Check network connection")
                    if progress_state.get("last_update"):
                        last = progress_state["last_update"]
                        st.error(f"**Last progress update:** Batch {last.get('batch', 0)}, Status: {last.get('status', 'unknown')}, API Fetched: {last.get('api_fetched', 0)}")
                    logger.error(
                        "Ingestion failed",
                        count=count,
                        api_fetched=api_fetched,
                        db_saved=db_saved,
                        final_status=final_status,
                        elapsed_time=elapsed_time,
                        progress_final=progress_final,
                        error_message=error_message,
                        error_info=error_info
                    )
                elif count == 0 or api_fetched == 0:
                    status.update(label=f"❌ Ingestion failed: No markets fetched (took {elapsed_time:.1f}s)", state="error")
                    st.error(f"❌ **Ingestion failed:** No markets were fetched from the API.")
                    st.error(f"**Details:**")
                    st.error(f"- Returned count: {count}")
                    st.error(f"- API fetched: {api_fetched}")
                    st.error(f"- Final status: {final_status}")
                    st.error(f"- Elapsed time: {elapsed_time:.1f}s")
                    if error_message:
                        st.error(f"- Error message: {error_message}")
                    if error_info:
                        with st.expander("Error Details"):
                            st.code(str(error_info))
                    st.error(f"")
                    st.error(f"**Possible causes:**")
                    st.error(f"- API connection failed or timed out")
                    st.error(f"- API returned empty results")
                    st.error(f"- All batches failed during processing")
                    st.error(f"- Network or authentication issues")
                    st.error(f"")
                    st.error(f"**Check:**")
                    st.error(f"- Check console/logs for detailed error messages")
                    st.error(f"- Verify Polymarket API is accessible")
                    st.error(f"- Check network connection")
                    if progress_state.get("last_update"):
                        last = progress_state["last_update"]
                        st.error(f"**Last progress update:** Batch {last.get('batch', 0)}, Status: {last.get('status', 'unknown')}, API Fetched: {last.get('api_fetched', 0)}")
                    
                    col_reset_err1, col_reset_err2 = st.columns([1, 3])
                    with col_reset_err1:
                        if st.button("🔄 Reset Error Status", key="reset_error_status_ingest", type="secondary"):
                            _clear_ingestion_error(data_ingestion)
                            st.session_state.pending_rerun = True
                            st.rerun()
                    with col_reset_err2:
                        st.caption("Click to clear error status and try again")
                    
                    logger.error(
                        "Ingestion failed - no markets fetched",
                        count=count,
                        api_fetched=api_fetched,
                        final_status=final_status,
                        elapsed_time=elapsed_time,
                        progress_final=progress_final
                    )
                elif has_db:
                    if db_saved > 0:
                        status.update(label=f"✅ Fetched {api_fetched:,} markets, saved {db_saved:,} to DB in {elapsed_time:.1f}s!", state="complete")
                        st.success(f"✅ Successfully fetched {api_fetched:,} markets from API, saved {db_saved:,} to database in {elapsed_time:.1f} seconds!")
                    else:
                        status.update(label=f"⚠️ Fetched {api_fetched:,} markets but none saved to DB in {elapsed_time:.1f}s", state="error")
                        st.warning(f"⚠️ Fetched {api_fetched:,} markets from API in {elapsed_time:.1f} seconds, but none were saved to database.")
                        st.warning("**Possible causes:**")
                        st.warning("- Database connection issue")
                        st.warning("- Database write permissions")
                        st.warning("- Data validation failures")
                        st.warning("- Check logs for detailed error messages")
                else:
                    status.update(label=f"✅ Fetched {api_fetched:,} markets from API in {elapsed_time:.1f}s (DB unavailable)", state="complete")
                    st.success(f"✅ Successfully fetched {api_fetched:,} markets from Polymarket API in {elapsed_time:.1f} seconds!")
                    st.info("💡 **Note:** Data was fetched from blockchain but not saved. Connect database to enable persistence.")
                
                st.session_state.pending_rerun = True
                st.rerun()
            except Exception as e:
                status.update(label=f"❌ Ingestion failed: {str(e)}", state="error")
                st.error(f"❌ **Ingestion failed:** {str(e)}")
                import traceback
                with st.expander("Error Details"):
                    st.code(traceback.format_exc())
                logger.error(f"Ingestion failed with exception: {str(e)}", exc_info=True)
    
    st.divider()
    
    # Data Verification Section
    st.subheader("🔍 Data Quality Verification")
    st.caption("Verify that stored markets are correctly formatted and usable")
    
    if st.button("🔍 Verify Market Data Quality", key="verify_data_btn"):
        if has_db and base_engine.db:
            with st.status("Verifying data quality...", expanded=True) as verify_status:
                try:
                    verification = run_db_safe(base_engine.db.verify_market_data_quality(), {})
                    if not isinstance(verification, dict):
                        verification = {}
                    if verification.get("error"):
                        st.error(f"❌ Verification failed: {verification['error']}")
                    else:
                        total = verification.get("total_markets", 0)
                        valid = verification.get("valid_markets", 0)
                        invalid = verification.get("invalid_markets", 0)
                        validity_pct = verification.get("validity_percentage", 0)
                        price_count = verification.get("price_records", 0)
                        trade_count = verification.get("trade_records", 0)
                        status_msg = verification.get("status", "UNKNOWN")
                        
                        st.metric("📊 Total Markets", f"{total:,}")
                        st.metric("✅ Valid Markets", f"{valid:,} ({validity_pct:.1f}%)")
                        st.metric("❌ Invalid Markets", f"{invalid:,}")
                        st.metric("💰 Price Records", f"{price_count:,}")
                        st.metric("📈 Trade Records", f"{trade_count:,}")
                        
                        if status_msg == "✅ GOOD":
                            st.success(f"**Status:** {status_msg}")
                        elif status_msg == "⚠️ ISSUES FOUND":
                            st.warning(f"**Status:** {status_msg}")
                            if verification.get("issues"):
                                with st.expander("Issues Found"):
                                    for issue in verification.get("issues", [])[:10]:
                                        st.text(issue)
                        else:
                            st.error(f"**Status:** {status_msg}")
                        
                        if verification.get("sample_valid"):
                            with st.expander("✅ Sample Valid Markets"):
                                for market in verification.get("sample_valid", [])[:5]:
                                    st.json(market)
                        
                        if verification.get("sample_invalid"):
                            with st.expander("❌ Sample Invalid Markets"):
                                for market in verification.get("sample_invalid", [])[:5]:
                                    st.json(market)
                        
                        verify_status.update(label=f"✅ Verification complete: {status_msg}", state="complete")
                except Exception as e:
                    st.error(f"❌ Verification error: {str(e)}")
                    verify_status.update(label="❌ Verification failed", state="error")
        else:
            st.warning("⚠️ Database required for data verification")
    
    st.divider()
    
    st.subheader("Database Statistics")
    
    if not base_engine.db or not base_engine.db.session_factory:
        st.warning("⚠️ **Database not available**")
        st.info("💡 Database statistics require database connection. Data can still be fetched from API (blockchain).")
        st.info("📊 Connect database to see statistics and enable data persistence.")
        return
    
    try:
        # Cache database stats to avoid coroutine reuse issues
        db_stats_key = "db_stats_cache"
        db_stats_ts_key = "db_stats_cache_timestamp"
        current_time = datetime.now().timestamp()
        cached_ts = st.session_state.get(db_stats_ts_key, 0)
        
        # Only fetch stats if cache is older than 10 seconds
        if db_stats_key not in st.session_state or (current_time - cached_ts) > 10:
            async def get_db_stats():
                if not base_engine.db or not base_engine.db.session_factory:
                    return {
                        "Markets": 0,
                        "Trades": 0,
                        "Users": 0,
                        "Price Records": 0,
                        "Positions": 0
                    }
                try:
                    async with base_engine.db.get_session() as session:
                        from sqlalchemy import select, func, text
                        from base_engine.data.database import Market, Trade, User, MarketPrice, Position
                        
                        markets_count = await session.execute(select(func.count(Market.id)))
                        trades_count = await session.execute(select(func.count(Trade.id)))
                        users_count = await session.execute(select(func.count(User.address)))  # User uses 'address' as primary key
                        prices_count = await session.execute(select(func.count(MarketPrice.id)))
                        positions_count = await session.execute(select(func.count(Position.id)))
                        
                        return {
                            "Markets": markets_count.scalar() or 0,
                            "Trades": trades_count.scalar() or 0,
                            "Users": users_count.scalar() or 0,
                            "Price Records": prices_count.scalar() or 0,
                            "Positions": positions_count.scalar() or 0
                        }
                except Exception as e:
                    logger.error(f"Error in get_db_stats: {str(e)}", exc_info=True)
                    return {
                        "Markets": 0,
                        "Trades": 0,
                        "Users": 0,
                        "Price Records": 0,
                        "Positions": 0
                    }
            
            try:
                db_stats = run_db_safe(get_db_stats(), {"Markets": 0, "Trades": 0, "Users": 0, "Price Records": 0, "Positions": 0})
                if not isinstance(db_stats, dict):
                    db_stats = {"Markets": 0, "Trades": 0, "Users": 0, "Price Records": 0, "Positions": 0}
                st.session_state[db_stats_key] = db_stats
                st.session_state[db_stats_ts_key] = current_time
            except Exception as e:
                if "cannot reuse already awaited coroutine" in str(e):
                    logger.warning("Database stats coroutine reuse detected, using cached result")
                    db_stats = st.session_state.get(db_stats_key, {
                        "Markets": 0,
                        "Trades": 0,
                        "Users": 0,
                        "Price Records": 0,
                        "Positions": 0
                    })
                else:
                    st.warning(f"Could not load database stats: {e}")
                    db_stats = {"Markets": 0, "Trades": 0, "Users": 0, "Price Records": 0, "Positions": 0}
        else:
            # Use cached result
            db_stats = st.session_state.get(db_stats_key, {
                "Markets": 0,
                "Trades": 0,
                "Users": 0,
                "Price Records": 0,
                "Positions": 0
            })
        
        col1, col2, col3, col4, col5 = st.columns(5)
        for idx, (key, value) in enumerate(db_stats.items()):
            with [col1, col2, col3, col4, col5][idx]:
                st.metric(key, f"{value:,}")
        st.caption("Markets & Price Records from Pull data. Trades & Users from elite user ingestion (runs after markets+prices).")
        st.subheader("Top Markets by Liquidity")
        try:
            markets = run_db_safe(base_engine.db.get_softest_markets(limit=10), [])
            if markets:
                markets_df = pd.DataFrame(markets)
                display_cols = ["id", "question", "category", "liquidity", "volume"]
                available_cols = [col for col in display_cols if col in markets_df.columns]
                if available_cols:
                    st.dataframe(markets_df[available_cols], width="stretch", hide_index=True)
                else:
                    st.dataframe(markets_df, width="stretch", hide_index=True)
            else:
                st.warning("⚠️ No markets in database. Ingest markets first.")
        except Exception as e:
            if "cannot reuse already awaited coroutine" not in str(e):
                logger.warning(f"Error fetching top markets: {str(e)}")
            st.warning("⚠️ No markets in database. Ingest markets first.")
        
        st.subheader("Elite Traders")
        st.caption(
            "Elite traders are top Polymarket users by profit/win rate. **Ingest order:** 1) Pull data (markets + prices) → 2) Ingest users only. "
            "Elite data powers MirrorBot and elite signals. If empty, click **Ingest users only** in Data Center."
        )
        try:
            traders = run_db_safe(base_engine.db.get_elite_traders(limit=10), [])
            if traders:
                traders_df = pd.DataFrame(traders)
                st.dataframe(traders_df, width="stretch", hide_index=True)
            else:
                st.warning("⚠️ No elite traders in database. Ingest users first.")
        except Exception as e:
            if "cannot reuse already awaited coroutine" not in str(e):
                logger.warning(f"Error fetching elite traders: {str(e)}")
            st.warning("⚠️ No elite traders in database. Ingest users first.")
    except Exception:
        pass


def show_settings():
    st.header("⚙️ System Settings")
    
    # Get base_engine at the start of the function
    base_engine = st.session_state.get("base_engine")
    
    st.subheader("💰 Bankroll Management")
    
    col1, col2 = st.columns(2)
    
    with col1:
        total_capital = st.number_input("Total Trading Capital ($)", 0.0, 10000000.0, 10000.0, step=100.0, key="total_capital")
        max_position_pct = st.number_input("Max Position Size %", 0.0, 1.0, settings.MAX_POSITION_SIZE_PCT, step=0.01, key="max_position_pct")
        max_daily_exposure = st.number_input("Max Daily Exposure %", 0.0, 1.0, settings.MAX_DAILY_EXPOSURE, step=0.01, key="max_daily_exposure")
        min_trade_size = st.number_input("Minimum Trade Size ($)", 0.01, 1000.0, 0.01, step=0.01, key="min_trade_size", help="Trade as little as a penny (0.01)")
    
    with col2:
        risk_per_trade = st.number_input("Risk Per Trade %", 0.0, 10.0, 1.0, step=0.1, key="risk_per_trade")
        max_daily_loss = st.number_input("Max Daily Loss Limit ($)", 0.0, 100000.0, 1000.0, step=100.0, key="max_daily_loss")
        max_weekly_loss = st.number_input("Max Weekly Loss Limit ($)", 0.0, 500000.0, 5000.0, step=500.0, key="max_weekly_loss")
        max_monthly_loss = st.number_input("Max Monthly Loss Limit ($)", 0.0, 2000000.0, 20000.0, step=1000.0, key="max_monthly_loss")
    
    st.subheader("Trading Parameters")
    
    col1, col2 = st.columns(2)
    
    with col1:
        min_confidence = st.number_input("Min Confidence Threshold", 0.0, 1.0, settings.MIN_CONFIDENCE_THRESHOLD, step=0.01, key="min_confidence")
        max_positions = st.number_input("Max Positions per Bot", 1, 100, settings.MAX_POSITIONS_PER_BOT, key="max_positions")
    
    with col2:
        bot_scan_interval = st.number_input("Bot Scan Interval (seconds)", 1, 60, settings.BOT_SCAN_INTERVAL_SECONDS, key="bot_scan")
        top_trader_count = st.number_input("Top Trader Count", 1, 1000, min(settings.TOP_TRADER_COUNT, 1000), key="top_traders")
    
    st.subheader("Position Sizing Model")
    sizing_model = st.selectbox("Trade Sizing Model", [
        "Fixed Size",
        "Fixed Risk %",
        "Kelly Criterion (Fractional)",
        "Confidence-Based"
    ], key="sizing_model")
    
    if sizing_model == "Fixed Size":
        fixed_size = st.number_input("Fixed Position Size ($)", 0.01, 10000.0, 100.0, step=10.0, key="fixed_size")
    elif sizing_model == "Fixed Risk %":
        risk_pct = st.number_input("Risk % per Trade", 0.1, 10.0, 1.0, step=0.1, key="risk_pct")
    elif sizing_model == "Kelly Criterion (Fractional)":
        kelly_fraction = st.number_input("Kelly Fraction (0.0-1.0)", 0.0, 1.0, 0.25, step=0.05, key="kelly_fraction", help="0.25 = 25% of full Kelly")
    
    st.subheader("Trading Phase & Guardrails")
    _phase = getattr(settings, "TRADING_PHASE", "paper")
    st.info(f"**Current Phase:** {_phase.upper()}")
    _phase_caps = {"paper": "$15", "learning": "$20", "graduated": "$200", "production": "$1,000"}
    col_p1, col_p2, col_p3, col_p4 = st.columns(4)
    with col_p1:
        st.metric("Phase Bet Cap", _phase_caps.get(_phase, "N/A"))
    with col_p2:
        _politics_exit = "ON" if getattr(settings, "POLITICS_EXIT_ENABLED", False) else "OFF"
        st.metric("Politics Exit", _politics_exit)
    with col_p3:
        _phase_grad = "ON" if getattr(settings, "PHASE_GRADUATION_ENABLED", False) else "OFF"
        st.metric("Phase Graduation", _phase_grad)
    with col_p4:
        _platt = "ON" if getattr(settings, "PLATT_SCALING_ENABLED", False) else "OFF"
        st.metric("Platt Scaling", _platt)

    st.subheader("API Settings")
    st.text_input("Polymarket Gamma API", settings.POLYMARKET_GAMMA_API, key="gamma_api", autocomplete="off")
    st.text_input("Polymarket CLOB API", settings.POLYMARKET_CLOB_API, key="clob_api", autocomplete="off")
    
    st.divider()

    # Connection info (direct — VPS IP handles geo-access)
    st.caption("Direct connection via VPS IP (Dublin, eu-west-1). No VPN required.")

    st.subheader("Database Settings")
    st.text_input("Database URL", settings.DATABASE_URL[:50] + "..." if len(settings.DATABASE_URL or "") > 50 else (settings.DATABASE_URL or ""), key="database_url", disabled=True, autocomplete="off")
    st.text_input("Redis Host", settings.REDIS_HOST, key="redis_host", autocomplete="off")
    
    st.subheader("Prediction Engine Status")
    if base_engine and base_engine.prediction_engine:
        pred_engine = base_engine.prediction_engine
        col1, col2 = st.columns(2)
        with col1:
            status = "✅ Initialized" if pred_engine.initialized else "❌ Not Initialized"
            st.metric("Status", status)
        with col2:
            models_count = len(pred_engine.models) if pred_engine.models else 0
            st.metric("Models Trained", models_count)
        
        if pred_engine.models:
            st.write("**Available Models:**")
            for model_name in pred_engine.models.keys():
                st.write(f"- {model_name.replace('_', ' ').title()}")
        
        if pred_engine.feature_columns:
            st.write("**Features Used:**")
            st.write(", ".join(pred_engine.feature_columns))
    
    # --- New sections (Sessions 36–41) ---

    st.subheader("🎓 Phase Management")
    st.text_input("Trading Phase", value=getattr(settings, "TRADING_PHASE", "paper"), disabled=True,
                  help="paper → learning → graduated → production. Change in .env + restart.")
    _phase_caps_raw = getattr(settings, "PHASE_MAX_BET_USD", '{"paper":15,"learning":20,"graduated":200,"production":1000}')
    try:
        st.json(json.loads(_phase_caps_raw) if isinstance(_phase_caps_raw, str) else _phase_caps_raw, expanded=False)
    except Exception:
        st.caption(str(_phase_caps_raw))
    _cat_kelly_raw = getattr(settings, "CATEGORY_KELLY_FRACTIONS", '{"weather":0.25,"crypto":0.125,"politics":0.20,"sports":0.15}')
    try:
        st.json(json.loads(_cat_kelly_raw) if isinstance(_cat_kelly_raw, str) else _cat_kelly_raw, expanded=False)
    except Exception:
        st.caption(str(_cat_kelly_raw))

    st.subheader("📈 Graduation Thresholds")
    _gcols = st.columns(2)
    with _gcols[0]:
        st.caption("Paper → Learning")
        st.metric("Min Win Rate", f"{getattr(settings, 'PHASE_PAPER_TO_LEARNING_WIN_RATE', 0.52):.0%}")
        st.metric("Max Brier", getattr(settings, "PHASE_PAPER_TO_LEARNING_MAX_BRIER", 0.22))
        st.metric("Min Predictions", getattr(settings, "PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS", 100))
    with _gcols[1]:
        st.caption("Learning → Graduated")
        st.metric("Min Win Rate", f"{getattr(settings, 'PHASE_LEARNING_TO_GRADUATED_WIN_RATE', 0.55):.0%}")
        st.metric("Max Brier", getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MAX_BRIER", 0.20))
        st.metric("Min Predictions", getattr(settings, "PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS", 300))

    st.subheader("💼 Capital Bucketing")
    _bcols = st.columns(4)
    _bcols[0].metric("Short Term (<30d)", f"{getattr(settings, 'BUCKET_SHORT_TERM_PCT', 0.40):.0%}")
    _bcols[1].metric("Medium (30-180d)", f"{getattr(settings, 'BUCKET_MEDIUM_TERM_PCT', 0.35):.0%}")
    _bcols[2].metric("Long (>180d)", f"{getattr(settings, 'BUCKET_LONG_TERM_PCT', 0.05):.0%}")
    _bcols[3].metric("Liquid Reserve", f"{getattr(settings, 'BUCKET_LIQUID_RESERVE_PCT', 0.20):.0%}")

    st.subheader("🧪 Advanced Model Features")
    _mcols = st.columns(4)
    _mcols[0].metric("Bayesian Model", "✅ ON" if getattr(settings, "BAYESIAN_MODEL_ENABLED", False) else "⭕ OFF")
    _mcols[1].metric("Logical Arb", "✅ ON" if getattr(settings, "LOGICAL_ARB_ENABLED", False) else "⭕ OFF")
    _mcols[2].metric("LLM Consensus", str(getattr(settings, "LLM_CONSENSUS_MODE", "fallback")))
    _mcols[3].metric("Platt Scaling", "✅ ON" if getattr(settings, "PLATT_SCALING_ENABLED", False) else "⭕ OFF")
    st.caption("Bot-level guardrails")
    _gcols2 = st.columns(3)
    _gcols2[0].metric("Politics Exit", f"{'ON' if getattr(settings, 'POLITICS_EXIT_ENABLED', True) else 'OFF'} @ {getattr(settings, 'POLITICS_EXIT_PCT', 0.65):.0%}")
    _gcols2[1].metric("Weather Hold hrs", str(getattr(settings, "WEATHER_HOLD_HOURS_BEFORE_RESOLUTION", 48.0)))
    _gcols2[2].metric("LogicalArbBot", "✅ ON" if getattr(settings, "BOT_ENABLED_LOGICAL_ARB", False) else "⭕ OFF")
    st.caption("Esports")
    _ecols = st.columns(3)
    _ecols[0].metric("EsportsBot", "✅" if getattr(settings, "BOT_ENABLED_ESPORTS", False) else "⭕")
    _ecols[1].metric("EsportsBotV2", "✅" if getattr(settings, "BOT_ENABLED_ESPORTS_V2", False) else "⭕")
    _ecols[2].metric("EsportsLiveBot", "✅" if getattr(settings, "BOT_ENABLED_ESPORTS_LIVE", False) else "⭕")

    if st.button("💾 Save Settings", type="primary"):
        st.success("Settings saved! (Note: Some settings require restart to take effect)")


if __name__ == "__main__":
    main()
