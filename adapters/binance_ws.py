"""
Binance WebSocket адаптер (Production-Ready).
Архитектура Producer-Consumer: гарантирует, что сетевой цикл чтения никогда не блокируется 
медленной обработкой сообщений (EventBus, логи), предотвращая разрывы соединения по таймауту.
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
        self._connected = False
        self._healthy = False
        self._handlers: Dict[str, Callable[[Dict], Awaitable[None]]] = {}
        self._json_logger = None
        self._on_reconnect: Optional[Callable[[], Awaitable[None]]] = None
        
        # 🔥 Ключевой элемент стабильности: очередь сообщений. 
        # maxsize=1000 предотвращает утечку памяти, если обработка вдруг встанет.
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        
        self.logger = get_logger(__name__)

    def set_json_logger(self, json_logger):
        self._json_logger = json_logger

    def on(self, event_type: str, handler: Callable[[Dict], Awaitable[None]]):
        """Подписаться на событие."""
        self._handlers[event_type] = handler

    async def connect(self, retries: int = 3):
        """Подключиться к WebSocket с повторными попытками."""
        for attempt in range(retries):
            try:
                # Безопасно закрываем старый сокет, если он "висит"
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                
                self.logger.info(f"Connecting to {self.base_url} (attempt {attempt+1}/{retries})")
                
                # 🔥 Явные таймауты. Библиотека websockets сама шлет protocol ping каждые 20с.
                self._ws = await websockets.connect(
                    self.base_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                )
                
                self._connected = True
                self._running = True
                self._healthy = True
                self.logger.info("✅ WebSocket connected")
                
                # 🔥 Вызываем колбэк переподписки ПОСЛЕ успешного установления соединения
                if self._on_reconnect:
                    await self._on_reconnect()
                return
                
            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {e}")
                self._connected = False
                await asyncio.sleep(2)

        raise RuntimeError(f"Failed to connect after {retries} attempts")

    async def subscribe_user_data(self, listen_key: str):
        if not self._connected or self._ws is None:
            self.logger.warning("Cannot subscribe: WebSocket not connected")
            return
        msg = {"method": "SUBSCRIBE", "params": [listen_key], "id": id(self)}
        try:
            await self._ws.send(json.dumps(msg))
            self.logger.info(f"Subscribed to user data: {listen_key[:10]}...")
        except Exception as e:
            self.logger.warning(f"Failed to subscribe to user data: {e}")
            self._connected = False

    async def subscribe_depth(self, symbol: str):
        if not self._connected or self._ws is None:
            self.logger.warning("Cannot subscribe: WebSocket not connected")
            return
        stream = f"{symbol.lower()}@depth20@100ms"
        msg = {"method": "SUBSCRIBE", "params": [stream], "id": id(self) + 1}
        try:
            await self._ws.send(json.dumps(msg))
            self.logger.info(f"Subscribed to depth: {symbol}")
        except Exception as e:
            self.logger.warning(f"Failed to subscribe to depth: {e}")
            self._connected = False

    async def run(self):
        """
        Запускает два независимых цикла: 
        1. Чтение из сети (Producer) - всегда быстрый.
        2. Обработка сообщений (Consumer) - может быть медленным, это безопасно.
        """
        self._running = True
        
        # 🔥 Запускаем обработчик в отдельной фоновой задаче
        processor_task = asyncio.create_task(self._process_queue())
        
        try:
            while self._running:
                if not self._connected or self._ws is None:
                    self.logger.warning("Connection lost. Reconnecting...")
                    await self.connect()
                    continue

                try:
                    # 🔥 ЧТЕНИЕ: Это ВСЕГДА быстро (< 1мс). Сетевой стек свободен для Ping/Pong.
                    message = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
                    await self._message_queue.put(message)
                    self._healthy = True
                    
                except asyncio.TimeoutError:
                    self.logger.warning("WS recv timeout (30s). Forcing reconnect...")
                    self._connected = False
                    self._healthy = False
                    
                except websockets.ConnectionClosed as e:
                    self.logger.warning(f"Connection closed by server (code: {e.code}). Reconnecting...")
                    self._connected = False
                    self._healthy = False
                    
                except Exception as e:
                    self.logger.error(f"Critical error in WS run loop: {e}")
                    self._connected = False
                    self._healthy = False
                    
        finally:
            self._running = False
            processor_task.cancel() # Останавливаем обработчик при выходе

    async def _process_queue(self):
        """Обрабатывает сообщения из очереди. Может быть медленным, это безопасно."""
        while self._running:
            try:
                message = await self._message_queue.get()
                data = json.loads(message)
                await self._handle_message(data)
                self._message_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error processing message from queue: {e}")

    async def _handle_message(self, data: Dict):
        """Логика обработки (теперь она гарантированно не блокирует сеть)."""
        event_type = data.get('e', 'UNKNOWN')

        # 🔥 1. ФИЛЬТР ШУМА
        if event_type == 'UNKNOWN' or ('id' in data and 'result' in data):
            return

        # 🔥 2. УСЕЧЕНИЕ PAYLOAD для логирования
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

        # 🔥 3. Логируем в JSON
        if self._json_logger and event_type in ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'listenKeyExpired', 'depthUpdate']:
            self._json_logger.log(module="ws", event=event_type, data=log_data, level="DEBUG")

        # 🔥 4. Вывод в терминал
        important_events = ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE']
        if event_type in important_events:
            extra_info = ""
            if event_type == 'ORDER_TRADE_UPDATE' and 'o' in data:
                extra_info = f" | {data['o'].get('c')} | {data['o'].get('X')}"
            print(f"📥 [WS_EVENT] {event_type}{extra_info}")

        # ===== МАРШРУТИЗАЦИЯ =====
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
                'update_time': data.get('E', 0),
                'dedup_key': f"OTU:{order_data.get('i')}:{order_data.get('X')}:{order_data.get('z')}:{data.get('E', 0)}",
            }
            handler = self._handlers.get('ORDER_TRADE_UPDATE')
            if handler:
                await handler(payload)

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

        elif event_type == 'depthUpdate':
            handler = self._handlers.get('depthUpdate')
            if handler:
                await handler(data)

    def is_healthy(self) -> bool:
        """Вернуть статус здоровья WS."""
        return self._healthy and self._connected

    # ========================================================================
    # НОВЫЙ МЕТОД: Подписка на спотовые сделки (Spot AggTrades)
    # ========================================================================
    async def subscribe_spot_agg_trade(self, symbol: str, callback):
        """
        Подписка на поток спотовых сделок (aggTrade) для Whale/Spoofing детекторов.
        Использует отдельное публичное WS-подключение к спотовому рынку Binance.
        """
        import logging
        import json
        import asyncio
        import websockets
        
        # Локальный логгер, чтобы не зависеть от импортов в начале файла
        logger = logging.getLogger(__name__)
        spot_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@aggTrade"
        
        logger.info(f"🔄 Connecting to SPOT aggTrade stream: {spot_url}")
        
        # Цикл с автоматическим переподключением
        while getattr(self, '_running', True):
            try:
                async with websockets.connect(spot_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info(f"✅ Spot WS connected for {symbol}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            # Нормализуем формат под наш DetectorBridge
                            normalized_data = {
                                "e": "aggTrade",
                                "s": data.get("s"),
                                "p": data.get("p"),
                                "q": data.get("q"),
                                "m": data.get("m"),  # m=True значит продавец был мейкером (агрессор - BUY)
                                "T": data.get("T")
                            }
                            # Вызываем переданный callback (аналогично тому, как работает self.on в main.py)
                            if callback:
                                await callback("MARKET_TRADE", normalized_data)
                        except Exception as e:
                            logger.error(f"Error processing spot trade: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Spot WS connection lost or failed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def subscribe_spot_depth(self, symbol: str, callback):
        """
        Подписка на поток спотового стакана (depth@100ms) для расчета имбаланса и поиска стен.
        Использует отдельное публичное WS-подключение к спотовому рынку Binance.
        """
        import logging
        import json
        import asyncio
        import websockets
        
        logger = logging.getLogger(__name__)
        spot_depth_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@depth@100ms"
        
        logger.info(f"🔄 Connecting to SPOT depth stream (100ms): {spot_depth_url}")
        
        while getattr(self, '_running', True):
            try:
                async with websockets.connect(spot_depth_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info(f"✅ Spot Depth WS connected for {symbol}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            
                            # Нормализуем формат под наш EventBus (аналогично фьючерсам)
                            # Binance отдает 'b' (bids) и 'a' (asks) как массивы [price, qty]
                            normalized_data = {
                                "e": "depthUpdate",
                                "s": symbol.upper(),
                                "b": data.get("b", []),  # bids
                                "a": data.get("a", []),  # asks
                                "E": data.get("E", int(asyncio.get_event_loop().time() * 1000)) # timestamp
                            }
                            
                            # Вызываем переданный callback
                            if callback:
                                await callback("SPOT_ORDERBOOK_UPDATE", normalized_data)
                                
                        except Exception as e:
                            logger.error(f"Error processing spot depth update: {e}")
                            
            except Exception as e:
                logger.warning(f"⚠️ Spot Depth WS connection lost or failed: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def close(self):
        """Корректное закрытие соединения."""
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._connected = False
        self.logger.info("🛑 WebSocket closed gracefully")