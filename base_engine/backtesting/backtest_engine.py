import numpy as np
import pandas as pd
import inspect
from typing import Any, Dict, List, Optional, Callable, Awaitable, Literal
from datetime import datetime, timedelta
from structlog import get_logger
from base_engine.data.database import Database
from base_engine.utils.validation import validate_market_ids, safe_divide
from config.settings import settings

logger = get_logger()


class BacktestResult:
    """
    Container for backtest results including performance metrics.
    
    Attributes:
        total_return: Total return percentage
        total_trades: Number of trades executed
        winning_trades: Number of profitable trades
        losing_trades: Number of losing trades
        win_rate: Percentage of winning trades
        sharpe_ratio: Risk-adjusted return metric
        max_drawdown: Maximum peak-to-trough decline percentage
        profit_factor: Ratio of gross profit to gross loss
        avg_win: Average profit per winning trade
        avg_loss: Average loss per losing trade
        total_pnl: Total profit and loss
        trades: List of individual trade records
    """
    def __init__(self):
        self.total_return = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.win_rate = 0.0
        self.sharpe_ratio = 0.0
        self.max_drawdown = 0.0
        self.profit_factor = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.total_pnl = 0.0
        self.trades = []


class BacktestEngine:
    """
    Engine for running backtests on trading strategies using historical data.

    Provides methods to simulate strategy execution over historical periods
    and calculate performance metrics including Sharpe ratio, drawdown, and win rate.

    Temporal leakage note:
    - Trades/prices are replayed in chronological order (ORDER BY timestamp ASC)
    - Strategy function only receives data up to the current timestamp (no lookahead)
    - Slippage (BACKTEST_SLIPPAGE_BPS) and fees (TAKER_FEE_BPS) are deducted to avoid
      overstating returns. Set BACKTEST_INCLUDE_FEES=false to disable fee modeling.
    - The strategy_func is responsible for not using future data internally
    """
    def __init__(self, db: Database):
        self.db = db
    
    async def run_backtest(
        self,
        strategy_func: Callable,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0,
        market_ids: Optional[List[str]] = None,
        data_source: str = "auto",
    ) -> BacktestResult:
        logger.info(
            "Starting backtest",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            initial_capital=initial_capital,
            market_ids_count=len(market_ids) if market_ids else None
        )
        
        from base_engine.utils.validation import validate_numeric
        
        if not callable(strategy_func):
            raise TypeError(f"strategy_func must be callable, got {type(strategy_func).__name__}")
        
        if not inspect.iscoroutinefunction(strategy_func):
            logger.warning(f"strategy_func is not async. It will be called without await, which may cause issues.")
        
        initial_capital = validate_numeric(initial_capital, "initial_capital", min_val=0.0, allow_zero=False)
        market_ids = validate_market_ids(market_ids)
        
        # Strip timezone info to match DB columns (TIMESTAMP WITHOUT TIME ZONE)
        if start_date.tzinfo is not None:
            start_date = start_date.replace(tzinfo=None)
        if end_date.tzinfo is not None:
            end_date = end_date.replace(tzinfo=None)

        if start_date >= end_date:
            raise ValueError(f"start_date {start_date} must be before end_date {end_date}")

        date_range_days = (end_date - start_date).days
        if date_range_days > 3650:
            raise ValueError(f"Date range too large: {date_range_days} days (maximum 3650 days / 10 years)")
        
        if date_range_days < 1:
            raise ValueError(f"Date range too small: {date_range_days} days (minimum 1 day)")
        
        result = BacktestResult()
        
        if self.db.session_factory is None:
            raise RuntimeError("Database required for backtesting. Cannot proceed without database connection.")
        
        capital = initial_capital
        positions = {}
        equity_curve = [initial_capital]
        returns = []
        
        prefer_prices = data_source == "prices" or (
            data_source == "auto" and getattr(settings, "BACKTEST_PREFER_PRICE_HISTORY", False)
        )
        
        async with self.db.get_session() as session:
            if prefer_prices:
                trades_df = await self._get_price_dataframe(session, start_date, end_date, market_ids)
                if trades_df.empty:
                    trades_df = await self._get_trades_dataframe(session, start_date, end_date, market_ids)
            else:
                trades_df = await self._get_trades_dataframe(session, start_date, end_date, market_ids)
        
        # FALLBACK: If no trades, use real historical price data from market_prices table
        if trades_df.empty:
            logger.info("No trades found, attempting to use real historical price data for backtesting")
            async with self.db.get_session() as session:
                trades_df = await self._get_price_dataframe(session, start_date, end_date, market_ids)
            
            if trades_df.empty:
                # Provide detailed diagnostics
                async with self.db.get_session() as session:
                    from sqlalchemy import text
                    
                    # Check what data exists in database
                    check_trades = text("SELECT COUNT(*) as count FROM trades WHERE timestamp >= :start_date AND timestamp <= :end_date")
                    check_prices = text("SELECT COUNT(*) as count FROM market_prices WHERE timestamp >= :start_date AND timestamp <= :end_date")
                    
                    trades_result = await session.execute(check_trades, {"start_date": start_date, "end_date": end_date})
                    prices_result = await session.execute(check_prices, {"start_date": start_date, "end_date": end_date})
                    
                    trades_count = trades_result.scalar() or 0
                    prices_count = prices_result.scalar() or 0
                    
                    # Check overall data availability
                    check_all_trades = text("SELECT COUNT(*) as count FROM trades")
                    check_all_prices = text("SELECT COUNT(*) as count FROM market_prices")
                    check_trades_date_range = text("SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM trades")
                    check_prices_date_range = text("SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM market_prices")
                    
                    all_trades_result = await session.execute(check_all_trades)
                    all_prices_result = await session.execute(check_all_prices)
                    trades_range_result = await session.execute(check_trades_date_range)
                    prices_range_result = await session.execute(check_prices_date_range)
                    
                    all_trades_count = all_trades_result.scalar() or 0
                    all_prices_count = all_prices_result.scalar() or 0
                    trades_range = trades_range_result.fetchone()
                    prices_range = prices_range_result.fetchone()
                
                error_msg = (
                    f"No historical trades or price data found in database for period {start_date} to {end_date}.\n"
                    f"Backtesting requires real historical data.\n\n"
                )
                
                if all_trades_count == 0 and all_prices_count == 0:
                    error_msg += (
                        "❌ **No data in database at all.**\n"
                        "Please ingest historical trades or historical prices from the Polymarket API first.\n"
                        "See the Data Center tab for data ingestion options."
                    )
                elif trades_count == 0 and prices_count == 0:
                    error_msg += (
                        f"⚠️ **No data found for this date range.**\n\n"
                        f"**Database Status:**\n"
                        f"- Total trades in database: {all_trades_count:,}\n"
                        f"- Total prices in database: {all_prices_count:,}\n"
                    )
                    
                    if trades_range and trades_range[0]:
                        error_msg += f"- Trades date range: {trades_range[0]} to {trades_range[1]}\n"
                    if prices_range and prices_range[0]:
                        error_msg += f"- Prices date range: {prices_range[0]} to {prices_range[1]}\n"
                    
                    error_msg += (
                        f"\n**Requested Range:** {start_date} to {end_date}\n\n"
                        f"💡 **Solution:** Ingest historical data for the date range you want to backtest.\n"
                        f"Make sure the ingestion date range covers your backtest date range."
                    )
                else:
                    error_msg += (
                        f"⚠️ **Data exists but query failed.**\n"
                        f"- Trades in range: {trades_count:,}\n"
                        f"- Prices in range: {prices_count:,}\n"
                        f"Please check database connection and query parameters."
                    )
                
                raise RuntimeError(error_msg)
        
        for idx, trade in trades_df.iterrows():
            try:
                if not isinstance(trade, pd.Series):
                    logger.warning(f"Trade at index {idx} is not a Series, skipping")
                    continue
                
                required_fields = ["id", "market_id", "token_id", "side", "timestamp"]
                missing_fields = [f for f in required_fields if f not in trade.index]
                if missing_fields:
                    logger.warning(f"Trade at index {idx} missing fields: {missing_fields}, skipping")
                    continue
                
                if not inspect.iscoroutinefunction(strategy_func):
                    logger.warning(
                        "Strategy function is not async",
                        function_name=strategy_func.__name__ if hasattr(strategy_func, '__name__') else 'unknown',
                        will_call_without_await=True
                    )
                    decision = strategy_func(trade.to_dict(), positions, capital)
                else:
                    decision = await strategy_func(trade.to_dict(), positions, capital)
                
                if not isinstance(decision, dict):
                    logger.warning(f"Strategy function returned non-dict: {type(decision)}, skipping")
                    continue
                
                action = decision.get("action")
                if action not in ["BUY", "SELL", "HOLD"]:
                    logger.warning(f"Invalid action '{action}' from strategy, skipping")
                    continue
                
                # Phase 3: slippage + fee modeling (realistic backtest P&L)
                slippage_bps = getattr(settings, "BACKTEST_SLIPPAGE_BPS", 50) / 10000.0
                _include_fees = getattr(settings, "BACKTEST_INCLUDE_FEES", True)
                _fee_rate = (getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0) if _include_fees else 0.0
                # Latency simulation: optional delay before fill
                latency_ms = getattr(settings, "BACKTEST_LATENCY_SIMULATION_MS", 0)
                if latency_ms > 0:
                    import asyncio
                    await asyncio.sleep(latency_ms / 1000.0)

                if decision.get("action") == "BUY":
                    decision_size = decision.get("size", 0.0)
                    decision_price = decision.get("price", 0.0)
                    fill_price = decision_price * (1 + slippage_bps) if decision_price > 0 else 0.0

                    if decision_size > 0 and fill_price > 0:
                        cost = decision_size * fill_price
                        entry_fee = cost * _fee_rate
                        total_cost = cost + entry_fee
                        if capital >= total_cost:
                            positions[trade["id"]] = {
                                "market_id": trade["market_id"],
                                "token_id": trade["token_id"],
                                "side": trade["side"],
                                "size": decision_size,
                                "entry_price": fill_price,
                                "entry_time": trade["timestamp"],
                                "entry_fee": entry_fee,
                            }
                            capital -= total_cost
                            result.total_trades += 1

                elif decision.get("action") == "SELL" and trade["id"] in positions:
                    pos = positions[trade["id"]]
                    exit_price_raw = decision.get("price", 0.0)
                    exit_price = exit_price_raw * (1 - slippage_bps) if exit_price_raw > 0 else 0.0

                    if exit_price > 0:
                        exit_proceeds = decision.get("size", pos["size"]) * exit_price
                        exit_fee = exit_proceeds * _fee_rate
                        pnl = (exit_price - pos["entry_price"]) * pos["size"] - pos.get("entry_fee", 0) - exit_fee
                        if pos["side"] == "NO":
                            pnl = -pnl
                        
                        capital += exit_proceeds - exit_fee
                        result.total_pnl += pnl
                        returns.append(pnl)
                        
                        if pnl > 0:
                            result.winning_trades += 1
                            result.avg_win = safe_divide(
                                result.avg_win * (result.winning_trades - 1) + pnl,
                                result.winning_trades,
                                default=0.0
                            )
                        else:
                            result.losing_trades += 1
                            result.avg_loss = safe_divide(
                                result.avg_loss * (result.losing_trades - 1) + abs(pnl),
                                result.losing_trades,
                                default=0.0
                            )
                        
                        result.trades.append({
                            "market_id": pos["market_id"],
                            "entry_price": pos["entry_price"],
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "entry_time": pos["entry_time"],
                            "exit_time": trade["timestamp"]
                        })
                        
                        del positions[trade["id"]]
            except Exception as e:
                logger.warning(
                    "Error processing trade",
                    trade_index=idx,
                    error=str(e),
                    exc_info=True
                )
                continue
            
            equity_curve.append(capital)
        
        result.total_return = safe_divide((capital - initial_capital) * 100, initial_capital, default=0.0)
        result.win_rate = safe_divide(result.winning_trades, result.total_trades, default=0.0)
        result.sharpe_ratio = self._calculate_sharpe_ratio(returns)
        result.max_drawdown = self._calculate_max_drawdown(equity_curve)
        
        if result.losing_trades > 0 and result.avg_loss > 0:
            result.profit_factor = abs(safe_divide(
                result.avg_win * result.winning_trades,
                result.avg_loss * result.losing_trades,
                default=0.0
            ))
        else:
            result.profit_factor = 0.0
        
        logger.info(
            "Backtest complete",
            total_return=f"{result.total_return:.2f}%",
            win_rate=f"{result.win_rate:.2%}",
            total_trades=result.total_trades,
            winning_trades=result.winning_trades,
            losing_trades=result.losing_trades,
            sharpe_ratio=f"{result.sharpe_ratio:.2f}",
            max_drawdown=f"{result.max_drawdown:.2f}%",
            profit_factor=f"{result.profit_factor:.2f}"
        )
        return result
    
    async def _get_trades_dataframe(self, session, start_date: datetime, end_date: datetime, market_ids: Optional[List[str]]):
        """
        Retrieve trades from database as pandas DataFrame.
        
        Args:
            session: Database session
            start_date: Start date filter
            end_date: End date filter
            market_ids: Optional list of market IDs to filter
        
        Returns:
            DataFrame with trade data, empty DataFrame if no trades found
        """
        from sqlalchemy import text
        
        query = text("""
            SELECT * FROM trades
            WHERE timestamp >= :start_date AND timestamp <= :end_date
            ORDER BY timestamp ASC
        """)
        
        params = {"start_date": start_date, "end_date": end_date}
        if market_ids:
            # SQLite-compatible: use IN instead of ANY
            placeholders = ','.join([':market_id_' + str(i) for i in range(len(market_ids))])
            query = text(f"""
                SELECT * FROM trades
                WHERE timestamp >= :start_date AND timestamp <= :end_date
                AND market_id IN ({placeholders})
                ORDER BY timestamp ASC
            """)
            params.update({f'market_id_{i}': mid for i, mid in enumerate(market_ids)})
        
        result = await session.execute(query, params)
        rows = result.fetchall()
        
        if not rows:
            return pd.DataFrame()
        
        return pd.DataFrame([dict(row._mapping) for row in rows])
    
    async def _get_price_dataframe(self, session, start_date: datetime, end_date: datetime, market_ids: Optional[List[str]]):
        """
        Retrieve real historical price data from market_prices table and convert to trade-like format.
        
        This uses ONLY real price data from the database - no synthetic or placeholder data.
        
        Args:
            session: Database session
            start_date: Start date filter
            end_date: End date filter
            market_ids: Optional list of market IDs to filter
        
        Returns:
            DataFrame with trade-like data based on real historical prices
        """
        from sqlalchemy import text
        
        query = text("""
            SELECT market_id, token_id, price, timestamp
            FROM market_prices
            WHERE timestamp >= :start_date AND timestamp <= :end_date
            ORDER BY timestamp ASC, market_id ASC
        """)
        
        params = {"start_date": start_date, "end_date": end_date}
        if market_ids:
            # SQLite-compatible: use IN instead of ANY
            placeholders = ','.join([':market_id_' + str(i) for i in range(len(market_ids))])
            query = text(f"""
                SELECT market_id, token_id, price, timestamp
                FROM market_prices
                WHERE timestamp >= :start_date AND timestamp <= :end_date
                AND market_id IN ({placeholders})
                ORDER BY timestamp ASC, market_id ASC
            """)
            params.update({f'market_id_{i}': mid for i, mid in enumerate(market_ids)})
        
        result = await session.execute(query, params)
        rows = result.fetchall()
        
        if not rows:
            return pd.DataFrame()
        
        # Convert real price data to trade-like format for backtesting
        # This is still REAL data, just restructured for the backtest engine
        price_df = pd.DataFrame([dict(row._mapping) for row in rows])
        
        # Create trade-like structure from real price data
        # Each price point becomes a potential trade opportunity
        price_df['id'] = price_df.index.astype(str)
        price_df['side'] = 'YES'  # Default side (can be determined from token_id if needed)
        price_df['size'] = 1.0  # Default size for backtesting
        price_df['user_address'] = 'backtest_price_data'  # Identifier that this is from price data
        
        # Return in trade format: id, market_id, token_id, side, size, price, timestamp, user_address
        return price_df[['id', 'market_id', 'token_id', 'side', 'size', 'price', 'timestamp', 'user_address']]
    
    
    async def run_walk_forward(
        self,
        strategy_func: Callable,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10000.0,
        market_ids: Optional[List[str]] = None,
        train_days: int = 30,
        test_days: int = 7,
        data_source: str = "auto",
    ) -> Dict[str, Any]:
        """Walk-forward backtest: split the period into rolling train/test windows.

        For each window:
        1. Train window: strategy_func sees data but doesn't trade (warm-up).
        2. Test window: strategy_func trades on unseen data.
        3. Slide forward by test_days and repeat.

        Returns aggregate results across all test windows with per-window breakdown.
        """
        logger.info(
            "Starting walk-forward backtest",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            train_days=train_days,
            test_days=test_days,
        )

        total_days = (end_date - start_date).days
        if total_days < train_days + test_days:
            raise ValueError(
                f"Date range ({total_days}d) too short for train_days={train_days} + test_days={test_days}"
            )

        windows: List[Dict[str, Any]] = []
        all_returns: List[float] = []
        all_equity: List[float] = [initial_capital]
        capital = initial_capital
        total_trades = 0
        total_wins = 0
        total_losses = 0
        total_pnl = 0.0

        cursor = start_date
        window_num = 0
        while cursor + timedelta(days=train_days + test_days) <= end_date:
            window_num += 1
            train_start = cursor
            train_end = cursor + timedelta(days=train_days)
            test_start = train_end
            test_end = min(test_start + timedelta(days=test_days), end_date)

            # Run backtest only on the test window (train window is warm-up)
            try:
                result = await self.run_backtest(
                    strategy_func=strategy_func,
                    start_date=test_start,
                    end_date=test_end,
                    initial_capital=capital,
                    market_ids=market_ids,
                    data_source=data_source,
                )
                capital = capital + result.total_pnl
                total_trades += result.total_trades
                total_wins += result.winning_trades
                total_losses += result.losing_trades
                total_pnl += result.total_pnl
                all_equity.append(capital)

                # Collect per-trade returns for aggregate Sharpe
                for t in result.trades:
                    all_returns.append(t.get("pnl", 0.0))

                windows.append({
                    "window": window_num,
                    "train_start": train_start.isoformat(),
                    "train_end": train_end.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "trades": result.total_trades,
                    "pnl": round(result.total_pnl, 4),
                    "win_rate": round(result.win_rate, 4),
                    "sharpe": round(result.sharpe_ratio, 2),
                    "max_drawdown": round(result.max_drawdown, 2),
                })
            except RuntimeError as e:
                # No data in this window — skip
                logger.debug("Walk-forward window %d skipped: %s", window_num, e)
                windows.append({
                    "window": window_num,
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "skipped": True,
                    "reason": str(e)[:100],
                })

            cursor += timedelta(days=test_days)

        aggregate_sharpe = self._calculate_sharpe_ratio(all_returns)
        aggregate_drawdown = self._calculate_max_drawdown(all_equity)

        summary = {
            "windows_total": window_num,
            "windows_traded": sum(1 for w in windows if not w.get("skipped")),
            "total_trades": total_trades,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_pnl": round(total_pnl, 4),
            "final_capital": round(capital, 4),
            "total_return_pct": round((capital - initial_capital) / initial_capital * 100, 2) if initial_capital else 0.0,
            "aggregate_sharpe": round(aggregate_sharpe, 2),
            "aggregate_max_drawdown_pct": round(aggregate_drawdown, 2),
            "aggregate_win_rate": round(total_wins / max(total_trades, 1), 4),
            "windows": windows,
        }

        logger.info(
            "Walk-forward backtest complete",
            windows=window_num,
            total_trades=total_trades,
            total_pnl=f"${total_pnl:.2f}",
            sharpe=f"{aggregate_sharpe:.2f}",
            max_dd=f"{aggregate_drawdown:.2f}%",
        )
        return summary

    def _calculate_sharpe_ratio(self, returns: List[float]) -> float:
        if not returns or len(returns) < 2:
            return 0.0
        returns_array = np.array(returns)
        std_dev = returns_array.std()
        if std_dev == 0 or np.isnan(std_dev) or np.isinf(std_dev):
            return 0.0  # No volatility or invalid standard deviation
        # BUG FIX: Additional check to prevent division by values too close to zero
        if abs(std_dev) < 1e-10:
            return 0.0
        return (returns_array.mean() / std_dev) * np.sqrt(252)

    def _calculate_max_drawdown(self, equity_curve: List[float]) -> float:
        """Peak-to-trough decline as percentage (0-100)."""
        if not equity_curve or len(equity_curve) < 2:
            return 0.0
        peak = float(equity_curve[0])
        max_dd = 0.0
        for v in equity_curve:
            v = float(v)
            if v > peak:
                peak = v
            if peak > 0:
                dd = (peak - v) / peak * 100.0
                if dd > max_dd:
                    max_dd = dd
        return round(max_dd, 2)

    @staticmethod
    def calculate_binary_resolution_pnl(
        entry_price: float,
        size: float,
        side: str,
        resolved_yes: bool,
        fee_bps: int = 150,
    ) -> float:
        """
        Calculate P&L for a binary prediction market position at resolution.
        Markets resolve to 0 or 1 (not continuous prices).

        Args:
            entry_price: Price paid for the token (0-1)
            size: Number of tokens (shares)
            side: "YES" or "NO"
            resolved_yes: True if market resolved YES
            fee_bps: Taker fee in basis points

        Returns:
            Net P&L in USD
        """
        fee_rate = fee_bps / 10000.0
        cost = entry_price * size * (1 + fee_rate)

        if side.upper() == "YES":
            payout = size if resolved_yes else 0.0
        else:
            payout = size if not resolved_yes else 0.0

        return payout - cost

    async def backtest_with_resolutions(
        self,
        strategy_func: Callable,
        initial_capital: float = 10000.0,
        market_ids: Optional[List[str]] = None,
    ) -> BacktestResult:
        """
        Run backtest using actual market resolutions (binary payoffs).
        Only works with resolved markets. More realistic than price-based P&L.
        """
        result = BacktestResult()
        if self.db.session_factory is None:
            return result

        from sqlalchemy import text
        async with self.db.get_session() as session:
            # Get resolved markets with their resolution
            q = """
                SELECT id, question, resolution, price, category
                FROM markets
                WHERE resolved = true AND resolution IN ('YES', 'NO')
            """
            if market_ids:
                q += " AND id = ANY(:mids)"
                rows = await session.execute(text(q), {"mids": market_ids})
            else:
                rows = await session.execute(text(q))
            markets = rows.fetchall()

        if not markets:
            return result

        capital = initial_capital
        trades = []

        for market in markets:
            mid, question, resolution, price, category = market
            if price is None or price <= 0 or price >= 1:
                continue
            resolved_yes = resolution == "YES"

            # Call strategy function to get trade decision
            market_data = {
                "id": mid, "question": question, "price": price,
                "category": category, "resolution": resolution,
            }
            try:
                if inspect.iscoroutinefunction(strategy_func):
                    decision = await strategy_func(market_data)
                else:
                    decision = strategy_func(market_data)
            except Exception:
                continue

            if not decision or not decision.get("trade"):
                continue

            side = decision.get("side", "YES")
            size = min(decision.get("size", 10.0), capital * 0.1)  # Max 10% per trade
            entry_price = price

            pnl = self.calculate_binary_resolution_pnl(
                entry_price=entry_price, size=size, side=side,
                resolved_yes=resolved_yes,
            )
            capital += pnl
            trades.append({"market_id": mid, "side": side, "entry_price": entry_price,
                           "size": size, "pnl": pnl, "resolved_yes": resolved_yes})

        # Calculate metrics
        result.total_trades = len(trades)
        result.trades = trades
        result.winning_trades = sum(1 for t in trades if t["pnl"] > 0)
        result.losing_trades = sum(1 for t in trades if t["pnl"] <= 0)
        result.win_rate = result.winning_trades / max(result.total_trades, 1) * 100
        result.total_pnl = sum(t["pnl"] for t in trades)
        result.total_return = (capital - initial_capital) / initial_capital * 100

        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
        result.avg_win = sum(wins) / len(wins) if wins else 0
        result.avg_loss = sum(losses) / len(losses) if losses else 0
        result.profit_factor = abs(sum(wins)) / max(abs(sum(losses)), 0.01) if losses else float("inf")

        return result

