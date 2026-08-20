"""
Binance WebSocket адаптер.
"""

import asyncio
import json
import websockets
from typing import Dict, Any, Optional, Callable, Awaitable
from core.logger import get_logger


class BinanceWsAdapter:
    """WebSocket клиент для Binance."""

    def __init__(self, base_url: str = "wss://stream.binancefuture.com/ws"):
        self.base_url = base_url
        self._ws = None
        self._running = False
        self._handlers: Dict[str, Callable] = {}
        self.logger = get_logger(__name__)
        self._connected = False
        self._json_logger = None
        self._on_reconnect: Optional[Callable[[], Awaitable[None]]] = None

    def set_json_logger(self, json_logger):
        self._json_logger = json_logger

    def on(self, event_type: str, handler: Callable[[Dict], Awaitable[None]]):
        """Подписаться на событие."""
        self._handlers[event_type] = handler

    async def connect(self, retries: int = 3):
        """Подключиться к WebSocket с повторными попытками."""
        for attempt in range(retries):
            try:
                self.logger.info(f"Connecting to {self.base_url} (attempt {attempt+1}/{retries})")
                self._ws = await websockets.connect(
                    self.base_url,
                    ping_interval=20,
                    ping_timeout=10
                )
                self._connected = True
                self._running = True
                self.logger.info("✅ WebSocket connected")
                
                # 🔥 Вызываем колбэк после переподключения
                if self._on_reconnect:
                    await self._on_reconnect()
                return
            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {e}")
                await asyncio.sleep(2)

        raise RuntimeError(f"Failed to connect after {retries} attempts")

    async def subscribe_user_data(self, listen_key: str):
        """Подписаться на user data stream."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [listen_key],
            "id": 1
        }
        await self._ws.send(json.dumps(subscribe_msg))
        self.logger.info(f"Subscribed to user data: {listen_key}")

    async def subscribe_depth(self, symbol: str):
        """Подписаться на стакан."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        stream = f"{symbol.lower()}@depth20@100ms"
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [stream],
            "id": 2
        }
        await self._ws.send(json.dumps(subscribe_msg))
        self.logger.info(f"Subscribed to depth: {symbol}")

    async def run(self):
        """Запустить обработку сообщений."""
        if not self._ws:
            raise RuntimeError("WebSocket not connected")

        self._running = True

        try:
            while self._running:
                try:
                    message = await self._ws.recv()
                    data = json.loads(message)
                    await self._handle_message(data)
                except websockets.ConnectionClosed:
                    self.logger.warning("Connection closed, reconnecting...")
                    await self.connect()
                except Exception as e:
                    self.logger.error(f"Error in run loop: {e}")
                    await asyncio.sleep(1)
        finally:
            self._running = False

    async def _handle_message(self, data: Dict):
        """Обработать входящее сообщение с фильтрацией шума и усечением payload."""
        event_type = data.get('e', 'UNKNOWN')

        # 🔥 1. ФИЛЬТР ШУМА: Игнорируем технические ответы (ping/pong, подтверждение подписки)
        if event_type == 'UNKNOWN' or ('id' in data and 'result' in data):
            return

        # 🔥 2. УСЕЧЕНИЕ PAYLOAD для логирования (чтобы не писать гигантские словари в файл)
        log_data = data
        if event_type == 'ORDER_TRADE_UPDATE' and 'o' in data:
            o = data['o']
            log_data = {
                "symbol": o.get('s'),
                "client_order_id": o.get('c'),
                "side": o.get('S'),
                "type": o.get('o'),
                "status": o.get('X'),
                "quantity": o.get('q'),
                "price": o.get('ap') or o.get('p'),
                "position_side": o.get('ps')
            }

        # 🔥 3. Логируем в JSON только важные события (уже в усеченном виде для ордеров)
        if self._json_logger and event_type in ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'listenKeyExpired', 'depthUpdate']:
            self._json_logger.log(
                module="ws",
                event=event_type,
                data=log_data,
                level="DEBUG"
            )

        # 🔥 4. В терминал выводим ТОЛЬКО важные события с краткой сводкой
        important_events = ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE']
        if event_type in important_events:
            extra_info = ""
            if event_type == 'ORDER_TRADE_UPDATE' and 'o' in data:
                extra_info = f" | {data['o'].get('c')} | {data['o'].get('X')}"
            print(f"📥 [WS_EVENT] {event_type}{extra_info}")

        # ===== ОБРАБОТКА И МАРШРУТИЗАЦИЯ =====
        
        # ORDER_TRADE_UPDATE
        if event_type == 'ORDER_TRADE_UPDATE':
            order_data = data.get('o', {})
            payload = {
                'order_id': order_data.get('i'),
                'client_order_id': order_data.get('c'),
                'symbol': order_data.get('s'),
                'side': order_data.get('S'),
                'status': order_data.get('X'),
                'price': float(order_data.get('p', 0) or order_data.get('ap', 0)),
                'quantity': float(order_data.get('q', 0)),
                'executed_qty': float(order_data.get('z', 0)),
                'update_time': data.get('E', 0)
            }
            handler = self._handlers.get('ORDER_TRADE_UPDATE')
            if handler:
                await handler(payload)

        # ACCOUNT_UPDATE
        elif event_type == 'ACCOUNT_UPDATE':
            account_data = data.get('a', {})
            positions = account_data.get('P', [])
            for pos in positions:
                payload = {
                    'symbol': pos.get('s'),
                    'size': float(pos.get('pa', 0)),
                    'entry_price': float(pos.get('ep', 0)),
                    'unrealized_pnl': float(pos.get('up', 0))
                }
                handler = self._handlers.get('ACCOUNT_UPDATE')
                if handler:
                    await handler(payload)

        # depthUpdate
        elif event_type == 'depthUpdate':
            handler = self._handlers.get('depthUpdate')
            if handler:
                await handler(data)

        # Все остальные события (например, TRADE_LITE) попадают только в JSON-лог (если разрешены выше)

    async def ping(self) -> bool:
        """Проверить, жив ли WebSocket."""
        try:
            if self._ws:
                await self._ws.send(json.dumps({"method": "ping"}))
                return True
            return False
        except Exception:
            return False

    async def health_check_loop(self, interval: int = 5):
        """Фоновый цикл проверки здоровья WS (без ping)."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._ws:
                    self._healthy = True
                else:
                    self._healthy = False
                    print(f"⚠️ [WS] Health check FAILED (connection closed)")
            except Exception:
                self._healthy = False
                print(f"⚠️ [WS] Health check FAILED")
    def is_healthy(self) -> bool:
        """Вернуть статус здоровья WS."""
        return self._healthy

    async def send_order(self, symbol: str, side: str, quantity: float, new_client_order_id: Optional[str] = None) -> Dict:
        """Отправить ордер через WebSocket."""
        # 🔥 TODO: Реализация отправки ордера через WS
        # Пока возвращаем ошибку (будет реализовано позже)
        return {'success': False, 'error': 'WS send_order not implemented yet'}    

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Отменить ордер через WebSocket."""
        # 🔥 TODO: Реализация отмены ордера через WS
        # Пока возвращаем ошибку
        return {'success': False, 'error': 'WS cancel_order not implemented yet'}

    async def get_position(self, symbol: str) -> Dict:
        """Получить позицию через WebSocket."""
        # 🔥 TODO: Реализация получения позиции через WS
        # Пока возвращаем ошибку
        return {'success': False, 'error': 'WS get_position not implemented yet'}

    async def stop(self):
        """Остановить WebSocket."""
        self._running = False
        self._connected = False
        if self._ws:
            await self._ws.close()