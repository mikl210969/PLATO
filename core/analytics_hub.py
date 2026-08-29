"""Analytics Hub — единая точка инициализации всех аналитических модулей Order Flow."""
import logging
from typing import Any

logger = logging.getLogger(__name__)

class AnalyticsHub:
    def __init__(self, event_bus: Any, symbol: str, rest_client: Any):
        self.symbol = symbol
        self.event_bus = event_bus
        
        # 1. Spot Price Provider (Источник истины)
        from extensions.analytics.spot_price_provider import SpotPriceProvider
        self.spot_price = SpotPriceProvider(event_bus, symbol)
        
        # 2. Volatility Filter (Реальный ATR)
        from extensions.analytics.volatility_filter import VolatilityFilter
        self.volatility = VolatilityFilter(rest_client=rest_client)
        
        # 3. Delta Analyzer (Агрессивный поток)
        from extensions.analytics.delta_analyzer import DeltaAnalyzer
        self.delta = DeltaAnalyzer(window_seconds=1800)
        event_bus.subscribe("MARKET_TRADE", self._on_market_trade_for_delta)
        
        # 4. Imbalance Calculator (Пассивная ликвидность)
        from extensions.analytics.imbalance_calculator import ImbalanceCalculator
        self.imbalance = ImbalanceCalculator(event_bus, symbol, depth_levels=10)
        
        # 5. Trend Context (Направление рынка для Continuation/Reversal)
        from extensions.analytics.trend_context import TrendContext
        self.trend = TrendContext(event_bus, symbol, lookback_minutes=15, threshold_pct=0.5)
        
        logger.info(f"✅ AnalyticsHub initialized for {symbol} (Spot, Volatility, Delta, Imbalance, Trend)")

    def get_all_metrics(self) -> dict:
        """Возвращает сводку всех метрик для стратегий и логирования."""
        return {
            "spot_price": self.spot_price.get_current_price(),
            "atr": self.volatility._cached_atr.get(self.symbol, 0.5),
            "delta": self.delta.get_metrics(),
            "imbalance": self.imbalance.get_metrics(),
            "trend": self.trend.get_context() # 🔥 НОВОЕ
        }

    async def _on_market_trade_for_delta(self, event: Any):
        """Проксирование событий спотовых сделок в DeltaAnalyzer."""
        try:
            payload = getattr(event, "payload", {})
            self.delta.on_trade(payload)
        except Exception as e:
            logger.error(f"Error in AnalyticsHub delta processing: {e}")

