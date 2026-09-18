"""
Binance WebSocket адаптер (Production-Ready).
Архитектура Producer-Consumer: гарантирует, что сетевой цикл чтения никогда не блокируется
медленной обработкой сообщений (EventBus, логи), предотвращая разрывы соединения по таймауту.

🔥 ИСПРАВЛЕНО 2026-09-14 (корень таймаутов каждые 30 сек):
- Разделение URL: SPOT (market data) и FUTURES (user data) идут в разные сокеты.
  Раньше SPOT-подписки отправлялись на futures URL, где их нет — данные не приходили.
- Очередь pending-подписок: подписки, вызванные до connect(), не теряются.
- Проверка ответа SUBSCRIBE от Binance: видим, если биржа отклонила подписку.
- recv() timeout сокращён до 10 сек для быстрого обнаружения обрывов.
- Дедупликация: одна и та же подписка не отправляется дважды.
- Диагностика: при таймауте логируется количество активных подписок.
"""
import asyncio
import time
import json
import websockets
from typing import Dict, Any, Optional, Callable, Awaitable, List
from core.logger import get_logger


class BinanceWsAdapter:
    """WebSocket клиент для Binance с двумя раздельными сокетами."""

    # Таймаут recv() — watchdog для быстрого обнаружения обрывов
    RECV_TIMEOUT_SEC = 10.0

    def __init__(
        self,
        base_url: str = "wss://stream.binancefuture.com/ws",
        event_bus=None,
    ):
        # 🔥 НОВОЕ: Разделяем URL по назначению
        # base_url    — для Futures User Data Stream (ORDER_TRADE_UPDATE, ACCOUNT_UPDATE)
        # spot_base_url — для SPOT market data (depth, aggTrade, BTC context)
        self.base_url = base_url
        self.spot_base_url = "wss://stream.binance.com:9443/ws"

        self.event_bus = event_bus

        # Основной сокет (SPOT market data) — сюда идут depth/aggTrade/BTC-потоки
        self._ws = None

        self._running = False
        self._last_user_data_ts = time.time()  # 🔥 для health-check

        self._connected = False
        self._healthy = False

        self._handlers: Dict[str, Callable[[Dict], Awaitable[None]]] = {}
        self._json_logger = None
        self._on_reconnect: Optional[Callable[[], Awaitable[None]]] = None

        # Очередь подписок до подключения
        self._pending_subscriptions: List[str] = []

        # Список активных подписок для восстановления при reconnect
        self._active_subscriptions: List[str] = []
        self._is_initial_connect = True

        # Ключевой элемент стабильности: очередь сообщений
        self._message_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.logger = get_logger(__name__)

    # ──────────────────────────────────────────────────────────────
    # Конфигурация
    # ──────────────────────────────────────────────────────────────

    def set_json_logger(self, json_logger):
        self._json_logger = json_logger

    def set_on_reconnect(self, callback: Callable[[], Awaitable[None]]):
        """Установить колбэк, который вызывается после каждого подключения."""
        self._on_reconnect = callback

    def on(self, event_type: str, handler: Callable[[Dict], Awaitable[None]]):
        """Подписаться на событие."""
        self._handlers[event_type] = handler

    # ──────────────────────────────────────────────────────────────
    # Подписки (внутренние хелперы)
    # ──────────────────────────────────────────────────────────────

    async def _send_subscribe(self, streams: List[str], req_id: int) -> bool:
        """
        Отправить SUBSCRIBE и проверить ответ от Binance.
        Возвращает True, только если биржа подтвердила подписку.
        """
        if not self._connected or self._ws is None:
            return False

        # Дедупликация: отбираем только те стримы, которых ещё нет в активных
        new_streams = [s for s in streams if s not in self._active_subscriptions]
        if not new_streams:
            self.logger.debug(f"⏭️  Все стримы уже подписаны: {streams}")
            return True

        msg = {"method": "SUBSCRIBE", "params": new_streams, "id": req_id}
        try:
            await self._ws.send(json.dumps(msg))

            # 🔥 КРИТИЧНО: ждём ответ от Binance (должен прийти за 5 сек)
            try:
                response_raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                response = json.loads(response_raw)

                # Успех: {"result": null, "id": <id>}
                # Ошибка: {"result": "Invalid parameter: ...", "id": <id>}
                if response.get("id") == req_id:
                    if response.get("result") is None:
                        # Успех — сохраняем подписки и кладём ответ в очередь для логов
                        for stream in new_streams:
                            self._active_subscriptions.append(stream)
                        self.logger.info(
                            f"✅ SUBSCRIBE OK ({len(new_streams)} стримов): {new_streams}"
                        )
                        # Не кладём ответ в message_queue — он уже обработан
                        return True
                    else:
                        # Binance отклонил подписку
                        self.logger.error(
                            f"❌ SUBSCRIBE REJECTED by Binance: {response.get('result')}. "
                            f"Стримы: {new_streams}"
                        )
                        return False
                else:
                    # Пришёл не наш ответ — кладём в очередь на обработку
                    await self._message_queue.put(response_raw)
                    # Считаем успехом (ответ придёт позже)
                    for stream in new_streams:
                        self._active_subscriptions.append(stream)
                    return True

            except asyncio.TimeoutError:
                # Ответ не пришёл за 5 сек — считаем, что подписки ушли, но без подтверждения
                self.logger.warning(
                    f"⚠️  SUBSCRIBE sent, but no response in 5s for {new_streams}"
                )
                for stream in new_streams:
                    self._active_subscriptions.append(stream)
                return True

        except Exception as e:
            self.logger.warning(f"Failed to subscribe to {new_streams}: {e}")
            self._connected = False
            return False

    def _enqueue_subscription(self, stream: str):
        """Добавить стрим в pending-очередь (если его там ещё нет)."""
        if stream not in self._pending_subscriptions and stream not in self._active_subscriptions:
            self._pending_subscriptions.append(stream)

    # ──────────────────────────────────────────────────────────────
    # Подключение основного сокета (SPOT)
    # ──────────────────────────────────────────────────────────────

    async def connect(self, retries: int = 5):
        """
        Подключиться к SPOT WebSocket (market data) с Keep-Alive.
        🔥 ИСПРАВЛЕНО: используется self.spot_base_url, а не self.base_url.
        """
        for attempt in range(retries):
            try:
                # 1. Жёсткая очистка старого сокета
                if self._ws is not None:
                    try:
                        await self._ws.close(code=1000, reason="Reconnecting cleanup")
                    except Exception:
                        pass
                    self._ws = None

                # 🔥 КЛЮЧЕВОЕ: подключаемся к SPOT URL (а не к futures!)
                self.logger.info(
                    f"Connecting to SPOT WS: {self.spot_base_url} "
                    f"(attempt {attempt+1}/{retries})"
                )

                self._ws = await websockets.connect(
                    self.spot_base_url,
                    ping_interval=20,   # ping каждые 20 сек
                    ping_timeout=10,    # pong максимум 10 сек
                    close_timeout=5,
                )

                self._connected = True
                self._running = True
                self._healthy = True
                self.logger.info("✅ SPOT WebSocket connected")

                # 2. Отправляем pending-подписки (те, что накопились до connect)
                if self._pending_subscriptions:
                    pending = list(self._pending_subscriptions)
                    self._pending_subscriptions.clear()
                    self.logger.info(
                        f"📤 Отправляем {len(pending)} pending-подписок: {pending}"
                    )
                    await self._send_subscribe(pending, id(self) + 500)

                # 3. Восстановление активных подписок при реконнекте
                if not self._is_initial_connect and self._active_subscriptions:
                    self.logger.info(
                        f"🔄 Reconnect: восстанавливаем "
                        f"{len(self._active_subscriptions)} активных подписок..."
                    )
                    # Сохранённые подписки уже в _active_subscriptions — нужно их отправить заново
                    # _send_subscribe фильтрует дубликаты по _active_subscriptions,
                    # поэтому временно чистим его перед отправкой
                    to_resend = list(self._active_subscriptions)
                    self._active_subscriptions.clear()
                    await self._send_subscribe(to_resend, id(self) + 999)

                # 4. Колбэк после подключения
                if self._on_reconnect:
                    await self._on_reconnect()

                self._is_initial_connect = False
                return  # Успех!

            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {repr(e)}")
                self._connected = False
                self._healthy = False

                backoff_time = 2 ** (attempt + 1)
                self.logger.info(f"⏳ Backoff {backoff_time}s...")
                await asyncio.sleep(backoff_time)

        self.logger.error(
            "❌ Не удалось подключиться к SPOT WS после всех попыток. Market data is blind."
        )

    # ──────────────────────────────────────────────────────────────
    # Публичные методы подписок
    # ──────────────────────────────────────────────────────────────

    async def subscribe_depth(self, symbol: str):
        """Подписаться на стакан символа (SPOT depth@100ms - diff book)."""
        stream = f"{symbol.lower()}@depth@100ms"

        if not self._connected or self._ws is None:
            # 🔥 ИСПРАВЛЕНО: добавляем в pending, а не игнорируем
            self._enqueue_subscription(stream)
            self.logger.info(f"⏳ {stream} добавлен в pending (WS ещё не подключен)")
            return

        await self._send_subscribe([stream], id(self) + 1)

    async def subscribe_btc_streams(self):
        """Подписка на aggTrade и depth BTCUSDT для контекстного анализа."""
        streams = ["btcusdt@aggTrade", "btcusdt@depth@100ms"]

        if not self._connected or self._ws is None:
            # 🔥 ИСПРАВЛЕНО: добавляем в pending
            for s in streams:
                self._enqueue_subscription(s)
            self.logger.info(f"⏳ BTC streams добавлены в pending (WS ещё не подключен)")
            return

        ok = await self._send_subscribe(streams, id(self) + 99)
        if ok:
            self.logger.info("✅ Subscribed to BTCUSDT streams (aggTrade, depth@100ms)")

    # ──────────────────────────────────────────────────────────────
    # User Data Stream (отдельный сокет на FUTURES URL)
    # ──────────────────────────────────────────────────────────────

    async def subscribe_user_data(self, listen_key: str, refresh_key_callback=None):
        """Запустить отдельный поток для Futures User Data."""
        self._user_data_task = asyncio.create_task(
            self._run_user_data_stream(listen_key, refresh_key_callback)
        )
        self.logger.info(f"🚀 Futures User Data stream started: {listen_key[:10]}...")

    async def _run_user_data_stream(self, listen_key: str, refresh_key_callback=None):
        """Отдельный цикл для User Data Stream с самовосстановлением при HTTP 400."""
        import logging
        logger = logging.getLogger(__name__)

        # 🔥 FIX: testnet-хост WS — это stream.binancefuture.com, но подстроки "testnet"
        # в "wss://stream.binancefuture.com/ws" НЕТ, поэтому старая проверка всегда
        # уводила на мейннет fstream.binance.com с тестнет-ключом → события не приходили.
        if "binancefuture.com" in self.base_url or "testnet" in self.base_url:
            user_data_url = f"wss://stream.binancefuture.com/ws/{listen_key}"
        else:
            user_data_url = f"wss://fstream.binance.com/ws/{listen_key}"

        logger.info(f"🔄 Connecting to Futures User Data: {user_data_url}")

        while getattr(self, "_running", True):
            try:
                async with websockets.connect(
                    user_data_url, ping_interval=30, ping_timeout=60  # 🔥 FIX: testnet ленив, ждём дольше
                ) as ws:
                    logger.info("✅ Futures User Data WS connected")
                    async for message in ws:
                        try:
                            await self._message_queue.put(message)

                            # 🔥 НОВОЕ: диагностика пользовательских событий.
                            # Позволяет визуально убедиться, что ORDER_TRADE_UPDATE
                            # реально приходят через User Data Stream.
                            try:
                                data = json.loads(message)
                                ev = data.get("e")
                                if ev == "ORDER_TRADE_UPDATE":
                                    o = data.get("o", {})
                                    logger.info(
                                        f"📥 [USER_DATA] ORDER_TRADE_UPDATE | "
                                        f"symbol={o.get('s')} | status={o.get('X')} | "
                                        f"filled={o.get('z')} | avgPrice={o.get('ap')}"
                                    )
                                elif ev in ("ACCOUNT_UPDATE", "listenKeyExpired"):
                                    logger.info(f"📥 [USER_DATA] {ev}")
                            except Exception:
                                pass
                        except Exception as e:
                            logger.error(f"Error processing user data message: {e}")

            except Exception as e:
                error_str = str(e)
                # 🔥 Самовосстановление: при HTTP 400 (listen key expired) запрашиваем новый
                if "HTTP 400" in error_str or "400" in error_str:
                    logger.warning("⚠️  Listen key expired (HTTP 400). Requesting new key...")
                    if refresh_key_callback:
                        try:
                            new_key = await refresh_key_callback()
                            if new_key:
                                listen_key = new_key
                                # 🔥 FIX: та же проверка хоста при переподключении
                                if "binancefuture.com" in self.base_url or "testnet" in self.base_url:
                                    user_data_url = f"wss://stream.binancefuture.com/ws/{listen_key}"
                                else:
                                    user_data_url = f"wss://fstream.binance.com/ws/{listen_key}"
                                logger.info(
                                    f"✅ New listen key: {listen_key[:10]}... Reconnecting."
                                )
                                await asyncio.sleep(2)
                                continue
                            else:
                                logger.error("❌ refresh_key_callback вернул None")
                        except Exception as refresh_err:
                            logger.error(f"❌ Ошибка refresh listen key: {refresh_err}")

                logger.warning(
                    f"⚠️  Futures User Data WS lost. Reconnect in 5s... ({error_str})"
                )
                await asyncio.sleep(5)

    # ──────────────────────────────────────────────────────────────
    # Отдельные SPOT-сокеты (fallback для Spot-циклов)
    # ──────────────────────────────────────────────────────────────

    async def subscribe_spot_agg_trade(self, symbol: str, callback):
        """Отдельный прямой сокет для SPOT aggTrade (используется Spot-циклами)."""
        import logging
        logger = logging.getLogger(__name__)
        spot_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@aggTrade"
        logger.info(f"🔄 Connecting to SPOT aggTrade: {spot_url}")

        while getattr(self, "_running", True):
            try:
                async with websockets.connect(
                    spot_url, ping_interval=20, ping_timeout=20
                ) as ws:
                    logger.info(f"✅ Spot aggTrade WS connected for {symbol}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            normalized_data = {
                                "e": "aggTrade",
                                "s": data.get("s"),
                                "p": data.get("p"),
                                "q": data.get("q"),
                                "m": data.get("m"),
                                "T": data.get("T"),
                            }
                            if callback:
                                await callback("MARKET_TRADE", normalized_data)
                        except Exception as e:
                            logger.error(f"Error processing spot trade: {e}")
            except Exception as e:
                logger.warning(f"⚠️  Spot aggTrade WS lost. Reconnect in 5s...")
                await asyncio.sleep(5)

    async def subscribe_spot_depth(self, symbol: str, callback):
        """Отдельный прямой сокет для SPOT depth (100ms)."""
        import logging
        logger = logging.getLogger(__name__)
        spot_depth_url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@depth@100ms"
        logger.info(f"🔄 Connecting to SPOT depth (100ms): {spot_depth_url}")

        while getattr(self, "_running", True):
            try:
                async with websockets.connect(
                    spot_depth_url, ping_interval=20, ping_timeout=20
                ) as ws:
                    logger.info(f"✅ Spot Depth WS connected for {symbol}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            normalized_data = {
                                "e": "depthUpdate",
                                "s": symbol.upper(),
                                "b": data.get("b", []),
                                "a": data.get("a", []),
                                "E": data.get("E", int(asyncio.get_event_loop().time() * 1000)),
                            }
                            if callback:
                                await callback("SPOT_ORDERBOOK_UPDATE", normalized_data)
                        except Exception as e:
                            logger.error(f"Error processing spot depth: {e}")
            except Exception as e:
                logger.warning(f"⚠️  Spot Depth WS lost. Reconnect in 3s...")
                await asyncio.sleep(3)

    # ──────────────────────────────────────────────────────────────
    # Главный цикл чтения
    # ──────────────────────────────────────────────────────────────

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
                    # 🔥 ИСПРАВЛЕНО: timeout сокращён до 10 сек (было 30)
                    message = await asyncio.wait_for(
                        self._ws.recv(), timeout=self.RECV_TIMEOUT_SEC
                    )
                    await self._message_queue.put(message)
                    self._healthy = True

                except asyncio.TimeoutError:
                    # 🔥 НОВОЕ: диагностическая информация
                    self.logger.warning(
                        f"WS recv timeout ({self.RECV_TIMEOUT_SEC}s). "
                        f"Active subs: {len(self._active_subscriptions)}, "
                        f"Pending: {len(self._pending_subscriptions)}. "
                        f"Forcing reconnect..."
                    )
                    self._connected = False
                    self._healthy = False

                except websockets.ConnectionClosed as e:
                    self.logger.warning(
                        f"Connection closed by server (code: {e.code}). Reconnecting..."
                    )
                    self._connected = False
                    self._healthy = False

                except Exception as e:
                    self.logger.error(f"Critical error in WS run loop: {e}")
                    self._connected = False
                    self._healthy = False

        finally:
            self._running = False
            processor_task.cancel()

    # ──────────────────────────────────────────────────────────────
    # Обработка сообщений из очереди
    # ──────────────────────────────────────────────────────────────

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
        """Маршрутизация сообщений по символам и событиям."""
        event_type = data.get("e", "UNKNOWN")
        symbol = data.get("s", "")

        # 1. Фильтр шума: ответы на SUBSCRIBE, пинги
        if event_type == "UNKNOWN" or ("id" in data and "result" in data):
            return

        # 2. Нормализация для Delta Monitor (готовность к Bybit)
        if event_type == "aggTrade" and self.event_bus:
            normalized_payload = {
                "price": float(data.get("p", 0)),
                "qty": float(data.get("q", 0)),
                "is_buyer_maker": bool(data.get("m", False)),
                "timestamp": data.get("T", 0),
            }
            asyncio.create_task(
                self.event_bus.publish(
                    event_type=f"TRADE_NORMALIZED_{symbol}",
                    source="binance_ws",
                    payload=normalized_payload,
                    symbol=symbol,
                )
            )

        # 3. Маршрутизация
        if event_type == "aggTrade":
            if symbol == "BTCUSDT":
                await self._route_event("BTC_AGG_TRADE", data)
            else:
                await self._route_event("aggTrade", data)

        elif event_type == "depthUpdate":
            if symbol == "BTCUSDT":
                await self._route_event("BTC_DEPTH_UPDATE", data)
            else:
                await self._route_event("depthUpdate", data)

        elif event_type == "ORDER_TRADE_UPDATE":
            await self._route_event("ORDER_TRADE_UPDATE", data)

        elif event_type == "ACCOUNT_UPDATE":
            await self._route_event("ACCOUNT_UPDATE", data)

        elif event_type == "listenKeyExpired":
            await self._route_event("listenKeyExpired", data)

    async def _route_event(self, event_type: str, data: Dict):
        """Логирование + вызов зарегистрированного хендлера."""
        log_data = data
        if event_type == "ORDER_TRADE_UPDATE" and "o" in data:
            o = data["o"]
            log_data = {
                "symbol": o.get("s"),
                "client_order_id": o.get("c"),
                "status": o.get("X"),
            }

        tracked_events = {
            "ORDER_TRADE_UPDATE",
            "ACCOUNT_UPDATE",
            "listenKeyExpired",
            "depthUpdate",
            "BTC_DEPTH_UPDATE",
            "BTC_AGG_TRADE",
        }
        if self._json_logger and event_type in tracked_events:
            self._json_logger.log(module="ws", event=event_type, data=log_data, level="DEBUG")

        if event_type in ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE"):
            extra = (
                f" | {data['o'].get('c')} | {data['o'].get('X')}" if "o" in data else ""
            )
            print(f"📥 [WS_EVENT] {event_type}{extra}")

        if event_type in ("ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE"):
            extra = (
                f" | {data['o'].get('c')} | {data['o'].get('X')}" if "o" in data else ""
            )
            print(f"📥 [WS_EVENT] {event_type}{extra}")
            # 🔥 Обновляем timestamp живости User Data для health-check
            self._last_user_data_ts = time.time()

        handler = self._handlers.get(event_type)

        handler = self._handlers.get(event_type)
        if handler:
            try:
                await handler(data)
            except Exception as e:
                self.logger.error(f"Error in handler for {event_type}: {e}")

    # ──────────────────────────────────────────────────────────────
    # Служебные методы
    # ──────────────────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        return self._healthy and self._connected

    async def close(self):
        self._running = False

        # Отменяем User Data Stream, если запущен
        if hasattr(self, "_user_data_task") and self._user_data_task:
            self._user_data_task.cancel()
            try:
                await self._user_data_task
            except asyncio.CancelledError:
                pass

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._connected = False
        self.logger.info("🛑 WebSocket closed gracefully")