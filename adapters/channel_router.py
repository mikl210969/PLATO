"""
Channel Router — переключение между WS и REST.
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

    async def send_order(self, symbol: str, side: str, quantity: float, new_client_order_id: Optional[str] = None) -> Dict[str, Any]:
        """Отправить ордер через WS (основной) или REST (резерв)."""
        if self.ws_healthy and self.ws.is_healthy():
            print(f"🔵 [ROUTER] Sending order via WS")
            return await self.ws.send_order(symbol, side, quantity, new_client_order_id)
        else:
            print(f"🟠 [ROUTER] Sending order via REST (fallback)")
            return await self.rest.create_market_order(
                symbol, side, quantity,
                new_client_order_id=new_client_order_id
            )

    async def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Отменить ордер через WS или REST."""
        if self.ws_healthy and self.ws.is_healthy():
            print(f"🔵 [ROUTER] Cancelling order via WS")
            return await self.ws.cancel_order(symbol, order_id)
        else:
            print(f"🟠 [ROUTER] Cancelling order via REST (fallback)")
            return await self.rest.cancel_order(symbol, order_id)

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        """Получить позицию через WS или REST."""
        if self.ws_healthy and self.ws.is_healthy():
            return await self.ws.get_position(symbol)
        else:
            return await self.rest.get_position(symbol)