"""Безопасная инициализация Extensions. Единая точка входа для main.py.
Любое исключение ловится и НЕ пробивает в Stable Core."""
import asyncio
import logging
import time

from extensions.data_layer.db_manager import DatabaseManager
from extensions.analytics.whale_detector import WhaleDetector
from extensions.analytics.spoofing_detector import SpoofingDetector
from extensions.analytics.basis_monitor import BasisMonitor
from extensions.analytics.hvn_calculator import HVNCalculator
from extensions.bridge.detector_bridge import DetectorBridge

logger = logging.getLogger(__name__)

MARKET_TRADE_TYPES = ("MARKET_TRADE", "TRADE", "trade")
MARKET_BOOK_TYPES = ("MARKET_ORDERBOOK", "ORDERBOOK", "depth")


class ExtensionsBundle:
    def __init__(self, db, whale, spoof, basis, hvn, bridge, tasks):
        self.db = db
        self.whale = whale
        self.spoof = spoof
        self.basis = basis
        self.hvn = hvn
        self.bridge = bridge
        self.tasks = tasks


async def _hvn_background_job(symbol: str, hvn_calc: HVNCalculator):
    """Фоновая задача для периодического пересчета Micro и Macro HVN."""
    while True:
        try:
            await asyncio.sleep(120) # Пересчет каждые 2 минуты
            
            # 1. Micro HVN (60 минут) - для точки входа
            micro_hvns = hvn_calc.calculate_hvn(symbol, lookback_minutes=60)
            if micro_hvns:
                hvn_calc.save_hvn_to_db(symbol, micro_hvns, lookback_minutes=60)
                
            # 2. Macro HVN (24 часа = 1440 минут) - для глобального фильтра
            macro_hvns = hvn_calc.calculate_hvn(symbol, lookback_minutes=1440)
            if macro_hvns:
                hvn_calc.save_hvn_to_db(symbol, macro_hvns, lookback_minutes=1440)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче HVN: {e}")
            await asyncio.sleep(60)


def init_extensions_safe(bus, symbol: str = "SOLUSDT"):
    try:
        return _init(bus, symbol)
    except Exception as e:
        logger.error(f"Extensions init failed — ядро продолжает БЕЗ них: {e}", exc_info=True)
        return None


def _init(bus, symbol) -> ExtensionsBundle:
    db = DatabaseManager()
    whale = WhaleDetector()
    spoof = SpoofingDetector()
    
    basis = BasisMonitor(
        db_manager=db,
        on_event=lambda t, d: logger.info(
            f"[BASIS] {t} | {d.get('basis_pct', 0):.3f}%"
            + (" | 🚨 BASIS STOP" if d.get("basis_stop_triggered") else "")
        ),
    )
    
    # 🔥 ИСПРАВЛЕНИЕ 1: Явно передаем cold_storage_path и параметры
    hvn = HVNCalculator(
        db_manager=db,
        cold_storage_path="data/cold_storage",
        price_step_pct=0.001,
        min_prominence_pct=10.0, # Строгий порог для продакшена
        top_n=5
    )

    async def publish_to_bus(event_type: str, payload: dict):
        await bus.publish(event_type=event_type, source="extensions", payload=payload, symbol=symbol)

    bridge = DetectorBridge(whale, spoof, publish=publish_to_bus)
    for t in MARKET_TRADE_TYPES:
        bus.subscribe(t, bridge.on_market_event)
    for t in MARKET_BOOK_TYPES:
        bus.subscribe(t, bridge.on_market_event)

    tasks = []
    try:
        loop = asyncio.get_running_loop()
        # 🔥 ИСПРАВЛЕНИЕ 2: Используем нашу новую функцию вместо несуществующего hvn.run_background_job
        tasks.append(loop.create_task(_hvn_background_job(symbol, hvn)))
    except RuntimeError:
        logger.warning("Нет running loop — HVN-джоб стартует позже")

    logger.info("Extensions инициализированы: Whale, Spoofing, Basis, HVN, Bridge")
    return ExtensionsBundle(db, whale, spoof, basis, hvn, bridge, tasks)