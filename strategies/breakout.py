"""
Стратегия Breakout (Пробой) — вход при пробое уровня.
"""

from typing import Dict, Any, Optional
from core.types import Signal
from strategies.base import BaseStrategy


class BreakoutStrategy(BaseStrategy):
    """Стратегия Breakout — пробой уровня."""

    def __init__(self, config: Dict):
        super().__init__("Breakout", config)
        self.min_volume = config.get('min_volume', 10.0)
        self.min_confidence = config.get('min_confidence', 0.3)
        self.lookback_bars = config.get('lookback_bars', 20)
        self.breakout_threshold = config.get('breakout_threshold', 0.5)  # процент от цены

    def generate_signal(self, context: Dict[str, Any]) -> Optional[Signal]:
        """
        Генерирует сигнал при пробое уровня.
        """
        if not self.enabled:
            return None

        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        candles = context.get('candles', [])  # список свечей

        if not candles or current_price <= 0 or len(candles) < self.lookback_bars:
            return None

        # Находим локальные максимумы и минимумы
        highs = [float(c.get('high', 0)) for c in candles[-self.lookback_bars:]]
        lows = [float(c.get('low', 0)) for c in candles[-self.lookback_bars:]]
        closes = [float(c.get('close', 0)) for c in candles[-self.lookback_bars:]]

        if not highs or not lows or not closes:
            return None

        resistance = max(highs)
        support = min(lows)

        # Проверяем пробой сопротивления (лонг)
        if current_price > resistance:
            # Проверяем объём (если есть)
            volume = sum(float(c.get('volume', 0)) for c in candles[-5:])
            avg_volume = sum(float(c.get('volume', 0)) for c in candles[-self.lookback_bars:]) / self.lookback_bars

            if volume > avg_volume * 1.5:
                signal = Signal(
                    signal_id=f"Breakout_{current_price}",
                    symbol=symbol,
                    side='long',
                    entry_price=current_price,
                    confidence=0.7,
                    strategy=self.name,
                    metadata={
                        'resistance': resistance,
                        'support': support,
                        'volume_ratio': volume / avg_volume if avg_volume > 0 else 1.0
                    }
                )
                return signal

        # Проверяем пробой поддержки (шорт)
        if current_price < support:
            volume = sum(float(c.get('volume', 0)) for c in candles[-5:])
            avg_volume = sum(float(c.get('volume', 0)) for c in candles[-self.lookback_bars:]) / self.lookback_bars

            if volume > avg_volume * 1.5:
                signal = Signal(
                    signal_id=f"Breakout_{current_price}",
                    symbol=symbol,
                    side='short',
                    entry_price=current_price,
                    confidence=0.7,
                    strategy=self.name,
                    metadata={
                        'resistance': resistance,
                        'support': support,
                        'volume_ratio': volume / avg_volume if avg_volume > 0 else 1.0
                    }
                )
                return signal

        return None