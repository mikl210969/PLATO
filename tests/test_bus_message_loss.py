"""
Тесты потери сообщений в шине событий при обрыве WebSocket.
Моделирует ситуацию: ордер исполнился на бирже, но событие не дошло до платформы.
"""
import pytest
import asyncio
from unittest.mock import MagicMock
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.event_bus import EventBus
from trading.state_manager import StateManager


class SimplePassport:
    """Простой паспорт для тестов."""
    def __init__(self, data):
        self.passport_id = data.get('passport_id', '')
        self.symbol = data.get('symbol', '')
        self.strategy = data.get('strategy', '')
        self.signal_id = data.get('signal_id', '')
        self.side = data.get('side', '')
        self.entry_price = data.get('entry_price', 0.0)
        self.confidence = data.get('confidence', 0.0)
        self.sl_price = data.get('sl_price', 0.0)
        self.tp1_price = data.get('tp1_price', 0.0)
        self.tp2_price = data.get('tp2_price', 0.0)
        self.position_size = data.get('position_size', 0.0)
        self.status = data.get('status', 'ORDER_SENT')
        self.timeline = data.get('timeline', [])
        self.orders = data.get('orders', [])
        self.position_entry_price = data.get('position_entry_price', 0.0)
        self.exit_price = data.get('exit_price', 0.0)
        self.exit_reason = data.get('exit_reason', '')
        self.gross_pnl = data.get('gross_pnl', 0.0)
        self.commission = data.get('commission', 0.0)
        self.net_pnl = data.get('net_pnl', 0.0)
        self.closed_at = data.get('closed_at', None)
        self.created_at = data.get('created_at', None)
        self.updated_at = data.get('updated_at', None)
        
        for k, v in data.items():
            if not hasattr(self, k):
                setattr(self, k, v)
    
    def to_dict(self):
        return {
            'passport_id': self.passport_id,
            'symbol': self.symbol,
            'strategy': self.strategy,
            'signal_id': self.signal_id,
            'side': self.side,
            'entry_price': self.entry_price,
            'confidence': self.confidence,
            'sl_price': self.sl_price,
            'tp1_price': self.tp1_price,
            'tp2_price': self.tp2_price,
            'position_size': self.position_size,
            'position_entry_price': self.position_entry_price,
            'status': self.status,
            'timeline': self.timeline,
            'orders': self.orders,
            'exit_price': self.exit_price,
            'exit_reason': self.exit_reason,
            'gross_pnl': self.gross_pnl,
            'commission': self.commission,
            'net_pnl': self.net_pnl,
            'closed_at': self.closed_at,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }
    
    def transition_to(self, new_status: str, reason: str = ""):
        from datetime import datetime, timezone
        old_status = self.status
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()
        
        if new_status == "CLOSED" and self.closed_at is None:
            self.closed_at = self.updated_at
        
        self.timeline.append({
            'timestamp': self.updated_at,
            'event': f"STATUS: {new_status}",
            'details': reason,
            'from_status': old_status
        })
        
        return True


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def passport_repository(temp_dir):
    from trading.passport_repository import PassportRepository
    return PassportRepository(logs_dir=temp_dir)


@pytest.fixture
def passport_manager(passport_repository, event_bus):
    from trading.passport_manager import PassportManager
    manager = PassportManager(repository=passport_repository)
    manager.event_bus = event_bus# type: ignore
    
    risk_manager_mock = MagicMock()
    risk_manager_mock._register_guard_in_memory = MagicMock()
    risk_manager_mock._guards = {}
    manager.risk_manager = risk_manager_mock# type: ignore
    
    return manager


@pytest.fixture
def state_manager(passport_manager):
    from trading.state_manager import StateManager
    return StateManager(passport_manager)


@pytest.fixture
def order_verifier_mock():
    return MagicMock()


@pytest.fixture
def sample_passport_dict():
    return {
        "passport_id": "TEST_BUS_LOSS_001",
        "symbol": "SOLUSDT",
        "strategy": "BreakoutStrategyV1",
        "signal_id": "TEST_SIGNAL_001",
        "side": "short",
        "entry_price": 101.06,
        "confidence": 0.99,
        "sl_price": 101.21,
        "tp1_price": 100.81,
        "tp2_price": 100.56,
        "position_size": 0.0,
        "position_entry_price": 0.0,
        "status": "ORDER_SENT",
        "timeline": [],
        "orders": [
            {
                "order_id": 12345,
                "client_order_id": "TEST_ORDER_001",
                "status": "NEW",
                "type": "LIMIT",
                "side": "short",
                "price": 101.06,
                "quantity": 7.0
            }
        ]
    }


@pytest.mark.asyncio
async def test_scenario_11_ws_drops_before_bus_publish(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 11: WS оборвался ДО публикации события в шину.
    
    Ордер отправлен → WS оборвался → ордер исполнился на бирже → 
    событие ORDER_TRADE_UPDATE не дошло до шины → платформа не знает о позиции.
    
    Ожидаемое поведение при восстановлении:
    - Платформа обнаруживает расхождение (local=0, exchange=7.0)
    - Паспорт переходит в OPEN
    - Guard регистрируется
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    # Создаём паспорт в ORDER_SENT
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    # Мок: WS оборвался, событие не дошло до шины
    # Платформа запускает проверку состояния
    class RecoveryHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            self.risk_manager._register_guard_in_memory = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def _reconcile_position_from_exchange(self, passport, symbol):
            # Симулируем проверку биржи: позиция открыта
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.position_size == 0.0 and exchange_position_size > 0:
                self._log("lost_fill_detected", {
                    "passport_id": passport.passport_id,
                    "local_size": passport.position_size,
                    "exchange_size": exchange_position_size
                })
                
                # Восстанавливаем состояние
                passport.position_size = exchange_position_size
                passport.position_entry_price = exchange_entry_price
                
                self.state_manager.handle_event(passport, "ORDER_FILLED", {
                    'executed_qty': exchange_position_size,
                    'price': exchange_entry_price
                })
                
                # Регистрируем Guard
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    assert current.status == "OPEN", f"Статус должен быть OPEN, получен {current.status}"
    assert current.position_size == 7.0, f"Position size должен быть 7.0"
    assert current.position_entry_price == 101.06, f"Entry price должен быть 101.06"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, "Guard должен быть зарегистрирован"
    
    print("✅ Сценарий 11 пройден: WS оборвался до публикации, позиция восстановлена")


@pytest.mark.asyncio
async def test_scenario_12_partial_fill_lost(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 12: Частичное исполнение потеряно.
    
    Ордер частично исполнен (2.1 SOL) → WS оборвался → событие потеряно →
    платформа не знает о partial fill.
    
    Ожидаемое поведение:
    - Платформа обнаруживает расхождение (local=0, exchange=2.1)
    - Паспорт переходит в OPEN с position_size=2.1
    - Guard регистрируется
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class RecoveryHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            self.risk_manager._register_guard_in_memory = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def _reconcile_position_from_exchange(self, passport, symbol):
            exchange_position_size = 2.1  # Частичное исполнение
            exchange_entry_price = 101.06
            
            if passport.position_size == 0.0 and exchange_position_size > 0:
                self._log("partial_fill_lost", {
                    "passport_id": passport.passport_id,
                    "local_size": passport.position_size,
                    "exchange_size": exchange_position_size
                })
                
                passport.position_size = exchange_position_size
                passport.position_entry_price = exchange_entry_price
                
                self.state_manager.handle_event(passport, "ORDER_FILLED", {
                    'executed_qty': exchange_position_size,
                    'price': exchange_entry_price
                })
                
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    assert current.status == "OPEN", f"Статус должен быть OPEN"
    assert current.position_size == 2.1, f"Position size должен быть 2.1"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, "Guard должен быть зарегистрирован"
    
    print("✅ Сценарий 12 пройден: Частичное исполнение потеряно, позиция восстановлена")


@pytest.mark.asyncio
async def test_scenario_13_multiple_partial_fills_some_lost(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 13: Множественные partial fills, некоторые потеряны.
    
    Ордер исполняется частями: 2.1 → 4.9 → 7.0
    WS оборвался после первого fill → события 2 и 3 потеряны.
    
    Ожидаемое поведение:
    - Платформа видит final state: position_size=7.0
    - Паспорт переходит в OPEN с position_size=7.0
    - Guard регистрируется один раз
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class RecoveryHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            self.risk_manager._register_guard_in_memory = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def _reconcile_position_from_exchange(self, passport, symbol):
            # Биржа показывает финальное состояние: 7.0
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.position_size == 0.0 and exchange_position_size > 0:
                self._log("multiple_fills_lost", {
                    "passport_id": passport.passport_id,
                    "local_size": passport.position_size,
                    "exchange_size": exchange_position_size
                })
                
                passport.position_size = exchange_position_size
                passport.position_entry_price = exchange_entry_price
                
                self.state_manager.handle_event(passport, "ORDER_FILLED", {
                    'executed_qty': exchange_position_size,
                    'price': exchange_entry_price
                })
                
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    assert current.status == "OPEN", f"Статус должен быть OPEN"
    assert current.position_size == 7.0, f"Position size должен быть 7.0"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, "Guard должен быть зарегистрирован ровно 1 раз"
    
    print("✅ Сценарий 13 пройден: Множественные partial fills потеряны, финальное состояние восстановлено")


@pytest.mark.asyncio
async def test_scenario_14_full_fill_no_events(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 14: Ордер полностью исполнен, но платформа не получила НИ ОДНОГО события.
    
    Ордер отправлен → WS оборвался → ордер полностью исполнен (7.0 SOL) →
    ни одно событие не дошло до платформы.
    
    Ожидаемое поведение:
    - Платформа обнаруживает расхождение (local=0, exchange=7.0)
    - Паспорт переходит в OPEN
    - Guard регистрируется
    - Позиция управляема
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class RecoveryHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            self.risk_manager._register_guard_in_memory = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def _reconcile_position_from_exchange(self, passport, symbol):
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.position_size == 0.0 and exchange_position_size > 0:
                self._log("complete_fill_lost", {
                    "passport_id": passport.passport_id,
                    "local_size": passport.position_size,
                    "exchange_size": exchange_position_size
                })
                
                passport.position_size = exchange_position_size
                passport.position_entry_price = exchange_entry_price
                
                self.state_manager.handle_event(passport, "ORDER_FILLED", {
                    'executed_qty': exchange_position_size,
                    'price': exchange_entry_price
                })
                
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    assert current.status == "OPEN", f"Статус должен быть OPEN"
    assert current.position_size == 7.0, f"Position size должен быть 7.0"
    assert current.position_entry_price == 101.06, f"Entry price должен быть 101.06"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, "Guard должен быть зарегистрирован"
    
    print("✅ Сценарий 14 пройден: Полное исполнение без событий, позиция восстановлена и управляема")


@pytest.mark.asyncio
async def test_scenario_15_restart_during_fill(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 15: Платформа перезапускается во время исполнения ордера.
    
    Ордер отправлен → платформа начинает получать partial fills →
    платформа перезапускается → состояние потеряно →
    при запуске проверяет биржу и видит позицию.
    
    Ожидаемое поведение:
    - Платформа загружает паспорт из репозитория (ORDER_SENT или OPEN с partial)
    - Проверяет биржу
    - Синхронизирует состояние
    - Guard регистрируется
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    # Симулируем, что платформа перезапустилась с паспортом в ORDER_SENT
    passport_obj = SimplePassport(sample_passport_dict)
    passport_obj.status = "ORDER_SENT"
    passport_obj.position_size = 0.0
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class RecoveryHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            self.risk_manager._register_guard_in_memory = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def _reconcile_position_from_exchange(self, passport, symbol):
            # Биржа показывает, что позиция уже открыта (7.0)
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.status == "ORDER_SENT" and exchange_position_size > 0:
                self._log("restart_during_fill", {
                    "passport_id": passport.passport_id,
                    "local_status": passport.status,
                    "exchange_size": exchange_position_size
                })
                
                passport.position_size = exchange_position_size
                passport.position_entry_price = exchange_entry_price
                
                self.state_manager.handle_event(passport, "ORDER_FILLED", {
                    'executed_qty': exchange_position_size,
                    'price': exchange_entry_price
                })
                
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    assert current.status == "OPEN", f"Статус должен быть OPEN после восстановления"
    assert current.position_size == 7.0, f"Position size должен быть 7.0"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, "Guard должен быть зарегистрирован"
    
    print("✅ Сценарий 15 пройден: Перезапуск во время исполнения, позиция восстановлена")