"""
Шаг 5: Pre-Trade Gate отклоняет сигналы при дрейфе (T5).
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.drift_monitor import DriftMonitor
from unittest.mock import AsyncMock  # в шапку файла

@pytest.fixture
def setup():
    """Подготовка моков для теста Pre-Trade Gate."""
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
    handlers._last_signal_time = {}
    handlers._signal_cooldown = 5.0

    # Мок трейдера с AsyncMock для execute_order
    mock_trader = MagicMock()
    mock_trader.calculate_exit_levels = MagicMock(return_value={
        'sl_price': 97.47, 'tp1_price': 96.97, 'tp2_price': 96.72
    })
    mock_trader.execute_order = AsyncMock(return_value={"success": True})
    handlers.get_trader = lambda symbol: mock_trader

    # DriftMonitor с активным дрейфом
    drift_monitor = MagicMock()
    drift_monitor.is_drift_active = MagicMock(return_value=True)
    handlers.drift_monitor = drift_monitor

    # OrderVerifier (без активных задач)
    verifier = MagicMock()
    verifier._active_tasks = {}
    handlers.verifier = verifier

    return {
        'bus': bus,
        'passport_manager': passport_manager,
        'handlers': handlers,
        'drift_monitor': drift_monitor,
        'verifier': verifier,
        'published_events': published_events,
    }


@pytest.mark.asyncio
async def test_t5_signal_rejected_when_drift_active(setup):
    """
    Сценарий: DriftMonitor сообщает об активном дрейфе → сигнал отклоняется.
    """
    handlers = setup['handlers']
    drift_monitor = setup['drift_monitor']
    pm = setup['passport_manager']

    signal = MagicMock()
    signal.symbol = "SOLUSDT"
    signal.signal_id = "WallFade_96.95_001"
    signal.strategy = "WallFade"
    signal.side = "short"
    signal.entry_price = 96.95
    signal.confidence = 0.7

    event = MagicMock()
    event.type = "SIGNAL_GENERATED"
    event.payload = {"signal": signal}

    await handlers._on_signal(event)

    passports = pm.get_all()
    assert len(passports) == 0, f"Expected 0 passports, got {len(passports)}"

    log_calls = [call for call in handlers._log.call_args_list]
    rejection_logs = [call for call in log_calls if 'signal_rejected_drift_active' in str(call)]
    assert len(rejection_logs) == 1, f"Expected 1 rejection log, got {len(rejection_logs)}"


@pytest.mark.asyncio
async def test_t5_signal_accepted_when_no_drift(setup):
    """
    Сценарий: Дрейфа нет → сигнал принимается, паспорт создаётся.
    """
    handlers = setup['handlers']
    drift_monitor = setup['drift_monitor']
    pm = setup['passport_manager']

    drift_monitor.is_drift_active = MagicMock(return_value=False)

    # Ядро await'ит verifier.start_verification — обычный MagicMock это не умеет
    handlers.verifier.start_verification = AsyncMock()

    signal = MagicMock()
    signal.symbol = "SOLUSDT"
    signal.signal_id = "WallFade_96.95_002"
    signal.strategy = "WallFade"
    signal.side = "short"
    signal.entry_price = 96.95
    signal.confidence = 0.7

    event = MagicMock()
    event.type = "SIGNAL_GENERATED"
    event.payload = {"signal": signal}

    await handlers._on_signal(event)

    passports = pm.get_all()
    assert len(passports) == 1, f"Expected 1 passport, got {len(passports)}"
    assert passports[0].signal_id == "WallFade_96.95_002"


@pytest.mark.asyncio
async def test_t5_signal_rejected_when_verifier_active(setup):
    """
    Сценарий: OrderVerifier запущен для паспорта (состояние гонки: паспорт только что закрылся,
    но верификатор ещё не отменился) → сигнал отклоняется.
    """
    handlers = setup['handlers']
    drift_monitor = setup['drift_monitor']
    verifier = setup['verifier']
    pm = setup['passport_manager']

    drift_monitor.is_drift_active = MagicMock(return_value=False)

    # Создаём паспорт в статусе CLOSED (не активен для is_symbol_busy),
    # но с активным верификатором (состояние гонки)
    passport = TradePassport(symbol="SOLUSDT", status="CLOSED")
    pm.update(passport)

    # Имитируем активную задачу верификации
    verifier._active_tasks = {passport.passport_id: MagicMock()}

    signal = MagicMock()
    signal.symbol = "SOLUSDT"
    signal.signal_id = "WallFade_96.95_003"
    signal.strategy = "WallFade"
    signal.side = "short"
    signal.entry_price = 96.95
    signal.confidence = 0.7

    event = MagicMock()
    event.type = "SIGNAL_GENERATED"
    event.payload = {"signal": signal}

    await handlers._on_signal(event)

    # Проверяем: новый паспорт НЕ должен быть создан (только существующий CLOSED)
    passports = pm.get_all()
    assert len(passports) == 1, f"Expected 1 passport, got {len(passports)}"
    assert passports[0].passport_id == passport.passport_id
    assert passports[0].status == "CLOSED"

    # Проверяем: лог должен содержать rejection
    log_calls = [call for call in handlers._log.call_args_list]
    rejection_logs = [call for call in log_calls if 'signal_rejected_verifier_active' in str(call)]
    assert len(rejection_logs) == 1, f"Expected 1 rejection log, got {len(rejection_logs)}"