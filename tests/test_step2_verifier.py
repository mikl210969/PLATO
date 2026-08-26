"""
Шаг 2: OrderVerifier обнаруживает FILLED через REST (T2).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.order_verifier import OrderVerifier


@pytest.mark.asyncio
async def test_t2_verifier_detects_filled_via_rest():
    """WS молчит, но REST возвращает FILLED → публикуется ORDER_FILLED."""
    # Мок REST-клиента
    mock_rest = AsyncMock()
    mock_rest.get_order_status = AsyncMock(return_value={
        'status': 'FILLED',
        'executedQty': '7.0',
        'avgPrice': '96.95',
    })

    # Мок EventBus
    mock_bus = MagicMock()
    published_events = []

    async def capture_publish(event_type, source, payload, symbol=""):
        published_events.append((event_type, source, payload))

    mock_bus.publish = AsyncMock(side_effect=capture_publish)

    # Создаём верификатор
    verifier = OrderVerifier(
        rest_client=mock_rest,
        event_bus=mock_bus,
        poll_interval=0.1,  # Ускоренный опрос для теста
        max_attempts=5
    )

    # Запускаем проверку
    await verifier.start_verification(
        passport_id="PASS_TEST_001",
        order_id="12345",
        symbol="SOLUSDT",
        client_order_id="CLIENT_001"
    )

    # Ждём завершения
    await asyncio.sleep(0.5)

    # Проверяем
    assert len(published_events) == 1
    event_type, source, payload = published_events[0]
    assert event_type == "ORDER_FILLED"
    assert source == "rest_verifier"
    assert payload["executed_qty"] == 7.0
    assert payload["price"] == 96.95
    assert "dedup_key" in payload


@pytest.mark.asyncio
async def test_t2b_verifier_handles_canceled():
    """REST возвращает CANCELED → публикуется ORDER_CANCELED."""
    mock_rest = AsyncMock()
    mock_rest.get_order_status = AsyncMock(return_value={
        'status': 'CANCELED',
        'executedQty': '0',
        'avgPrice': '0',
    })

    mock_bus = MagicMock()
    published_events = []

    async def capture_publish(event_type, source, payload, symbol=""):
        published_events.append((event_type, source, payload))

    mock_bus.publish = AsyncMock(side_effect=capture_publish)

    verifier = OrderVerifier(
        rest_client=mock_rest,
        event_bus=mock_bus,
        poll_interval=0.1,
        max_attempts=3
    )

    await verifier.start_verification(
        passport_id="PASS_TEST_002",
        order_id="99999",
        symbol="SOLUSDT",
        client_order_id="CLIENT_002"
    )

    await asyncio.sleep(0.5)

    assert len(published_events) == 1
    event_type, source, payload = published_events[0]
    assert event_type == "ORDER_CANCELED"
    assert payload["status"] == "CANCELED"