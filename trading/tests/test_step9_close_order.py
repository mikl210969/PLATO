"""
Шаг 9: Парсинг passport_id из client_order_id закрытия (T15).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.state_manager import StateManager


@pytest.fixture
def setup():
    bus = MagicMock()
    bus.publish = AsyncMock()

    passport_manager = PassportManager()
    state_manager = StateManager(passport_manager)

    handlers = EventHandlersMixin()
    handlers.bus = bus
    handlers.passport_manager = passport_manager
    handlers.state_manager = state_manager
    handlers.repository = MagicMock()
    handlers.json_logger = MagicMock()
    handlers.config = {}
    handlers._log = MagicMock()
    handlers.verifier = MagicMock()
    handlers.verifier.cancel_verification = AsyncMock()

    return handlers, passport_manager


@pytest.mark.asyncio
async def test_t15_partial_close_updates_passport(setup):
    """
    Сценарий: TP1 ордер с client_order_id CLOSE_TP1_HIT_PASS_XXX
    должен обновить паспорт (position_size, статус PARTIAL_CLOSE).
    """
    handlers, pm = setup

    # Создаём паспорт с открытой позицией 7.0 SOL
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        signal_id="WallFade_97.06_001",
        strategy="WallFade",
        side="short",
        entry_price=97.06,
        position_size=7.0,
        position_entry_price=97.06,
    )
    pm.update(passport)

    # Событие закрытия TP1 (50% позиции = 3.5 SOL)
    event = MagicMock()
    event.source = "ws_adapter"
    event.payload = {
        "client_order_id": "CLOSE_TP1_HIT_PASS_20260826_115647_b4992c",
        "executed_qty": 3.5,
        "avg_price": 96.81,
    }

    await handlers._on_order_filled(event)

    # Проверяем: позиция должна уменьшиться
    updated = pm.get(passport.passport_id)
    assert updated.position_size == 3.5, f"Expected 3.5, got {updated.position_size}"
    assert updated.status == "PARTIAL_CLOSE"


@pytest.mark.asyncio
async def test_t15b_full_close_marks_passport_closed(setup):
    """
    Сценарий: Закрытие всей позиции (TP2 или SL) должно перевести паспорт в CLOSED.
    """
    handlers, pm = setup

    passport = TradePassport(
        symbol="SOLUSDT",
        status="PARTIAL_CLOSE",
        position_size=3.5,
    )
    pm.update(passport)

    event = MagicMock()
    event.source = "ws_adapter"
    event.payload = {
        "client_order_id": "CLOSE_TP2_HIT_PASS_20260826_115647_b4992c",
        "executed_qty": 3.5,
        "avg_price": 96.56,
    }

    await handlers._on_order_filled(event)

    updated = pm.get(passport.passport_id)
    assert updated.position_size == 0.0
    assert updated.status == "CLOSED"