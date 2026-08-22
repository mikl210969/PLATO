"""
Интеграционный тест: Recovery при перезапуске с открытой позицией.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, Optional

from trading.recovery import RecoveryMixin
from core.event_bus import Event
from unittest.mock import AsyncMock, MagicMock, patch, ANY

class MockRecoveryMixin(RecoveryMixin):
    """Моковый класс для тестирования с правильными аннотациями."""
    
    def __init__(self):
        pass  # Атрибуты будут установлены в тестах


@pytest.fixture
def mock_components():
    """Создаем моки для всех зависимостей."""
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    
    passport_manager = MagicMock()
    repository = MagicMock()
    state_manager = MagicMock()
    json_logger = MagicMock()
    json_logger.log = MagicMock()
    
    # Создаем экземпляр миксина
    mixin = MockRecoveryMixin()
    mixin.bus = event_bus  # type: ignore
    mixin.passport_manager = passport_manager  # type: ignore
    mixin.repository = repository  # type: ignore
    mixin.state_manager = state_manager  # type: ignore
    mixin.json_logger = json_logger  # type: ignore
    mixin.config = {"trading": {"atr_value": 0.5}}  # type: ignore
    mixin._log = MagicMock()  # type: ignore
    
    return {
        'mixin': mixin,
        'event_bus': event_bus,
        'passport_manager': passport_manager,
        'repository': repository,
        'state_manager': state_manager,
        'json_logger': json_logger
    }


@pytest.fixture
def mock_trader():
    """Создаем мок трейдера."""
    trader = MagicMock()
    
    # Мок REST клиента
    trader.rest = MagicMock()
    trader.rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 7.0,
        'entry_price': 91.3,
        'unrealized_pnl': 0.75
    })
    
    # Мок метода расчета уровней
    trader.calculate_exit_levels = MagicMock(return_value={
        'sl_price': 95.05,
        'tp1_price': 90.3,
        'tp2_price': 89.8
    })
    
    return trader


@pytest.fixture
def mock_passport():
    """Создаем мок паспорта."""
    passport = MagicMock()
    passport.passport_id = "RECOVERY_PASSPORT_789"
    passport.symbol = "SOLUSDT"
    passport.side = "short"
    passport.entry_price = 91.3
    passport.position_size = 0.0
    passport.position_entry_price = 0.0
    passport.sl_price = 0.0
    passport.tp1_price = 0.0
    passport.tp2_price = 0.0
    passport.status = "SIGNAL_GENERATED"
    passport.timeline = []
    return passport


@pytest.mark.asyncio
async def test_recovery_creates_passport_for_open_position(mock_components, mock_trader, mock_passport):
    """
    Тест: При наличии открытой позиции на бирже Recovery создает паспорт.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    event_bus = mock_components['event_bus']
    state_manager = mock_components['state_manager']
    
    # Настраиваем моки
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore
    passport_manager.get_active_by_symbol.return_value = None  # Паспорта еще нет
    passport_manager.create.return_value = mock_passport
    
    # Вызываем метод восстановления
    await mixin.perform_startup_recovery(symbol="SOLUSDT")
    
    # Проверяем, что REST API был вызван
    mock_trader.rest.get_position.assert_called_once_with("SOLUSDT")
    
    # Проверяем, что паспорт был создан
    passport_manager.create.assert_called_once_with(
        symbol="SOLUSDT",
        signal_id=ANY,  # ✅ Динамический ID
        strategy="Recovery",
        side="short",
        entry_price=91.3,
        confidence=1.0
    )
    
    # Проверяем, что размеры позиции установлены
    assert mock_passport.position_size == 7.0
    assert mock_passport.position_entry_price == 91.3
    
    # Проверяем, что SL/TP рассчитаны
    mock_trader.calculate_exit_levels.assert_called_once_with(
        side="short",
        entry_price=91.3,
        atr_value=0.5
    )
    assert mock_passport.sl_price == 95.05
    assert mock_passport.tp1_price == 90.3
    assert mock_passport.tp2_price == 89.8
    
    # Проверяем, что статус изменен на OPEN
    assert mock_passport.status == "OPEN"
    
    # Проверяем, что passport был сохранен
    repository.save.assert_called_once_with(mock_passport)
    
    # Проверяем, что событие POSITION_OPENED опубликовано
    event_bus.publish.assert_called_once_with(
        event_type="POSITION_OPENED",
        source="recovery",
        payload={
            "passport_id": "RECOVERY_PASSPORT_789",
            "symbol": "SOLUSDT",
            "side": "short",
            "entry_price": 91.3,
            "position_size": 7.0
        },
        symbol="SOLUSDT"
    )


@pytest.mark.asyncio
async def test_recovery_skips_when_no_position(mock_components, mock_trader):
    """
    Тест: Если позиции нет на бирже, Recovery ничего не создает.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    
    # Настраиваем моки: позиция закрыта (size = 0)
    mock_trader.rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'none',
        'size': 0.0,
        'entry_price': 0.0,
        'unrealized_pnl': 0.0
    })
    
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore
    
    # Вызываем метод восстановления
    await mixin.perform_startup_recovery(symbol="SOLUSDT")
    
    # Проверяем, что паспорт НЕ был создан
    passport_manager.create.assert_not_called()
    
    # Проверяем, что ничего не сохранено
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_skips_when_symbol_is_none(mock_components):
    """
    Тест: Если символ не передан, Recovery пропускает выполнение.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    
    mixin.get_trader = MagicMock()  # type: ignore
    
    # Вызываем метод восстановления без символа
    await mixin.perform_startup_recovery(symbol=None)
    
    # Проверяем, что get_trader даже не вызывался
    mixin.get_trader.assert_not_called()
    
    # Проверяем, что паспорт НЕ был создан
    passport_manager.create.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_skips_when_passport_already_exists(mock_components, mock_trader, mock_passport):
    """
    Тест: Если паспорт уже существует, Recovery не создает дубликат.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    
    # Настраиваем моки: паспорт уже есть
    passport_manager.get_active_by_symbol.return_value = mock_passport
    
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore
    
    # Вызываем метод восстановления
    await mixin.perform_startup_recovery(symbol="SOLUSDT")
    
    # Проверяем, что новый паспорт НЕ был создан
    passport_manager.create.assert_not_called()