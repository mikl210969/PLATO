"""
Тесты для проверок, внедренных сегодня:
1. Корректная обработка частичных исполнений (Partial Fill) и расчет VWAP.
2. Синхронизация состояния паспорта при обновлениях.
3. Устойчивость WebSocket (Exponential Backoff, Ping/Pong, строгая очистка).
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call
from datetime import datetime, timezone

# Импортируем тестируемые компоненты (пути могут потребовать корректировки под твою структуру)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from trading.passport import TradePassport
from core.types import PassportStatus


# ============================================================================
# БЛОК 1: Тесты частичного исполнения (Partial Fill) и VWAP
# ============================================================================

class TestPartialFillLogic:
    """Проверка логики накопления объема и расчета средневзвешенной цены."""

    def _simulate_order_update(self, passport, executed_qty: float, price: float, status: str, target_qty: float = 7.0):
        """Вспомогательная функция, эмулирующая логику из order_handler.py"""
        # 1. Обновляем filled_qty
        passport.filled_qty = getattr(passport, 'filled_qty', 0.0) + abs(executed_qty)
        
        # 2. Рассчитываем VWAP
        old_total = getattr(passport, 'avg_price', 0.0) * (passport.filled_qty - abs(executed_qty))
        new_total = price * abs(executed_qty)
        passport.avg_price = (old_total + new_total) / passport.filled_qty if passport.filled_qty > 0 else price
        
        # 3. Обновляем остатки
        passport.remaining_order_qty = max(0.0, target_qty - passport.filled_qty)
        passport.position_size = passport.filled_qty
        passport.position_entry_price = passport.avg_price
        
        # 4. Пересчитываем PnL
        passport.calculate_projected_pnls()
        
        # 5. Управление Guard
        if status == 'FILLED' or passport.remaining_order_qty < 0.01:
            passport.guard_status = "active"
        else:
            passport.guard_status = "pending_full_fill"

    def test_single_partial_fill(self):
        """Тест: Первое частичное исполнение 2.0 из 7.0 SOL по цене 100.0"""
        passport = TradePassport(symbol="SOLUSDT", side="short", entry_price=100.0, tp1_price=99.0)
        passport.target_size = 7.0
        
        self._simulate_order_update(passport, executed_qty=2.0, price=100.0, status="PARTIALLY_FILLED")
        
        assert passport.filled_qty == 2.0
        assert passport.avg_price == 100.0
        assert passport.remaining_order_qty == 5.0
        assert passport.position_size == 2.0
        assert passport.guard_status == "pending_full_fill"
        # PnL должен считаться от 2.0 (real) и 7.0 (projected)
        assert passport.real_pnl == round((100.0 - 99.0) * 2.0, 2)
        assert passport.tp1_projected_pnl == round((100.0 - 99.0) * 7.0, 2)

    def test_multiple_partial_fills_vwap(self):
        """Тест: Несколько частичных исполнений с разной ценой (проверка VWAP)"""
        passport = TradePassport(symbol="SOLUSDT", side="short", entry_price=100.0, tp1_price=99.0)
        passport.target_size = 7.0

        # Чанк 1: 2.0 SOL по 100.0 (Сумма: 200.0)
        self._simulate_order_update(passport, executed_qty=2.0, price=100.0, status="PARTIALLY_FILLED")
        assert passport.filled_qty == 2.0
        assert passport.avg_price == 100.0

        # Чанк 2: 3.0 SOL по 101.0 (Сумма: 303.0. Общая сумма: 503.0. Общий объем: 5.0)
        # Ожидаемая средняя: 503.0 / 5.0 = 100.6
        self._simulate_order_update(passport, executed_qty=3.0, price=101.0, status="PARTIALLY_FILLED")
        assert passport.filled_qty == 5.0
        assert round(passport.avg_price, 2) == 100.6
        assert passport.remaining_order_qty == 2.0
        assert passport.guard_status == "pending_full_fill"

        # Чанк 3 (Финальный): 2.0 SOL по 100.8 (Сумма: 201.6. Общая сумма: 704.6. Общий объем: 7.0)
        # Ожидаемая средняя: 704.6 / 7.0 = 100.657...
        self._simulate_order_update(passport, executed_qty=2.0, price=100.8, status="FILLED")
        assert passport.filled_qty == 7.0
        assert round(passport.avg_price, 4) == 100.6571
        assert passport.remaining_order_qty == 0.0
        assert passport.guard_status == "active" # Guard должен активироваться!


# ============================================================================
# БЛОК 2: Тесты устойчивости WebSocket (BinanceWsAdapter)
# ============================================================================

@pytest.mark.asyncio
class TestWebSocketResilience:
    """Проверка параметров подключения, очистки и экспоненциальной задержки."""

    @patch('adapters.binance_ws.websockets.connect', new_callable=AsyncMock)
    async def test_ws_ping_pong_parameters(self, mock_connect):
        """Тест: Проверка, что при подключении передаются правильные таймауты ping/pong"""
        from adapters.binance_ws import BinanceWsAdapter
        
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        await adapter.connect(retries=1)
        
        mock_connect.assert_called_once()
        call_kwargs = mock_connect.call_args.kwargs
        assert call_kwargs.get('ping_interval') == 20
        assert call_kwargs.get('ping_timeout') == 10
        assert call_kwargs.get('close_timeout') == 5

    @patch('adapters.binance_ws.asyncio.sleep', new_callable=AsyncMock)
    @patch('adapters.binance_ws.websockets.connect', new_callable=AsyncMock)
    async def test_ws_exponential_backoff(self, mock_connect, mock_sleep):
        """Тест: Проверка экспоненциальной задержки при неудачных попытках подключения"""
        from adapters.binance_ws import BinanceWsAdapter
        
        # Имитируем 2 неудачи и 1 успех (возвращаем AsyncMock как результат успешного connect)
        mock_connect.side_effect = [
            Exception("Connection refused 1"),
            Exception("Connection refused 2"),
            AsyncMock() 
        ]
        
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        await adapter.connect(retries=3)
        
        # Проверяем, что sleep вызывался 2 раза (после 1-й и 2-й неудачи)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([call(2), call(4)])

    @patch('adapters.binance_ws.websockets.connect', new_callable=AsyncMock)
    async def test_ws_strict_cleanup_on_reconnect(self, mock_connect):
        """Тест: Проверка, что перед новым подключением старый сокет жестко закрывается с кодом 1000"""
        from adapters.binance_ws import BinanceWsAdapter
        
        old_ws = AsyncMock()
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        adapter._ws = old_ws 
        
        mock_new_ws = AsyncMock()
        mock_connect.return_value = mock_new_ws
        
        await adapter.connect(retries=1)
        
        old_ws.close.assert_called_once_with(code=1000, reason="Reconnecting cleanup")
        assert adapter._ws == mock_new_ws


# ============================================================================
# БЛОК 3: Тесты синхронизации (Drift Monitor Recovery)
# ============================================================================

@pytest.mark.asyncio
class TestDriftSynchronization:
    """Проверка логики восстановления при рассинхронизации статуса."""

    @patch('trading.drift_monitor.DriftMonitor._recover_external_close', new_callable=AsyncMock)
    async def test_recovery_from_order_sent_to_open(self, mock_recover_external):
        """Тест: Если паспорт в ORDER_SENT, а на бирже уже есть позиция, он должен перейти в OPEN"""
        from trading.drift_monitor import DriftMonitor
        from trading.passport import TradePassport
        
        # 🔥 ИСПРАВЛЕНО: Используем AsyncMock для зависимостей, которые могут вызываться с await
        mock_rest = AsyncMock()
        mock_passport_manager = MagicMock()
        mock_repository = AsyncMock() 
        mock_event_bus = AsyncMock()
        
        mock_rest.get_position.return_value = {"size": "5.0", "positionSide": "SHORT"}
        mock_rest.get_open_orders.return_value = []
        
        passport = TradePassport(
            passport_id="TEST_123", symbol="SOLUSDT", side="short", 
            status="ORDER_SENT", entry_price=100.0, target_size=7.0
        )
        mock_passport_manager.get_active_by_symbol.return_value = passport
        # Мокаем update, чтобы он не падал
        mock_passport_manager.update = MagicMock()
        
        monitor = DriftMonitor(
            rest_client=mock_rest, 
            passport_manager=mock_passport_manager, 
            passport_repository=mock_repository,
            event_bus=mock_event_bus,
            risk_manager=MagicMock()
        )
        
        await monitor._check_drift("SOLUSDT")
        
        assert passport.status == "OPEN"
        assert passport.position_size == 5.0
        assert passport.filled_qty == 5.0  # Теперь это работает благодаря исправлению в drift_monitor.py
        assert passport.guard_status == "active"