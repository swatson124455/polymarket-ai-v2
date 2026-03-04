"""
Unit tests for Tier 2 #18, #19, #20:
  #18 — VPIN toxicity detection (EnsembleBot._get_vpin_toxicity)
  #19 — Wallet clustering signal (EnsembleBot._get_wallet_cluster_mult / _refresh_wallet_clusters)
  #20 — Order flow fingerprinting (EnsembleBot._get_order_flow_signal)

Covers:
  #18: cache hit skips TFA, B3 triggered/not triggered, toxic flow, no TFA fallback,
       cache expires → fresh call, _vpin_cache in __init__
  #19: neutral when no WC, boost for diverse clusters, penalty for concentrated clusters,
       refresh only called after TTL, _wallet_cluster_last_refresh in __init__
  #20: cache hit skips OFA, bullish flow × YES = boost, bearish flow × NO = boost,
       opposing flow = penalty, neutral = 1.0, no OFA fallback, _order_flow_cache in __init__
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_bot():
    """Minimal EnsembleBot without real DB/engine."""
    from bots.ensemble_bot import EnsembleBot
    engine = MagicMock()
    engine.db = None
    engine.client = None
    engine.trade_flow_analyzer = None
    engine.wallet_clustering = None
    engine.order_flow_analyzer = None
    engine.resolution_risk_analyzer = None
    engine.performance_tracker = None
    engine.streaming_persister = None
    engine.signal_ingestion = None
    engine.market_regime_detector = None
    with patch.object(EnsembleBot, "start", new=AsyncMock()):
        bot = EnsembleBot.__new__(EnsembleBot)
        EnsembleBot.__init__(bot, engine)
    return bot


def _make_tfa(vpin: float = 0.2, trade_count: int = 30,
              large_trades: list = None, avg_trade_size: float = 100.0) -> MagicMock:
    """Build a minimal TradeFlowAnalyzer mock."""
    tfa = MagicMock()
    tfa.get_vpin = AsyncMock(return_value={
        "vpin": vpin,
        "toxic": vpin > 0.7,
        "trade_count": trade_count,
    })
    tfa.analyze_recent_trades = AsyncMock(return_value={
        "trade_count": trade_count,
        "large_trades": large_trades if large_trades is not None else [],
        "avg_trade_size": avg_trade_size,
        "total_volume": avg_trade_size * trade_count,
        "buy_sell_ratio": 1.0,
    })
    return tfa


def _make_ofa(flow_signal: str = "neutral", flow_confidence: float = 0.3) -> MagicMock:
    ofa = MagicMock()
    ofa.analyze_order_flow = AsyncMock(return_value={
        "signals": {
            "flow_signal": flow_signal,
            "flow_confidence": flow_confidence,
        },
        "market_id": "mkt123",
    })
    return ofa


# ═══════════════════════════════════════════════════════════════════════════════
# Tier 2 #18: VPIN Toxicity
# ═══════════════════════════════════════════════════════════════════════════════

class TestVPINToxicity:

    @pytest.mark.asyncio
    async def test_no_tfa_returns_safe_fallback(self):
        """No trade_flow_analyzer wired → returns zeros, no crash."""
        bot = _make_bot()
        bot.base_engine.trade_flow_analyzer = None
        result = await bot._get_vpin_toxicity("tok1")
        assert result["vpin"] == 0.0
        assert result["toxic"] is False
        assert result["large_trade_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_low_vpin_not_toxic(self):
        """VPIN < 0.5 → not toxic."""
        bot = _make_bot()
        bot.base_engine.trade_flow_analyzer = _make_tfa(vpin=0.2, trade_count=30)
        result = await bot._get_vpin_toxicity("tok2")
        assert result["vpin"] == pytest.approx(0.2)
        assert result["toxic"] is False

    @pytest.mark.asyncio
    async def test_high_vpin_is_toxic(self):
        """VPIN > 0.7 → toxic=True."""
        bot = _make_bot()
        bot.base_engine.trade_flow_analyzer = _make_tfa(vpin=0.85, trade_count=30)
        result = await bot._get_vpin_toxicity("tok3")
        assert result["vpin"] == pytest.approx(0.85)
        assert result["toxic"] is True

    @pytest.mark.asyncio
    async def test_b3_informed_flow_triggers(self):
        """B3: large_trade_pct > 0.10 AND vpin < 0.5 → b3_informed_flow=True."""
        bot = _make_bot()
        # 30 trades, 15 returned as "large" (pct = 15/30 = 0.50 > 0.10)
        large = [{"size": 200.0, "side": "BUY", "price": 0.5}] * 15
        bot.base_engine.trade_flow_analyzer = _make_tfa(
            vpin=0.2, trade_count=30, large_trades=large, avg_trade_size=100.0
        )
        result = await bot._get_vpin_toxicity("tok4")
        assert result.get("b3_informed_flow") is True
        assert result["large_trade_pct"] > 0.10

    @pytest.mark.asyncio
    async def test_b3_not_triggered_when_vpin_high(self):
        """B3: large_trade_pct > 0.10 but VPIN >= 0.5 → b3_informed_flow NOT set."""
        bot = _make_bot()
        large = [{"size": 200.0, "side": "BUY", "price": 0.5}] * 10
        bot.base_engine.trade_flow_analyzer = _make_tfa(
            vpin=0.75, trade_count=30, large_trades=large, avg_trade_size=100.0
        )
        result = await bot._get_vpin_toxicity("tok5")
        assert result.get("b3_informed_flow") is not True

    @pytest.mark.asyncio
    async def test_b3_not_triggered_small_large_trade_pct(self):
        """B3: only 1 large trade out of 30 → pct ~0.033 → no b3_informed_flow."""
        bot = _make_bot()
        large = [{"size": 200.0, "side": "BUY", "price": 0.5}]  # only 1
        bot.base_engine.trade_flow_analyzer = _make_tfa(
            vpin=0.2, trade_count=30, large_trades=large, avg_trade_size=100.0
        )
        result = await bot._get_vpin_toxicity("tok6")
        assert result.get("b3_informed_flow") is not True
        assert result["large_trade_pct"] == pytest.approx(1 / 30, rel=0.01)

    @pytest.mark.asyncio
    async def test_cache_hit_skips_tfa_calls(self):
        """Second call within TTL → get_vpin NOT called again."""
        bot = _make_bot()
        tfa = _make_tfa(vpin=0.3, trade_count=25)
        bot.base_engine.trade_flow_analyzer = tfa

        r1 = await bot._get_vpin_toxicity("tok7")
        r2 = await bot._get_vpin_toxicity("tok7")

        # get_vpin called only once (second is cache hit)
        assert tfa.get_vpin.call_count == 1
        assert tfa.analyze_recent_trades.call_count == 1
        assert r1["vpin"] == r2["vpin"]

    @pytest.mark.asyncio
    async def test_cache_expires_triggers_fresh_call(self):
        """Pre-populate expired cache → next call makes fresh TFA queries."""
        bot = _make_bot()
        tfa = _make_tfa(vpin=0.4, trade_count=20)
        bot.base_engine.trade_flow_analyzer = tfa

        # Pre-populate with expired entry (200s ago → past 60s TTL)
        bot._vpin_cache["tok8"] = (
            {"vpin": 0.0, "toxic": False, "trade_count": 0, "large_trade_pct": 0.0},
            time.monotonic() - 200.0,
        )
        await bot._get_vpin_toxicity("tok8")
        assert tfa.get_vpin.call_count == 1  # Fresh call made

    def test_vpin_cache_in_init(self):
        """EnsembleBot.__init__ initializes _vpin_cache as empty dict."""
        bot = _make_bot()
        assert hasattr(bot, "_vpin_cache")
        assert isinstance(bot._vpin_cache, dict)
        assert len(bot._vpin_cache) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tier 2 #19: Wallet Clustering
# ═══════════════════════════════════════════════════════════════════════════════

class TestWalletClustering:

    def _make_wc(self, clusters: dict = None, wallet_to_cluster: dict = None) -> MagicMock:
        """Build a minimal WalletClustering mock with pre-populated data."""
        wc = MagicMock()
        wc.clusters = clusters or {}
        wc.wallet_to_cluster = wallet_to_cluster or {}
        wc.identify_clusters = AsyncMock(return_value=[])
        return wc

    @pytest.mark.asyncio
    async def test_no_wc_returns_neutral(self):
        """No wallet_clustering wired → multiplier is 1.0 (neutral)."""
        bot = _make_bot()
        bot.base_engine.wallet_clustering = None
        mult = await bot._get_wallet_cluster_mult()
        assert mult == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_no_clusters_returns_neutral(self):
        """WC wired but no clusters identified yet → 1.0."""
        bot = _make_bot()
        bot.base_engine.wallet_clustering = self._make_wc(clusters={}, wallet_to_cluster={})
        mult = await bot._get_wallet_cluster_mult()
        assert mult == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_many_small_clusters_returns_boost(self):
        """≥10 clusters with avg_size < 5 → 1.02 boost (diverse market)."""
        bot = _make_bot()
        # 12 clusters, 2 wallets each → avg_size=2 → boost
        clusters = {f"c{i}": MagicMock() for i in range(12)}
        wallet_to_cluster = {f"w{i}": f"c{i // 2}" for i in range(24)}
        bot.base_engine.wallet_clustering = self._make_wc(clusters=clusters, wallet_to_cluster=wallet_to_cluster)
        # Set TTL so refresh is not triggered
        bot._wallet_cluster_last_refresh = time.monotonic()
        mult = await bot._get_wallet_cluster_mult()
        assert mult == pytest.approx(1.02)

    @pytest.mark.asyncio
    async def test_concentrated_clusters_returns_penalty(self):
        """Avg cluster size ≥ 5 → 0.95 penalty (coordinated activity risk)."""
        bot = _make_bot()
        # 2 clusters, 10 wallets each → avg_size=10 → penalty
        clusters = {"c1": MagicMock(), "c2": MagicMock()}
        wallet_to_cluster = {f"w{i}": ("c1" if i < 10 else "c2") for i in range(20)}
        bot.base_engine.wallet_clustering = self._make_wc(clusters=clusters, wallet_to_cluster=wallet_to_cluster)
        bot._wallet_cluster_last_refresh = time.monotonic()
        mult = await bot._get_wallet_cluster_mult()
        assert mult == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_refresh_not_called_within_ttl(self):
        """identify_clusters NOT called when last refresh is within 30-min window."""
        bot = _make_bot()
        wc = self._make_wc()
        bot.base_engine.wallet_clustering = wc
        # Mark refresh as just done
        bot._wallet_cluster_last_refresh = time.monotonic()
        await bot._refresh_wallet_clusters()
        wc.identify_clusters.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_called_after_ttl(self):
        """identify_clusters IS called when last refresh is past 30-min TTL."""
        bot = _make_bot()
        wc = self._make_wc()
        bot.base_engine.wallet_clustering = wc
        # Mark refresh as 2000s ago (past 1800s TTL)
        bot._wallet_cluster_last_refresh = time.monotonic() - 2000.0
        await bot._refresh_wallet_clusters()
        wc.identify_clusters.assert_called_once()

    def test_wallet_cluster_attrs_in_init(self):
        """EnsembleBot.__init__ initializes wallet clustering tracking attrs."""
        bot = _make_bot()
        assert hasattr(bot, "_wallet_cluster_last_refresh")
        assert bot._wallet_cluster_last_refresh == 0.0
        assert hasattr(bot, "_WALLET_CLUSTER_REFRESH_INTERVAL")
        assert bot._WALLET_CLUSTER_REFRESH_INTERVAL == pytest.approx(1800.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Tier 2 #20: Order Flow Fingerprinting
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrderFlowFingerprinting:

    @pytest.mark.asyncio
    async def test_no_ofa_returns_neutral(self):
        """No order_flow_analyzer wired → 1.0."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = None
        mult = await bot._get_order_flow_signal("mkt1", "YES")
        assert mult == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_bullish_flow_yes_side_boost(self):
        """Bullish flow + side=YES → 1.05 boost."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = _make_ofa(flow_signal="bullish")
        mult = await bot._get_order_flow_signal("mkt2", "YES")
        assert mult == pytest.approx(1.05)

    @pytest.mark.asyncio
    async def test_bullish_flow_no_side_penalty(self):
        """Bullish flow + side=NO → 0.95 (opposing direction)."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = _make_ofa(flow_signal="bullish")
        mult = await bot._get_order_flow_signal("mkt3", "NO")
        assert mult == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_bearish_flow_no_side_boost(self):
        """Bearish flow + side=NO → 1.05 boost."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = _make_ofa(flow_signal="bearish")
        mult = await bot._get_order_flow_signal("mkt4", "NO")
        assert mult == pytest.approx(1.05)

    @pytest.mark.asyncio
    async def test_bearish_flow_yes_side_penalty(self):
        """Bearish flow + side=YES → 0.95 penalty."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = _make_ofa(flow_signal="bearish")
        mult = await bot._get_order_flow_signal("mkt5", "YES")
        assert mult == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_neutral_flow_returns_1(self):
        """Neutral flow → 1.0."""
        bot = _make_bot()
        bot.base_engine.order_flow_analyzer = _make_ofa(flow_signal="neutral")
        mult = await bot._get_order_flow_signal("mkt6", "YES")
        assert mult == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_cache_hit_skips_ofa(self):
        """Second call within 120s TTL → analyze_order_flow NOT called again."""
        bot = _make_bot()
        ofa = _make_ofa(flow_signal="bullish")
        bot.base_engine.order_flow_analyzer = ofa

        r1 = await bot._get_order_flow_signal("mkt7", "YES")
        r2 = await bot._get_order_flow_signal("mkt7", "YES")

        assert ofa.analyze_order_flow.call_count == 1
        assert r1 == r2 == pytest.approx(1.05)

    @pytest.mark.asyncio
    async def test_cache_expires_triggers_fresh_call(self):
        """Expired cache entry → fresh OFA call made."""
        bot = _make_bot()
        ofa = _make_ofa(flow_signal="neutral")
        bot.base_engine.order_flow_analyzer = ofa

        # Pre-populate expired cache entry
        bot._order_flow_cache["mkt8"] = (
            {"signals": {"flow_signal": "neutral"}},
            time.monotonic() - 300.0,  # 300s ago → past 120s TTL
        )
        await bot._get_order_flow_signal("mkt8", "YES")
        assert ofa.analyze_order_flow.call_count == 1

    def test_order_flow_cache_in_init(self):
        """EnsembleBot.__init__ initializes _order_flow_cache as empty dict."""
        bot = _make_bot()
        assert hasattr(bot, "_order_flow_cache")
        assert isinstance(bot._order_flow_cache, dict)
        assert len(bot._order_flow_cache) == 0

    def test_order_flow_mult_from_signals_static(self):
        """Static helper correctly maps signal + side combinations."""
        from bots.ensemble_bot import EnsembleBot
        assert EnsembleBot._order_flow_mult_from_signals({"flow_signal": "bullish"}, "YES") == pytest.approx(1.05)
        assert EnsembleBot._order_flow_mult_from_signals({"flow_signal": "bullish"}, "NO") == pytest.approx(0.95)
        assert EnsembleBot._order_flow_mult_from_signals({"flow_signal": "bearish"}, "YES") == pytest.approx(0.95)
        assert EnsembleBot._order_flow_mult_from_signals({"flow_signal": "bearish"}, "NO") == pytest.approx(1.05)
        assert EnsembleBot._order_flow_mult_from_signals({"flow_signal": "neutral"}, "YES") == pytest.approx(1.0)
        assert EnsembleBot._order_flow_mult_from_signals({}, "YES") == pytest.approx(1.0)
