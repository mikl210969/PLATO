"""
Шаг 4: DriftMonitor обнаруживает дрейф (T4).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from trading.drift_monitor import DriftMonitor
from trading.passport import TradePassport
from trading.passport_manager import PassportManager


@pytest.fixture
def setup():
    """Подготовка моков для теста DriftMonitor."""
    rest = MagicMock()
    rest.get_position = AsyncMock()
    rest.get_open_orders = AsyncMock()

    bus = MagicMock()
    published_events = []

    async def capture_publish(event_type, source, payload, symbol=""):
        published_events.append((event_type, source, payload))

    bus.publish = AsyncMock(side_effect=capture_publish)

    passport_manager = PassportManager()

    monitor = DriftMonitor(
        rest_client=rest,
        passport_manager=passport_manager,
        event_bus=bus,
        poll_interval=0.1  # Ускоренный опрос для теста
    )

    return {
        'rest': rest,
        'bus': bus,
        'passport_manager': passport_manager,
        'monitor': monitor,
        'published_events': published_events,
    }


@pytest.mark.asyncio
async def test_t4_drift_position_on_exchange_but_no_local_passport(setup):
    """
    Сценарий: Позиция на бирже есть (7 SOL), но локального паспорта нет.
    DriftMonitor должен обнаружить дрейф и опубликовать DRIFT_DETECTED.
    """
    monitor = setup['monitor']
    rest = setup['rest']
    published = setup['published_events']

    # Настраиваем моки: позиция есть, ордеров нет
    rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 7.0,
        'entry_price': 96.95,
    })
    rest.get_open_orders = AsyncMock(return_value=[])

    # Запускаем проверку
    await monitor._check_drift("SOLUSDT")

    # Проверяем: должно быть опубликовано DRIFT_DETECTED
    drift_events = [e for e in published if e[0] == "DRIFT_DETECTED"]
    assert len(drift_events) == 1

    event_type, source, payload = drift_events[0]
    assert source == "drift_monitor"
    assert payload["drift_type"] == "position_without_passport"
    assert payload["exchange_size"] == 7.0
    assert payload["local_size"] == 0.0

    # Флаг дрейфа должен быть установлен
    assert monitor.is_drift_active("SOLUSDT") is True


@pytest.mark.asyncio
async def test_t4b_drift_local_passport_but_no_position(setup):
    """
    Сценарий: Локальный паспорт с позицией (7 SOL), но на бирже позиции нет.
    DriftMonitor должен обнаружить дрейф.
    """
    monitor = setup['monitor']
    rest = setup['rest']
    pm = setup['passport_manager']
    published = setup['published_events']

    # Создаём локальный паспорт с позицией
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        position_size=7.0,
        position_entry_price=96.95
    )
    pm.update(passport)

    # Настраиваем моки: позиции нет
    rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'none',
        'size': 0.0,
        'entry_price': 0.0,
    })
    rest.get_open_orders = AsyncMock(return_value=[])

    await monitor._check_drift("SOLUSDT")

    drift_events = [e for e in published if e[0] == "DRIFT_DETECTED"]
    assert len(drift_events) == 1

    event_type, source, payload = drift_events[0]
    assert payload["drift_type"] == "passport_without_position"
    assert payload["exchange_size"] == 0.0
    assert payload["local_size"] == 7.0

    assert monitor.is_drift_active("SOLUSDT") is True


@pytest.mark.asyncio
async def test_t4c_no_drift_when_state_matches(setup):
    """
    Сценарий: Локальное состояние совпадает с биржей — дрейфа нет.
    """
    monitor = setup['monitor']
    rest = setup['rest']
    pm = setup['passport_manager']
    published = setup['published_events']

    # Создаём локальный паспорт
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        position_size=7.0,
        position_entry_price=96.95
    )
    pm.update(passport)

    # Настраиваем моки: состояние совпадает
    rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 7.0,
        'entry_price': 96.95,
    })
    rest.get_open_orders = AsyncMock(return_value=[])

    await monitor._check_drift("SOLUSDT")

    # DRIFT_DETECTED НЕ должен быть опубликован
    drift_events = [e for e in published if e[0] == "DRIFT_DETECTED"]
    assert len(drift_events) == 0

    # Флаг дрейфа должен быть False
    assert monitor.is_drift_active("SOLUSDT") is False