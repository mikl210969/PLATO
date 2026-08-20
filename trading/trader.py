"""
Трейдер — исполнитель команд. Только отправляет ордера и возвращает результат.
Никакой логики принятия решений, работы с паспортом или сохранения.

Hedge Mode:
- position_side передаётся явно (сторона ПОЗИЦИИ).
- reduceOnly в Hedge Mode НЕ шлётся (запрещён, -1106); биржа сама делает
  ордера reduce-only по связке side + positionSide.
"""

import time
from typing import Optional, Dict, Any

from core.types import OrderSide
from adapters.binance_rest import BinanceRestClient
from adapters.binance_ws import BinanceWsAdapter
from core.event_bus import EventBus


class Trader:
    """Исполнитель команд. Только отправляет ордера и возвращает результат."""

    def __init__(
        self,
        symbol: str,
        rest_client: BinanceRestClient,
        ws_adapter: BinanceWsAdapter,
        event_bus: EventBus,
        config: Dict
    ):
        self.symbol = symbol
        self.rest = rest_client
        self.ws = ws_adapter
        self.bus = event_bus
        self.config = config

        self._running = True
        self._opened_at: Optional[float] = None

        # Exit Calculator
        from trading.exit_calculator import ExitCalculator
        self.exit_calculator = ExitCalculator(config.get('trading', {}))

    def _get_order_type(self) -> str:
        """Получить тип ордера из конфига."""
        return self.config.get('trading', {}).get('entry_order_type', 'market')

    def _get_lot_size(self) -> float:
        """Получить размер позиции из конфига."""
        return float(self.config.get('trading', {}).get('lot_size', 7.0))

    async def execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = 'market',
        client_order_id: str = "",
        passport_id: str = "",
        reduce_only: bool = False,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        position_side: Optional[str] = None  # 🔥 явная сторона ПОЗИЦИИ (Hedge Mode)
    ) -> Dict[str, Any]:
        """
        Исполнить ордер.
        Возвращает результат.

        order_type: 'market', 'limit', 'stop_market', 'stop_limit'
        limit_price: цена для лимитного ордера (обязателен для limit, stop_limit)
        stop_price: цена активации (обязателен для stop_market, stop_limit)
        position_side: 'LONG'/'SHORT' — сторона ПОЗИЦИИ. Если None — выводится из side.
        """
        try:
            # Определяем сторону ордера
            order_side = OrderSide.SELL.value if side == 'short' else OrderSide.BUY.value

            # Hedge Mode: явная сторона позиции, либо вывод из стороны ордера (для входа)
            effective_position_side = position_side or ('SHORT' if side == 'short' else 'LONG')

            if order_type == 'market':
                result = await self.rest.create_market_order(
                    symbol=symbol,
                    side=order_side,
                    quantity=quantity,
                    new_client_order_id=client_order_id or f"ORD_{passport_id}",
                    reduce_only=reduce_only,
                    position_side=effective_position_side
                )

            elif order_type == 'limit':
                if limit_price is None or limit_price <= 0:
                    return {
                        'success': False,
                        'order_id': None,
                        'client_order_id': None,
                        'status': 'FAILED',
                        'order_type': 'LIMIT',
                        'quantity': 0,
                        'symbol': symbol,
                        'passport_id': passport_id,
                        'error': 'Limit price is required for limit order'
                    }
                result = await self.rest.create_limit_order(
                    symbol=symbol,
                    side=order_side,
                    price=limit_price,
                    quantity=quantity,
                    new_client_order_id=client_order_id or f"ORD_{passport_id}",
                    reduce_only=reduce_only,
                    position_side=effective_position_side
                )

            elif order_type == 'stop_market':
                if stop_price is None or stop_price <= 0:
                    return {
                        'success': False,
                        'order_id': None,
                        'client_order_id': None,
                        'status': 'FAILED',
                        'order_type': 'STOP_MARKET',
                        'quantity': 0,
                        'symbol': symbol,
                        'passport_id': passport_id,
                        'error': 'Stop price is required for stop_market order'
                    }
                result = await self.rest.create_stop_market_order(
                    symbol=symbol,
                    side=order_side,
                    stop_price=stop_price,
                    quantity=quantity,
                    new_client_order_id=client_order_id or f"ORD_{passport_id}",
                    reduce_only=reduce_only
                )

            elif order_type == 'stop_limit':
                if stop_price is None or stop_price <= 0:
                    return {
                        'success': False,
                        'order_id': None,
                        'client_order_id': None,
                        'status': 'FAILED',
                        'order_type': 'STOP_LIMIT',
                        'quantity': 0,
                        'symbol': symbol,
                        'passport_id': passport_id,
                        'error': 'Stop price is required for stop_limit order'
                    }
                if limit_price is None or limit_price <= 0:
                    return {
                        'success': False,
                        'order_id': None,
                        'client_order_id': None,
                        'status': 'FAILED',
                        'order_type': 'STOP_LIMIT',
                        'quantity': 0,
                        'symbol': symbol,
                        'passport_id': passport_id,
                        'error': 'Limit price is required for stop_limit order'
                    }
                result = await self.rest.create_stop_limit_order(
                    symbol=symbol,
                    side=order_side,
                    stop_price=stop_price,
                    limit_price=limit_price,
                    quantity=quantity,
                    new_client_order_id=client_order_id or f"ORD_{passport_id}",
                    reduce_only=reduce_only
                )

            else:
                return {
                    'success': False,
                    'order_id': None,
                    'client_order_id': None,
                    'status': 'FAILED',
                    'order_type': order_type.upper(),
                    'quantity': 0,
                    'symbol': symbol,
                    'passport_id': passport_id,
                    'error': f'Unknown order type: {order_type}'
                }

            # Проверяем результат
            if result.get('success'):
                return {
                    'success': True,
                    'order_id': result.get('order_id'),
                    'client_order_id': result.get('client_order_id'),
                    'status': result.get('status', 'NEW'),
                    'order_type': order_type.upper(),
                    'quantity': quantity,
                    'symbol': symbol,
                    'passport_id': passport_id,
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'order_id': None,
                    'client_order_id': None,
                    'status': 'FAILED',
                    'order_type': order_type.upper(),
                    'quantity': 0,
                    'symbol': symbol,
                    'passport_id': passport_id,
                    'error': result.get('error', 'Unknown error')
                }

        except Exception as e:
            return {
                'success': False,
                'order_id': None,
                'client_order_id': None,
                'status': 'FAILED',
                'order_type': order_type.upper(),
                'quantity': 0,
                'symbol': symbol,
                'passport_id': passport_id,
                'error': str(e)
            }

    def _get_limit_price(self, side: str, current_price: float = 0.0, offset: float = 0.0) -> float:
        """
        Получить цену для лимитного ордера.
        Если current_price = 0 — используем конфиг или возвращаем 0.
        """
        if current_price > 0:
            return current_price

        config_price = self.config.get('trading', {}).get('limit_price', 0)
        if config_price > 0:
            return config_price

        return 0.0

    async def close_position(
        self,
        symbol: str,
        quantity: float,
        exit_reason: str = "",
        exit_price: float = 0.0,
        position_side: Optional[str] = None  # 🔥 явная сторона закрываемой позиции
    ) -> Dict[str, Any]:
        """
        Закрыть позицию рыночным ордером.
        Hedge Mode: position_side = сторона закрываемой позиции, reduceOnly НЕ шлётся (-1106).
        """
        # Определяем сторону закрываемой позиции
        if position_side is None:
            try:
                position = await self.rest.get_position(symbol)
                size = float(position.get('size', 0) or 0)
                position_side = 'SHORT' if size < 0 else 'LONG'
            except Exception:
                position_side = 'LONG'

        # Закрытие SHORT = BUY, закрытие LONG = SELL
        order_side = OrderSide.BUY.value if position_side == 'SHORT' else OrderSide.SELL.value

        try:
            result = await self.rest.create_market_order(
                symbol=symbol,
                side=order_side,
                quantity=quantity,
                reduce_only=False,  # 🔥 в Hedge Mode reduceOnly запрещён
                new_client_order_id=f"CLOSE_{exit_reason}_{int(time.time() * 1000)}",
                position_side=position_side
            )

            if result.get('success'):
                return {
                    'success': True,
                    'order_id': result.get('order_id'),
                    'client_order_id': result.get('client_order_id'),
                    'status': result.get('status', 'NEW'),
                    'quantity': quantity,
                    'symbol': symbol,
                    'exit_reason': exit_reason,
                    'exit_price': exit_price or result.get('price', 0),
                    'error': None
                }
            else:
                return {
                    'success': False,
                    'order_id': None,
                    'client_order_id': None,
                    'status': 'FAILED',
                    'quantity': 0,
                    'symbol': symbol,
                    'exit_reason': exit_reason,
                    'exit_price': 0,
                    'error': result.get('error', 'Unknown error')
                }

        except Exception as e:
            return {
                'success': False,
                'order_id': None,
                'client_order_id': None,
                'status': 'FAILED',
                'quantity': 0,
                'symbol': symbol,
                'exit_reason': exit_reason,
                'exit_price': 0,
                'error': str(e)
            }

    async def get_position_from_exchange(self, symbol: str) -> Optional[Dict]:
        """Получить позицию с биржи."""
        try:
            position = await self.rest.get_position(symbol)
            return position
        except Exception as e:
            print(f"⚠️ [TRADER] Failed to get position: {e}")
            return None

    async def get_order_status(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Получить статус ордера с биржи."""
        try:
            return await self.rest.get_order_status(
                symbol=symbol,
                order_id=order_id,
                client_order_id=client_order_id
            )
        except Exception as e:
            print(f"⚠️ [TRADER] Failed to get order status: {e}")
            return None
        
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер (устойчиво к ошибке -2011)."""
        try:
            result = await self.rest.cancel_order(symbol, order_id)
            if result.get('success'):
                return True
            
            # Если биржа вернула ошибку "Unknown order" (-2011), считаем это успехом,
            # так как наша цель (ордер не активен) достигнута.
            error_msg = str(result.get('error', '')).lower()
            if 'unknown order' in error_msg or '-2011' in error_msg:
                return True
                
            return False
        except Exception as e:
            print(f"⚠️ [TRADER] Failed to cancel order: {e}")
            return False

    def calculate_exit_levels(self, side: str, entry_price: float, atr_value: float = 0.5) -> Dict:
        """Рассчитать уровни выхода (SL, TP1, TP2) через ExitCalculator."""
        return self.exit_calculator.calculate(
            side=side,
            entry_price=entry_price,
            atr_value=atr_value
        )

    async def stop(self):
        """Остановка трейдера."""
        self._running = False
        print(f"🛑 [TRADER] Stopped: {self.symbol}")