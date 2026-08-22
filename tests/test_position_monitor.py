"""
Интеграционные тесты: PositionMonitor (TP, SL, Break-Even, Basis Stop).
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict

from trading.position_monitor import PositionMonitor
from trading.passport import TradePassport


class MockPositionMonitor(PositionMonitor):
    """Моковый класс для тестирования с предзаполненными атрибутами."""
    def __init__(self):
        self._monitor_task = None
        self._position_running = True
        self._last_price_check = {}
        
        self._log = MagicMock()
        self.passport_manager = MagicMock()
        self.repository = MagicMock()
        self.state_manager = MagicMock()
        self.bus = MagicMock()
        self.bus.publish = AsyncMock()
        self.get_trader = MagicMock()
        # ATR = 2.0. min_profit_for_be = 0.5 * 2.0 = 1.0 (1.0%)
        # Порог Basis Stop = 1.5%
        self.config = {'trading': {'atr_value': 2.0}}


@pytest.fixture
def mock_passport_short():
    """Создаём мок паспорта для SHORT позиции с математически непересекающимися границами."""
    passport = MagicMock(spec=TradePassport)
    passport.passport_id = "TEST_PASSPORT_PM_001"
    passport.symbol = "SOLUSDT"
    passport.status = "OPEN"
    passport.side = "short"
    passport.entry_price = 100.0
    passport.position_entry_price = 100.0
    passport.position_size = 10.0
    
    # Все уровни строго внутри зоны < 1.5% от 100.0 (т.е. между 98.5 и 101.5)
    passport.sl_price = 101.2    # 1.2% убытка (сработает SL, но НЕ Basis Stop)
    passport.tp1_price = 98.8    # 1.2% прибыли (сработает TP1, но НЕ Basis Stop)
    passport.tp2_price = 98.6    # 1.4% прибыли (сработает TP2, но НЕ Basis Stop)
    
    passport.tp1_closed = False
    passport.tp2_closed = False
    passport.sl_moved_to_be = False
    passport.timeline = []
    return passport


@pytest.fixture
def mock_trader():
    """Создаём мок трейдера с REST и execute_order."""
    trader = MagicMock()
    trader.rest = MagicMock()
    trader.rest.get_orderbook = AsyncMock()
    trader.execute_order = AsyncMock(return_value={
        'success': True,
        'order_id': 99999,
        'status': 'FILLED'
    })
    return trader


def setup_price_mock(mock_trader, current_price: float):
    """Вспомогательная функция для настройки возврата текущей цены."""
    mock_trader.rest.get_orderbook.return_value = {
        'bids': [[str(current_price - 0.01), "100"]],
        'asks': [[str(current_price + 0.01), "100"]]
    }


@pytest.mark.asyncio
async def test_position_monitor_sl_hit(mock_passport_short, mock_trader):
    """
    Сценарий: Цена достигла SL (101.3 > 101.2). Убыток 1.3% (< 1.5% Basis Stop).
    """
    monitor = MockPositionMonitor()
    monitor.get_trader.return_value = mock_trader
    monitor.passport_manager.get_active.return_value = [mock_passport_short]
    
    setup_price_mock(mock_trader, current_price=101.3)

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            monitor._position_running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await monitor._position_monitor_loop()

    mock_trader.execute_order.assert_called_once_with(
        symbol="SOLUSDT",
        side="buy",
        quantity=10.0,
        order_type="market",
        reduce_only=True,
        client_order_id="SL_HIT_TEST_PASSPORT_PM_001",
        passport_id="TEST_PASSPORT_PM_001"
    )
    
    assert mock_passport_short.status == "CLOSED"
    assert mock_passport_short.exit_reason == "SL_HIT"
    monitor.repository.save.assert_called()


@pytest.mark.asyncio
async def test_position_monitor_tp1_hit(mock_passport_short, mock_trader):
    """
    Сценарий: Цена достигла TP1 (98.6 < 98.8). Прибыль 1.4% (< 1.5% Basis Stop).
    """
    monitor = MockPositionMonitor()
    monitor.get_trader.return_value = mock_trader
    monitor.passport_manager.get_active.return_value = [mock_passport_short]
    
    setup_price_mock(mock_trader, current_price=98.7)  # Строго между TP1 (98.8) и TP2 (98.6)

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            monitor._position_running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await monitor._position_monitor_loop()

    mock_trader.execute_order.assert_called_once_with(
        symbol="SOLUSDT",
        side="buy",
        quantity=5.0,  # 50% от 10.0
        order_type="market",
        reduce_only=True,
        client_order_id="TP1_TEST_PASSPORT_PM_001",
        passport_id="TEST_PASSPORT_PM_001"
    )
    
    assert mock_passport_short.position_size == 5.0
    assert mock_passport_short.tp1_closed is True
    assert mock_passport_short.sl_moved_to_be is True
    monitor.repository.save.assert_called()


@pytest.mark.asyncio
async def test_position_monitor_break_even_shift(mock_passport_short, mock_trader):
    """
    Сценарий: Цена в прибыли на 1.0% (99.0). 
    99.0 > 98.8 (TP1 НЕ сработает).
    Прибыль 1.0% >= 1.0% (BE сработает, т.к. min_profit = 0.5 * ATR 2.0 = 1.0).
    1.0% < 1.5% (Basis Stop НЕ сработает).
    """
    monitor = MockPositionMonitor()
    monitor.get_trader.return_value = mock_trader
    monitor.passport_manager.get_active.return_value = [mock_passport_short]
    
    setup_price_mock(mock_trader, current_price=99.0)

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            monitor._position_running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await monitor._position_monitor_loop()

    # Ордер на закрытие НЕ должен был быть отправлен
    mock_trader.execute_order.assert_not_called()
    
    # SL должен был сдвинуться по формуле trailing: current_price (99.0) + 0.25 * ATR (2.0) = 99.5
    assert mock_passport_short.sl_price == 99.5
    assert mock_passport_short.sl_moved_to_be is True
    monitor.repository.save.assert_called()


@pytest.mark.asyncio
async def test_position_monitor_basis_stop(mock_passport_short, mock_trader):
    """
    Сценарий: Цена резко пошла против позиции на 2.0% (102.0 > 101.5).
    Экстренное полное закрытие.
    """
    monitor = MockPositionMonitor()
    monitor.get_trader.return_value = mock_trader
    monitor.passport_manager.get_active.return_value = [mock_passport_short]
    
    setup_price_mock(mock_trader, current_price=102.0)

    original_sleep = asyncio.sleep
    iteration = 0
    async def mock_sleep(delay):
        nonlocal iteration
        iteration += 1
        if iteration > 1:
            monitor._position_running = False
        await original_sleep(0.001)

    with patch('asyncio.sleep', side_effect=mock_sleep):
        await monitor._position_monitor_loop()

    mock_trader.execute_order.assert_called_once()
    call_kwargs = mock_trader.execute_order.call_args.kwargs
    assert call_kwargs['quantity'] == 10.0
    assert call_kwargs['client_order_id'].startswith("BASIS_STOP_")
    
    assert mock_passport_short.status == "CLOSED"
    assert mock_passport_short.exit_reason == "BASIS_STOP"