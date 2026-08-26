"""
Шаг 3: TTL с ошибкой -2011 корректно верифицирует реальный статус через REST (T3).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.event_handlers import EventHandlersMixin


@pytest.fixture
def setup():
    """Собираем миксин вручную: атрибуты присваиваются извне (как в Orchestrator)."""
    bus = MagicMock()
    published_events = []

    async def capture_publish(event_type, source, payload, symbol=""):
        published_events.append((event_type, source, payload))

    bus.publish = AsyncMock(side_effect=capture_publish)

    passport_manager = PassportManager()
    repository = MagicMock()
    state_manager = MagicMock()

    handlers = EventHandlersMixin()
    handlers.bus = bus
    handlers.passport_manager = passport_manager
    handlers.repository = repository
    handlers.state_manager = state_manager
    handlers.json_logger = MagicMock()
    handlers.config = {}
    handlers._log = MagicMock()

    mock_trader = MagicMock()
    mock_trader.rest = MagicMock()
    mock_trader.rest.get_order_status = AsyncMock()
    mock_trader.cancel_order = AsyncMock()
    handlers.get_trader = lambda symbol: mock_trader

    return {
        'bus': bus,
        'passport_manager': passport_manager,
        'handlers': handlers,
        'trader': mock_trader,
        'published_events': published_events,
    }


@pytest.mark.asyncio
async def test_t3_ttl_2011_but_filled_on_exchange(setup):
    """
    Сценарий: TTL сработал, cancel_order вернул -2011,
    но REST get_order_status вернул FILLED → публикуется ORDER_FILLED.
    Паспорт НЕ должен стать CLOSED.
    """
    handlers = setup['handlers']
    trader = setup['trader']
    published = setup['published_events']
    pm = setup['passport_manager']

    passport = TradePassport(
        symbol="SOLUSDT",
        status="ORDER_ACK",
        signal_id="sig_001",
        strategy="WallFade",
        side="short",
        entry_price=96.95,
    )
    passport.orders = [{
        "order_id": 12345,
        "client_order_id": "WF_96.95_001",
        "status": "NEW",
        "type": "LIMIT",
        "side": "short",
        "price": 96.95,
        "quantity": 7.0
    }]
    pm.update(passport)

    trader.cancel_order = AsyncMock(return_value={
        "success": False,
        "error": "Binance API error: Unknown order sent. (code: -2011)",
        "code": -2011,
    })
    trader.rest.get_order_status = AsyncMock(return_value={
        "status": "FILLED",
        "executedQty": "7.0",
        "avgPrice": "96.95",
    })

    event = MagicMock()
    event.payload = {
        "passport_id": passport.passport_id,
        "symbol": "SOLUSDT",
        "order_id": 12345,
    }
    await handlers._on_ttl_expired(event)

    filled_events = [e for e in published if e[0] == "ORDER_FILLED"]
    assert len(filled_events) == 1, f"Expected 1 ORDER_FILLED, got {len(filled_events)}"

    event_type, source, payload = filled_events[0]
    assert source == "ttl_verifier"
    assert payload["executed_qty"] == 7.0
    assert payload["avg_price"] == 96.95
    assert "dedup_key" in payload

    closed_events = [e for e in published if e[0] == "POSITION_CLOSED"]
    assert len(closed_events) == 0, f"Expected 0 POSITION_CLOSED, got {len(closed_events)}"

    updated = pm.get(passport.passport_id)
    assert updated.status != "CLOSED"


@pytest.mark.asyncio
async def test_t3b_ttl_real_cancellation_still_works(setup):
    """
    Сценарий: cancel_order вернул success=True → паспорт корректно закрывается.
    """
    handlers = setup['handlers']
    trader = setup['trader']
    published = setup['published_events']
    pm = setup['passport_manager']

    passport = TradePassport(symbol="SOLUSDT", status="ORDER_ACK")
    passport.orders = [{"order_id": 99999, "client_order_id": "WF_001"}]
    pm.update(passport)

    trader.cancel_order = AsyncMock(return_value={"success": True, "status": "CANCELED"})

    event = MagicMock()
    event.payload = {
        "passport_id": passport.passport_id,
        "symbol": "SOLUSDT",
        "order_id": 99999,
    }
    await handlers._on_ttl_expired(event)

    closed_events = [e for e in published if e[0] == "POSITION_CLOSED"]
    assert len(closed_events) == 1

    updated = pm.get(passport.passport_id)
    assert updated.status == "CLOSED"
    assert updated.exit_reason == "TTL_EXPIRED"


@pytest.mark.asyncio
async def test_t3c_ttl_2011_with_canceled_on_exchange(setup):
    """
    Сценарий: cancel_order вернул -2011, REST вернул CANCELED → паспорт закрывается.
    """
    handlers = setup['handlers']
    trader = setup['trader']
    published = setup['published_events']
    pm = setup['passport_manager']

    passport = TradePassport(symbol="SOLUSDT", status="ORDER_ACK")
    passport.orders = [{"order_id": 55555, "client_order_id": "WF_555"}]
    pm.update(passport)

    trader.cancel_order = AsyncMock(return_value={
        "success": False,
        "error": "Unknown order sent. (code: -2011)",
        "code": -2011,
    })
    trader.rest.get_order_status = AsyncMock(return_value={
        "status": "CANCELED",
        "executedQty": "0",
        "avgPrice": "0",
    })

    event = MagicMock()
    event.payload = {
        "passport_id": passport.passport_id,
        "symbol": "SOLUSDT",
        "order_id": 55555,
    }
    await handlers._on_ttl_expired(event)

    closed_events = [e for e in published if e[0] == "POSITION_CLOSED"]
    assert len(closed_events) == 1

    updated = pm.get(passport.passport_id)
    assert updated.status == "CLOSED"