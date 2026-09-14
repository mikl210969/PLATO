"""
Channel Router — единая точка входа для торговых операций.

🔥 ИСПРАВЛЕНО (2026-09-14):
Binance Futures НЕ поддерживает отправку ордеров, отмену и запрос позиций
через WebSocket. WS используется только для рыночных данных (depth/aggTrade)
и user data stream (ORDER_TRADE_UPDATE / ACCOUNT_UPDATE).
Поэтому все торговые операции идут через REST, а роутер отвечает за:
  1. Единый интерфейс вызова (symbol/side/qty)
  2. Диагностику здоровья каналов (ws_healthy) для логов и Health Monitor
При миграции на Bybit (у него есть WS order API) сюда вернётся WS-ветка.
"""

from typing import Optional, Dict, Any

from adapters.binance_ws import BinanceWsAdapter
from adapters.binance_rest import BinanceRestClient


class ChannelRouter:
    def __init__(self, ws: BinanceWsAdapter, rest: BinanceRestClient):
        self.ws = ws
        self.rest = rest
        self.ws_healthy = True

    def set_ws_healthy(self, status: bool):
        self.ws_healthy = status

    async def send_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        new_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Отправить ордер. На Binance Futures единственный путь — REST."""
        print(f"🟠 [ROUTER] send_order via REST (ws_healthy={self.ws_healthy}, ws.is_healthy={self.ws.is_healthy()})")
        return await self.rest.create_market_order(
            symbol,
            side,
            quantity,
            new_client_order_id=new_client_order_id,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Отменить ордер. На Binance Futures единственный путь — REST."""
        return await self.rest.cancel_order(symbol, order_id)

    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получить позицию. WS не предоставляет query-позиций, только REST.
        Возвращает None при бане/ошибке — вызывающий код обязан это обработать."""
        return await self.rest.get_position(symbol)