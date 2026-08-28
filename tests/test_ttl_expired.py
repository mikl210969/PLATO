"""
Интеграционный тест: TTL истек → лимитный ордер отменяется.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from typing import Any, Dict

from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from core.event_bus import Event


class MockEventHandlersMixin(EventHandlersMixin):
    """Моковый класс для тестирования с правильными аннотациями."""
    
    def __init__(self):
        # Инициализируем только то, что нужно
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 5.0
        # Остальные атрибуты будут установлены в тестах


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
    
    # 🔥 ДОБАВЛЕНО: мок для метода _log (он живет в Orchestrator, а не в миксине)
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
    """Создаем тестовый паспорт."""
    passport = MagicMock(spec=TradePassport)
    passport.passport_id = "TEST_PASSPORT_123"
    passport.symbol = "SOLUSDT"
    passport.status = "ORDER_SENT"
    passport.orders = [
        {
            "order_id": "12345",
            "client_order_id": "test_order",
            "status": "NEW"
        }
    ]
    passport.timeline = []
    passport.entry_price = 90.0
    passport.side = "short"
    return passport


@pytest.mark.asyncio
async def test_ttl_expired_cancels_order(mock_components, mock_passport):
    """
    Тест: При истечении TTL лимитный ордер должен быть отменен, 
    а статус паспорта изменен на CLOSED (чтобы освободить символ).
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    event_bus = mock_components['event_bus']

    # Настраиваем моки
    passport_manager.get.return_value = mock_passport
    mock_passport.status = "ORDER_ACK"  # Имитируем, что ордер еще не исполнился

    # Мок трейдера
    mock_trader = MagicMock()
    mock_trader.cancel_order = AsyncMock(return_value={
        'success': True,
        'order_id': '12345',
        'status': 'CANCELED'
    })
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore

    # Создаем событие TTL_EXPIRED
    event = Event(
        type="TTL_EXPIRED",
        source="lifecycle_manager",
        payload={
            "passport_id": "TEST_PASSPORT_123",
            "symbol": "SOLUSDT",
            "order_id": "12345"
        }
    )

    # Вызываем обработчик
    await mixin._on_ttl_expired(event)

    # Проверяем, что ордер был отправлен на отмену
    mock_trader.cancel_order.assert_called_once_with("SOLUSDT", "12345")

    # 🔥 ИСПРАВЛЕНО: Статус должен быть CLOSED, а причина отмены — TTL_EXPIRED
    assert mock_passport.status == "CLOSED"
    assert mock_passport.exit_reason == "TTL_EXPIRED"
    
    # Проверяем, что паспорт был сохранен после изменений
    repository.save.assert_called_once_with(mock_passport)


@pytest.mark.asyncio
async def test_ttl_expired_skip_if_already_filled(mock_components, mock_passport):
    """
    Тест: Если ордер уже исполнился (статус OPEN), TTL не должен его отменять.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    
    # Настраиваем паспорт как уже открытый
    mock_passport.status = "OPEN"
    passport_manager.get.return_value = mock_passport
    
    # Мок трейдера
    mock_trader = MagicMock()
    mock_trader.cancel_order = AsyncMock()
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore
    
    # Создаем событие TTL_EXPIRED
    event = Event(
        type="TTL_EXPIRED",
        source="lifecycle_manager",
        payload={
            "passport_id": "TEST_PASSPORT_123",
            "symbol": "SOLUSDT",
            "order_id": "12345"
        }
    )
    
    # Вызываем обработчик
    await mixin._on_ttl_expired(event)
    
    # Проверяем, что ордер НЕ был отменен
    mock_trader.cancel_order.assert_not_called()

@pytest.mark.asyncio
async def test_ttl_expired_partial_fill(mock_components, mock_passport):
    """
    Тест: При частичном исполнении и истечении TTL остаток отменяется, позиция остается.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    event_bus = mock_components['event_bus']
    
    # Настраиваем паспорт как частично исполненный
    mock_passport.status = "PARTIALLY_FILLED"
    mock_passport.position_size = 2.1  # Исполнено 30% из 7.0
    mock_passport.position_entry_price = 90.0
    mock_passport.side = "short"
    mock_passport.exit_reason = ""    
    passport_manager.get.return_value = mock_passport
    
    # Мок трейдера
    mock_trader = MagicMock()
    mock_trader.cancel_order = AsyncMock(return_value={
        'success': True,
        'order_id': '12345',
        'status': 'CANCELED'
    })
    mixin.get_trader = MagicMock(return_value=mock_trader)  # type: ignore
    
    # Создаем событие TTL_EXPIRED
    event = Event(
        type="TTL_EXPIRED",
        source="lifecycle_manager",
        payload={
            "passport_id": "TEST_PASSPORT_123",
            "symbol": "SOLUSDT",
            "order_id": "12345"
        }
    )
    
    # Вызываем обработчик
    await mixin._on_ttl_expired(event)
    
    # Проверяем, что ордер был отменен (остаток)
    mock_trader.cancel_order.assert_called_once_with("SOLUSDT", "12345")
    
    # Ядро осознанно оставляет статус PARTIALLY_FILLED: остаток позиции
    # передаётся под управление RiskManager через событие POSITION_OPENED
    # (см. ветку PARTIALLY_FILLED в _on_ttl_expired).
    assert mock_passport.status == "PARTIALLY_FILLED"
    # Факт отмены остатка зафиксирован в timeline
    # Факт отмены остатка зафиксирован в timeline (timeline — реальный список)
    events = [e['event'] for e in mock_passport.timeline]
    assert "TTL_EXPIRED_PARTIAL_FILL" in events
    assert mock_passport.position_size == 2.1  # Размер сохранился
    assert mock_passport.exit_reason == ""  # Позиция открыта
    
    # Проверяем, что passport был сохранен
    repository.save.assert_called_once_with(mock_passport)
    
    # Проверяем, что событие POSITION_OPENED было опубликовано (не POSITION_CLOSED!)
    event_bus.publish.assert_called_once_with(
        event_type="POSITION_OPENED",
        source="lifecycle_manager",
        payload={
            "passport_id": "TEST_PASSPORT_123",
            "symbol": "SOLUSDT",
            "side": "short",
            "entry_price": 90.0,
            "position_size": 2.1
        },
        symbol="SOLUSDT"
    )    