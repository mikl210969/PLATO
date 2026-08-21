#!/usr/bin/env python3
"""
PLAT_WALLS_NEW — Торговая платформа (чистая версия).
"""

import asyncio
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.logger import get_logger
from core.event_bus import EventBus
from core.config_loader import ConfigLoader
from adapters.binance_rest import BinanceRestClient
from adapters.binance_ws import BinanceWsAdapter
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.trader import Trader
from trading.orchestrator import Orchestrator
from trading.state_manager import StateManager
from strategies.wall_fade import WallFadeStrategy
from strategies.absorption import AbsorptionStrategy
from strategies.breakout import BreakoutStrategy
from adapters.channel_router import ChannelRouter
from core.json_logger import JsonLogger
from trading.lifecycle_manager import LifecycleManager
from core.event_bus import EventBus, Event
logger = get_logger(__name__)


class Platform:
    def __init__(self, profile: str = "testnet_24h_real"):
        self.profile = profile
        self._running = True
        self._is_reconnecting = False        
        self._listen_key = None

        # 🔥 Переменная для хранения цены из WS
        self.ws_price = 0.0
        # 🔥 Переменная для хранения стакана из WS
        self.ws_orderbook = {'bids': [], 'asks': []}

        # 1. Загрузка конфигов
        self.config = ConfigLoader().load_all()
        secrets = ConfigLoader().load_secrets()

        exchange_config = self.config.get('exchange', {})
        api_key = secrets.get('api_key', '') or exchange_config.get('api_key', '')
        api_secret = secrets.get('api_secret', '') or exchange_config.get('api_secret', '')

        self.symbol = exchange_config.get('symbol', 'SOLUSDT')
        trading_config = self.config.get('trading', {})

        # 2. JSON Logger
        self.json_logger = JsonLogger(enabled=True)
        print(f"✅ [PLATFORM] JSON Logger initialized: enabled={self.json_logger.enabled}")

        # 3. Инициализация компонентов
        self.bus = EventBus()
        self.passport_manager = PassportManager()
        self.passport_repository = PassportRepository()

        # 4. REST клиент
        self.rest = BinanceRestClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=exchange_config.get('rest_base_url', 'https://testnet.binancefuture.com')
        )

        # 5. WS адаптер
        self.ws = BinanceWsAdapter(
            base_url=exchange_config.get('ws_base_url', 'wss://stream.binancefuture.com/ws')
        )
        self.ws.set_json_logger(self.json_logger)

        # 6. Channel Router
        self.router = ChannelRouter(self.ws, self.rest)

        # 7. StateManager
        self.state_manager = StateManager(self.passport_manager)

        # 8. Оркестратор
        self.orchestrator = Orchestrator(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,
            state_manager=self.state_manager,
            config=self.config,
            json_logger=self.json_logger
        )
        print(f"✅ [PLATFORM] Orchestrator initialized with json_logger")

        # 9. Трейдер (без PassportManager!)
        symbol = exchange_config.get('symbol', 'SOLUSDT')
        self.trader = Trader(
            symbol=symbol,
            rest_client=self.rest,
            ws_adapter=self.ws,
            event_bus=self.bus,
            config=self.config
        )

        self.orchestrator.register_trader(symbol, self.trader)

        # 10. LifecycleManager
        from trading.lifecycle_manager import LifecycleManager

        self.lifecycle_manager = LifecycleManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            config=self.config,
            json_logger=self.json_logger
        )
        print(f"✅ [PLATFORM] LifecycleManager initialized")

        # 11. RecoveryManager
        from trading.recovery_manager import RecoveryManager

        self.recovery_manager = RecoveryManager(
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,
            trader=self.trader,
            config=self.config,
            json_logger=self.json_logger
        )
        print(f"✅ [PLATFORM] RecoveryManager initialized")

        # 12. RiskManager (создаётся ПОСЛЕ трейдера)
        from trading.risk_manager import RiskManager

        self.risk_manager = RiskManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            trader=self.trader,
            config=self.config,
            json_logger=self.json_logger
        )
        print(f"✅ [PLATFORM] RiskManager initialized")

        # 13. Передаём RiskManager в Оркестратор (ТОЛЬКО ПОСЛЕ создания)
        self.orchestrator.set_risk_manager(self.risk_manager)
        print(f"✅ [PLATFORM] RiskManager set in Orchestrator")

        # 14. Стратегии
        strategies_config = self.config.get('strategies', {})
        self.wall_fade = WallFadeStrategy(strategies_config.get('wall_fade', {}))
        self.absorption = AbsorptionStrategy(strategies_config.get('absorption', {}))
        self.breakout = BreakoutStrategy(strategies_config.get('breakout', {}))

        logger.info(f"✅ Platform initialized | symbol={symbol} | profile={profile}")

        # Тест JSON Logger
        self.json_logger.log(
            module="platform",
            event="test_log",
            data={"message": "JSON Logger is working"}
        )
        print(f"✅ [PLATFORM] Test log written to platform_log.json")

    async def _generate_signals(self, context: dict):
        signals = []
        signal = self.wall_fade.generate_signal(context)
        if signal:
            signals.append(signal)
        signal = self.absorption.generate_signal(context)
        if signal:
            signals.append(signal)
        signal = self.breakout.generate_signal(context)
        if signal:
            signals.append(signal)
        return signals

    async def _main_loop(self):
        logger.info("🔄 Main loop started")

        listen_key = await self.rest.get_listen_key()
        self._listen_key = listen_key
        logger.info(f"✅ Listen key obtained: {listen_key[:10]}...")

        self.json_logger.log(
            module="platform",
            event="listen_key_obtained",
            data={"listen_key": listen_key[:10] + "..."}
        )

        await self.ws.connect()
        asyncio.create_task(self.ws.health_check_loop())
        await self.ws.subscribe_depth(self.symbol)

        # Инициализация таймеров
        self._last_user_data_ts = time.time()
        self._last_price_update_ts = time.time()

        # ===== Обработчик переподключения WS =====
        async def on_ws_reconnect():
            if getattr(self, '_is_reconnecting', False):
                logger.debug("Reconnect already in progress, skipping.")
                return
            
            self._is_reconnecting = True
            last_error = None
            refreshed = False
            
            try:
                for attempt in range(3):
                    try:
                        new_listen_key = await asyncio.wait_for(self.rest.get_listen_key(), timeout=3.0)
                        self._listen_key = new_listen_key
                        await self.ws.subscribe_user_data(new_listen_key)
                        self._last_user_data_ts = time.time()
                        refreshed = True
                        self.json_logger.log(
                            module="platform",
                            event="listen_key_refreshed_on_reconnect",
                            data={"listen_key": new_listen_key[:10] + "...", "attempt": attempt + 1}
                        )
                        logger.info(f"✅ Listen key refreshed on reconnect: {new_listen_key[:10]}...")
                        break
                    except Exception as e:
                        last_error = e
                        logger.error(f"❌ Refresh listen key attempt {attempt + 1}/3 failed: {e}")
                        self.json_logger.log(
                            module="platform",
                            event="listen_key_refresh_attempt_failed",
                            data={"attempt": attempt + 1, "error": str(e)}
                        )
                        try:
                            await self.rest.reset_session()
                        except Exception:
                            pass
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))

                if not refreshed:
                    logger.critical(f"❌ CRITICAL: listen key not refreshed after 3 attempts: {last_error}")
                    self.json_logger.log(
                        module="platform",
                        event="listen_key_refresh_failed_all_attempts",
                        data={"error": str(last_error)}
                    )
            finally:
                self._is_reconnecting = False

            await self.bus.publish(
                event_type="SYNC_REQUEST",
                source="platform",
                payload={"symbol": self.symbol},
                symbol=self.symbol
            )

        self.ws._on_reconnect = on_ws_reconnect

        # ===== Обработчик принудительного реконнекта WS =====
        async def on_ws_reconnect_forced(event: Event):
            self.json_logger.log(
                module="platform",
                event="ws_reconnect_forced",
                data={"passport_id": event.payload.get('passport_id')}
            )
            logger.warning(f"⚠️ WS reconnect forced for passport {event.payload.get('passport_id')}")
            
            close_method = getattr(self.ws, 'close', None)
            if close_method is not None and callable(close_method):
                await close_method()  # type: ignore[misc]
            else:
                logger.warning("⚠️ WS adapter has no 'close' method. Standard reconnect will handle it.")

        self.bus.subscribe("WS_RECONNECT_FORCED", on_ws_reconnect_forced)

        # ===== Подписка на события WS → Шина =====
        async def on_order_update(data):
            self._last_user_data_ts = time.time()            
            
            # 🔥 ОТЛАДКА: Печатаем реальные ключи, которые пришли от адаптера
            print(f"🔍 [DEBUG] RAW DATA KEYS: {list(data.keys())}")
            
            # Пробуем разные варианты извлечения
            if 'o' in data and isinstance(data['o'], dict):
                order_data = data['o']
            else:
                order_data = data
                
            # Ищем ключи в любом из возможных написаний (Binance или нормализованные)
            client_order_id = str(order_data.get('c') or order_data.get('clientOrderId') or order_data.get('client_order_id') or '')
            order_status = str(order_data.get('X') or order_data.get('status') or '')
            symbol = str(order_data.get('s') or order_data.get('symbol') or '')

            print(f"🔍 [PLATFORM] Publishing ORDER_TRADE_UPDATE: '{client_order_id}' | '{order_status}' | '{symbol}'")
            
            # Если ключи всё ещё не найдены, печатаем содержимое для точного анализа
            if not client_order_id:
                print(f"⚠️ [DEBUG] FULL ORDER_DATA CONTENT: {order_data}")

            await self.bus.publish(
                event_type="ORDER_TRADE_UPDATE",
                source="ws_adapter",
                payload={
                    "client_order_id": client_order_id,
                    "status": order_status,
                    "symbol": symbol
                },
                symbol=symbol
            )
        self.ws.on("ORDER_TRADE_UPDATE", on_order_update)

        async def on_account_update(data):
            self._last_user_data_ts = time.time()            
            await self.bus.publish(
                event_type="ACCOUNT_UPDATE",
                source="ws_adapter",
                payload=data,
                symbol=self.symbol
            )
        self.ws.on("ACCOUNT_UPDATE", on_account_update)

        async def on_depth_update(data):
            try:
                bids = data.get('b', [])
                asks = data.get('a', [])
                self.ws_orderbook = {'bids': bids, 'asks': asks}
                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    self.ws_price = (best_bid + best_ask) / 2

                    # 🔥 КРИТИЧЕСКИ ВАЖНО: Обновляем таймер живости WS
                    self._last_price_update_ts = time.time()

                    await self.bus.publish(
                        event_type="PRICE_UPDATE",
                        source="main",
                        payload={
                            'symbol': self.symbol,
                            'price': self.ws_price,
                            'ts': time.time(),
                        },
                        symbol=self.symbol
                    )
            except Exception as e:
                logger.error(f"Error processing depth update: {e}")
        self.ws.on("depthUpdate", on_depth_update)

        await self.ws.subscribe_user_data(listen_key)
        print(f"✅ [PLATFORM] User data stream subscribed: {listen_key[:10]}...")

        # =====================================================================
        # 🔥 1. ОПРЕДЕЛЕНИЕ ФУНКЦИИ HEALTH CHECK
        # =====================================================================
        async def user_data_health_check():
            """Проверяет живость WS на основе ПОТОКА ЦЕН, а не ACCOUNT_UPDATE."""
            while getattr(self, '_running', True):
                await asyncio.sleep(10)
                
                price_age = time.time() - getattr(self, '_last_price_update_ts', time.time())
                user_data_age = time.time() - getattr(self, '_last_user_data_ts', time.time())
                
                has_active = bool(self.passport_manager.get_active_by_symbol(self.symbol))
                
                # Если цена обновлялась менее 60 секунд назад, всё отлично
                if has_active and price_age < 60:
                    continue 
                
                # Порог увеличен до 60 секунд для стабильности
                if has_active and price_age > 60:
                    logger.warning(f"⚠️ WS DEAD: No price updates for {price_age:.0f}s. Forcing refresh.")
                    self.json_logger.log(
                        module="platform",
                        event="ws_dead_no_price_updates",
                        data={"age_sec": round(price_age, 1)}
                    )
                    self._last_price_update_ts = time.time()
                    await on_ws_reconnect()

        # =====================================================================
        # 🔥 2. ЗАПУСК ВСЕХ ФОНОВЫХ ЗАДАЧ
        # =====================================================================
        asyncio.create_task(self.ws.run())
        asyncio.create_task(self._keep_alive_loop())
        asyncio.create_task(user_data_health_check())
        
        await self.orchestrator.start_stuck_orders_monitor()

        # =====================================================================
        # 🔥 3. БЛОКИРУЮЩАЯ СИНХРОНИЗАЦИЯ ПРИ СТАРТЕ
        # =====================================================================
        logger.info("🔄 [STARTUP] Performing exchange state recovery (blocking)...")
        await self.orchestrator.perform_startup_recovery(self.symbol)
        logger.info("✅ [STARTUP] Recovery complete. Main loop starting.")

        # =====================================================================
        # 🔥 4. ОСНОВНОЙ ЦИКЛ
        # =====================================================================
        last_log_time = 0
        last_position_check_time = 0

        while self._running:
            try:
                if self.ws_price > 0:
                    current_price = self.ws_price
                else:
                    orderbook = await self.rest.get_orderbook(self.symbol)
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    if bids and asks:
                        best_bid = float(bids[0][0])
                        best_ask = float(asks[0][0])
                        current_price = (best_bid + best_ask) / 2
                    else:
                        current_price = 0.0

                current_time = time.time()
                if current_time - last_position_check_time >= 10:
                    position = await self.rest.get_position(self.symbol)
                    last_position_check_time = current_time

                if current_time - last_log_time >= 30:
                    logger.info(f"🔄 Price: {current_price} (from {'WS' if self.ws_price > 0 else 'REST'})")
                    last_log_time = current_time

                if self.passport_manager.is_symbol_busy(self.symbol):
                    await asyncio.sleep(2)
                    continue

                context = {
                    'symbol': self.symbol,
                    'current_price': current_price,
                    'orderbook': self.ws_orderbook,
                }

                signals = await self._generate_signals(context)

                if signals:
                    logger.info(f"📊 Generated {len(signals)} signals")
                    for s in signals:
                        logger.info(f"  - {s.signal_id} | {s.side} @ {s.entry_price}")

                    if not self.passport_manager.is_symbol_busy(self.symbol):
                        signal = signals[0]
                        await self.bus.publish(
                            event_type="SIGNAL_GENERATED",
                            source="strategy",
                            payload={"signal": signal},
                            symbol=self.symbol
                        )

                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(1)

    async def _keep_alive_loop(self):
        while self._running:
            await asyncio.sleep(20 * 60)
            try:
                if self._listen_key:
                    await self.rest.renew_listen_key(self._listen_key)
                    logger.info("✅ Listen key renewed")
                    if hasattr(self, 'json_logger') and self.json_logger:
                        self.json_logger.log(
                            module="platform",
                            event="listen_key_renewed",
                            data={"listen_key": self._listen_key[:10] + "..."}
                        )
            except Exception as e:
                logger.error(f"❌ Failed to renew listen key: {e}")
                if hasattr(self, 'json_logger') and self.json_logger:
                    self.json_logger.log(
                        module="platform",
                        event="listen_key_renew_failed",
                        data={"error": str(e)}
                    )

    async def run(self):
        logger.info("🚀 Starting platform...")
        
        # 🔥 Восстановление после перезапуска
        #recovery_stats = await self.recovery_manager.recover()
        #logger.info(f"♻️ Recovery stats: {recovery_stats}")
        
        await self.orchestrator.start()
        await self._main_loop()

    async def stop(self):
        self._running = False
        await self.orchestrator.stop()
        await self.rest.close()
        self.json_logger.close()
        logger.info("🛑 Platform stopped")


def signal_handler(platform: Platform):
    def handler(sig, frame):
        print("\n⏹️  Stopping...")
        asyncio.create_task(platform.stop())
    return handler


async def main():
    platform = Platform(profile="testnet_24h_real")
    signal.signal(signal.SIGINT, signal_handler(platform))

    try:
        await platform.run()
    except KeyboardInterrupt:
        print("\n⏹️  Stopping...")
    finally:
        await platform.stop()


if __name__ == "__main__":
    asyncio.run(main())