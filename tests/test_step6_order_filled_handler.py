"""
Шаг 6: Обработчик ORDER_FILLED применяет переход и публикует POSITION_OPENED (T6).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.state_manager import StateManager


@pytest.fixture
def setup():
    bus = MagicMock()
    published_events = []

    async def capture_publish(event_type, source, payload, symbol=""):
        published_events.append((event_type, source, payload))

    bus.publish = AsyncMock(side_effect=capture_publish)

    passport_manager = PassportManager()
    state_manager = StateManager(passport_manager)
    repository = MagicMock()

    handlers = EventHandlersMixin()
    handlers.bus = bus
    handlers.passport_manager = passport_manager
    handlers.state_manager = state_manager
    handlers.repository = repository
    handlers.json_logger = MagicMock()
    handlers.config = {}
    handlers._log = MagicMock()
    handlers.verifier = MagicMock()
    handlers.verifier.cancel_verification = AsyncMock()

    return {
        'bus': bus,
        'passport_manager': passport_manager,
        'state_manager': state_manager,
        'handlers': handlers,
        'published_events': published_events,
    }


@pytest.mark.asyncio
async def test_t6_order_filled_updates_passport_and_publishes_position_opened(setup):
    """
    ORDER_FILLED (от любого источника) → паспорт переходит в OPEN,
    публикуется POSITION_OPENED для RiskManager.
    """
    handlers = setup['handlers']
    pm = setup['passport_manager']
    published = setup['published_events']

    # Создаём паспорт в состоянии ORDER_ACK с активным ордером
    passport = TradePassport(
        symbol="SOLUSDT",
        status="ORDER_ACK",
        signal_id="WallFade_96.09_001",
        strategy="WallFade",
        side="short",
        entry_price=96.09,
    )
    passport.orders = [{
        "order_id": 4188222393,
        "client_order_id": "WallFade_96.09_001",
        "status": "NEW",
        "type": "LIMIT",
        "side": "short",
        "price": 96.09,
        "quantity": 7.0
    }]
    pm.update(passport)

    # Создаём событие ORDER_FILLED (как будто от REST-верификатора)
    event = MagicMock()
    event.source = "rest_verifier"
    event.payload = {
        "client_order_id": "WallFade_96.09_001",
        "executed_qty": 7.0,
        "avg_price": 96.09,
    }

    await handlers._on_order_filled(event)

    # 1. Паспорт должен перейти в OPEN
    updated = pm.get(passport.passport_id)
    assert updated.status == "OPEN", f"Expected OPEN, got {updated.status}"
    assert updated.position_size == 7.0
    assert updated.position_entry_price == 96.09

    # 2. Должно быть опубликовано POSITION_OPENED
    pos_opened_events = [e for e in published if e[0] == "POSITION_OPENED"]
    assert len(pos_opened_events) == 1, f"Expected 1 POSITION_OPENED, got {len(pos_opened_events)}"

    _, source, payload = pos_opened_events[0]
    assert source == "rest_verifier"
    assert payload["passport_id"] == passport.passport_id
    assert payload["position_size"] == 7.0

    # 3. Верификатор должен быть отменён
    handlers.verifier.cancel_verification.assert_awaited_once_with(passport.passport_id)


@pytest.mark.asyncio
async def test_t6b_order_filled_idempotent_for_already_open(setup):
    """
    ORDER_FILLED по уже OPEN паспорту — идемпотентный no-op.
    Паспорт не ломается, POSITION_OPENED не публикуется повторно.
    """
    handlers = setup['handlers']
    pm = setup['passport_manager']
    published = setup['published_events']

    passport = TradePassport(symbol="SOLUSDT", status="OPEN")
    passport.orders = [{"order_id": 111, "client_order_id": "CL_111"}]
    passport.position_size = 7.0
    passport.position_entry_price = 96.95
    pm.update(passport)

    event = MagicMock()
    event.source = "rest_verifier"
    event.payload = {
        "client_order_id": "CL_111",
        "executed_qty": 7.0,
        "avg_price": 96.95,
    }

    await handlers._on_order_filled(event)

    # Паспорт остаётся OPEN
    updated = pm.get(passport.passport_id)
    assert updated.status == "OPEN"

    # POSITION_OPENED не должен быть опубликован повторно
    # (поскольку handle_event вернул False — идемпотентность)
    pos_opened_events = [e for e in published if e[0] == "POSITION_OPENED"]
    assert len(pos_opened_events) == 0, f"Expected 0 POSITION_OPENED, got {len(pos_opened_events)}"


@pytest.mark.asyncio
async def test_t6c_order_filled_for_unknown_passport_is_safe(setup):
    """
    ORDER_FILLED для неизвестного client_order_id не падает.
    """
    handlers = setup['handlers']
    published = setup['published_events']

    event = MagicMock()
    event.source = "rest_verifier"
    event.payload = {
        "client_order_id": "UNKNOWN_CLIENT_ID",
        "executed_qty": 7.0,
        "avg_price": 96.95,
    }

    # Не должно упасть
    await handlers._on_order_filled(event)

    # Паспорт не должен быть создан
    assert len(handlers.passport_manager.get_all()) == 0

    # POSITION_OPENED не публикуется
    pos_opened_events = [e for e in published if e[0] == "POSITION_OPENED"]
    assert len(pos_opened_events) == 0