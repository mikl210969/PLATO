"""Trend Context — определение направления и силы тренда в реальном времени."""
import logging
import time
from collections import deque
from typing import Any, Literal

logger = logging.getLogger(__name__)

TrendState = Literal["UPTREND", "DOWNTREND", "RANGING"]

class TrendContext:
    def __init__(self, event_bus: Any, symbol: str, lookback_minutes: int = 15, threshold_pct: float = 0.5):
        """
        :param lookback_minutes: Окно для оценки тренда (по умолчанию 15 мин).
        :param threshold_pct: Минимальное изменение цены в % для признания тренда (по умолчанию 0.5%).
        """
        self.event_bus = event_bus
        self.symbol = symbol
        self.lookback_seconds = lookback_minutes * 60
        self.threshold_pct = threshold_pct
        
        self._price_history = deque()  # Хранит кортежи: (timestamp, price)
        self._current_trend: TrendState = "RANGING"
        self._trend_change_pct = 0.0
        
        # Подписка на обновления спотовой цены
        self.event_bus.subscribe("SPOT_PRICE_UPDATE", self._on_price_update)
        logger.info(f"✅ TrendContext initialized for {symbol} (window={lookback_minutes}m, threshold={threshold_pct}%)")

    async def _on_price_update(self, event: Any):
        """Обновляет историю цен и пересчитывает тренд."""
        try:
            payload = getattr(event, "payload", {})
            price = float(payload.get("price", 0.0))
            ts = float(payload.get("ts", time.time()))
            
            if price <= 0:
                return

            self._price_history.append((ts, price))
            
            # Очистка старых данных
            cutoff = ts - self.lookback_seconds
            while self._price_history and self._price_history[0][0] < cutoff:
                self._price_history.popleft()
            
            # Пересчет тренда, если есть достаточно данных (минимум 2 точки)
            if len(self._price_history) >= 2:
                oldest_price = self._price_history[0][1]
                if oldest_price > 0:
                    self._trend_change_pct = ((price - oldest_price) / oldest_price) * 100
                    
                    if self._trend_change_pct >= self.threshold_pct:
                        self._current_trend = "UPTREND"
                    elif self._trend_change_pct <= -self.threshold_pct:
                        self._current_trend = "DOWNTREND"
                    else:
                        self._current_trend = "RANGING"
                        
        except Exception as e:
            logger.error(f"Error in TrendContext price update: {e}")

    def get_context(self) -> dict:
        """Возвращает текущий контекст тренда для передачи в стратегии."""
        return {
            "state": self._current_trend,
            "change_pct": self._trend_change_pct,
            "is_continuation_for_short": self._current_trend == "DOWNTREND",
            "is_continuation_for_long": self._current_trend == "UPTREND",
            "is_reversal_for_short": self._current_trend == "UPTREND",
            "is_reversal_for_long": self._current_trend == "DOWNTREND"
        }