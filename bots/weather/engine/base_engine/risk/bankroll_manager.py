"""
Per-Bot Bankroll Manager — Independent Kelly sizing + capital allocation.

Session 47: Replaces the global KELLY_ACTIVE_BOTS divisor approach with true per-bot
independence. Each bot gets its own capital pool, Kelly fraction, and daily/per-trade caps.
Follows the pattern established by SportsBankrollManager.

Architecture:
  - BotBankrollManager is instantiated per-bot (in base_bot.__init__)
  - Config loaded from BOT_BANKROLL_CONFIG JSON setting (per-bot overrides)
  - Fallback to built-in defaults (EnsembleBot=8k, ArbitrageBot=1k, etc.)
  - risk_manager.check_risk_limits() remains the SAFETY layer (max position, exposure, loss)
  - BotBankrollManager handles SIZING; risk_manager handles LIMITS. Both must pass.

Usage::
    mgr = BotBankrollManager("EnsembleBot", order_gateway=og, db=db)
    size_usd = await mgr.get_bet_size(confidence=0.65, price=0.50, category="politics")
    shares = size_usd / price
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional, Tuple

from structlog import get_logger

from bots.weather.engine.config.settings import settings

logger = get_logger()

# Built-in defaults per bot. Override via BOT_BANKROLL_CONFIG env var.
_DEFAULT_BOT_CONFIGS: Dict[str, Dict[str, Any]] = {
    # S105: Aligned all active bots to $20K/$300/$10K per CLAUDE.md Key Config.
    # Inactive bots keep conservative defaults. Override via BOT_BANKROLL_CONFIG env var.
    "EnsembleBot":         {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000},
    "ArbitrageBot":        {"capital": 1000, "kelly_fraction": 0.25, "max_bet_usd": 100, "max_daily_usd": 500},
    "MirrorBot":           {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 5000},  # S137: 10k→5k (39.3% WR, -$159K all-time)
    "CrossPlatformArbBot": {"capital": 500,  "kelly_fraction": 0.20, "max_bet_usd": 50,  "max_daily_usd": 200},
    "OracleBot":           {"capital": 500,  "kelly_fraction": 0.20, "max_bet_usd": 50,  "max_daily_usd": 200},
    "LLMForecasterBot":    {"capital": 500,  "kelly_fraction": 0.20, "max_bet_usd": 50,  "max_daily_usd": 200},
    "WeatherBot":          {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 200, "max_daily_usd": 20000},  # S173: 600→200 (Day 2 RC fix: $200+ positions = 67% of losses)
    "LogicalArbBot":       {"capital": 500,  "kelly_fraction": 0.20, "max_bet_usd": 200, "max_daily_usd": 500},
    "EsportsBot":          {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000},
    "EsportsLiveBot":      {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000},
    "EsportsBotV2":        {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000},  # S210: parity with EsportsBot — Phase 5v2-D paper trading
}

_FALLBACK_CONFIG: Dict[str, Any] = {
    "capital": 1000,
    "kelly_fraction": 0.25,
    "max_bet_usd": 100,
    "max_daily_usd": 500,
}


class BotBankrollManager:
    """
    Per-bot bankroll manager with Kelly sizing, capital isolation, and hard caps.

    Each bot instance gets its own:
      - Capital pool (not shared with other bots)
      - Kelly fraction (not divided by number of active bots)
      - Per-trade and daily USD caps
      - Calibration-aware sizing (Brier score scaling)
    """

    def __init__(
        self,
        bot_name: str,
        order_gateway: Any = None,
        db: Any = None,
    ) -> None:
        self.bot_name = bot_name
        self._gw = order_gateway
        self._db = db
        self._daily_lock = asyncio.Lock()

        # Load per-bot config
        cfg = self._load_bot_config(bot_name)
        self.capital: float = cfg["capital"]
        self.kelly_fraction: float = cfg["kelly_fraction"]
        self.max_bet_usd: float = cfg["max_bet_usd"]
        self.max_daily_usd: float = cfg["max_daily_usd"]
        # S217: per-bot wallet-derived bankroll (replaces BOT_BANKROLL_CONFIG capital
        # fiction with on-chain USDC.e balance). kelly_fraction / max_bet_usd /
        # max_daily_usd remain config-driven — those are operator policy, not
        # capacity facts. Gated per-bot via BOT_WALLET_BANKROLL_ENABLED JSON.
        self._wallet_bankroll_enabled: bool = self._is_wallet_bankroll_enabled(bot_name)
        self._last_wallet_capital: Optional[float] = None
        self._last_wallet_read_ts: float = 0.0  # monotonic; 0 = never initialized
        self._wallet_refresh_task: Optional[asyncio.Task] = None
        # Defensive float coercion — MagicMock settings (test fixtures) have
        # auto-attribute __float__ that returns 1.0; would silently set 1.0 here.
        def _safe_float(name: str, default: float) -> float:
            raw = getattr(settings, name, default)
            if isinstance(raw, (int, float)):
                return float(raw)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default
        # Only meaningful when _wallet_bankroll_enabled is True; harmless otherwise.
        self._wallet_stale_threshold_s: float = (
            _safe_float("WALLET_BANKROLL_STALE_THRESHOLD_S", 3600.0)
            if self._wallet_bankroll_enabled else 3600.0
        )
        self._wallet_refresh_interval_s: float = (
            _safe_float("WALLET_BANKROLL_REFRESH_INTERVAL_S", 600.0)
            if self._wallet_bankroll_enabled else 600.0
        )
        # Day 2 (S173): Shared phase cap across all bots via PHASE_MAX_BET_USD JSON + TRADING_PHASE
        # Parsed from risk_manager.py:934-946 pattern. Read here for bankroll-level enforcement.
        try:
            import json as _json_bm
            _phase = getattr(settings, "TRADING_PHASE", "paper").lower()
            _phase_caps = _json_bm.loads(getattr(settings, "PHASE_MAX_BET_USD", "{}"))
            self.phase_max_bet_usd: float = float(_phase_caps.get(_phase, 200.0))
        except Exception:
            self.phase_max_bet_usd = 200.0

        logger.info(
            "BotBankrollManager initialized",
            bot_name=bot_name,
            capital=self.capital,
            kelly_fraction=self.kelly_fraction,
            max_bet_usd=self.max_bet_usd,
            max_daily_usd=self.max_daily_usd,
        )

    @staticmethod
    def _load_bot_config(bot_name: str) -> Dict[str, Any]:
        """Load per-bot config from BOT_BANKROLL_CONFIG JSON setting with fallback defaults."""
        raw = getattr(settings, "BOT_BANKROLL_CONFIG", "{}")
        try:
            overrides = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            overrides = {}

        # Merge priority: env override > built-in default for this bot > generic fallback
        bot_default = _DEFAULT_BOT_CONFIGS.get(bot_name, {})
        merged = {**_FALLBACK_CONFIG, **bot_default, **overrides.get(bot_name, {})}

        return {
            "capital": float(merged["capital"]),
            "kelly_fraction": float(merged["kelly_fraction"]),
            "max_bet_usd": float(merged["max_bet_usd"]),
            "max_daily_usd": float(merged["max_daily_usd"]),
        }

    @staticmethod
    def _is_wallet_bankroll_enabled(bot_name: str) -> bool:
        """S217: per-bot opt-in for wallet-derived capital. JSON form:
        BOT_WALLET_BANKROLL_ENABLED='{"MirrorBot": true}'.
        Defaults False for all bots — the 13 unmigrated bots keep config capital.
        Defensive against MagicMock (test fixtures patch `settings` to MagicMock,
        whose auto-attributes are truthy): only string-JSON or dict accepted."""
        raw = getattr(settings, "BOT_WALLET_BANKROLL_ENABLED", "{}")
        try:
            if isinstance(raw, str):
                cfg = json.loads(raw)
            elif isinstance(raw, dict):
                cfg = raw
            else:
                return False
            if not isinstance(cfg, dict):
                return False
            return bool(cfg.get(bot_name, False))
        except Exception:
            return False

    async def init_wallet_bankroll(self) -> None:
        """S217: Read wallet USDC.e balance and set `capital` accordingly.
        No-op if BOT_WALLET_BANKROLL_ENABLED is off for this bot.

        Cold-start guarantees:
          - Raises RuntimeError if wallet read fails (after one retry).
          - Raises RuntimeError if wallet balance is $0.
          - Spawns background refresh loop on success.

        Idempotent: subsequent calls are no-ops if refresh loop already running.
        Called from base_bot.start() after constructor.
        """
        if not self._wallet_bankroll_enabled:
            return
        if self._wallet_refresh_task is not None and not self._wallet_refresh_task.done():
            return  # already initialized
        wallet = (getattr(settings, "WALLET_ADDRESS", None) or "").strip()
        if not wallet:
            raise RuntimeError(
                f"BotBankrollManager[{self.bot_name}]: BOT_WALLET_BANKROLL_ENABLED but "
                "WALLET_ADDRESS not set in env. Refusing to start."
            )
        from bots.weather.engine.base_engine.execution.clob_adapter import check_usdc_balance
        balance = await check_usdc_balance(wallet_address=wallet)
        if balance is None:
            # one retry with backoff
            await asyncio.sleep(2.0)
            balance = await check_usdc_balance(wallet_address=wallet)
        if balance is None:
            raise RuntimeError(
                f"BotBankrollManager[{self.bot_name}]: cold-start wallet read failed "
                "after retry. Refusing to start with config-fallback capital."
            )
        if balance <= 0:
            raise RuntimeError(
                f"BotBankrollManager[{self.bot_name}]: wallet balance is $0 "
                f"({wallet[:8]}...). Refusing to start — fund the wallet first."
            )
        _old_capital = self.capital
        self.capital = float(balance)
        self._last_wallet_capital = float(balance)
        self._last_wallet_read_ts = time.monotonic()
        logger.info(
            "bankroll_initialized_from_wallet",
            bot_name=self.bot_name,
            capital=round(balance, 4),
            source="wallet",
            config_capital_overridden=_old_capital,
            wallet=wallet[:8] + "...",
        )
        self._wallet_refresh_task = asyncio.create_task(self._wallet_refresh_loop())

    async def _wallet_refresh_loop(self) -> None:
        """S217: Periodic wallet-balance refresh (10 min default).
        Material changes (>20%) are logged. Read failures hold last-known-good;
        get_bet_size() refuses to size once read is older than stale threshold."""
        from bots.weather.engine.base_engine.execution.clob_adapter import check_usdc_balance
        while True:
            try:
                await asyncio.sleep(self._wallet_refresh_interval_s)
                balance = await check_usdc_balance()
                if balance is None:
                    await asyncio.sleep(5.0)
                    balance = await check_usdc_balance()
                age = time.monotonic() - self._last_wallet_read_ts
                if balance is None:
                    logger.critical(
                        "bankroll_wallet_read_failed",
                        bot_name=self.bot_name,
                        last_known_capital=self._last_wallet_capital,
                        stale_age_s=round(age, 1),
                        stale_threshold_s=self._wallet_stale_threshold_s,
                    )
                    continue
                if balance <= 0:
                    logger.critical(
                        "bankroll_wallet_zero_balance",
                        bot_name=self.bot_name,
                        previous_capital=self._last_wallet_capital,
                    )
                    self._last_wallet_capital = 0.0
                    self._last_wallet_read_ts = time.monotonic()
                    self.capital = 0.0
                    continue
                # material-change watch
                if self._last_wallet_capital and self._last_wallet_capital > 0:
                    pct = abs(balance - self._last_wallet_capital) / self._last_wallet_capital
                    if pct > 0.20:
                        logger.info(
                            "bankroll_wallet_material_change",
                            bot_name=self.bot_name,
                            old_capital=round(self._last_wallet_capital, 4),
                            new_capital=round(balance, 4),
                            pct_change=round(pct * 100, 1),
                        )
                self.capital = float(balance)
                self._last_wallet_capital = float(balance)
                self._last_wallet_read_ts = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as _err:
                logger.warning(
                    "bankroll_wallet_refresh_loop_error",
                    bot_name=self.bot_name,
                    error=str(_err),
                )

    def _wallet_bankroll_fresh(self) -> bool:
        """S217: True when wallet bankroll is fresh enough to size against,
        OR when wallet bankroll is disabled (config-capital path). False
        when wallet read is older than stale threshold."""
        if not self._wallet_bankroll_enabled:
            return True
        if self._last_wallet_read_ts == 0:
            return False  # never initialized
        age = time.monotonic() - self._last_wallet_read_ts
        return age <= self._wallet_stale_threshold_s

    async def get_bet_size(
        self,
        confidence: float,
        price: float,
        calibration_quality: Optional[Dict[str, float]] = None,
        category: str = "",
        conformal_interval: Optional[tuple] = None,
    ) -> Tuple[float, float]:
        """
        Compute Kelly-sized bet in USD with per-bot capital, fraction, and caps.

        Args:
            confidence: Model's P(this side wins), after ensemble consensus + signal enhancements.
            price:      Current market price (0-1).
            calibration_quality: Optional dict with "brier" and "count" for calibration scaling.
            category:   Market category for category-specific Kelly fractions.
            conformal_interval: Optional (p_low, p_high) from MAPIE conformal prediction.
                When provided, Kelly uses p_low instead of point estimate for conservative sizing.

        Returns:
            (final_size_usd, intended_size_usd) tuple.
            final_size_usd: Capped bet size — 0.0 means do not bet.
            intended_size_usd: Pre-cap Kelly size for counterfactual analysis (P0.3).
        """
        # Validate inputs
        if price <= 0 or price >= 1 or confidence <= 0 or confidence >= 1:
            return 0.0, 0.0
        if confidence <= price:
            return 0.0, 0.0  # No positive edge — don't bet

        # S217: wallet-bankroll freshness + zero-balance guards. Both no-op when
        # this bot is on the config-capital path (BOT_WALLET_BANKROLL_ENABLED off).
        if self._wallet_bankroll_enabled:
            if not self._wallet_bankroll_fresh():
                logger.warning(
                    "bankroll_stale_refusing_to_size",
                    bot_name=self.bot_name,
                    stale_age_s=round(time.monotonic() - self._last_wallet_read_ts, 1)
                    if self._last_wallet_read_ts else None,
                    stale_threshold_s=self._wallet_stale_threshold_s,
                )
                return 0.0, 0.0
            if self.capital <= 0:
                logger.warning("bankroll_zero_capital_refusing_to_size", bot_name=self.bot_name)
                return 0.0, 0.0

        # S91: Conformal-aware Kelly via width-based dampening.
        # Old approach: used p_low as kelly_confidence. With binary outcomes and
        # predictions near 0.55, p_low ≈ 0.05 (logit-space residuals ~3.0), which
        # blocked ALL trades at prices above $0.05.
        # New approach: keep point estimate for Kelly edge. Use interval WIDTH to
        # dampen the Kelly fraction. Wide interval = more uncertainty = smaller bet.
        _conformal_dampener = 1.0
        if conformal_interval is not None:
            p_low, p_high = conformal_interval
            if 0 < p_low < 1 and 0 < p_high < 1:
                width = p_high - p_low
                # Width 0.0→1.0x, Width 0.5→0.50x, Width≥0.9→0.25x floor
                _conformal_dampener = max(0.25, 1.0 - width)

        # Kelly criterion: f* = (p*b - q) / b
        #   p = confidence (point estimate), b = (1 - price) / price, q = 1 - p
        b = (1.0 - price) / price
        if b <= 0:
            return 0.0, 0.0
        q = 1.0 - confidence
        kelly_full = (confidence * b - q) / b
        if kelly_full <= 0:
            return 0.0, 0.0

        # Apply fraction (category-specific override if available)
        fraction = self.kelly_fraction
        if category:
            try:
                cat_fracs = json.loads(
                    getattr(settings, "CATEGORY_KELLY_FRACTIONS", "{}")
                )
                if category in cat_fracs:
                    fraction = float(cat_fracs[category])
            except Exception:
                pass

        # Calibration scaling: reduce fraction when model is poorly calibrated
        # Good Brier (< 0.15): full fraction. Mediocre (0.15-0.30): reduce 15-50%.
        if calibration_quality and calibration_quality.get("count", 0) >= 20:
            brier = calibration_quality.get("brier", 0.25)
            if brier > 0.15:
                cal_floor = 0.50
                cal_mult = max(cal_floor, 1.0 - (brier - 0.15) * 3.33)
                fraction *= cal_mult

        # Drawdown compression (read from risk_manager if available)
        try:
            rm = getattr(self._db, "risk_manager", None) if self._db else None
            if rm is None and self._gw is not None:
                # S159: was "_risk_manager" (private) — OrderGateway exposes it as
                # "risk_manager" (public). The underscore prefix caused getattr to
                # always return None, so drawdown compression never fired.
                rm = getattr(self._gw, "risk_manager", None)
            dd_pct = getattr(rm, "_cached_drawdown_pct", 0.05) if rm else 0.05
            if dd_pct > 0.02:
                compress = max(0.30, 1.0 - dd_pct * 4.0)
                fraction *= compress
        except Exception:
            pass

        # S91: Apply conformal width-based dampener to fraction
        fraction *= _conformal_dampener

        # Compute USD size (pre-cap = intended for counterfactual analysis)
        size_usd = kelly_full * fraction * self.capital
        intended_size_usd = size_usd

        # Per-bet cap
        size_usd = min(size_usd, self.max_bet_usd)

        # Day 2 (S173): Phase max bet cap — shared across all bots
        size_usd = min(size_usd, self.phase_max_bet_usd)

        # Daily cap — lock-guarded for concurrent bots
        async with self._daily_lock:
            daily_spent = self._get_daily_spent()
            remaining = max(0.0, self.max_daily_usd - daily_spent)
            size_usd = min(size_usd, remaining)

        # Minimum meaningful bet ($1 for paper trading)
        if size_usd < 1.0:
            return 0.0, round(intended_size_usd, 2)

        result = round(size_usd, 2)
        intended_result = round(intended_size_usd, 2)

        logger.info(
            "BotBankrollManager.get_bet_size",
            bot=self.bot_name,
            confidence=round(confidence, 4),
            price=round(price, 4),
            kelly_full=round(kelly_full, 4),
            fraction=round(fraction, 4),
            capital=self.capital,
            size_usd=result,
            intended_size_usd=intended_result,
            daily_spent=round(daily_spent, 2),
            category=category or "?",
        )

        return result, intended_result

    def _get_daily_spent(self) -> float:
        """Read today's daily exposure for this bot from OrderGateway (day-rollover aware)."""
        if self._gw is None:
            return 0.0
        # P3: Use accessor method which handles midnight UTC day rollover,
        # instead of reading raw dict which may contain stale previous-day data.
        _method = getattr(self._gw, "get_daily_exposure_usd", None)
        if callable(_method):
            return float(_method(self.bot_name))
        # Fallback for mocked/incomplete gateways
        daily_exposure = getattr(self._gw, "_daily_exposure_usd", {})
        return float(daily_exposure.get(self.bot_name, 0.0))

    async def get_daily_exposure(self) -> float:
        """Lock-guarded read of today's daily exposure for this bot."""
        async with self._daily_lock:
            return self._get_daily_spent()

    def get_state(self) -> Dict[str, Any]:
        """Return current bankroll state for diagnostics."""
        return {
            "bot_name": self.bot_name,
            "capital": self.capital,
            "kelly_fraction": self.kelly_fraction,
            "max_bet_usd": self.max_bet_usd,
            "phase_max_bet_usd": self.phase_max_bet_usd,
            "max_daily_usd": self.max_daily_usd,
            "daily_spent": self._get_daily_spent(),
        }
