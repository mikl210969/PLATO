"""Spot Price Provider — агрегатор спотовой mid-price (источник истины)."""
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class SpotPriceProvider:
    def __init__(self, event_bus: Any, symbol: str):
        self.event_bus = event_bus
        self.symbol = symbol
        self._current_spot_price = 0.0
        self._last_update_ts = 0.0
        
        # Подписка на обновления спотового стакана из EventBus
        self.event_bus.subscribe("SPOT_ORDERBOOK_UPDATE", self.on_spot_orderbook_update)
        logger.info(f"✅ SpotPriceProvider initialized and subscribed to SPOT_ORDERBOOK_UPDATE for {symbol}")

    async def on_spot_orderbook_update(self, event: Any):
        """Обработчик событий обновления спотового стакана."""
        try:
            payload = getattr(event, "payload", {})
            bids = payload.get("b", [])
            asks = payload.get("a", [])
            
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                
                # Рассчитываем mid-price
                self._current_spot_price = (best_bid + best_ask) / 2.0
                self._last_update_ts = time.time()
                
                # Публикуем событие обновления спотовой цены для стратегий
                await self.event_bus.publish(
                    event_type="SPOT_PRICE_UPDATE",
                    source="spot_price_provider",
                    payload={
                        "price": self._current_spot_price, 
                        "ts": self._last_update_ts
                    },
                    symbol=self.symbol
                )
        except Exception as e:
            logger.error(f"Error processing spot orderbook for mid-price: {e}")

    def get_current_price(self) -> float:
        """Возвращает текущую спотовую mid-price."""
        return self._current_spot_price

    def is_fresh(self, max_age_sec: float = 5.0) -> bool:
        """Проверяет, что цена обновлялась недавно (защита от зависания WS)."""
        return (time.time() - self._last_update_ts) < max_age_sec