"""
Стратегия Absorption — вход при поглощении объёма.
"""

from typing import Dict, Any, Optional
from core.types import Signal
from strategies.base import BaseStrategy


class AbsorptionStrategy(BaseStrategy):
    """Стратегия Absorption."""

    def __init__(self, config: Dict):
        super().__init__("Absorption", config)
        self.min_wall_volume = config.get('min_wall_volume', 5.0)
        self.min_confidence = config.get('min_confidence', 0.3)

    def generate_signal(self, context: Dict[str, Any]) -> Optional[Signal]:
        """
        Генерирует сигнал на основе поглощения объёма.
        """
        if not self.enabled:
            return None

        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {})

        if not orderbook or current_price <= 0:
            return None

        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        # Проверяем поглощение на bid (для long)
        bid_volume = sum(float(vol) for _, vol in bids[:3])
        ask_volume = sum(float(vol) for _, vol in asks[:3])

        if bid_volume > ask_volume * 1.5:
            # Поглощение объёма на покупку
            signal = Signal(
                signal_id=f"Absorption_{current_price}",
                symbol=symbol,
                side='long',
                entry_price=current_price,
                confidence=0.6,
                strategy=self.name,
                metadata={'bid_volume': bid_volume, 'ask_volume': ask_volume}
            )
            return signal

        return None