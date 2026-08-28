"""
Интеграционные тесты: Размещение ордеров.
Сценарии 1.1, 1.2, 1.4 — Happy Path, ошибка биржи, cooldown.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict
from datetime import datetime, timezone

from trading.event_handlers import EventHandlersMixin
from trading.passport import TradePassport
from core.event_bus import Event


class MockEventHandlersMixin(EventHandlersMixin):
    """Моковый класс для тестирования."""
    def __init__(self):
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 5.0  # секунд
        # Атрибуты, которые в реальности даёт Orchestrator
        self._log = MagicMock()
        self.bus = MagicMock()
        self.bus.publish = AsyncMock()
        self.passport_manager = MagicMock()
        self.repository = MagicMock()
        self.state_manager = MagicMock()
        self.config = {
            'trading': {
                'lot_size': 7.0,
                'entry_order_type': 'limit',
                'ttl_seconds': 300,
                'atr_value': 0.5,
            }
        }
        self.get_trader = MagicMock()


@pytest.fixture
def mock_components():
    """Создаём моки для всех зависимостей."""
    mixin = MockEventHandlersMixin()
    
    return {
        'mixin': mixin,
        'event_bus': mixin.bus,
        'passport_manager': mixin.passport_manager,
        'repository': mixin.repository,
        'state_manager': mixin.state_manager,
    }


@pytest.fixture
def mock_trader():
    """Создаём мок трейдера."""
    trader = MagicMock()
    trader.calculate_exit_levels = MagicMock(return_value={
        'sl_price': 95.0,
        'tp1_price': 89.0,
        'tp2_price': 88.0,
    })
    return trader


@pytest.fixture
def mock_passport():
    """Создаём мок паспорта."""
    passport = MagicMock(spec=TradePassport)
    passport.passport_id = "TEST_PASSPORT_PLACE_001"
    passport.symbol = "SOLUSDT"
    passport.status = "SIGNAL_GENERATED"
    passport.orders = []
    passport.timeline = []
    passport.sl_price = 0.0
    passport.tp1_price = 0.0
    passport.tp2_price = 0.0
    return passport


def make_signal_event(signal_id="WallFade_91.0_100_1787312149729", symbol="SOLUSDT", side="short", entry_price=91.0):
    """Вспомогательная функция для создания события SIGNAL_GENERATED."""
    signal = MagicMock()
    signal.signal_id = signal_id
    signal.symbol = symbol
    signal.side = side
    signal.entry_price = entry_price
    signal.strategy = "WallFade"
    signal.confidence = 0.7
    
    return Event(
        type="SIGNAL_GENERATED",
        source="strategy",
        payload={"signal": signal},
        symbol=symbol,
    )


@pytest.mark.asyncio
async def test_order_placement_happy_path(mock_components, mock_trader, mock_passport):
    """
    Сценарий 1.1: Сигнал получен → Лимитный ордер успешно размещен → ORDER_SENT.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    state_manager = mock_components['state_manager']
    event_bus = mock_components['event_bus']
    
    # Настраиваем моки
    mixin.get_trader.return_value = mock_trader
    passport_manager.is_symbol_busy.return_value = False
    passport_manager.create.return_value = mock_passport
    
    # Ордер успешно размещён на бирже
    mock_trader.execute_order = AsyncMock(return_value={
        'success': True,
        'order_id': 4185748100,
        'client_order_id': 'WallFade_91.0_100_1787312149729',
        'status': 'NEW',
        'order_type': 'LIMIT',
        'quantity': 7.0,
    })
    
    # Вызываем обработчик сигнала
    event = make_signal_event()
    await mixin._on_signal(event)
    
    # Проверяем, что паспорт был создан
    passport_manager.create.assert_called_once_with(
        symbol="SOLUSDT",
        signal_id="WallFade_91.0_100_1787312149729",
        strategy="WallFade",
        side="short",
        entry_price=91.0,
        confidence=0.7,
    )
    
    # Проверяем, что были рассчитаны уровни SL/TP
    mock_trader.calculate_exit_levels.assert_called_once_with(
        side="short",
        entry_price=91.0,
        atr_value=0.5,
    )
    assert mock_passport.sl_price == 95.0
    assert mock_passport.tp1_price == 89.0
    assert mock_passport.tp2_price == 88.0
    
    # Проверяем, что ордер был отправлен на биржу с правильными параметрами
    mock_trader.execute_order.assert_called_once_with(
        symbol="SOLUSDT",
        side="short",
        quantity=7.0,
        order_type="limit",
        client_order_id="WallFade_91.0_100_1787312149729",
        passport_id="TEST_PASSPORT_PLACE_001",
        limit_price=91.0,  # 🔥 Ключевой параметр для лимитного ордера!
    )
    
    # Проверяем переход статуса в ORDER_SENT
    state_manager.handle_event.assert_called_with(
        mock_passport, "ORDER_SENT", "Order sent to exchange"
    )
    
    # Проверяем, что ордер добавлен в паспорт
    assert len(mock_passport.add_order.call_args_list) == 1
    added_order = mock_passport.add_order.call_args[0][0]
    assert added_order['order_id'] == 4185748100
    assert added_order['client_order_id'] == 'WallFade_91.0_100_1787312149729'
    assert added_order['status'] == 'NEW'
    
    # Проверяем сохранение паспорта (несколько раз: после создания, после SL/TP, после ORDER_SENT, после add_order)
    assert repository.save.call_count >= 2
    
    # Проверяем публикацию события PASSPORT_CREATED для LifecycleManager (TTL)
    event_bus.publish.assert_any_call(
        event_type="PASSPORT_CREATED",
        source="orchestrator",
        payload={
            "passport_id": "TEST_PASSPORT_PLACE_001",
            "order_type": "limit"  # ← Добавлено, чтобы соответствовать новому коду
        },
        symbol="SOLUSDT",
    )


@pytest.mark.asyncio
async def test_order_placement_exchange_error(mock_components, mock_trader, mock_passport):
    """
    Сценарий 1.2: Биржа отклоняет ордер → Статус паспорта FAILED.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    state_manager = mock_components['state_manager']
    event_bus = mock_components['event_bus']  # 🔥 ДОБАВЛЕНО
    
    mixin.get_trader.return_value = mock_trader
    passport_manager.is_symbol_busy.return_value = False
    passport_manager.create.return_value = mock_passport
    
    # Биржа возвращает ошибку (например, недостаточно средств)
    mock_trader.execute_order = AsyncMock(return_value={
        'success': False,
        'order_id': None,
        'client_order_id': None,
        'status': 'FAILED',
        'error': 'Insufficient balance',
    })
    
    event = make_signal_event()
    await mixin._on_signal(event)
    
    # Паспорт всё равно создаётся (чтобы зафиксировать попытку)
    passport_manager.create.assert_called_once()
    
    # Но статус должен быть FAILED
    state_manager.handle_event.assert_called_with(
        mock_passport, "ORDER_FAILED", {"error": "Insufficient balance"}
    )
    
    # Ордер НЕ должен был быть добавлен в паспорт
    mock_passport.add_order.assert_not_called()
    
    # Событие PASSPORT_CREATED НЕ должно было быть опубликовано (ордер не размещён)
    for call in event_bus.publish.call_args_list:
        assert call.kwargs.get('event_type') != "PASSPORT_CREATED" or \
               call.kwargs.get('payload', {}).get('passport_id') is None


@pytest.mark.asyncio
async def test_order_placement_cooldown(mock_components, mock_trader, mock_passport):
    """
    Сценарий 1.4: Два сигнала подряд → Второй игнорируется (cooldown).
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    
    mixin.get_trader.return_value = mock_trader
    passport_manager.is_symbol_busy.return_value = False
    passport_manager.create.return_value = mock_passport
    
    mock_trader.execute_order = AsyncMock(return_value={
        'success': True,
        'order_id': 123,
        'client_order_id': 'order_1',
        'status': 'NEW',
        'quantity': 7.0,
    })
    
    # Первый сигнал — должен пройти
    event1 = make_signal_event(signal_id="signal_1")
    await mixin._on_signal(event1)
    
    assert passport_manager.create.call_count == 1
    
    # Второй сигнал сразу после первого — должен быть заблокирован cooldown'ом
    event2 = make_signal_event(signal_id="signal_2")
    await mixin._on_signal(event2)
    
    # Паспорт НЕ должен был быть создан второй раз
    assert passport_manager.create.call_count == 1
    
    # Ордер НЕ должен был быть отправлен второй раз
    assert mock_trader.execute_order.call_count == 1


@pytest.mark.asyncio
async def test_order_placement_symbol_busy(mock_components, mock_trader, mock_passport):
    """
    Негативный тест: Символ уже занят (есть активная позиция) → сигнал игнорируется.
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    
    mixin.get_trader.return_value = mock_trader
    # Символ занят — уже есть активная позиция
    passport_manager.is_symbol_busy.return_value = True
    
    event = make_signal_event()
    await mixin._on_signal(event)
    
    # Паспорт НЕ должен был быть создан
    passport_manager.create.assert_not_called()
    mock_trader.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_order_placement_market_order(mock_components, mock_trader, mock_passport):
    """
    Тест: Размещение MARKET ордера (без limit_price).
    """
    mixin = mock_components['mixin']
    passport_manager = mock_components['passport_manager']
    repository = mock_components['repository']
    
    # Меняем тип ордера на market
    mixin.config['trading']['entry_order_type'] = 'market'
    
    mixin.get_trader.return_value = mock_trader
    passport_manager.is_symbol_busy.return_value = False
    passport_manager.create.return_value = mock_passport
    
    mock_trader.execute_order = AsyncMock(return_value={
        'success': True,
        'order_id': 456,
        'client_order_id': 'market_order_1',
        'status': 'FILLED',
        'quantity': 7.0,
    })
    
    event = make_signal_event()
    await mixin._on_signal(event)
    
    # Проверяем, что limit_price был передан как None для market ордера
    mock_trader.execute_order.assert_called_once_with(
        symbol="SOLUSDT",
        side="short",
        quantity=7.0,
        order_type="market",
        client_order_id="WallFade_91.0_100_1787312149729",
        passport_id="TEST_PASSPORT_PLACE_001",
        limit_price=None,  # 🔥 Для market ордера limit_price должен быть None
    )

# ============================================================
# Hedge Mode: проверка position_side и reduce_only
# ============================================================

@pytest.mark.asyncio
async def test_hedge_mode_short_entry():
    """
    Hedge Mode: SHORT вход → в REST передаётся side='SELL' + position_side='SHORT'.
    reduce_only НЕ передаётся (или False).
    """
    from trading.trader import Trader
    
    # Мок REST-клиента
    rest = MagicMock()
    rest.create_market_order = AsyncMock(return_value={
        'success': True,
        'order_id': 123,
        'client_order_id': 'test_short',
        'status': 'FILLED',
    })
    ws = MagicMock()
    bus = MagicMock()
    
    trader = Trader(
        symbol='SOLUSDT',
        rest_client=rest,
        ws_adapter=ws,
        event_bus=bus,
        config={'trading': {}}
    )
    
    # SHORT вход: side='short' → trader сам выводит position_side='SHORT'
    result = await trader.execute_order(
        symbol='SOLUSDT',
        side='short',
        quantity=7.0,
        order_type='market',
        passport_id='TEST_HEDGE_SHORT_001'
    )
    
    assert result['success'] is True
    
    # Проверяем, что в REST переданы правильные параметры Hedge Mode
    rest.create_market_order.assert_called_once()
    call_kwargs = rest.create_market_order.call_args.kwargs
    
    assert call_kwargs['side'] == 'SELL', "SHORT вход = SELL ордер"
    assert call_kwargs['position_side'] == 'SHORT', "Hedge Mode: сторона ПОЗИЦИИ = SHORT"
    assert call_kwargs['quantity'] == 7.0
    # reduce_only НЕ должен быть True в Hedge Mode
    assert call_kwargs.get('reduce_only', False) is False


@pytest.mark.asyncio
async def test_hedge_mode_long_entry():
    """
    Hedge Mode: LONG вход → в REST передаётся side='BUY' + position_side='LONG'.
    """
    from trading.trader import Trader
    
    rest = MagicMock()
    rest.create_market_order = AsyncMock(return_value={
        'success': True,
        'order_id': 456,
        'client_order_id': 'test_long',
        'status': 'FILLED',
    })
    ws = MagicMock()
    bus = MagicMock()
    
    trader = Trader(
        symbol='SOLUSDT',
        rest_client=rest,
        ws_adapter=ws,
        event_bus=bus,
        config={'trading': {}}
    )
    
    # LONG вход: side='long' → trader сам выводит position_side='LONG'
    result = await trader.execute_order(
        symbol='SOLUSDT',
        side='long',
        quantity=5.0,
        order_type='market',
        passport_id='TEST_HEDGE_LONG_001'
    )
    
    assert result['success'] is True
    
    call_kwargs = rest.create_market_order.call_args.kwargs
    
    assert call_kwargs['side'] == 'BUY', "LONG вход = BUY ордер"
    assert call_kwargs['position_side'] == 'LONG', "Hedge Mode: сторона ПОЗИЦИИ = LONG"
    assert call_kwargs.get('reduce_only', False) is False


@pytest.mark.asyncio
async def test_hedge_mode_close_position():
    """
    Hedge Mode: закрытие SHORT позиции → BUY + position_side='SHORT', reduce_only=False.
    """
    from trading.trader import Trader
    
    rest = MagicMock()
    rest.create_market_order = AsyncMock(return_value={
        'success': True,
        'order_id': 789,
        'client_order_id': 'close_short',
        'status': 'FILLED',
    })
    ws = MagicMock()
    bus = MagicMock()
    
    trader = Trader(
        symbol='SOLUSDT',
        rest_client=rest,
        ws_adapter=ws,
        event_bus=bus,
        config={'trading': {}}
    )
    
    # Закрываем SHORT позицию: position_side='SHORT' → BUY ордер
    result = await trader.close_position(
        symbol='SOLUSDT',
        quantity=7.0,
        exit_reason='SL_HIT',
        position_side='SHORT'
    )
    
    assert result['success'] is True
    
    call_kwargs = rest.create_market_order.call_args.kwargs
    
    assert call_kwargs['side'] == 'BUY', "Закрытие SHORT = BUY ордер"
    assert call_kwargs['position_side'] == 'SHORT', "Hedge Mode: закрываем SHORT позицию"
    assert call_kwargs['quantity'] == 7.0
    # 🔥 КРИТИЧНО: reduce_only=False (в Hedge Mode запрещён, даёт -1106)
    assert call_kwargs['reduce_only'] is False


@pytest.mark.asyncio
async def test_hedge_mode_limit_order_position_side():
    """
    Hedge Mode: LIMIT ордер тоже получает position_side.
    """
    from trading.trader import Trader
    
    rest = MagicMock()
    rest.create_limit_order = AsyncMock(return_value={
        'success': True,
        'order_id': 999,
        'client_order_id': 'limit_short',
        'status': 'NEW',
    })
    ws = MagicMock()
    bus = MagicMock()
    
    trader = Trader(
        symbol='SOLUSDT',
        rest_client=rest,
        ws_adapter=ws,
        event_bus=bus,
        config={'trading': {}}
    )
    
    result = await trader.execute_order(
        symbol='SOLUSDT',
        side='short',
        quantity=7.0,
        order_type='limit',
        limit_price=91.0,
        passport_id='TEST_HEDGE_LIMIT_001'
    )
    
    assert result['success'] is True
    
    call_kwargs = rest.create_limit_order.call_args.kwargs
    
    assert call_kwargs['side'] == 'SELL'
    assert call_kwargs['position_side'] == 'SHORT'
    assert call_kwargs['price'] == 91.0
    assert call_kwargs.get('reduce_only', False) is False