"""Imbalance Calculator — расчет дисбаланса пассивной ликвидности в стакане."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ImbalanceCalculator:
    def __init__(self, event_bus: Any, symbol: str, depth_levels: int = 10):
        """
        :param event_bus: Шина событий для подписки на обновления стакана.
        :param symbol: Торговая пара (например, 'SOLUSDT').
        :param depth_levels: Количество уровней стакана для расчета (по умолчанию 10).
        """
        self.event_bus = event_bus
        self.symbol = symbol
        self.depth_levels = depth_levels
        
        self.current_imbalance = 0.0
        self.bid_volume = 0.0
        self.ask_volume = 0.0
        
        # Подписка на обновления спотового стакана
        self.event_bus.subscribe("SPOT_ORDERBOOK_UPDATE", self.on_orderbook_update)
        logger.info(f"✅ ImbalanceCalculator initialized for {symbol} (depth={depth_levels})")

    async def on_orderbook_update(self, event: Any):
        """Обработчик событий обновления спотового стакана."""
        try:
            payload = getattr(event, "payload", {})
            
            # Берем только указанные верхние уровни
            bids = payload.get("b", [])[:self.depth_levels]
            asks = payload.get("a", [])[:self.depth_levels]
            
            if not bids or not asks:
                return

            # Суммируем объемы (price, qty) -> берем qty (индекс 1)
            self.bid_volume = sum(float(qty) for price, qty in bids)
            self.ask_volume = sum(float(qty) for price, qty in asks)
            
            total_volume = self.bid_volume + self.ask_volume
            
            if total_volume > 0:
                # Формула имбаланса: от -1.0 (полный перекос в ask) до +1.0 (полный перекос в bid)
                self.current_imbalance = (self.bid_volume - self.ask_volume) / total_volume
            else:
                self.current_imbalance = 0.0
                
        except Exception as e:
            logger.error(f"Error calculating imbalance: {e}")

    def get_metrics(self) -> dict:
        """Возвращает текущие метрики имбаланса для использования стратегиями."""
        return {
            "imbalance": self.current_imbalance,
            "bid_volume": self.bid_volume,
            "ask_volume": self.ask_volume
        }