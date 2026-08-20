"""
Стратегия WallFade — вход от уровня с большим объёмом.
"""

import time  # <-- ДОБАВИТЬ ЭТОТ ИМПОРТ
from typing import Dict, Any, Optional
from core.types import Signal
from strategies.base import BaseStrategy


class WallFadeStrategy(BaseStrategy):
    """Стратегия WallFade."""

    def __init__(self, config: Dict):
        super().__init__("WallFade", config)
        self.min_wall_volume = config.get('min_wall_volume', 20.0)
        self.min_confidence = config.get('min_confidence', 0.3)
        self.price_distance_pct = config.get('price_distance_pct', 0.5)

    def generate_signal(self, context: Dict[str, Any]) -> Optional[Signal]:
        if not self.enabled:
            return None

        orderbook = context.get('orderbook', {})
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)

        if not orderbook or current_price <= 0:
            return None

        bids = orderbook.get('bids', [])
        asks = orderbook.get('asks', [])

        wall_volume = 0.0
        wall_price = 0.0

        for price, volume in asks:
            try:
                vol = float(volume)
                if vol > self.min_wall_volume:
                    wall_volume = vol
                    wall_price = float(price)
                    break
            except (ValueError, TypeError):
                continue

        if wall_volume == 0:
            return None

        distance = abs(wall_price - current_price) / current_price * 100
        if distance > self.price_distance_pct:
            return None

        # 🔥 ИСПРАВЛЕНИЕ: Добавляем уникальный суффикс (timestamp)
        # Теперь каждый сигнал будет иметь уникальный ID, даже если цена и объем те же
        unique_suffix = int(time.time() * 1000) 
        
        signal = Signal(
            signal_id=f"WallFade_{wall_price}_{int(wall_volume)}_{unique_suffix}",
            symbol=symbol,
            side='short',
            entry_price=wall_price,
            confidence=0.7,
            strategy=self.name,
            metadata={'wall_volume': wall_volume, 'wall_price': wall_price}
        )

        return signal