"""
Интеграционный тест: REST fallback при потере события WS.
Сценарий 3.3: Ордер исполнился, но WS пропустил событие → Monitor подхватывает через REST.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from trading.monitor import MonitorMixin
from trading.passport import TradePassport
from core.types import PassportStatus


class MockMonitorMixin(MonitorMixin):
    """Моковый класс для тестирования с предзаполненными атрибутами."""
    def __init__(self):
        self._stuck_orders_task = None
        self._running = True
        self._log = MagicMock()
        self.passport_manager = MagicMock()
        self.repository = MagicMock()
        self.state_manager = MagicMock()
        self.bus = MagicMock()
        self.bus.publish = AsyncMock()
        self.get_trader = MagicMock()


@pytest.fixture
def mock_passport_order_ack():
    """Создаем паспорт в статусе ORDER_ACK, созданный 15 секунд назад."""
    passport = MagicMock(spec=TradePassport)
    passport.passport_id = "TEST_PASSPORT_REST_001"
    passport.symbol = "SOLUSDT"
    passport.status = PassportStatus.ORDER_ACK.value
    passport.side = "short"
    passport.entry_price = 91.0
    passport.position_size = 0.0
    passport.position_entry_price = 0.0
    passport.orders = [
        {
            "order_id": 4185748100,
            "client_order_id": "WallFade_91.0_100_1787312149729",
            "status": "NEW",
            "type": "LIMIT",
            "side": "short",
            "price": 91.0,
            "quantity": 7.0
        }
    ]
    passport.timeline = []
    passport.created_at = datetime.now(timezone.utc) - timedelta(seconds=15)
    return passport


@pytest.fixture
def mock_trader():
    """Создаем мок трейдера с REST API."""
    trader = MagicMock()
    trader.get_order_status = AsyncMock(return_value={
        'status': 'FILLED',
        'price': '91.0',
        'avgPrice': '91.0',
        'executedQty': '7.0',
        'qty': '7.0'
    })
    return trader


@pytest.mark.asyncio
async def test_rest_fallback_detects_filled_order(mock_passport_order_ack, mock_trader):
    """
    Тест: Если WS пропустил событие FILLED, Monitor через REST подхватывает исполненный ордер.
    """
    mixin = MockMonitorMixin()
    mixin.passport_manager.get_active.return_value = [mock_passport_order_ack]
    mixin.get_trader.return_value = mock_trader

    # 🔥 КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: мок state_manager должен менять статус паспорта, как в реальности.
    # Это предотвращает бесконечный опрос одного и того же ордера.
    def mock_handle_event(passport, event, data):
        if event == "ORDER_FILLED":
            passport.status = "OPEN"
            passport.position_size = data['quantity']
            passport.position_entry_price = data['price']
        elif event == "ORDER_CANCELED":
            passport.status = "CANCELED"

    mixin.state_manager.handle_event.side_effect = mock_handle_event

    original_sleep = asyncio.sleep
    iteration = 0
    
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            mixin._running = False  # Останавливаем цикл после первой полной итерации
        await original_sleep(0.001)  # Используем оригинальный sleep, чтобы избежать рекурсии

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await mixin._check_stuck_orders_loop()

    # Теперь get_order_status будет вызван ровно 1 раз
    mock_trader.get_order_status.assert_called_once_with(
        symbol="SOLUSDT",
        client_order_id="WallFade_91.0_100_1787312149729"
    )
    
    mixin.state_manager.handle_event.assert_called_with(
        mock_passport_order_ack, "ORDER_FILLED", {'price': 91.0, 'quantity': 7.0}
    )
    
    mixin.repository.save.assert_called_once_with(mock_passport_order_ack)
    
    mixin.bus.publish.assert_called_once_with(
        event_type="POSITION_OPENED",
        source="orchestrator",
        payload={
            "passport_id": "TEST_PASSPORT_REST_001",
            "symbol": "SOLUSDT",
            "side": "short",
            "entry_price": 91.0,
            "position_size": 7.0
        },
        symbol="SOLUSDT"
    )


@pytest.mark.asyncio
async def test_rest_fallback_skips_when_order_not_filled(mock_passport_order_ack, mock_trader):
    """
    Тест: Если ордер всё ещё в статусе NEW, Monitor не должен менять паспорт.
    """
    mixin = MockMonitorMixin()
    mixin.passport_manager.get_active.return_value = [mock_passport_order_ack]
    
    mock_trader.get_order_status = AsyncMock(return_value={
        'status': 'NEW',
        'price': '91.0',
        'executedQty': '0',
    })
    mixin.get_trader.return_value = mock_trader

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            mixin._running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await mixin._check_stuck_orders_loop()

    # Статус не должен был измениться, так как ордер еще NEW
    assert mock_passport_order_ack.status == "ORDER_ACK"
    mixin.state_manager.handle_event.assert_not_called()
    mixin.repository.save.assert_not_called()
    mixin.bus.publish.assert_not_called()


@pytest.mark.asyncio
async def test_rest_fallback_handles_canceled_order(mock_passport_order_ack, mock_trader):
    """
    Тест: Если ордер отменен на бирже, Monitor должен пометить паспорт как CANCELED.
    """
    mixin = MockMonitorMixin()
    mixin.passport_manager.get_active.return_value = [mock_passport_order_ack]
    
    mock_trader.get_order_status = AsyncMock(return_value={
        'status': 'CANCELED',
        'price': '91.0',
        'executedQty': '0',
    })
    mixin.get_trader.return_value = mock_trader

    # 🔥 Мок меняет статус, как в реальности
    def mock_handle_event(passport, event, data):
        if event == "ORDER_CANCELED":
            passport.status = "CANCELED"

    mixin.state_manager.handle_event.side_effect = mock_handle_event

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            mixin._running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await mixin._check_stuck_orders_loop()

    mixin.state_manager.handle_event.assert_called_once_with(
        mock_passport_order_ack, "ORDER_CANCELED", {"details": "REST fallback: CANCELED"}
    )
    mixin.repository.save.assert_called_once_with(mock_passport_order_ack)