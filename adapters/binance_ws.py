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
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                
                self.logger.info(f"Connecting to {self.base_url} (attempt {attempt+1}/{retries})")
                
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

    async def subscribe_btc_streams(self):
        """Подписка на агрегированные сделки и стакан BTCUSDT для контекстного анализа."""
        if not self._connected or self._ws is None:
            self.logger.warning("Cannot subscribe to BTC: WebSocket not connected")
            return
        
        streams = ["btcusdt@aggTrade", "btcusdt@depth@100ms"]
        msg = {"method": "SUBSCRIBE", "params": streams, "id": id(self) + 99}
        
        try:
            await self._ws.send(json.dumps(msg))
            self.logger.info("✅ Subscribed to BTCUSDT streams (aggTrade, depth@100ms)")
        except Exception as e:
            self.logger.warning(f"Failed to subscribe to BTC streams: {e}")
            self._connected = False

    async def run(self):
        self._running = True
        processor_task = asyncio.create_task(self._process_queue())
        
        try:
            while self._running:
                if not self._connected or self._ws is None:
                    self.logger.warning("Connection lost. Reconnecting...")
                    await self.connect()
                    continue

                try:
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
            processor_task.cancel()

    async def _process_queue(self):
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
        """Логика обработки с маршрутизацией по символам."""
        event_type = data.get('e', 'UNKNOWN')
        symbol = data.get('s', '')

        # 🔥 1. ФИЛЬТР ШУМА
        if event_type == 'UNKNOWN' or ('id' in data and 'result' in data):
            return

        # 🔥 2. МАРШРУТИЗАЦИЯ ПО СИМВОЛАМ И СОБЫТИЯМ
        if event_type == 'aggTrade':
            if symbol == 'BTCUSDT':
                await self._route_event('BTC_AGG_TRADE', data)
                
        elif event_type == 'depthUpdate':
            if symbol == 'BTCUSDT':
                await self._route_event('BTC_DEPTH_UPDATE', data)
            else:
                await self._route_event('depthUpdate', data)

        elif event_type == 'ORDER_TRADE_UPDATE':
            await self._route_event('ORDER_TRADE_UPDATE', data)

        elif event_type == 'ACCOUNT_UPDATE':
            await self._route_event('ACCOUNT_UPDATE', data)

        elif event_type == 'listenKeyExpired':
            await self._route_event('listenKeyExpired', data)

    async def _route_event(self, event_type: str, data: Dict):
        """Вспомогательный метод для логирования и вызова хендлера."""
        log_data = data
        if event_type == 'ORDER_TRADE_UPDATE' and 'o' in data:
            o = data['o']
            log_data = {
                "symbol": o.get('s'), "client_order_id": o.get('c'), "status": o.get('X')
            }

        if self._json_logger and event_type in ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE', 'listenKeyExpired', 'depthUpdate', 'BTC_DEPTH_UPDATE', 'BTC_AGG_TRADE']:
            self._json_logger.log(module="ws", event=event_type, data=log_data, level="DEBUG")

        if event_type in ['ORDER_TRADE_UPDATE', 'ACCOUNT_UPDATE']:
            extra = f" | {data['o'].get('c')} | {data['o'].get('X')}" if 'o' in data else ""
            print(f"📥 [WS_EVENT] {event_type}{extra}")

        handler = self._handlers.get(event_type)
        if handler:
            try:
                await handler(data)
            except Exception as e:
                self.logger.error(f"Error in handler for {event_type}: {e}")

    def is_healthy(self) -> bool:
        return self._healthy and self._connected

    async def subscribe_spot_agg_trade(self, symbol: str, callback):
        import logging
        logger = logging.getLogger(__name__)
        spot_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@aggTrade"
        logger.info(f"🔄 Connecting to SPOT aggTrade stream: {spot_url}")
        
        while getattr(self, '_running', True):
            try:
                async with websockets.connect(spot_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info(f"✅ Spot WS connected for {symbol}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            normalized_data = {
                                "e": "aggTrade", "s": data.get("s"), "p": data.get("p"),
                                "q": data.get("q"), "m": data.get("m"), "T": data.get("T")
                            }
                            if callback:
                                await callback("MARKET_TRADE", normalized_data)
                        except Exception as e:
                            logger.error(f"Error processing spot trade: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Spot WS connection lost. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def subscribe_spot_depth(self, symbol: str, callback):
        import logging
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
                            normalized_data = {
                                "e": "depthUpdate", "s": symbol.upper(),
                                "b": data.get("b", []), "a": data.get("a", []),
                                "E": data.get("E", int(asyncio.get_event_loop().time() * 1000))
                            }
                            if callback:
                                await callback("SPOT_ORDERBOOK_UPDATE", normalized_data)
                        except Exception as e:
                            logger.error(f"Error processing spot depth update: {e}")
            except Exception as e:
                logger.warning(f"⚠️ Spot Depth WS connection lost. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def close(self):
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._connected = False
        self.logger.info("🛑 WebSocket closed gracefully")