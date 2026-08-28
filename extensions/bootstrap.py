"""Безопасная инициализация Extensions. Единая точка входа для main.py.
Любое исключение ловится и НЕ пробивает в Stable Core."""
import asyncio
import logging

from extensions.data_layer.db_manager import DatabaseManager
from extensions.analytics.whale_detector import WhaleDetector
from extensions.analytics.spoofing_detector import SpoofingDetector
from extensions.analytics.basis_monitor import BasisMonitor
from extensions.analytics.hvn_calculator import HVNCalculator
from extensions.bridge.detector_bridge import DetectorBridge

logger = logging.getLogger(__name__)

# Имена событий WS-адаптера — УТОЧНИТЬ по фрагменту publish в binance_ws.py
MARKET_TRADE_TYPES = ("MARKET_TRADE", "TRADE", "trade")
MARKET_BOOK_TYPES = ("MARKET_ORDERBOOK", "ORDERBOOK", "depth")


class ExtensionsBundle:
    def __init__(self, db, whale, spoof, basis, hvn, bridge, tasks):
        self.db, self.whale, self.spoof = db, whale, spoof
        self.basis, self.hvn, self.bridge, self.tasks = basis, hvn, bridge, tasks


def init_extensions_safe(bus, symbol: str = "SOLUSDT"):
    try:
        return _init(bus, symbol)
    except Exception as e:
        logger.error(f"Extensions init failed — ядро продолжает БЕЗ них: {e}",
                     exc_info=True)
        return None


def _init(bus, symbol) -> ExtensionsBundle:
    db = DatabaseManager()
    whale = WhaleDetector()
    spoof = SpoofingDetector()
    basis = BasisMonitor(
        db_manager=db,
        on_event=lambda t, d: logger.info(
            f"[BASIS] {t} | {d.get('basis_pct', 0):.3f}%"
            + (" | 🚨 BASIS STOP" if d.get("basis_stop_triggered") else "")),
    )
    hvn = HVNCalculator(db_manager=db)

    async def publish_to_bus(event_type: str, payload: dict):
        await bus.publish(event_type=event_type, source="extensions",
                          payload=payload, symbol=symbol)

    bridge = DetectorBridge(whale, spoof, publish=publish_to_bus)
    for t in MARKET_TRADE_TYPES:
        bus.subscribe(t, bridge.on_market_event)
    for t in MARKET_BOOK_TYPES:
        bus.subscribe(t, bridge.on_market_event)

    tasks = []
    try:
        loop = asyncio.get_running_loop()
        tasks.append(loop.create_task(hvn.run_background_job([symbol], 600)))
    except RuntimeError:
        logger.warning("Нет running loop — HVN-джоб стартует позже")

    logger.info("Extensions инициализированы: Whale, Spoofing, Basis, HVN, Bridge")
    return ExtensionsBundle(db, whale, spoof, basis, hvn, bridge, tasks)