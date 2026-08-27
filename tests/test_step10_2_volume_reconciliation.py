"""
Шаг 10.2: Умный ORDER_FILLED — рекonsиляция объёма при несовпадении (T18).
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

    return handlers, passport_manager, bus


@pytest.mark.asyncio
async def test_t18_volume_reconciliation_when_filled_qty_differs(setup):
    """
    Сценарий: WS partial дал 4.43, rest_verifier принёс 7.0.
    Паспорт OPEN с position_size=4.43 должен быть обновлён до 7.0.
    """
    handlers, pm, bus = setup

    # Создаём паспорт с частичным исполнением
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        signal_id="WallFade_101.15_001",
        strategy="WallFade",
        side="short",
        entry_price=101.15,
        position_size=4.43,  # WS partial
        position_entry_price=101.15,
    )
    # Добавляем ордер, чтобы поиск по client_order_id сработал
    passport.orders = [{
        "client_order_id": "WallFade_101.15_001",
        "order_id": 12345,
        "status": "NEW",
    }]
    pm.update(passport)

    # rest_verifier приносит FILLED 7.0
    event = MagicMock()
    event.source = "rest_verifier"
    event.payload = {
        "client_order_id": "WallFade_101.15_001",
        "executed_qty": 7.0,
        "avg_price": 101.15,
    }

    await handlers._on_order_filled(event)

    # Проверяем: объём должен быть реконсилирован
    updated = pm.get(passport.passport_id)
    assert updated.position_size == 7.0, f"Expected 7.0, got {updated.position_size}"

    # Проверяем: POSITION_OPENED должен быть опубликован (для RiskManager)
    position_opened_calls = [
        call for call in bus.publish.call_args_list
        if call.kwargs.get('event_type') == 'POSITION_OPENED'
    ]
    assert len(position_opened_calls) == 1, f"Expected 1 POSITION_OPENED, got {len(position_opened_calls)}"

    # Проверяем: volume_reconciled должен быть в логах
    reconcile_logs = [
        call for call in handlers._log.call_args_list
        if call.args[0] == 'volume_reconciled'
    ]
    assert len(reconcile_logs) == 1
    assert reconcile_logs[0].args[1]['old_size'] == 4.43
    assert reconcile_logs[0].args[1]['new_size'] == 7.0


@pytest.mark.asyncio
async def test_t18b_noop_when_volumes_match(setup):
    """
    Сценарий: Объёмы совпадают — обычный noop, без реконсиляции.
    """
    handlers, pm, bus = setup

    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        position_size=7.0,
    )
    passport.orders = [{"client_order_id": "test_001"}]
    pm.update(passport)

    event = MagicMock()
    event.source = "rest_verifier"
    event.payload = {
        "client_order_id": "test_001",
        "executed_qty": 7.0,
        "avg_price": 101.15,
    }

    await handlers._on_order_filled(event)

    # Проверяем: volume_reconciled НЕ должен быть в логах
    reconcile_logs = [
        call for call in handlers._log.call_args_list
        if call.args[0] == 'volume_reconciled'
    ]
    assert len(reconcile_logs) == 0

    # Проверяем: order_filled_noop должен быть в логах
    noop_logs = [
        call for call in handlers._log.call_args_list
        if call.args[0] == 'order_filled_noop'
    ]
    assert len(noop_logs) == 1