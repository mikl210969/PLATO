"""
Тестовый стенд для отладки платформы.
Имитирует ответы биржи.

Эмуляция Hedge Mode:
- позиция хранится знаково (<0 = short, >0 = long);
- MARKET-ордер с position_side влияет только на свою сторону:
  BUY+SHORT уменьшает шорт (не может перевернуться в лонг);
- после каждого market-исполнения генерируются ORDER_TRADE_UPDATE и ACCOUNT_UPDATE.
"""

import asyncio
import time
from typing import Dict, Optional, Any, List
from dataclasses import dataclass

from core.event_bus import EventBus, Event


@dataclass
class MockOrder:
    """Имитация ордера на бирже."""
    order_id: int
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: float
    status: str
    executed_qty: float = 0.0
    avg_price: float = 0.0
    reduce_only: bool = False
    position_side: str = 'BOTH'


class MockBinanceRestClient:
    """Имитация REST клиента Binance (Hedge Mode)."""

    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self._orders: Dict[str, MockOrder] = {}
        self._position_size: float = 0.0   # знаково: <0 short, >0 long
        self._entry_price: float = 0.0
        self._order_counter = 1000000
        self._events: List[Dict] = []

    def _generate_order_id(self) -> int:
        self._order_counter += 1
        return self._order_counter

    async def get_listen_key(self) -> str:
        return f"mock_listen_key_{int(time.time())}"

    async def renew_listen_key(self, listen_key: str) -> bool:
        return True

    async def get_position(self, symbol: str) -> Dict:
        size = self._position_size
        side = 'short' if size < 0 else ('long' if size > 0 else 'none')
        return {
            'symbol': symbol,
            'side': side,
            'size': size,
            'entry_price': self._entry_price if size != 0 else 0.0,
            'unrealized_pnl': 0.0
        }

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        return {
            'bids': [[76.00, 100], [75.90, 200]],
            'asks': [[76.10, 100], [76.20, 200]]
        }

    def _apply_market_fill(self, side: str, quantity: float, position_side: str):
        """Эмуляция исполнения market-ордера в Hedge Mode."""
        signed = quantity if side == 'BUY' else -quantity
        if position_side == 'SHORT':
            new = min(self._position_size + signed, 0.0)   # BUY+SHORT не может стать лонгом
        elif position_side == 'LONG':
            new = max(self._position_size + signed, 0.0)
        else:
            new = self._position_size + signed
        if self._position_size == 0 and new != 0:
            self._entry_price = 76.00
        self._position_size = new

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None,
        position_side: str = 'BOTH'
    ) -> Dict:
        order_id = self._generate_order_id()
        client_order_id = new_client_order_id or f"mock_ord_{order_id}"

        order = MockOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type='MARKET',
            price=0.0,
            quantity=quantity,
            status='FILLED',
            executed_qty=quantity,
            avg_price=76.00,
            reduce_only=reduce_only,
            position_side=position_side
        )
        self._orders[client_order_id] = order

        self._apply_market_fill(side, quantity, position_side)

        self._events.append({
            'type': 'ORDER_TRADE_UPDATE',
            'data': {
                'client_order_id': client_order_id,
                'status': 'FILLED',
                'symbol': symbol,
                'price': 76.00,
                'executed_qty': quantity,
                'order_type': 'MARKET',
                'side': side
            }
        })
        self._events.append({
            'type': 'ACCOUNT_UPDATE',
            'data': {
                'symbol': symbol,
                'size': self._position_size,
                'entry_price': self._entry_price,
                'unrealized_pnl': 0.0
            }
        })

        return {
            'success': True,
            'order_id': order_id,
            'client_order_id': client_order_id,
            'status': 'FILLED',
            'raw_response': {'orderId': order_id}
        }

    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None,
        position_side: str = 'BOTH'
    ) -> Dict:
        order_id = self._generate_order_id()
        client_order_id = new_client_order_id or f"mock_lim_{order_id}"

        order = MockOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type='LIMIT',
            price=price,
            quantity=quantity,
            status='NEW',
            reduce_only=reduce_only,
            position_side=position_side
        )
        self._orders[client_order_id] = order

        self._events.append({
            'type': 'ORDER_TRADE_UPDATE',
            'data': {
                'client_order_id': client_order_id,
                'status': 'NEW',
                'symbol': symbol,
                'price': price,
                'executed_qty': 0,
                'order_type': 'LIMIT',
                'side': side
            }
        })

        return {
            'success': True,
            'order_id': order_id,
            'client_order_id': client_order_id,
            'status': 'NEW',
            'raw_response': {'orderId': order_id}
        }

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        for client_id, order in self._orders.items():
            if str(order.order_id) == str(order_id) or client_id == order_id:
                if order.status in ('NEW', 'PARTIALLY_FILLED'):
                    order.status = 'CANCELED'
                    self._events.append({
                        'type': 'ORDER_TRADE_UPDATE',
                        'data': {
                            'client_order_id': order.client_order_id,
                            'status': 'CANCELED',
                            'symbol': symbol,
                            'price': order.price,
                            'executed_qty': order.executed_qty,
                            'order_type': order.order_type,
                            'side': order.side
                        }
                    })
                    return {'success': True}
        return {'success': False, 'error': 'Order not found'}

    def close_position_manually(self, symbol: str):
        """Имитировать ручное закрытие позиции."""
        self._position_size = 0.0
        self._events.append({
            'type': 'ACCOUNT_UPDATE',
            'data': {'symbol': symbol, 'size': 0, 'status': 'CLOSED'}
        })

    def get_pending_events(self) -> List[Dict]:
        events = self._events.copy()
        self._events.clear()
        return events

    async def close(self):
        pass


class MockBinanceWsAdapter:
    """Имитация WebSocket адаптера."""

    def __init__(self, base_url: str = ""):
        self.base_url = base_url
        self._listeners: Dict[str, List] = {}
        self._running = False

    async def connect(self):
        self._running = True
        print("✅ [MOCK WS] WebSocket connected")

    async def run(self):
        while self._running:
            await asyncio.sleep(1)

    async def subscribe_depth(self, symbol: str):
        print(f"✅ [MOCK WS] Subscribed to depth: {symbol}")

    async def subscribe_user_data(self, listen_key: str):
        print(f"✅ [MOCK WS] Subscribed to user data: {listen_key[:10]}...")

    def on(self, event_type: str, handler):
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(handler)

    async def emit(self, event_type: str, data: Dict):
        if event_type in self._listeners:
            for handler in self._listeners[event_type]:
                await handler(data)

    def set_json_logger(self, logger):
        pass

    async def health_check_loop(self):
        pass

    async def stop(self):
        self._running = False