"""
Шаг 1: Тесты дедупликации EventBus (T1).
"""
import asyncio
import pytest
from core.event_bus import EventBus


@pytest.mark.asyncio
async def test_t1_duplicate_events_deduped():
    """Один и тот же ключ публикуется дважды -> обработчик вызван 1 раз."""
    bus = EventBus()
    calls = []

    async def handler(event):
        calls.append(event.payload["status"])

    bus.subscribe("ORDER_TRADE_UPDATE", handler)

    payload = {
        "dedup_key": "OTU:4188072396:FILLED:7.0:1787725526000",
        "status": "FILLED",
    }
    await bus.publish("ORDER_TRADE_UPDATE", "ws_adapter", payload, symbol="SOLUSDT")
    await bus.publish("ORDER_TRADE_UPDATE", "rest_verifier", payload, symbol="SOLUSDT")
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    assert bus.dedup_hits == 1


@pytest.mark.asyncio
async def test_t1b_distinct_events_pass_through():
    """Разные ключи (и события без ключа) не дедуплицируются."""
    bus = EventBus()
    calls = []

    async def handler(event):
        calls.append(event.payload["status"])

    bus.subscribe("ORDER_TRADE_UPDATE", handler)

    await bus.publish("ORDER_TRADE_UPDATE", "ws", {"dedup_key": "K1", "status": "NEW"})
    await bus.publish("ORDER_TRADE_UPDATE", "ws", {"dedup_key": "K2", "status": "FILLED"})
    await bus.publish("ORDER_TRADE_UPDATE", "ws", {"status": "FILLED"})  # без ключа
    await asyncio.sleep(0.05)

    assert len(calls) == 3
    assert bus.dedup_hits == 0