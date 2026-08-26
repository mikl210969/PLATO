#!/usr/bin/env python3
"""
PLAT_WALLS_NEW — Торговая платформа (чистая версия).
"""

import asyncio
import signal
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.logger import get_logger
from core.event_bus import EventBus, Event
from core.config_loader import ConfigLoader
from core.json_logger import JsonLogger

from adapters.binance_rest import BinanceRestClient
from adapters.binance_ws import BinanceWsAdapter
from adapters.channel_router import ChannelRouter

from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.trader import Trader
from trading.orchestrator import Orchestrator
from trading.state_manager import StateManager
from trading.lifecycle_manager import LifecycleManager
from trading.risk_manager import RiskManager
from trading.order_verifier import OrderVerifier  # 🔥 НОВЫЙ ИМПОРТ

from strategies.wall_fade import WallFadeStrategy
from strategies.absorption import AbsorptionStrategy
from strategies.breakout import BreakoutStrategy

logger = get_logger(__name__)


class Platform:
    def __init__(self, profile: str = "testnet_24h_real"):
        self.profile = profile
        self._running = True
        self._is_reconnecting = False        
        self._listen_key = None

        # Переменные для хранения данных из WS
        self.ws_price = 0.0
        self.ws_orderbook = {'bids': [], 'asks': []}

        # 1. Загрузка конфигов
        self.config = ConfigLoader().load_all()
        secrets = ConfigLoader().load_secrets()

        exchange_config = self.config.get('exchange', {})
        api_key = secrets.get('api_key', '') or exchange_config.get('api_key', '')
        api_secret = secrets.get('api_secret', '') or exchange_config.get('api_secret', '')

        self.symbol = exchange_config.get('symbol', 'SOLUSDT')

        # 2. JSON Logger
        self.json_logger = JsonLogger(enabled=True)
        logger.info(f"✅ JSON Logger initialized: enabled={self.json_logger.enabled}")

        # 3. Инициализация базовых компонентов
        self.bus = EventBus()
        self.passport_manager = PassportManager()
        self.passport_repository = PassportRepository()

        # 4. REST и WS клиенты
        self.rest = BinanceRestClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=exchange_config.get('rest_base_url', 'https://testnet.binancefuture.com')
        )

        self.ws = BinanceWsAdapter(
            base_url=exchange_config.get('ws_base_url', 'wss://stream.binancefuture.com/ws')
        )
        self.ws.set_json_logger(self.json_logger)
        self.router = ChannelRouter(self.ws, self.rest)

        # 5. StateManager
        self.state_manager = StateManager(self.passport_manager)

        # 6. Оркестратор (включает в себя миксины: EventHandlers, Monitor, Recovery, PositionMonitor)
        self.orchestrator = Orchestrator(
            config=self.config,
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,
            state_manager=self.state_manager,
            json_logger=self.json_logger
        )
        logger.info("✅ Orchestrator initialized")

        # 7. Трейдер
        self.trader = Trader(
            symbol=self.symbol,
            rest_client=self.rest,
            ws_adapter=self.ws,
            event_bus=self.bus,
            config=self.config
        )
        self.orchestrator.register_trader(self.symbol, self.trader)

        # 8. LifecycleManager (управление TTL)
        self.lifecycle_manager = LifecycleManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            config=self.config,
            json_logger=self.json_logger
        )
        logger.info("✅ LifecycleManager initialized")

        # 9. RiskManager
        self.risk_manager = RiskManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            trader=self.trader,
            config=self.config,
            json_logger=self.json_logger
        )
        self.orchestrator.set_risk_manager(self.risk_manager)
        logger.info("✅ RiskManager initialized and set in Orchestrator")

        # 10. 🔥 OrderVerifier: асинхронная проверка ордеров через REST
        self.verifier = OrderVerifier(
            rest_client=self.rest,
            event_bus=self.bus,
            poll_interval=3.0,
            max_attempts=20
        )
        logger.info("✅ OrderVerifier initialized")

        # 11. 🔥 DriftMonitor: периодическая сверка с биржей
        from trading.drift_monitor import DriftMonitor
        self.drift_monitor = DriftMonitor(
            rest_client=self.rest,
            passport_manager=self.passport_manager,
            event_bus=self.bus,
            poll_interval=30.0
        )
        logger.info("✅ DriftMonitor initialized")

        # 🔥 Передаём DriftMonitor и OrderVerifier в Orchestrator для Pre-Trade Gate
        # (ДОЛЖНО БЫТЬ ПОСЛЕ СОЗДАНИЯ обоих компонентов!)
        self.orchestrator.set_drift_monitor(self.drift_monitor)
        self.orchestrator.set_verifier(self.verifier)
        logger.info("✅ DriftMonitor and OrderVerifier set in Orchestrator")

        # 12. Стратегии
        strategies_config = self.config.get('strategies', {})
        self.wall_fade = WallFadeStrategy(strategies_config.get('wall_fade', {}))
        self.absorption = AbsorptionStrategy(strategies_config.get('absorption', {}))
        self.breakout = BreakoutStrategy(strategies_config.get('breakout', {}))

        logger.info(f"✅ Platform initialized | symbol={self.symbol} | profile={profile}")

        # Тест JSON Logger
        self.json_logger.log(
            module="platform",
            event="test_log",
            data={"message": "JSON Logger is working"}
        )

    async def _generate_signals(self, context: dict):
        signals = []
        for strategy in [self.wall_fade, self.absorption, self.breakout]:
            signal = strategy.generate_signal(context)
            if signal:
                signals.append(signal)
        return signals

    async def _main_loop(self):
        logger.info("🔄 Main loop started")

        listen_key = await self.rest.get_listen_key()
        self._listen_key = listen_key
        logger.info(f"✅ Listen key obtained: {listen_key[:10]}...")

        await self.ws.connect()
        
        # Первичная подписка на стакан и пользовательские данные
        await self.ws.subscribe_depth(self.symbol)

        self._last_user_data_ts = time.time()
        self._last_price_update_ts = time.time()

        # ===== 🔥 ИСПРАВЛЕННЫЙ Обработчик переподключения WS =====
        async def on_ws_reconnect():
            if getattr(self, '_is_reconnecting', False):
                return
            
            self._is_reconnecting = True
            refreshed = False
            
            try:
                for attempt in range(3):
                    try:
                        new_listen_key = await asyncio.wait_for(self.rest.get_listen_key(), timeout=3.0)
                        self._listen_key = new_listen_key
                        
                        # 1. Подписываемся на пользовательские данные
                        await self.ws.subscribe_user_data(new_listen_key)
                        self._last_user_data_ts = time.time()
                        
                        # 2. 🔥 КРИТИЧЕСКИЙ ФИКС: ЗАНОВО подписываемся на стакан (depth) при реконнекте!
                        await self.ws.subscribe_depth(self.symbol)
                        
                        refreshed = True
                        logger.info(f"✅ Listen key refreshed on reconnect: {new_listen_key[:10]}...")
                        logger.info(f"✅ Depth stream (orderbook) resubscribed for {self.symbol}")
                        break
                        
                    except Exception as e:
                        error_details = repr(e) or str(e) or "Unknown empty error"
                        logger.error(f"❌ Refresh listen key attempt {attempt + 1}/3 failed: {error_details}")
                        logger.debug(f"Traceback details:\n{traceback.format_exc()}")
                        
                        try:
                            await self.rest.reset_session()
                        except Exception:
                            pass
                        if attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))

                if not refreshed:
                    logger.critical("❌ CRITICAL: listen key not refreshed after 3 attempts")
            finally:
                self._is_reconnecting = False

            # Запрашиваем синхронизацию состояния после реконнекта
            await self.bus.publish(
                event_type="SYNC_REQUEST",
                source="platform",
                payload={"symbol": self.symbol},
                symbol=self.symbol
            )

        self.ws._on_reconnect = on_ws_reconnect

        # ===== Обработчик принудительного реконнекта WS =====
        async def on_ws_reconnect_forced(event: Event):
            logger.warning(f"⚠️ WS reconnect forced for passport {event.payload.get('passport_id')}")
            
            if hasattr(self.ws, 'close'):
                await self.ws.close()

        self.bus.subscribe("WS_RECONNECT_FORCED", on_ws_reconnect_forced)

        # ===== Подписка на события WS → Шина =====

        async def on_order_update(data):
            self._last_user_data_ts = time.time()            
            
            order_data = data.get('o', data)
            
            client_order_id = str(order_data.get('c') or order_data.get('clientOrderId') or order_data.get('client_order_id') or '')
            order_status = str(order_data.get('X') or order_data.get('status') or '')
            symbol = str(order_data.get('s') or order_data.get('symbol') or '')
            
            executed_qty = float(order_data.get('z') or order_data.get('executedQty') or order_data.get('executed_qty') or 0.0)
            avg_price = float(order_data.get('ap') or order_data.get('avgPrice') or order_data.get('price') or 0.0)
            
            await self.bus.publish(
                event_type="ORDER_TRADE_UPDATE",
                source="ws_adapter",
                payload={
                    "client_order_id": client_order_id,
                    "status": order_status,
                    "symbol": symbol,
                    "executed_qty": executed_qty,
                    "avg_price": avg_price,
                    "dedup_key": data.get("dedup_key"),
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
        logger.info(f"✅ User data stream subscribed: {listen_key[:10]}...")

        # ===== Фоновые задачи =====
        async def user_data_health_check():
            while getattr(self, '_running', True):
                await asyncio.sleep(10)
                price_age = time.time() - getattr(self, '_last_price_update_ts', time.time())
                has_active = bool(self.passport_manager.get_active_by_symbol(self.symbol))
                
                if has_active and price_age > 60:
                    logger.warning(f"⚠️ WS DEAD: No price updates for {price_age:.0f}s. Forcing refresh.")
                    self._last_price_update_ts = time.time()
                    await on_ws_reconnect()

        asyncio.create_task(self.ws.run())
        # 🔥 Запускаем DriftMonitor
        await self.drift_monitor.start(symbols=[self.symbol])        
        asyncio.create_task(self._keep_alive_loop())
        asyncio.create_task(user_data_health_check())
        
        # Запуск мониторинга зависших ордеров (из MonitorMixin)
        await self.orchestrator.start_stuck_orders_monitor()

        # ===== Блокирующая синхронизация при старте (из RecoveryMixin) =====
        logger.info("🔄 [STARTUP] Performing exchange state recovery (blocking)...")
        await self.orchestrator.perform_startup_recovery(self.symbol)
        logger.info("✅ [STARTUP] Recovery complete. Main loop starting.")

        # ===== Основной цикл =====
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
                        current_price = (float(bids[0][0]) + float(asks[0][0])) / 2
                    else:
                        current_price = 0.0

                current_time = time.time()
                
                # Периодическая проверка позиции через REST (для надёжности)
                if current_time - last_position_check_time >= 10:
                    await self.rest.get_position(self.symbol)
                    last_position_check_time = current_time

                # Логирование цены каждые 30 секунд
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
                        await self.bus.publish(
                            event_type="SIGNAL_GENERATED",
                            source="strategy",
                            payload={"signal": signals[0]},
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
            except Exception as e:
                logger.error(f"❌ Failed to renew listen key: {repr(e)}")

    async def run(self):
        logger.info("🚀 Starting platform...")
        await self.orchestrator.start()
        await self._main_loop()

    async def stop(self):
        self._running = False
        await self.orchestrator.stop()

        # 🔥 1. Сначала DriftMonitor — чтобы он перестал делать REST-запросы
        if hasattr(self, 'drift_monitor'):
            try:
                await self.drift_monitor.stop()
            except Exception as e:
                logger.error(f"Error stopping DriftMonitor: {e}")

        # 🔥 2. Потом OrderVerifier — отменяем фоновые проверки ордеров
        if hasattr(self, 'verifier'):
            try:
                await self.verifier.stop_all()
            except Exception as e:
                logger.error(f"Error stopping OrderVerifier: {e}")

        # 🔥 3. В конце закрываем REST-клиент и логи
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
    
    # Перехватываем сигналы завершения от ОС
    signal.signal(signal.SIGINT, signal_handler(platform))
    signal.signal(signal.SIGTERM, signal_handler(platform))

    try:
        await platform.run()
    except KeyboardInterrupt:
        print("\n⏹️ Остановка по команде пользователя (Ctrl+C)")
    except asyncio.CancelledError:
        print("\n⚠️ ВНИМАНИЕ: Главный цикл был принудительно отменён!")
        print("   Возможные причины: закрытие терминала VS Code, перезапуск Python-расширения,")
        print("   или принудительное завершение процесса операционной системой.")
    except Exception as e:
        print(f"\n💥 КРИТИЧЕСКАЯ НЕПРЕДВИДЕННАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🛑 Завершение работы платформы и очистка ресурсов...")
        await platform.stop()
        print("✅ Платформа полностью остановлена.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass