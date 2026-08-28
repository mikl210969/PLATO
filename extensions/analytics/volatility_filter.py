import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

class VolatilityFilter:
    def __init__(self, atr_period: int = 14, avg_atr_period: int = 100):
        self.atr_period = atr_period
        self.avg_atr_period = avg_atr_period

    def calculate_atr(self, candles: List[Dict[str, float]]) -> float:
        """
        Рассчитывает ATR на основе списка свечей.
        Ожидаемый формат свечи: {'high': float, 'low': float, 'close': float, 'open': float}
        """
        if len(candles) < self.atr_period + 1:
            logger.warning(f"Недостаточно свечей для расчета ATR. Есть {len(candles)}, нужно {self.atr_period + 1}")
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            current = candles[i]
            previous = candles[i-1]
            
            high_low = current['high'] - current['low']
            high_close = abs(current['high'] - previous['close'])
            low_close = abs(current['low'] - previous['close'])
            
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)

        # Простое скользящее среднее для ATR (можно заменить на RMA/Wilder's Smoothing при необходимости)
        atr = sum(true_ranges[-self.atr_period:]) / self.atr_period
        return round(atr, 6)

    def determine_volatility_regime(self, current_atr: float, avg_atr: float) -> str:
        """
        Определяет режим волатильности согласно Стратегии.txt v2.0 (Модуль 1.3)
        """
        if avg_atr == 0:
            return "normal"
            
        ratio = current_atr / avg_atr
        
        if ratio > 2.5:
            return "high"
        elif ratio < 0.5:
            return "low"
        else:
            return "normal"