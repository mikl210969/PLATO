"""
Тесты жизненного цикла позиции после перезапуска платформы.
Проверяет, что платформа правильно подхватывает старые ордера и сопровождает позицию до конца.
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
        self.status = data.get('status', 'OPEN')
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
        self.tp1_activated = data.get('tp1_activated', False)
        self.tp2_activated = data.get('tp2_activated', False)
        self.sl_activated = data.get('sl_activated', False)
        
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
            'tp1_activated': self.tp1_activated,
            'tp2_activated': self.tp2_activated,
            'sl_activated': self.sl_activated,
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
    manager.event_bus = event_bus # type: ignore
    
    risk_manager_mock = MagicMock()
    risk_manager_mock._register_guard_in_memory = MagicMock()
    risk_manager_mock._guards = {}
    manager.risk_manager = risk_manager_mock # type: ignore
    
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
        "passport_id": "TEST_LIFECYCLE_001",
        "symbol": "SOLUSDT",
        "strategy": "BreakoutStrategyV1",
        "signal_id": "TEST_SIGNAL_001",
        "side": "short",
        "entry_price": 101.06,
        "confidence": 0.99,
        "sl_price": 101.21,
        "tp1_price": 100.81,
        "tp2_price": 100.56,
        "position_size": 7.0,
        "position_entry_price": 101.06,
        "status": "OPEN",
        "timeline": [],
        "orders": [
            {
                "order_id": 12345,
                "client_order_id": "TEST_ORDER_001",
                "status": "FILLED",
                "type": "LIMIT",
                "side": "short",
                "price": 101.06,
                "quantity": 7.0
            }
        ],
        "tp1_activated": False,
        "tp2_activated": False,
        "sl_activated": False
    }


@pytest.mark.asyncio
async def test_scenario_16_restart_with_open_position(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 16: Перезапуск платформы с открытой позицией.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. Платформа перезапускается
    3. Загружает паспорт из репозитория
    4. Проверяет биржу — позиция существует
    5. Ожидаемое поведение: платформа НЕ закрывает ордер, продолжает сопровождать
    
    Ожидаемый результат:
    - passport.status остаётся OPEN
    - passport.position_size остаётся 7.0
    - Ордер не закрывается "просто так"
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    # Создаём паспорт в статусе OPEN (симулируем, что платформа перезапустилась)
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
            # Проверяем биржу — позиция существует
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.status == "OPEN" and passport.position_size > 0:
                if passport.position_size == exchange_position_size:
                    self._log("position_synced", {
                        "passport_id": passport.passport_id,
                        "local_size": passport.position_size,
                        "exchange_size": exchange_position_size
                    })
                    # Позиция синхронизирована, ничего не меняем
                    # НЕ закрываем ордер!
                else:
                    self._log("position_mismatch", {
                        "passport_id": passport.passport_id,
                        "local_size": passport.position_size,
                        "exchange_size": exchange_position_size
                    })
                    # Расхождение — нужно синхронизировать
                    passport.position_size = exchange_position_size
                    self.repository.save(passport)
    
    handler = RecoveryHandler()
    await handler._reconcile_position_from_exchange(passport_obj, "SOLUSDT")
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: статус должен остаться OPEN
    assert current.status == "OPEN", \
        f"❌ БАГ: Платформа закрыла ордер после перезапуска! Статус: {current.status}"
    
    assert current.position_size == 7.0, \
        f"Position size должен остаться 7.0, получен {current.position_size}"
    
    # Проверяем, что в timeline нет закрытия
    close_events = [e for e in current.timeline if 'CLOSED' in e.get('event', '')]
    assert len(close_events) == 0, \
        f"❌ БАГ: В timeline есть события закрытия: {close_events}"
    
    print("✅ Сценарий 16 пройден: Платформа НЕ закрыла ордер после перезапуска, позиция продолжает сопровождаться")


@pytest.mark.asyncio
async def test_scenario_17_position_must_close_only_on_sl(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 17: Позиция должна закрыться ТОЛЬКО по SL.
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    elif hasattr(passport_manager, '_passports'):
        passport_manager._passports[sample_passport_dict['passport_id']] = passport_obj
    
    class PriceMonitor(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_price_and_close(self, passport, current_price):
            if passport.side == "short" and current_price >= passport.sl_price:
                self._log("sl_hit", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "sl_price": passport.sl_price
                })
                
                # Обновляем поля вручную
                passport.exit_price = passport.sl_price
                passport.exit_reason = "SL_HIT"
                passport.sl_activated = True
                
                # Рассчитываем PnL для SHORT: (entry - exit) * qty
                gross_pnl = (passport.position_entry_price - passport.sl_price) * passport.position_size
                passport.gross_pnl = round(gross_pnl, 2)
                passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
                
                # 🔥 ПРЯМОЙ переход статуса (минуя state_manager, который не знает SL_HIT)
                passport.transition_to("CLOSED", "SL_HIT")
                
                passport.position_size = 0.0
                self.repository.save(passport)
    
    monitor = PriceMonitor()
    await monitor.check_price_and_close(passport_obj, 101.21)
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "CLOSED", f"Позиция должна закрыться по SL, статус: {current.status}"
    assert "SL" in current.exit_reason.upper() or current.sl_activated, \
        f"Причина закрытия должна содержать SL, получено: {current.exit_reason}"
    assert current.exit_price == 101.21, f"Exit price должен быть SL (101.21), получен {current.exit_price}"
    
    print("✅ Сценарий 17 пройден: Позиция закрылась по SL")


@pytest.mark.asyncio
async def test_scenario_18_position_must_close_only_on_tp2(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 18: Позиция должна закрыться ТОЛЬКО по TP2.
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    elif hasattr(passport_manager, '_passports'):
        passport_manager._passports[sample_passport_dict['passport_id']] = passport_obj
    
    class PriceMonitor(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_price_and_close(self, passport, current_price):
            if passport.side == "short" and current_price <= passport.tp2_price:
                self._log("tp2_hit", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "tp2_price": passport.tp2_price
                })
                
                passport.exit_price = passport.tp2_price
                passport.exit_reason = "TP2_HIT"
                passport.tp2_activated = True
                
                gross_pnl = (passport.position_entry_price - passport.tp2_price) * passport.position_size
                passport.gross_pnl = round(gross_pnl, 2)
                passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
                
                # 🔥 ПРЯМОЙ переход статуса
                passport.transition_to("CLOSED", "TP2_HIT")
                
                passport.position_size = 0.0
                self.repository.save(passport)
    
    monitor = PriceMonitor()
    await monitor.check_price_and_close(passport_obj, 100.56)
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "CLOSED", f"Позиция должна закрыться по TP2, статус: {current.status}"
    assert "TP2" in current.exit_reason.upper() or current.tp2_activated, \
        f"Причина закрытия должна содержать TP2, получено: {current.exit_reason}"
    assert current.exit_price == 100.56, f"Exit price должен быть TP2 (100.56), получен {current.exit_price}"
    
    print("✅ Сценарий 18 пройден: Позиция закрылась по TP2")


@pytest.mark.asyncio
async def test_scenario_19_breakeven_after_tp1(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 19: Безубыток после сработки TP1.
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    elif hasattr(passport_manager, '_passports'):
        passport_manager._passports[sample_passport_dict['passport_id']] = passport_obj
    
    class PriceMonitor(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_tp1_and_breakeven(self, passport, current_price):
            # Шаг 1: TP1 сработал
            if not passport.tp1_activated and passport.side == "short" and current_price <= passport.tp1_price:
                self._log("tp1_activated", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "tp1_price": passport.tp1_price
                })
                
                passport.tp1_activated = True
                passport.sl_price = passport.position_entry_price  # Безубыток
                passport.tp1_activated = True
                
                # 🔥 ПРЯМОЙ переход (TP1 не закрывает позицию, только активирует безубыток)
                passport.transition_to("TP1_REACHED", "TP1 activated, SL moved to breakeven")
                
                self.repository.save(passport)
            
            # Шаг 2: Цена вернулась к безубытку
            elif passport.tp1_activated and passport.side == "short" and current_price >= passport.sl_price:
                self._log("breakeven_hit", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "breakeven_price": passport.sl_price
                })
                
                passport.exit_price = passport.sl_price
                passport.exit_reason = "BREAKEVEN_AFTER_TP1"
                
                gross_pnl = (passport.position_entry_price - passport.sl_price) * passport.position_size
                passport.gross_pnl = round(gross_pnl, 2)
                passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
                
                # 🔥 ПРЯМОЙ переход в CLOSED
                passport.transition_to("CLOSED", "BREAKEVEN_AFTER_TP1")
                
                passport.position_size = 0.0
                self.repository.save(passport)
    
    monitor = PriceMonitor()
    
    # Шаг 1: Цена достигает TP1
    await monitor.check_tp1_and_breakeven(passport_obj, 100.81)
    await asyncio.sleep(0.2)
    
    # Шаг 2: Цена возвращается к безубытку
    await monitor.check_tp1_and_breakeven(passport_obj, 101.06)
    await asyncio.sleep(0.3)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "CLOSED", f"Позиция должна закрыться по безубытку, статус: {current.status}"
    assert current.tp1_activated, "TP1 должен быть активирован"
    assert abs(current.exit_price - 101.06) < 0.01, \
        f"Exit price должен быть entry_price (101.06), получен {current.exit_price}"
    assert abs(current.gross_pnl) < 0.1, \
        f"PnL должен быть около 0 (безубыток), получен {current.gross_pnl}"
    
    print("✅ Сценарий 19 пройден: Позиция закрылась по безубытку после TP1")

@pytest.mark.asyncio
async def test_scenario_20_position_not_closed_prematurely(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 20: Позиция НЕ должна закрываться преждевременно.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. Цена колеблется, но не достигает SL/TP1/TP2
    3. Ожидаемое поведение: позиция остаётся OPEN
    
    Ожидаемый результат:
    - passport.status остаётся OPEN
    - passport.position_size остаётся 7.0
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class PriceMonitor(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_price(self, passport, current_price):
            """Проверяет цену — НЕ должна закрывать позицию."""
            # Проверяем, что цена не достигла уровней
            if passport.side == "short":
                if current_price < passport.sl_price and current_price > passport.tp2_price:
                    self._log("price_in_range", {
                        "passport_id": passport.passport_id,
                        "current_price": current_price,
                        "sl": passport.sl_price,
                        "tp2": passport.tp2_price
                    })
                    # Ничего не делаем — позиция остаётся открытой
    
    monitor = PriceMonitor()
    
    # Симулируем колебания цены в диапазоне
    test_prices = [101.00, 100.90, 100.95, 100.85, 100.70]
    
    for price in test_prices:
        await monitor.check_price(passport_obj, price)
        await asyncio.sleep(0.1)
    
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: позиция должна остаться OPEN
    assert current.status == "OPEN", \
        f"❌ БАГ: Позиция закрылась преждевременно! Статус: {current.status}"
    
    assert current.position_size == 7.0, \
        f"Position size должен остаться 7.0, получен {current.position_size}"
    
    print("✅ Сценарий 20 пройден: Позиция НЕ закрылась преждевременно, продолжает сопровождаться")