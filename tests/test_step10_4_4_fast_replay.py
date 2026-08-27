"""
Шаг 10.4.4: Быстрый replay через allOrders (T24).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.state_manager import StateManager
from trading.orchestrator import Orchestrator


@pytest.fixture
def setup(tmp_path):
    bus = MagicMock()
    bus.publish = AsyncMock()
    pm = PassportManager()
    repo = PassportRepository(logs_dir=str(tmp_path))
    sm = StateManager(pm)

    orch = Orchestrator(
        config={"trading": {"atr_value": 0.5}},
        event_bus=bus,
        passport_manager=pm,
        passport_repository=repo,
        state_manager=sm,
        json_logger=MagicMock(),
    )
    return orch, pm


@pytest.mark.asyncio
async def test_t24_replay_uses_allorders_map(setup):
    """Replay применяет закрытие к загруженному паспорту без get_order_status."""
    orch, pm = setup

    passport = TradePassport(
        symbol="SOLUSDT", status="OPEN",
        position_size=7.0, position_entry_price=101.5, side="short",
    )
    pm.update(passport)

    rest = MagicMock()
    rest.get_user_trades = AsyncMock(return_value=[
        {"orderId": 111, "qty": "3.5", "quoteQty": "355.0", "price": "101.4"},
    ])
    rest.get_all_orders = AsyncMock(return_value=[
        {"orderId": 111, "origClientOrderId": f"C1_{passport.passport_id}"},
    ])
    rest.get_order_status = AsyncMock(return_value=None)  # НЕ должен вызываться

    applied = await orch._replay_user_trades("SOLUSDT", rest)

    assert applied == 1
    updated = pm.get(passport.passport_id)
    assert updated.position_size == 3.5
    rest.get_order_status.assert_not_called()  # 🔥 ни одного лишнего REST-запроса


@pytest.mark.asyncio
async def test_t24b_closed_passports_skipped_silently(setup):
    """Закрытия для незагруженных (CLOSED) паспортов не применяются и не шумят."""
    orch, pm = setup

    rest = MagicMock()
    rest.get_user_trades = AsyncMock(return_value=[
        {"orderId": 222, "qty": "7.0", "quoteQty": "710.0"},
    ])
    rest.get_all_orders = AsyncMock(return_value=[
        {"orderId": 222, "origClientOrderId": "CS_PASS_20260827_000000_deadbe"},
    ])

    applied = await orch._replay_user_trades("SOLUSDT", rest)

    assert applied == 0