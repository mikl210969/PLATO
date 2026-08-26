"""
Шаг 7: Сбой отправки ордера -> паспорт FAILED, символ освобождается (T8).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.event_handlers import EventHandlersMixin
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
    handlers._last_signal_time = {}
    handlers._signal_cooldown = 0.0  # отключаем cooldown для теста

    mock_trader = MagicMock()
    mock_trader.calculate_exit_levels = MagicMock(return_value={
        'sl_price': 97.76, 'tp1_price': 97.26, 'tp2_price': 97.01
    })
    mock_trader.execute_order = AsyncMock(return_value={
        "success": False,
        "error": "[WinError 121] The semaphore timeout period has expired",
    })
    handlers.get_trader = lambda symbol: mock_trader

    return handlers, passport_manager, mock_trader


def make_signal(signal_id):
    signal = MagicMock()
    signal.symbol = "SOLUSDT"
    signal.signal_id = signal_id
    signal.strategy = "WallFade"
    signal.side = "short"
    signal.entry_price = 97.51
    signal.confidence = 0.7
    event = MagicMock()
    event.type = "SIGNAL_GENERATED"
    event.payload = {"signal": signal}
    return event


@pytest.mark.asyncio
async def test_t8_failed_send_marks_passport_failed_and_frees_symbol(setup):
    """Сбой execute_order -> паспорт FAILED, символ НЕ занят."""
    handlers, pm, trader = setup

    await handlers._on_signal(make_signal("WF_fail_001"))

    passports = pm.get_all()
    assert len(passports) == 1
    assert passports[0].status == "FAILED", f"Expected FAILED, got {passports[0].status}"

    # КРИТИЧЕСКАЯ ПРОВЕРКА: символ должен быть свободен
    assert pm.is_symbol_busy("SOLUSDT") is False


@pytest.mark.asyncio
async def test_t8b_next_signal_accepted_after_failed_send(setup):
    """После FAILED следующий сигнал принимается и создаёт новый паспорт."""
    handlers, pm, trader = setup

    # Первый сигнал падает
    await handlers._on_signal(make_signal("WF_fail_002"))
    assert pm.get_all()[0].status == "FAILED"

    # Второй сигнал: отправка успешна
    trader.execute_order = AsyncMock(return_value={
        "success": True, "order_id": 12345,
        "client_order_id": "WF_ok_002", "status": "NEW",
    })
    handlers.verifier = MagicMock()
    handlers.verifier.start_verification = AsyncMock()

    await handlers._on_signal(make_signal("WF_ok_002"))

    passports = pm.get_all()
    assert len(passports) == 2, f"Expected 2 passports, got {len(passports)}"
    statuses = {p.signal_id: p.status for p in passports}
    assert statuses["WF_ok_002"] == "ORDER_SENT"