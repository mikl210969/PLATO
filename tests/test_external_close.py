"""
Интеграционный тест: Внешнее закрытие позиции (ручное закрытие на бирже).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from typing import Dict

from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from core.event_bus import Event


class MockEventHandlersMixin(EventHandlersMixin):
    """Моковый класс для тестирования."""
    
    def __init__(self):
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 5.0


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
    mixin = MockEventHandlersMixin()
    mixin.bus = event_bus  # type: ignore
    mixin.passport_manager = passport_manager  # type: ignore
    mixin.repository = repository  # type: ignore
    mixin.state_manager = state_manager  # type: ignore
    mixin.json_logger = json_logger  # type: ignore
    mixin.config = {"trading": {"ttl_seconds": 300}}  # type: ignore
    mixin.get_trader = MagicMock()  # type: ignore
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
def mock_passport():
    """Создаем тестовый паспорт с открытой позицией."""
    passport = MagicMock(spec=TradePassport)
    passport.passport_id = "TEST_PASSPORT_456"
    passport.symbol = "SOLUSDT"
    passport.status = "OPEN"
    passport.position_size = 7.0
    passport.position_entry_price = 90.0
    passport.side = "short"
    passport.orders = [
        {
            "order_id": "12345",
            "client_order_id": "test_order",
            "status": "FILLED"
        }
    ]
    passport.timeline = []
    return passport


@pytest.mark.asyncio
async def test_external_close_detected(mock_components, mock_passport):
    """
    Тест: При ручном закрытии позиции на бирже паспорт должен перейти в EXTERNAL_CLOSE.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    
    # Настраиваем моки
    passport_manager.get_active_by_symbol.return_value = mock_passport
    
    # Создаем событие ACCOUNT_UPDATE с размером позиции = 0 (позиция закрыта)
    event = Event(
        type="ACCOUNT_UPDATE",
        source="ws_adapter",
        payload={
            "a": {
                "P": [
                    {
                        "s": "SOLUSDT",
                        "pa": "0"  # Позиция закрыта
                    }
                ]
            }
        }
    )
    
    # Вызываем обработчик
    await mixin._on_account_update(event)
    
    # Проверяем, что статус паспорта изменился
    assert mock_passport.status == "EXTERNAL_CLOSE"
    assert mock_passport.position_size == 0.0
    assert mock_passport.exit_reason == "EXTERNAL_CLOSE"
    
    # Проверяем, что в timeline добавлена запись
    assert len(mock_passport.timeline) == 1
    assert mock_passport.timeline[0]["event"] == "STATUS: EXTERNAL_CLOSE"
    assert "manually or liquidated" in mock_passport.timeline[0]["details"]
    
    # Проверяем, что passport был сохранен
    repository.save.assert_called_once_with(mock_passport)


@pytest.mark.asyncio
async def test_external_close_not_triggered_when_position_exists(mock_components, mock_passport):
    """
    Тест: Если позиция всё ещё открыта на бирже, паспорт не должен меняться.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    
    # Настраиваем моки
    passport_manager.get_active_by_symbol.return_value = mock_passport
    
    # Создаем событие ACCOUNT_UPDATE с размером позиции > 0 (позиция открыта)
    event = Event(
        type="ACCOUNT_UPDATE",
        source="ws_adapter",
        payload={
            "a": {
                "P": [
                    {
                        "s": "SOLUSDT",
                        "pa": "7.0"  # Позиция всё ещё открыта
                    }
                ]
            }
        }
    )
    
    # Вызываем обработчик
    await mixin._on_account_update(event)
    
    # Проверяем, что статус паспорта НЕ изменился
    assert mock_passport.status == "OPEN"
    assert mock_passport.position_size == 7.0
    
    # Проверяем, что passport НЕ был сохранен (изменений нет)
    repository.save.assert_not_called()


@pytest.mark.asyncio
async def test_external_close_not_triggered_for_wrong_symbol(mock_components, mock_passport):
    """
    Тест: Если событие пришло для другого символа, паспорт не должен меняться.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    
    #  ИСПРАВЛЕНИЕ: мок возвращает passport только для SOLUSDT, для других — None
    def get_passport_by_symbol(symbol):
        if symbol == "SOLUSDT":
            return mock_passport
        return None
    
    passport_manager.get_active_by_symbol.side_effect = get_passport_by_symbol
    
    # Создаем событие ACCOUNT_UPDATE для другого символа
    event = Event(
        type="ACCOUNT_UPDATE",
        source="ws_adapter",
        payload={
            "a": {
                "P": [
                    {
                        "s": "BTCUSDT",  # Другой символ
                        "pa": "0"
                    }
                ]
            }
        }
    )
    
    # Вызываем обработчик
    await mixin._on_account_update(event)
    
    # Проверяем, что статус паспорта НЕ изменился
    assert mock_passport.status == "OPEN"
    assert mock_passport.position_size == 7.0
    
    # Проверяем, что passport НЕ был сохранен
    repository.save.assert_not_called()