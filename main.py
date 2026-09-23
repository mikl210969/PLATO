#!/usr/bin/env python3
"""
PLAT_WALLS_NEW — Торговая платформа (чистая версия, рефакторинг v3.1).
"""

import asyncio
import signal
import sys
import time
import logging
import traceback
from pathlib import Path
from typing import Optional, Dict, List, Any

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
from features.factory import FeatureRegistry
from trading.state_manager import StateManager
from trading.lifecycle_manager import LifecycleManager
from trading.risk_manager import RiskManager
from trading.order_verifier import OrderVerifier

from strategies.wall_fade_v3 import WallFadeStrategyV3
from strategies.absorption_v2 import AbsorptionStrategyV2
from strategies.breakout_v1 import BreakoutStrategyV1

from datetime import datetime, timezone

from extensions.risk.position_sizer import PositionSizer
from extensions.analytics.monitor_factory import MonitorFactory  # 🔥 НОВОЕ: Фабрика мониторов
from extensions.analytics.atr_monitor import AtrMonitor  # 🔥 УРОВЕНЬ 5: Dynamic ATR
from core.json_logger import JsonLogger, JsonLoggerHandler

from features.volume_rolling_window import VolumeRollingWindow
from features.volume_context_manager import VolumeContextManager

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

        # 🔥 НОВОЕ: кэш последней известной цены для мягкой деградации при сбоях REST
        self.last_known_price = 0.0
        self._last_user_data_ts = time.time()  # 🔥 свежесть User Data (для health-check)        
        # 🔥 ФАЗА 1: порог свежести WS-стакана (сек).
        # Стакан старше этого возраста = рынок не виден = итерацию пропускаем.
        # REST больше НЕ используется для стакана вообще.
        self.depth_freshness_sec = 3.0
        self._stale_skips = 0  # счётчик пропусков для диагностики        

        # 1. Загрузка конфигов
        self.config = ConfigLoader().load_all()

        # 🔥 НОВОЕ: Инициализация Базы Данных для хранения свечей и метрик
        from extensions.data_layer.db_manager import DatabaseManager
        self.db_manager = DatabaseManager(db_path="extensions/data_layer/plato_metrics.db")
        secrets = ConfigLoader().load_secrets()

        exchange_config = self.config.get('exchange', {})
        api_key = secrets.get('api_key', '') or exchange_config.get('api_key', '')
        api_secret = secrets.get('api_secret', '') or exchange_config.get('api_secret', '')

        self.symbol = exchange_config.get('symbol', 'SOLUSDT')

        # 2. JSON Logger
        log_config = self.config.get('logging', {})
        self.json_logger = JsonLogger(config=log_config)
        
        # 🔥 НОВОЕ: Подключаем мост к СТАНДАРТНОМУ root-логгеру Python
        import logging
        json_handler = JsonLoggerHandler(self.json_logger)
        json_handler.setLevel(logging.INFO)
        
        # Добавляем handler к root logger, чтобы он перехватывал вызовы из ВСЕХ модулей
        logging.getLogger().addHandler(json_handler)
        
        # Теперь используем твой стандартный logger для вывода в консоль
        logger.info(f"✅ JSON Logger initialized | Level: {log_config.get('level', 'INFO')} | WS Raw: {log_config.get('optional_modules', {}).get('ws', False)}")

        # 3. Инициализация базовых компонентов
        self.bus = EventBus()
        # 1. Сначала создаем репозиторий
        self.passport_repository = PassportRepository()

        # 2. Затем передаем его в менеджер паспортов
        self.passport_manager = PassportManager(repository=self.passport_repository)
        
        # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ (Шаг 10.4): Восстановление состояния при старте
        # Загружаем паспорта с диска в оперативную память, чтобы is_symbol_busy работал корректно
        # и предотвращал фантомное увеличение лота при перезапусках или сбоях.
        saved_passports = self.passport_repository.load_all()
        active_count = 0
        for passport in saved_passports:
            # Добавляем в память только те, что еще не закрыты
            if passport.status not in ("CLOSED", "CANCELED", "FAILED"):
                self.passport_manager.update(passport)
                active_count += 1
        
        logger.info(f"✅ Загружено {len(saved_passports)} паспортов из хранилища, {active_count} активных добавлено в память")

        # 4. REST и WS клиенты
        self.rest = BinanceRestClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=exchange_config.get('rest_base_url', 'https://testnet.binancefuture.com')
        )

        # 🔥 НОВОЕ: Передаем event_bus в адаптер для нормализации событий
        # base_url используется ТОЛЬКО для Futures User Data Stream и определения testnet/mainnet.
        # SPOT market data идёт через внутренний spot_base_url адаптера.
        self.ws = BinanceWsAdapter(
            base_url=exchange_config.get('ws_base_url', 'wss://stream.binancefuture.com/ws'),
            event_bus=self.bus
        )
        self.ws.set_json_logger(self.json_logger)
        self.router = ChannelRouter(self.ws, self.rest)

        # 5. Analytics Hub
        from core.analytics_hub import AnalyticsHub
        self.analytics = AnalyticsHub(self.bus, self.symbol, self.rest)
        self.volatility_filter = self.analytics.volatility 

        # 6. StateManager
        self.state_manager = StateManager(self.passport_manager)

        # 7. PositionSizer (Создаем ПЕРЕД Orchestrator)
        max_pos_size = self.config.get('risk', {}).get('max_position_size', 5.0)
        self.position_sizer = PositionSizer(
            rest_client=self.rest, 
            max_position_size=max_pos_size
        )
        logger.info(f"✅ PositionSizer initialized | Max Size Cap: {max_pos_size}")

        # 8. Оркестратор
        self.orchestrator = Orchestrator(
            config=self.config,
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,
            state_manager=self.state_manager,
            json_logger=self.json_logger,
            position_sizer=self.position_sizer
        )
        logger.info("✅ Orchestrator initialized")

        # 9. Трейдер
        self.trader = Trader(
            symbol=self.symbol,
            rest_client=self.rest,
            ws_adapter=self.ws,
            event_bus=self.bus,
            config=self.config
        )
        self.orchestrator.register_trader(self.symbol, self.trader)
        # 9. Features (слой анализа рынка)
        self.features = FeatureRegistry(self.config, logger)
        logger.info("✅ FeatureRegistry initialized")
        
        # Запускаем периодическое логирование OUTPUT
        self._features_output_task = asyncio.create_task(self.features.start())

        # 10. LifecycleManager
        self.lifecycle_manager = LifecycleManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            config=self.config,
            json_logger=self.json_logger
        )
        logger.info("✅ LifecycleManager initialized")

        # 11. RiskManager
        self.risk_manager = RiskManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            trader=self.trader,
            config=self.config,
            json_logger=self.json_logger,
            passport_repository=self.passport_repository  # 🔥 ДОБАВИТЬ ЭТУ СТРОКУ
        )
        self.orchestrator.set_risk_manager(self.risk_manager)
        logger.info("✅ RiskManager initialized and set in Orchestrator")

        # 12. OrderVerifier
        self.verifier = OrderVerifier(
            rest_client=self.rest,
            event_bus=self.bus,
            poll_interval=5.0,
            max_attempts=20
        )
        logger.info("✅ OrderVerifier initialized")

        # 13. DriftMonitor
        from trading.drift_monitor import DriftMonitor
        self.drift_monitor = DriftMonitor(
            rest_client=self.rest,
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,  # 🔥 ДОБАВИТЬ ЭТО
            event_bus=self.bus,
            risk_manager=self.risk_manager,  # 🔥 ДОБАВИТЬ ЭТУ СТРОКУ
            poll_interval=900.0  # 🔥 ЧИСТКА: 15-минутный heartbeat в HEALTHY (Reconciler и так сверяет каждую минуту)
        )
        logger.info("✅ DriftMonitor initialized")

        self.orchestrator.set_drift_monitor(self.drift_monitor)
        self.orchestrator.set_verifier(self.verifier)
        logger.info("✅ DriftMonitor and OrderVerifier set in Orchestrator")

        # 13.5 ExchangeReconciler — непрерывный гарант «биржа = источник правды»
        from trading.reconciler import ExchangeReconciler
        self.reconciler = ExchangeReconciler(
            rest_client=self.rest,
            passport_manager=self.passport_manager,
            repository=self.passport_repository,
            event_bus=self.bus,
            symbols=[self.symbol],
            interval_sec=60.0,
            stale_order_age_sec=120.0,
            risk_manager=self.risk_manager
        )
        logger.info("✅ ExchangeReconciler initialized")

        # 14. Стратегии
        strategies_config = self.config.get('strategies', {})
        debug_mode = self.config.get('debug_mode', {})
        strategies_debug = debug_mode.get('strategies', {})
        
        # ========================================================================
        # 🔥 НОВОЕ: Инициализация адаптивных объемных компонентов
        # ========================================================================
        from features.volume_rolling_window import VolumeRollingWindow
        from features.volume_context_manager import VolumeContextManager

        self.monitored_symbols = ["BTCUSDT", "SOLUSDT"] 
        
        self.volume_rolling_windows = {}
        for symbol in self.monitored_symbols:
            self.volume_rolling_windows[symbol] = VolumeRollingWindow(
                db_manager=self.db_manager,
                lookback_candles=30
            )

        # ========================================================================
        # 🔥 УМНОЕ ЧТЕНИЕ КОНФИГА с защитным fallback
        # ========================================================================
        raw_volume_config = self.config.get("volume_context", {})
        
        # Если конфига нет или он выключен, применяем наши проверенные настройки по умолчанию
        if not raw_volume_config or not raw_volume_config.get("enabled", False):
            logger.warning("⚠️ [CONFIG] volume_context не найден или выключен в main config. Применяем fallback-настройки!")
            volume_config = {
                "enabled": True,
                "recalc_interval_sec": 60,
                "lookback_candles": 20,  # Возвращаем нормальное значение, т.к. данные уже идут (volumes=30)
                "baseline_avg_vol": 100.0,
                "dry_run": False,
                "force_regime": None,
                "regimes": {
                    "calm": {"vol_ratio_max": 0.7, "overrides": {"min_wall_volume": 10, "price_distance_pct": 0.3, "min_confidence": 0.4, "delta_spike_ratio": 2.0, "cooldown_sec": 60, "min_eaten_pct": 0.6}},
                    "normal": {"vol_ratio_max": 2.0, "overrides": {"min_wall_volume": 20, "price_distance_pct": 0.5, "min_confidence": 0.5, "delta_spike_ratio": 3.0, "cooldown_sec": 30, "min_eaten_pct": 0.7}},
                    "volatile": {"vol_ratio_max": 999, "overrides": {"min_wall_volume": 50, "price_distance_pct": 1.0, "min_confidence": 0.65, "delta_spike_ratio": 5.0, "cooldown_sec": 15, "min_eaten_pct": 0.8}}
                }
            }
        else:
            volume_config = raw_volume_config

        base_strategy_params = {
            "min_wall_volume": 20.0,
            "price_distance_pct": 0.5,
            "min_confidence": 0.5,
            "cooldown_sec": 30,
            "fixed_lot_size": 7.0,
            "fixed_sl_distance": 0.25,
            "fixed_tp1_distance": 0.25,
            "fixed_tp2_distance": 0.50,
            "bypass_filters": False,
            "bypass_btc_filter": False,
            "bypass_confidence_threshold": False,
            "bypass_hvn_filter": False,
        }
        
        self.volume_context_manager = VolumeContextManager(
            volume_config=volume_config,
            base_strategy_params=base_strategy_params
        )
        logger.info("✅ VolumeContextManager и VolumeRollingWindow инициализированы")
        # ========================================================================

        # ========================================================================
        # 🔥 ИСПРАВЛЕНО: merge базового конфига стратегии с debug-настройками
        # ========================================================================
        wall_fade_config = strategies_config.get('wall_fade', {})
        wall_fade_debug = strategies_debug.get('wall_fade_v3', {})
        wall_fade_merged = {**wall_fade_config, **wall_fade_debug}
        
        # 🔥 ИЗМЕНЕНО: передаем context_manager
        self.wall_fade = WallFadeStrategyV3(wall_fade_merged, atr_value=0.5, context_manager=self.volume_context_manager)
        self.wall_fade.subscribe_to_events(self.bus)

        absorption_config = strategies_config.get('absorption', {})
        absorption_debug = strategies_debug.get('absorption_v2', {})
        absorption_merged = {**absorption_config, **absorption_debug}
        # 🔥 ИЗМЕНЕНО: передаем context_manager
        self.absorption = AbsorptionStrategyV2(absorption_merged, atr_value=0.5, context_manager=self.volume_context_manager)
        self.absorption.subscribe_to_events(self.bus)

        breakout_config = strategies_config.get('breakout', {})
        breakout_debug = strategies_debug.get('breakout_v1', {})
        breakout_merged = {**breakout_config, **breakout_debug}
        # 🔥 ИЗМЕНЕНО: передаем context_manager
        self.breakout = BreakoutStrategyV1(breakout_merged, atr_value=0.5, context_manager=self.volume_context_manager)
        self.breakout.subscribe_to_events(self.bus)    

        # ========================================================================
        # 🔥 15. НОВОЕ: DeltaMonitor Factory (Универсальный мониторинг + Дивергенции)
        # ========================================================================
        self.monitored_symbols = ["BTCUSDT", "SOLUSDT"] 
        
        self.delta_monitors = MonitorFactory.create_delta_monitors(
            symbols=self.monitored_symbols, 
            event_bus=self.bus, 
            timeframe_sec=300  # 5 минут
        )
        # 🔥 Хранилище последних контекстов дельты для передачи в стратегии
        self.delta_contexts = {
            "BTCUSDT": {"trend": "FLAT", "delta_strength": 0.0, "current_price": 0.0},
            "SOLUSDT": {"trend": "FLAT", "delta_strength": 0.0, "current_price": 0.0}
        }        
        # Подписчик для логирования и сохранения контекста
        # 🔥 ЧИСТКА ЛОГА: DELTA_CTX печатается только когда что-то реально меняется:
        # смена тренда, смена знака дельты, сдвиг цены >0.5% или пульс раз в 60 сек.
        # Информативность та же, шума в ~10 раз меньше.
        self._delta_log_state = {}

        # 🔥 Объявление задач для корректной остановки и устранения предупреждений Pylance
        self._ws_task = None
        self._keep_alive_task = None
        self._health_check_task = None
        self._spot_trades_task = None
        self._spot_depth_task = None
        self._features_output_task = None

        async def update_and_log_delta_context(event):
            payload = event.payload
            symbol = getattr(event, 'symbol', 'UNKNOWN')

            trend = payload.get('trend', 'FLAT')
            delta = float(payload.get('delta_strength', 0.0) or 0.0)
            price = float(payload.get('current_price', 0.0) or 0.0)

            # Сохраняем актуальное состояние в платформу (всегда, без throttling)
            if symbol in self.delta_contexts:
                self.delta_contexts[symbol] = {
                    "trend": trend,
                    "delta_strength": delta,
                    "current_price": price
                }

            if event.type == "DIVERGENCE_DETECTED":
                logger.warning(f"🚨 [DIVERGENCE {symbol}] ОБНАРУЖЕНА ДИВЕРГЕНЦИЯ: {payload.get('type')} @ {payload.get('price')}")
                return

            if "CONTEXT" not in event.type and event.type != "BTC_CONTEXT_UPDATED":
                return

            now = time.time()
            st = self._delta_log_state.get(symbol)
            sign = 1 if delta > 0 else (-1 if delta < 0 else 0)

            should_log = False
            if st is None:
                should_log = True                                   # первое появление символа
            elif trend != st["trend"]:
                should_log = True                                   # сменился режим рынка
            elif sign != st["sign"]:
                should_log = True                                   # дельта сменила знак
            elif st["price"] > 0 and abs(price - st["price"]) / st["price"] > 0.005:
                should_log = True                                   # цена ушла на >0.5%
            elif now - st["ts"] >= 60.0:
                should_log = True                                   # пульс: поток жив

            if should_log:
                self._delta_log_state[symbol] = {"trend": trend, "sign": sign, "price": price, "ts": now}
                logger.info(
                    f"📊 [DELTA_CTX {symbol}] Trend: {trend:<5} | "
                    f"Delta: {delta:>8} | "
                    f"Price: {price}"
                )

        self.bus.subscribe("BTC_CONTEXT_UPDATED", update_and_log_delta_context)
        self.bus.subscribe("CONTEXT_UPDATED_SOLUSDT", update_and_log_delta_context)
        self.bus.subscribe("DIVERGENCE_DETECTED", update_and_log_delta_context)
        
        logger.info(f"✅ DeltaMonitor Factory initialized for {self.monitored_symbols}")

        # ========================================================================
        # 🔥 16. AtrMonitor Factory (Dynamic ATR — живой пересчёт каждые 5 минут)
        # ========================================================================
        self.atr_monitors: Dict[str, AtrMonitor] = {}
        
        for symbol in self.monitored_symbols:
            atr_monitor = AtrMonitor(
                symbol=symbol,
                event_bus=self.bus,
                volatility_filter=self.volatility_filter,
                update_interval_sec=300  # 5 минут
            )
            self.atr_monitors[symbol] = atr_monitor
        
        logger.info(f"✅ AtrMonitor Factory initialized for {self.monitored_symbols}")

        # 17. Extensions (Safe Bootstrap)
        from extensions.bootstrap import init_extensions_safe
        self.extensions = init_extensions_safe(self.bus, self.symbol)
        if self.extensions:
            logger.info("✅ Extensions (Whale, Spoofing, HVN, Basis) initialized and wired to EventBus")
        else:
            logger.warning("⚠️ Extensions failed to initialize, running in Core-only mode")

        # 18. Shadow Advanced Risk Evaluator
        from extensions.risk.advanced_risk_service import AdvancedRiskService
        self.shadow_risk = AdvancedRiskService(
            basis_monitor=self.extensions.basis if (hasattr(self, 'extensions') and self.extensions) else None,
            volatility_filter=self.volatility_filter
        )
        
        async def evaluate_shadow_signal(event):
            logger.info("[SHADOW DEBUG] Событие SIGNAL_GENERATED перехвачено!")
            await self.shadow_risk.on_signal(event)
            
        self.bus.subscribe("SIGNAL_GENERATED", evaluate_shadow_signal)
        logger.info("✅ Shadow Advanced Risk Service wired to SIGNAL_GENERATED")

        logger.info(f"✅ Platform initialized | symbol={self.symbol} | profile={self.profile}")

    async def _generate_signals(self, context: dict):
        # 🔥 Логирование факта вызова (для отладки)
        logger.info("🚨 [ГЛАВНЫЙ ЦИКЛ] _generate_signals вызван")
        print("🚨 [ГЛАВНЫЙ ЦИКЛ] _generate_signals ВЫЗВАН!")
        
        # 🔥 ЗАДАЧА 2: Блокировка новых ордеров только за счет статуса паспорта
        symbol = context.get('symbol', 'SOLUSDT')
        active_passport = self.passport_manager.get_active_by_symbol(symbol)
        
        if active_passport and active_passport.status in ('OPEN', 'PARTIAL_CLOSE', 'ORDER_SENT'):
            logger.warning(f"🚫 [ГЛАВНЫЙ ЦИКЛ] Сигналы заблокированы: активный паспорт {active_passport.passport_id} в статусе {active_passport.status}")
            print(f"🚫 [ГЛАВНЫЙ ЦИКЛ] Сигналы заблокированы: активный паспорт {active_passport.passport_id}")
            return []

        signals = []
        
        # Проверяем, существуют ли вообще объекты стратегий
        print(f"🔍 Стратегии: wall_fade={self.wall_fade is not None}, absorption={self.absorption is not None}, breakout={self.breakout is not None}")
        
        for strategy in [self.wall_fade, self.absorption, self.breakout]:
            if strategy is None:
                print("⚠️ Одна из стратегий равна None!")
                continue
                
            print(f"🚨 Вызываем generate_signal для {strategy.__class__.__name__}...")
            
            # 🔥 ИЗМЕНЕНО: добавлен await, так как generate_signal теперь async
            signal = await strategy.generate_signal(context)
            
            if signal:
                print(f"✅ СИГНАЛ ПОЛУЧЕН ОТ {strategy.__class__.__name__}!")
                signals.append(signal)
            else:
                print(f"⚪ {strategy.__class__.__name__} вернула None")
                
        return signals

    async def _main_loop(self):
        logger.info("🔄 Main loop started")

        # 1. Инициализация Listen Key
        listen_key = await self.rest.get_listen_key()
        self._listen_key = listen_key
        logger.info(f"✅ Listen key obtained: {listen_key[:10]}...")

        # 2. Подключение основного WebSocket и первичная подписка
        await self.ws.connect()
        await self.ws.subscribe_depth(self.symbol)

        self._last_user_data_ts = time.time()
        self._last_price_update_ts = time.time()

        # 3. Обработчик переподключения ОСНОВНОГО WebSocket
        async def on_ws_reconnect():
            if getattr(self, '_is_reconnecting', False):
                return
            
            self._is_reconnecting = True
            try:
                # 🔥 ИСПРАВЛЕНО: Мы НЕ трогаем здесь listen_key и subscribe_user_data.
                # User Data Stream управляется своим фоновым процессом и callback-ом.
                # Здесь мы только восстанавливаем рыночные данные.
                await self.ws.subscribe_depth(self.symbol)
                await self.ws.subscribe_btc_streams()
                
                logger.info("✅ Основные потоки (depth, btc) переподписаны после reconnect.")
                
                await self.bus.publish(
                    event_type="SYNC_REQUEST",
                    source="platform",
                    payload={"symbol": self.symbol},
                    symbol=self.symbol
                )
            except Exception as e:
                logger.error(f"❌ Reconnect handler error: {e}")
            finally:
                self._is_reconnecting = False

        async def on_ws_reconnect_forced(event: Event):
            logger.warning(f"⚠️ WS reconnect forced for passport {event.payload.get('passport_id')}")
            if hasattr(self.ws, 'close'):
                await self.ws.close()

        self.bus.subscribe("WS_RECONNECT_FORCED", on_ws_reconnect_forced)

        # 4. Обработчики событий WebSocket
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
                    # 🔥 ЦЕНА НЕ ИЗ DIFF-СТАКАНА: без snapshot его mid даёт фантом (кейс 103.095).
                    # Источник цены — только сделки. Стакан обслуживает детекторы.
                    self._last_price_update_ts = time.time()
            except Exception as e:
                logger.error(f"Error processing depth update: {e}")
        self.ws.on("depthUpdate", on_depth_update)
        # 🔥 FIX: используем цену из Spot aggTrade (работает стабильно)
        # Diff depth stream требует initial snapshot — это сложная правка.
        # Trades уже работают и дают актуальную цену.
        async def on_normalized_trade(event: Event):
            price = event.payload.get('price', 0.0)
            if price > 0:
                self.ws_price = price
                self._last_price_update_ts = time.time()
                # 🔥 ЕДИНСТВЕННЫЙ источник PRICE_UPDATE для RiskManager — сделки
                await self.bus.publish(
                    event_type="PRICE_UPDATE",
                    source="main",
                    payload={'symbol': self.symbol, 'price': price, 'ts': time.time()},
                    symbol=self.symbol
                )
        
        self.bus.subscribe("TRADE_NORMALIZED_SOLUSDT", on_normalized_trade)
        
        # 🔥 НОВОЕ: Подписка VolumeRollingWindow на тики для агрегации свечей
        for symbol in self.monitored_symbols:
            self.bus.subscribe(f"TRADE_NORMALIZED_{symbol}", self.volume_rolling_windows[symbol].on_trade_event)
        logger.info(f"✅ VolumeRollingWindow subscribed to {self.monitored_symbols}")

        # 🔥 ИСПРАВЛЕНИЕ: Нормализация тиков BTC для DeltaMonitor
        # Возвращаем два аргумента, так как WS-адаптер передает именно их (event_type и data)
        async def on_btc_agg_trade(event_type: str, data: dict):
            # 1. Нормализуем формат (как это делается для SOLUSDT)
            normalized_payload = {
                "price": float(data.get("p", 0)),
                "qty": float(data.get("q", 0)),
                "is_buyer_maker": bool(data.get("m", False)),
                "timestamp": data.get("T", 0)
            }
            
            # 2. Публикуем ИМЕННО ТОТ event_type, на который подписан DeltaMonitor
            await self.bus.publish(
                event_type="TRADE_NORMALIZED_BTCUSDT",
                source="btc_ws_adapter",
                payload=normalized_payload,
                symbol="BTCUSDT"
            )
            
            # 3. Отладочный принт (чтобы убедиться, что тики идут)
            #print(f"🟠 [BTC TICK] Price: {normalized_payload['price']}, Qty: {normalized_payload['qty']}")

        # Подписка (сигнатуры теперь совпадают с тем, что ждет адаптер)
        self.ws.on("BTC_AGG_TRADE", on_btc_agg_trade)  # type: ignore[arg-type]

        # 5. 🔥 ИСПРАВЛЕНО: Callback для продления listen_key (дешевый PUT-запрос)
        async def refresh_listen_key_callback():
            try:
                if self._listen_key:
                    # Используем renew вместо get (дешевле по лимитам)
                    success = await self.rest.renew_listen_key(self._listen_key)
                    if success:
                        logger.info(f"✅ Listen key продлен: {self._listen_key[:10]}...")
                    else:
                        logger.warning("⚠️ renew_listen_key вернул False. Запрашиваем новый ключ...")
                        new_key = await self.rest.get_listen_key()
                        if new_key:
                            self._listen_key = new_key
                            logger.info(f"✅ Получен новый listen key: {self._listen_key[:10]}...")
                else:
                    logger.warning("️ listen_key is None. Запрашиваем новый...")
                    new_key = await self.rest.get_listen_key()
                    if new_key:
                        self._listen_key = new_key
            except Exception as e:
                logger.error(f"❌ Ошибка при обновлении listen key: {e}")

        # 6. Запуск User Data Stream с переданным callback-ом
        await self.ws.subscribe_user_data(listen_key, refresh_key_callback=refresh_listen_key_callback)
        logger.info(f"✅ User data stream subscribed: {listen_key[:10]}...")

        # 🔥 LIVE RECOVERY: после каждого reconnect User Data запускаем сверку
        async def on_user_data_reconnected():
            logger.warning("🔁 [LIVE_RECOVERY] User Data reconnected — запускаем сверку")
            try:
                await self.orchestrator.perform_live_recovery(self.symbol, minutes_back=30)
            except Exception as e:
                logger.error(f"❌ [LIVE_RECOVERY] Failed: {e}")
        
        self.ws.set_on_reconnect(on_user_data_reconnected)
        logger.info("✅ Live recovery callback registered")        
        
        await self.ws.subscribe_btc_streams()

        # 7. Монитор здоровья платформы и Guard (обновлённая версия)
        async def user_data_health_check():
            blind_start_time = None
            
            while getattr(self, '_running', True):
                try:
                    await asyncio.sleep(10) # Проверка каждые 10 секунд
                    
                    # 1. Оцениваем состояние каналов.
                    # 🔥 ИСПРАВЛЕНО: здоровье считаем по СВЕЖЕСТИ РЫНОЧНЫХ ДАННЫХ
                    # (depth-обновления идут ~10 раз/сек при живом соединении),
                    # а НЕ по user-data событиям: user-data молчит, когда нет ордеров,
                    # из-за чего платформа ложно уходила в DEGRADED при живом WS.
                    md_age = time.time() - getattr(self, '_last_price_update_ts', time.time())
                    rest_is_banned = getattr(self.rest, '_ban_active', lambda: False)()
                    ws_ok = md_age < 45
                    
                    # 🔥 НОВОЕ: проверяем живость User Data отдельно
                    user_data_age = time.time() - getattr(self.ws, '_last_user_data_ts', time.time())
                    has_active = self.passport_manager.get_active_by_symbol(self.symbol) is not None
                    user_data_dead = user_data_age > 180 and has_active  # > 3 мин при активных паспортах
                    
                    # 2. Определяем platform_health
                    # 🔥 BLIND = полная слепота: market data МЁРТВА и REST недоступен.
                    # Тишина User Data при живом REST — это DEGRADED: drift-монитор
                    # сверяет позицию через REST, guard живёт на ценах сделок.
                    if ws_ok and not rest_is_banned and not user_data_dead:
                        new_health = "HEALTHY"
                    elif not ws_ok and rest_is_banned:
                        new_health = "BLIND"
                    else:
                        new_health = "DEGRADED"

                    
                    # 3. Обновляем статусы во всех активных паспортах
                    active_passports = self.passport_manager.get_active()
                    for passport in active_passports:
                        old_health = getattr(passport, 'platform_health', "HEALTHY")
                        
                        # Обновляем health только при смене состояния, чтобы не спамить диск
                        if old_health != new_health:
                            passport.platform_health = new_health
                            passport.updated_at = datetime.now(timezone.utc).isoformat()
                            
                            # Обновляем guard_status
                            if new_health == "HEALTHY":
                                passport.guard_status = "active"
                                passport.add_timeline_event("HEALTH_RESTORED", "Connection restored, Guard active")
                            elif new_health == "DEGRADED":
                                # 🔥 FIX: Guard не спит при обрыве — переходит на REST-цену
                                passport.guard_status = "active_rest"
                                passport.add_timeline_event("HEALTH_DEGRADED", "WS lost, Guard switched to REST price feed")
                            elif new_health == "BLIND":
                                passport.guard_status = "active_rest"
                                passport.add_timeline_event("HEALTH_BLIND", "Total blindness, Guard on REST price feed")
                            
                            # Сохраняем изменение на диск
                            self.passport_repository.save(passport)
                            logger.warning(f"⚠️ [{passport.passport_id}] Health changed to {new_health}, Guard: {passport.guard_status}")

                    # 4. Логика безопасного перезапуска при полной слепоте
                    if new_health == "BLIND":
                        if blind_start_time is None:
                            blind_start_time = time.time()
                            logger.critical("🚨 CRITICAL: Platform went BLIND. Starting 60s countdown to safe restart...")
                        
                        elapsed = time.time() - blind_start_time
                        if elapsed > 60: # Если слепота длится больше 60 секунд
                            logger.critical("🛑 MAX BLINDNESS REACHED. Initiating SAFE RESTART (Exit Code 42).")
                            # Здесь можно добавить отправку уведомления в Telegram
                            self._running = False # Останавливаем циклы
                            sys.exit(42) # Специальный код для Watchdog-скрипта
                    else:
                        blind_start_time = None # Сбрасываем таймер, если связь вернулась

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Health check error: {e}")
                    await asyncio.sleep(1)

        # 8. Инициализация Cold Storage для Spot тиков
        import json
        self._cold_storage_dir = Path("data/cold_storage")
        self._cold_storage_dir.mkdir(parents=True, exist_ok=True)
        self._tick_file = self._cold_storage_dir / f"{self.symbol}_trades.jsonl"

        async def on_spot_trade(event_type: str, data: dict):
            normalized_payload = {
                "price": float(data.get("p", 0)),
                "qty": float(data.get("q", 0)),
                "is_buyer_maker": bool(data.get("m", False)),
                "timestamp": data.get("T", 0)
            }
            
            await self.bus.publish(
                event_type=f"TRADE_NORMALIZED_{self.symbol}",
                source="spot_ws_adapter",
                payload=normalized_payload,
                symbol=self.symbol
            )
            
            # 🔥 Features v2: кормим FeatureSet спот-лентой (синхронный on_trade)
            # Обёрнуто в свой try/except, чтобы падение Features не ломало ни шину,
            # ни cold storage. Семантика Binance: m=True → taker is buyer → is_buy=True.
            try:
                self.features.get(self.symbol).on_trade(
                    price=float(data.get("p", 0)),
                    qty=float(data.get("q", 0)),
                    is_buy=bool(data.get("m", False)),
                    ts=float(data.get("T", 0)) / 1000.0
                )
            except Exception as e:
                logger.warning(f"🔸 [FEATURES] on_trade упал (лента продолжает идти): {e}")

            try:
                side = "BUY" if not data.get("m") else "SELL"
                price = float(data.get("p", 0))
                qty = float(data.get("q", 0))
                ts_ms = data.get("T", 0)
                tick_record = {
                    "timestamp": float(ts_ms) / 1000.0,
                    "price": price,
                    "quantity": qty,
                    "value_usdt": price * qty,
                    "side": side
                }
                with open(self._tick_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(tick_record) + "\n")
            except Exception as e:
                logger.warning(f"Не удалось сохранить тик в Cold Storage: {e}")

        async def on_spot_depth(event_type: str, data: dict):
            # 1. Штатная публикация в шину (оставляем без изменений для совместимости)
            await self.bus.publish(event_type=event_type, source="spot_ws_adapter", payload=data, symbol=self.symbol)

            # 2. 🔥 Features v2: кормим FeatureSet стаканом
            try:
                # Binance depth payload: "b" = bids, "a" = asks. Формат: [[price, qty], ...]
                bids = [(float(p), float(q)) for p, q in data.get("b", [])]
                asks = [(float(p), float(q)) for p, q in data.get("a", [])]
                # "E" - это время события в миллисекундах, переводим в секунды
                ts = float(data.get("E", time.time() * 1000)) / 1000.0
                
                self.features.get(self.symbol).on_orderbook(bids, asks, ts)
            except Exception as e:
                logger.warning(f"🔸 [FEATURES] on_orderbook упал (стакан пропущен): {e}")
        # 🔥 WATCHDOG: следит за живостью фоновых задач

        # ==========================================
        # 🔥 3. Запуск фоновых задач СПОТ (Вернули на место!)
        # ==========================================
        logger.info("🚀 Запускаю задачи сбора спот-данных (aggTrade + depth)...")
        
        self._spot_trades_task = asyncio.create_task(
            self.ws.subscribe_spot_agg_trade(self.symbol, on_spot_trade)
        )
        self._spot_depth_task = asyncio.create_task(
            self.ws.subscribe_spot_depth(self.symbol, on_spot_depth)
        )
        
        # 🔥 НОВОЕ: Явная подписка на Spot тики BTC для нашего обработчика
        self._btc_spot_trades_task = asyncio.create_task(
            self.ws.subscribe_spot_agg_trade("BTCUSDT", on_btc_agg_trade)
        )
        
        logger.info("✅ Задачи спота (SOL + BTC) успешно созданы и запущены!")

        # ========================================================================
        # 🔥 НОВОЕ: Фоновая задача для пересчета VolumeContext (адаптивные фильтры)
        # ========================================================================
        async def context_updater_loop():
            logger.info("🚀 [DEBUG LOOP] context_updater_loop ЗАПУЩЕН!")
            interval = self.config.get("volume_context", {}).get("recalc_interval_sec", 60)
            logger.info(f"🚀 [DEBUG LOOP] Интервал пересчета: {interval} сек")
            
            while self._running:
                logger.info("🔄 [DEBUG LOOP] Начинаю итерацию цикла...")
                try:
                    for symbol in self.monitored_symbols:
                        rolling_window = self.volume_rolling_windows.get(symbol)
                        if rolling_window:
                            recent_volumes = rolling_window.get_recent_volumes(symbol, limit=50)
                            logger.info(f"🔍 [DEBUG LOOP] {symbol}: volumes_count={len(recent_volumes)}")
                            
                            if len(recent_volumes) < self.volume_context_manager.lookback:
                                logger.warning(f"⚠️ [DEBUG LOOP] {symbol}: Недостаточно данных ({len(recent_volumes)}/{self.volume_context_manager.lookback})")
                            
                            logger.info(f"📞 [DEBUG LOOP] Вызываю update_context для {symbol}...")
                            await self.volume_context_manager.update_context(recent_volumes)
                            logger.info(f"✅ [DEBUG LOOP] update_context для {symbol} завершен")
                except Exception as e:
                    logger.error(f"❌ [DEBUG LOOP] Error in context updater: {e}")
                await asyncio.sleep(interval)

        self._context_updater_task = asyncio.create_task(context_updater_loop())
        logger.info("✅ VolumeContext updater task started")
        # ========================================================================

        # ==========================================

        async def tasks_watchdog():
            while getattr(self, '_running', True):
                await asyncio.sleep(60)
                dead_tasks = []
                
                if hasattr(self, 'reconciler') and self.reconciler._task and self.reconciler._task.done():
                    dead_tasks.append("reconciler")
                    await self.reconciler.start()
                
                if hasattr(self, 'drift_monitor') and self.drift_monitor._task and self.drift_monitor._task.done():
                    dead_tasks.append("drift_monitor")
                    await self.drift_monitor.start(symbols=[self.symbol])
                
                if dead_tasks:
                    logger.warning(f"🚨 [WATCHDOG] Перезапущены мёртвые задачи: {', '.join(dead_tasks)}")
        
        self._watchdog_task = asyncio.create_task(tasks_watchdog())

        # 🔥 PRAGMATIC TRIO #1: Startup Prefetch
        # Загружаем exchangeInfo ОДИН раз, синхронно, ДО старта фоновых задач.
        # PositionSizer и стратегии будут брать данные из кэша,
        # не создавая параллельных REST-запросов на старте.
        try:
            await self.rest.get_exchange_info(self.symbol, force_refresh=True)
            logger.info(f"✅ [STARTUP PREFETCH] Exchange info loaded for {self.symbol}")
        except Exception as e:
            logger.warning(f"⚠️ [STARTUP PREFETCH] Failed: {type(e).__name__} — будет использован кэш/fallback")

        await self.drift_monitor.start(symbols=[self.symbol])        
        await self.orchestrator.start_stuck_orders_monitor()
        await self.reconciler.start()

        # 🔥 FIX: Guard на REST-цене во время обрывов WS.
        # Работает ТОЛЬКО когда health != HEALTHY и есть открытая позиция.
        # Один вызов positionRisk даёт mark price (для SL/TP) и размер (усыновление правды).
        async def _degraded_guard_loop():
            from adapters.binance_rest import REST_CALLER
            REST_CALLER.set("degraded_guard")
            POLL_SEC = 3.0
            rest_fail_streak = 0
            while self._running:
                await asyncio.sleep(POLL_SEC)
                # 🔥 FIX: гейт по свежести цены, а не по несуществующему self.platform_health
                # (health живёт на паспортах; getattr всегда возвращал HEALTHY, поллер не работал)
                price_fresh = (time.time() - getattr(self, '_last_price_update_ts', 0)) < 5.0
                if price_fresh:
                    rest_fail_streak = 0
                    continue
                passport = self.passport_manager.get_active_by_symbol(self.symbol)
                if not passport or passport.status not in ("OPEN", "PARTIAL_CLOSE"):
                    continue
                try:
                    pos = await self.rest.get_position(self.symbol)
                    if pos is None:
                        raise RuntimeError("position=None (ban or error)")
                    mark = float(pos.get('markPrice', 0) or pos.get('mark_price', 0) or 0)
                    size = abs(float(pos.get('size', 0) or 0))
                    if mark <= 0:
                        ob = await self.rest.get_orderbook(self.symbol, limit=5)
                        bids, asks = ob.get('bids', []), ob.get('asks', [])
                        if bids and asks:
                            mark = (float(bids[0][0]) + float(asks[0][0])) / 2
                    if mark <= 0:
                        raise RuntimeError("no price from REST")
                    rest_fail_streak = 0
                    # Усыновляем размер с биржи: лечит дрейф filled_qty во время обрыва
                    if size > 0 and abs(size - float(passport.position_size or 0)) > 0.001:
                        passport.position_size = size
                        passport.filled_qty = size
                        self.passport_repository.save(passport)
                    # 🔥 Усыновление цены входа с биржи: честный PnL при потерянных филлах
                    ex_entry = float(pos.get('entryPrice', 0) or 0)
                    if ex_entry > 0 and float(passport.position_entry_price or 0) <= 0:
                        passport.position_entry_price = ex_entry
                        passport.avg_price = ex_entry
                        self.passport_repository.save(passport)
                    # 🔥 Синхронизация remaining guard'а с биржей (каждые 3 сек в аварии)
                    _rm = getattr(self, 'risk_manager', None)
                    if _rm is not None and hasattr(_rm, 'sync_guard_remaining') and size > 0:
                        _rm.sync_guard_remaining(passport.passport_id, size)
                        logger.warning(
                            f"🔧 [{passport.passport_id}] DEGRADED: размер усыновлён с биржи = {size}"
                        )

                    # 🔥 FIX: DegradedGuard регистрирует guard если его нет
                    # Это страховка: даже если все пути регистрации провалились,
                    # поллинг закроет дыру за 3 секунды
                    if hasattr(self, 'risk_manager') and self.risk_manager:
                        if passport.passport_id not in self.risk_manager._guards:
                            logger.warning(
                                f"🔧 [{passport.passport_id}] DEGRADED: guard отсутствует, принудительная регистрация"
                            )
                            await self.risk_manager.ensure_guard_registered(passport)

                    await self.bus.publish(
                        event_type="PRICE_UPDATE",
                        source="rest_poll",
                        payload={'symbol': self.symbol, 'price': mark, 'ts': time.time()},
                        symbol=self.symbol
                    )
                except Exception as e:
                    rest_fail_streak += 1
                    logger.warning(f"⚠️ [DEGRADED_GUARD] poll failed ({rest_fail_streak}): {type(e).__name__}")
                    if rest_fail_streak == 3:
                        passport.guard_status = "suspended"
                        passport.add_timeline_event(
                            "GUARD_SUSPENDED", "REST price feed failed 3 times - true blindness"
                        )
                        self.passport_repository.save(passport)

        # Передаём risk_manager в DegradedGuard для принудительной регистрации guard
        if hasattr(self, 'risk_manager'):
            # DegradedGuard получает доступ к risk_manager через self
            pass

        self._degraded_guard_task = asyncio.create_task(_degraded_guard_loop())
        logger.info("✅ DegradedGuard poller started (REST price on WS outage)")

        logger.info("🔄 [STARTUP] Performing exchange state recovery (blocking)...")
        await self.orchestrator.perform_startup_recovery(self.symbol)
        logger.info("✅ [STARTUP] Recovery complete. Main loop starting.")

        # 10. Основной цикл платформы
        # 🔥 FIX: сбрасываем caller-тег после recovery, иначе он "заражает"
        # все запросы main loop (ордера помечались как startup_recovery)
        from adapters.binance_rest import REST_CALLER
        REST_CALLER.set("main_loop")

        last_log_time = 0
        last_position_check_time = 0

        while self._running:
            try:
                # 🔥 ФАЗА 1: цена ТОЛЬКО из живого WS-стакана. REST-fallback УДАЛЁН.
                # Если стакан протух (> depth_freshness_sec) — пропускаем итерацию:
                # решения на протухших данных запрещены, REST бережём только под ордера.
                depth_age = time.time() - getattr(self, '_last_price_update_ts', 0)

                if self.ws_price > 0 and depth_age <= self.depth_freshness_sec:
                    current_price = self.ws_price
                    self.last_known_price = current_price
                else:
                    self._stale_skips += 1
                    if self._stale_skips % 20 == 1:
                        logger.warning(
                            f"⚠️ [MAIN LOOP] WS-стакан протух ({depth_age:.1f}с) — "
                            f"итерация пропущена без REST. Всего пропусков: {self._stale_skips}"
                        )
                    await asyncio.sleep(0.5)
                    continue

                current_time = time.time()
                
                # 🔥 Проверка позиции закомментирована/удалена, чтобы не спамить REST
                if current_time - last_position_check_time >= 10:
                    # await self.rest.get_position(self.symbol) 
                    last_position_check_time = current_time

                if current_time - last_log_time >= 60:
                    logger.debug(f"🔄 Price: {current_price} (from {'WS' if self.ws_price > 0 else 'REST'})")
                    last_log_time = current_time

                if self.passport_manager.is_symbol_busy(self.symbol):
                    await asyncio.sleep(2)
                    continue

                spot_price = self.analytics.spot_price.get_current_price()
                hvn_micro = []
                hvn_macro = []
                if hasattr(self, 'extensions') and self.extensions and self.extensions.hvn:
                    hvn_micro = self.extensions.hvn.calculate_hvn(self.symbol, lookback_minutes=60)[:3]
                    hvn_macro = self.extensions.hvn.calculate_hvn(self.symbol, lookback_minutes=1440)[:3]

                context = {
                    'symbol': self.symbol,
                    'current_price': current_price,
                    'spot_price': spot_price,
                    'orderbook': self.ws_orderbook,
                    'hvn_micro': hvn_micro,
                    'hvn_macro': hvn_macro,
                    'delta': self.analytics.delta.get_metrics(),
                    'imbalance': self.analytics.imbalance.get_metrics(),
                    'trend': self.analytics.trend.get_context(),
                    'btc_delta_context': self.delta_contexts.get("BTCUSDT", {}),
                    'sol_delta_context': self.delta_contexts.get("SOLUSDT", {})
                }
                
                # 🔍 РЕНТГЕН: проверяем, ожили ли данные BTC
                btc_ctx = context.get('btc_delta_context', {})
                if btc_ctx.get('current_price', 0.0) > 0:
                    logger.info(f"🔍 [BTC FILTER] Активные данные: trend='{btc_ctx.get('trend')}', price={btc_ctx.get('current_price')}, delta={btc_ctx.get('delta_strength')}")
                else:
                    logger.warning("⚠️ [BTC FILTER] Данные BTC все еще нулевые! Проверь нормализацию тиков.")

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

            except asyncio.CancelledError:
                await asyncio.sleep(1)
                continue
            except Exception as e:
                import traceback
                logger.error(
                    f"Main loop error: {type(e).__name__}: {e}\n"
                    f"Traceback:\n{traceback.format_exc()}"
                )
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
        
        # 🔥 НОВОЕ: Запуск всех мониторов через Фабрику
        await MonitorFactory.start_all(self.delta_monitors)
        
        # 🔥 УРОВЕНЬ 5: Запуск AtrMonitor'ов
        logger.info(f"🚀 [Factory] Starting {len(self.atr_monitors)} ATR monitors...")
        for symbol, monitor in self.atr_monitors.items():
            await monitor.start()
            logger.info(f"✅ [Factory] Started ATR monitor for {symbol}")
        
        await self.orchestrator.start()
        await self._main_loop()

    async def stop(self):
        self._running = False
        
        tasks_to_cancel = []
        
        # 🔥 Исправлено: добавлена проверка на None для всех задач
        if self._ws_task is not None and not self._ws_task.done():
            self._ws_task.cancel()
            tasks_to_cancel.append(self._ws_task)
            
        if self._keep_alive_task is not None and not self._keep_alive_task.done():
            self._keep_alive_task.cancel()
            tasks_to_cancel.append(self._keep_alive_task)
            
        if self._health_check_task is not None and not self._health_check_task.done():
            self._health_check_task.cancel()
            tasks_to_cancel.append(self._health_check_task)
            
        if self._spot_trades_task is not None and not self._spot_trades_task.done():
            self._spot_trades_task.cancel()
            tasks_to_cancel.append(self._spot_trades_task)
            
        if self._spot_depth_task is not None and not self._spot_depth_task.done():
            self._spot_depth_task.cancel()
            tasks_to_cancel.append(self._spot_depth_task)
            
        # 🔥 Features v2: отмена задачи периодического логирования OUTPUT
        if self._features_output_task is not None and not self._features_output_task.done():
            self._features_output_task.cancel()
            tasks_to_cancel.append(self._features_output_task)
        
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        
        await self.orchestrator.stop()

        # 🔥 НОВОЕ: Остановка всех мониторов через Фабрику
        await MonitorFactory.stop_all(self.delta_monitors)
        
        # 🔥 УРОВЕНЬ 5: Остановка AtrMonitor'ов
        for symbol, monitor in self.atr_monitors.items():
            await monitor.stop()
                
        if hasattr(self, 'drift_monitor'):
            try:
                await self.drift_monitor.stop()
            except Exception as e:
                logger.error(f"Error stopping DriftMonitor: {e}")

        if hasattr(self, 'verifier'):
            try:
                await self.verifier.stop_all()
            except Exception as e:
                logger.error(f"Error stopping OrderVerifier: {e}")

        if hasattr(self, 'reconciler'):
            try:
                await self.reconciler.stop()
            except Exception as e:
                logger.error(f"Error stopping Reconciler: {e}")

        if hasattr(self, '_degraded_guard_task'):
            try:
                self._degraded_guard_task.cancel()
            except Exception:
                pass

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
    signal.signal(signal.SIGTERM, signal_handler(platform))

    try:
        await platform.run()
    except KeyboardInterrupt:
        print("\n⏹️ Остановка по команде пользователя (Ctrl+C)")
    except asyncio.CancelledError:
        print("\n⚠️ ВНИМАНИЕ: Главный цикл был принудительно отменён!")
        traceback.print_exc()
    except Exception as e:
        print(f"\n💥 КРИТИЧЕСКАЯ НЕПРЕДВИДЕННАЯ ОШИБКА: {e}")
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