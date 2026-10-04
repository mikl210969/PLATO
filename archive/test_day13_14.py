"""Тест моста: сырой формат Binance + нормализованный, события в шине."""
import asyncio, logging, time
from types import SimpleNamespace
from extensions.analytics.whale_detector import WhaleDetector
from extensions.analytics.spoofing_detector import SpoofingDetector
from extensions.bridge.detector_bridge import DetectorBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("test_day13_14")
published = []

async def fake_publish(event_type, payload):
    published.append((event_type, payload))
    log.info(f">>> BUS: {event_type}")

async def main():
    bridge = DetectorBridge(WhaleDetector(), SpoofingDetector(), publish=fake_publish)
    base_ms = int(time.time() * 1000)

    log.info("--- 1) Сырой Binance trade: 110 розничных ---")
    for i in range(110):
        await bridge.on_market_event(SimpleNamespace(type="MARKET_TRADE", payload={
            "e": "trade", "s": "SOLUSDT", "p": "140.0", "q": "3.0",
            "m": i % 2 == 0, "T": base_ms + i * 250}))

    log.info("--- 2) Киты: сырой + нормализованный (кластер) ---")
    await bridge.on_market_event(SimpleNamespace(type="MARKET_TRADE", payload={
        "e": "trade", "p": "140.1", "q": "180.0", "m": False, "T": base_ms + 28000}))
    await bridge.on_market_event(SimpleNamespace(type="trade", payload={
        "price": 140.2, "quantity": 150.0, "value_usdt": 21000.0,
        "aggressor_side": "BUY", "timestamp": (base_ms + 29000) / 1000}))

    log.info("--- 3) Стакан: стена живёт 6 сек ---")
    for i in range(60):
        bids = [[139.9 - j * 0.1, 100.0] for j in range(10)]
        asks = [[140.1 + j * 0.1, 100.0] for j in range(10)]
        bids[3] = [139.6, 1000.0]
        await bridge.on_market_event(SimpleNamespace(type="MARKET_ORDERBOOK", payload={
            "bids": bids, "asks": asks, "E": base_ms + 30000 + i * 100}))

    log.info(f"Stats: {bridge.get_stats()}")
    types = [p[0] for p in published]
    log.info(f"События в шине: {types}")

    assert bridge.get_stats()["trades"] == 112
    assert bridge.get_stats()["books"] == 60
    for expected in ("WHALE_BUY", "WHALE_CLUSTER", "WALL_DETECTED", "WALL_CONFIRMED"):
        assert expected in types, f"Нет {expected}"
    log.info("✅ Тест моста пройден: оба формата распознаны, события в шине")

asyncio.run(main())