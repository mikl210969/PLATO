"""Delta Analyzer — расчет кумулятивной дельты и дельта-профиля."""
import logging
import time
from collections import deque
from typing import Dict, Any

logger = logging.getLogger(__name__)


class DeltaAnalyzer:
    def __init__(self, window_seconds: int = 1800):
        """
        :param window_seconds: Размер скользящего окна в секундах (по умолчанию 30 минут = 1800 сек).
        """
        self.window_seconds = window_seconds
        self.trades = deque()  # Хранит кортежи: (timestamp, delta, price)
        self.cumulative_delta = 0.0
        self.delta_per_price_level: Dict[float, float] = {}

    def on_trade(self, trade_data: dict):
        """
        Обработка входящей спотовой сделки для расчета дельты.
        Определяет агрессию по полю 'm' (maker side).
        """
        try:
            price = float(trade_data.get("p", 0))
            qty = float(trade_data.get("q", 0))
            # Binance aggTrade: T is in ms, convert to seconds
            timestamp = float(trade_data.get("T", time.time() * 1000)) / 1000.0
            is_buyer_maker = trade_data.get("m", False)
            
            value = price * qty
            
            # Определение агрессии:
            # m=True  -> buyer is maker -> seller was taker -> SELL aggressive (negative delta)
            # m=False -> seller is maker -> buyer was taker -> BUY aggressive (positive delta)
            if is_buyer_maker:
                delta = -value
            else:
                delta = value
            
            # Добавляем в окно
            self.trades.append((timestamp, delta, price))
            self.cumulative_delta += delta
            self.delta_per_price_level[price] = self.delta_per_price_level.get(price, 0.0) + delta
            
            # Очистка старых сделок за пределами скользящего окна
            cutoff = timestamp - self.window_seconds
            while self.trades and self.trades[0][0] < cutoff:
                old_ts, old_delta, old_price = self.trades.popleft()
                self.cumulative_delta -= old_delta
                self.delta_per_price_level[old_price] -= old_delta
                
                # Удаляем уровень из словаря, если дельта стала близка к нулю (оптимизация памяти)
                if abs(self.delta_per_price_level[old_price]) < 1e-6:
                    del self.delta_per_price_level[old_price]
                    
        except Exception as e:
            logger.error(f"Error processing trade for delta: {e}")

    def get_metrics(self) -> dict:
        """Возвращает текущие метрики дельты для использования стратегиями."""
        return {
            "cumulative_delta": self.cumulative_delta,
            "delta_profile": dict(self.delta_per_price_level),
            "trade_count": len(self.trades)
        }