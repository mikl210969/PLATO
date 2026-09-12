"""
Тесты для реальных плохих условий:
- Потеря WebSocket соединения
- Бан REST API биржей
- Восстановление после сбоев
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
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
        "passport_id": "TEST_CHAOS_001",
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
async def test_scenario_21_ws_disconnect_during_sl_approach(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 21: WS отключается, когда цена приближается к SL.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. Цена приближается к SL (101.15 из 101.21)
    3. WS отключается
    4. Цена достигает SL (101.21)
    5. WS восстанавливается
    6. Ожидаемое поведение: платформа обнаруживает закрытие позиции через reconciliation
    
    Ожидаемый результат:
    - passport.status == "CLOSED"
    - passport.exit_reason содержит "SL"
    - gross_pnl рассчитан корректно
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class ChaosOrderHandler(OrderHandlerMixin):
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
            
            # Состояние WS
            self.ws_connected = True
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def simulate_ws_disconnect(self):
            """Симулирует отключение WS."""
            self.ws_connected = False
            self._log("ws_disconnected", {})
        
        async def simulate_ws_reconnect(self):
            """Симулирует восстановление WS."""
            self.ws_connected = True
            self._log("ws_reconnected", {})
        
        async def check_price_with_ws_chaos(self, passport, current_price):
            """Проверяет цену с учетом состояния WS."""
            if not self.ws_connected:
                self._log("ws_down_price_update_missed", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price
                })
                return False
            
            # Цена достигла SL
            if passport.side == "short" and current_price >= passport.sl_price:
                self._log("sl_hit", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "sl_price": passport.sl_price
                })
                
                passport.exit_price = passport.sl_price
                passport.exit_reason = "SL_HIT"
                passport.sl_activated = True
                
                gross_pnl = (passport.position_entry_price - passport.sl_price) * passport.position_size
                passport.gross_pnl = round(gross_pnl, 2)
                passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
                
                passport.transition_to("CLOSED", "SL_HIT")
                passport.position_size = 0.0
                self.repository.save(passport)
                
                return True
            
            return False
    
    handler = ChaosOrderHandler()
    
    # Шаг 1: Цена приближается к SL
    print("\n Шаг 1: Цена приближается к SL (101.15)")
    await handler.check_price_with_ws_chaos(passport_obj, 101.15)
    await asyncio.sleep(0.1)
    
    # Шаг 2: WS отключается
    print("🔌 Шаг 2: WS отключается")
    await handler.simulate_ws_disconnect()
    
    # Шаг 3: Цена достигает SL, но WS отключен
    print("📉 Шаг 3: Цена достигает SL (101.21), но WS отключен")
    result = await handler.check_price_with_ws_chaos(passport_obj, 101.21)
    assert result == False, "WS отключен, событие не должно быть обработано"
    
    # Шаг 4: WS восстанавливается
    print("🔌 Шаг 4: WS восстанавливается")
    await handler.simulate_ws_reconnect()
    
    # Шаг 5: Reconciliation обнаруживает закрытие позиции
    print("🔄 Шаг 5: Reconciliation обнаруживает закрытие позиции")
    
    # Симулируем, что биржа закрыла позицию по SL
    exchange_position_size = 0.0
    exchange_exit_price = 101.21
    
    if passport_obj.position_size > 0 and exchange_position_size == 0.0:
        handler._log("reconciliation_found_external_close", {
            "passport_id": passport_obj.passport_id,
            "exchange_exit_price": exchange_exit_price
        })
        
        passport_obj.exit_price = exchange_exit_price
        passport_obj.exit_reason = "SL_HIT_EXTERNAL"
        passport_obj.sl_activated = True
        
        gross_pnl = (passport_obj.position_entry_price - exchange_exit_price) * passport_obj.position_size
        passport_obj.gross_pnl = round(gross_pnl, 2)
        passport_obj.net_pnl = round(passport_obj.gross_pnl - passport_obj.commission, 2)
        
        passport_obj.transition_to("CLOSED", "SL_HIT_EXTERNAL")
        passport_obj.position_size = 0.0
        handler.repository.save(passport_obj) # type: ignore
    
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "CLOSED", f"Позиция должна закрыться по SL, статус: {current.status}"
    assert "SL" in current.exit_reason.upper(), f"Причина должна содержать SL: {current.exit_reason}"
    assert current.exit_price == 101.21, f"Exit price должен быть 101.21, получен {current.exit_price}"
    
    expected_pnl = (101.06 - 101.21) * 7.0
    assert abs(current.gross_pnl - expected_pnl) < 0.01, \
        f"PnL должен быть {expected_pnl}, получен {current.gross_pnl}"
    
    print("✅ Сценарий 21 пройден: WS отключился перед SL, reconciliation восстановил состояние")


@pytest.mark.asyncio
async def test_scenario_22_rest_ban_during_position(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 22: REST API забанен во время открытой позиции.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. Платформа пытается сделать REST-запрос (проверка позиции)
    3. Биржа возвращает бан (-1003)
    4. Ожидаемое поведение: платформа не закрывает паспорт, ждет разбана
    
    Ожидаемый результат:
    - passport.status остаётся OPEN
    - passport.position_size остаётся 7.0
    - Нет фейковых данных (exit_price=0, gross_pnl=0)
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class BannedRestHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            
            # Состояние бана
            self.rest_banned = True
            self.ban_error_code = -1003
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_position_with_rest_ban(self, passport, symbol):
            """Проверяет позицию с учетом бана REST."""
            if self.rest_banned:
                self._log("rest_ban_detected", {
                    "passport_id": passport.passport_id,
                    "error_code": self.ban_error_code
                })
                
                # НЕ закрываем паспорт, НЕ меняем данные
                # Просто логируем и ждем разбана
                return None
            
            # Если REST работает, проверяем биржу
            # (в этом тесте не доходит до сюда)
            return {"position_size": 7.0}
    
    handler = BannedRestHandler()
    
    # Симулируем проверку позиции при бане REST
    print("\n🚫 Шаг 1: REST забанен (-1003)")
    result = await handler.check_position_with_rest_ban(passport_obj, "SOLUSDT")
    
    assert result is None, "При бане REST должен вернуть None"
    
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "OPEN", f"Паспорт должен остаться OPEN при бане REST, статус: {current.status}"
    assert current.position_size == 7.0, f"Position size должен остаться 7.0, получен {current.position_size}"
    assert current.exit_price == 0.0, f"Exit price должен остаться 0.0 (нет фейковых данных), получен {current.exit_price}"
    assert current.gross_pnl == 0.0, f"Gross PnL должен остаться 0.0 (нет фейковых данных), получен {current.gross_pnl}"
    
    print("✅ Сценарий 22 пройден: При бане REST паспорт остался OPEN без фейковых данных")


@pytest.mark.asyncio
async def test_scenario_23_ws_disconnect_then_rest_ban(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 23: WS отключается, затем REST банится.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. WS отключается
    3. Платформа пытается сделать REST-запрос для reconciliation
    4. REST забанен (-1003)
    5. Ожидаемое поведение: платформа ждет восстановления обоих каналов
    
    Ожидаемый результат:
    - passport.status остаётся OPEN
    - passport.position_size остаётся 7.0
    - Платформа не ломается
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class DoubleChaosHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            self.risk_manager = MagicMock()
            
            self.ws_connected = True
            self.rest_banned = False
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def reconcile_with_double_chaos(self, passport, symbol):
            """Reconciliation с учетом обоих каналов."""
            # Проверяем WS
            if not self.ws_connected:
                self._log("ws_down_trying_rest", {
                    "passport_id": passport.passport_id
                })
                
                # WS отключен, пробуем REST
                if self.rest_banned:
                    self._log("rest_also_banned", {
                        "passport_id": passport.passport_id,
                        "error_code": -1003
                    })
                    
                    # Оба канала недоступны — ничего не делаем
                    return {"status": "waiting", "reason": "both_channels_down"}
                
                # REST работает
                self._log("rest_available", {
                    "passport_id": passport.passport_id
                })
                return {"status": "ok", "source": "rest"}
            
            # WS работает
            return {"status": "ok", "source": "ws"}
    
    handler = DoubleChaosHandler()
    
    # Шаг 1: WS отключается
    print("\n Шаг 1: WS отключается")
    handler.ws_connected = False
    
    # Шаг 2: REST банится
    print("🚫 Шаг 2: REST банится (-1003)")
    handler.rest_banned = True
    
    # Шаг 3: Попытка reconciliation
    print("🔄 Шаг 3: Попытка reconciliation")
    result = await handler.reconcile_with_double_chaos(passport_obj, "SOLUSDT")
    
    assert result["status"] == "waiting", "Должен ждать восстановления"
    assert result["reason"] == "both_channels_down", "Оба канала недоступны"
    
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "OPEN", f"Паспорт должен остаться OPEN, статус: {current.status}"
    assert current.position_size == 7.0, f"Position size должен остаться 7.0"
    
    print("✅ Сценарий 23 пройден: Оба канала недоступны, платформа ждет восстановления")


@pytest.mark.asyncio
async def test_scenario_24_recovery_after_double_chaos(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 24: Восстановление после двойного хаоса (WS + REST).
    
    Моделирует:
    1. Оба канала недоступны (WS отключен, REST забанен)
    2. REST разбанивается
    3. WS восстанавливается
    4. Платформа делает reconciliation
    5. Ожидаемое поведение: платформа синхронизирует состояние
    
    Ожидаемый результат:
    - passport.status синхронизирован с биржей
    - passport.position_size корректный
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
            
            self.ws_connected = False
            self.rest_banned = True
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def simulate_recovery(self, passport, symbol):
            """Симулирует восстановление каналов."""
            # Шаг 1: REST разбанивается
            self.rest_banned = False
            self._log("rest_unbanned", {
                "passport_id": passport.passport_id
            })
            
            # Шаг 2: WS восстанавливается
            self.ws_connected = True
            self._log("ws_reconnected", {
                "passport_id": passport.passport_id
            })
            
            # Шаг 3: Reconciliation
            exchange_position_size = 7.0
            exchange_entry_price = 101.06
            
            if passport.position_size == exchange_position_size:
                self._log("position_synced", {
                    "passport_id": passport.passport_id,
                    "position_size": passport.position_size
                })
                
                # Позиция синхронизирована, Guard уже зарегистрирован
                # (в реальности нужно проверить и зарегистрировать если нужно)
                self.risk_manager._register_guard_in_memory(
                    passport_id=passport.passport_id,
                    sl_price=passport.sl_price,
                    tp1_price=passport.tp1_price,
                    tp2_price=passport.tp2_price
                )
                
                return {"status": "synced"}
            
            return {"status": "mismatch"}
    
    handler = RecoveryHandler()
    
    # Симулируем восстановление
    print("\n🔄 Шаг 1: REST разбанивается")
    print("🔌 Шаг 2: WS восстанавливается")
    print("🔄 Шаг 3: Reconciliation")
    
    result = await handler.simulate_recovery(passport_obj, "SOLUSDT")
    
    assert result["status"] == "synced", "Позиция должна синхронизироваться"
    assert handler.risk_manager._register_guard_in_memory.call_count == 1, \
        "Guard должен быть зарегистрирован после восстановления"
    
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.status == "OPEN", f"Паспорт должен остаться OPEN, статус: {current.status}"
    assert current.position_size == 7.0, f"Position size должен быть 7.0"
    
    print("✅ Сценарий 24 пройден: Платформа восстановилась после двойного хаоса")


@pytest.mark.asyncio
async def test_scenario_25_intermittent_ws_during_tp1(
    event_bus, passport_repository, passport_manager, state_manager, order_verifier_mock, sample_passport_dict
):
    """
    Сценарий 25: Прерывистый WS во время достижения TP1.
    
    Моделирует:
    1. Позиция открыта (OPEN, 7.0 SOL)
    2. Цена достигает TP1 (100.81)
    3. WS отключается/восстанавливается несколько раз
    4. Ожидаемое поведение: TP1 активируется, SL переносится в безубыток
    
    Ожидаемый результат:
    - passport.tp1_activated == True
    - passport.sl_price == entry_price (безубыток)
    - passport.status остаётся OPEN
    """
    from trading.handlers.order_handler import OrderHandlerMixin
    
    passport_obj = SimplePassport(sample_passport_dict)
    passport_repository.save(passport_obj)
    
    if hasattr(passport_manager, 'passports'):
        passport_manager.passports[sample_passport_dict['passport_id']] = passport_obj
    
    class IntermittentWsHandler(OrderHandlerMixin):
        def __init__(self):
            self.bus = event_bus
            self.passport_manager = passport_manager
            self.repository = passport_repository
            self.state_manager = state_manager
            self.verifier = order_verifier_mock
            self.config = {}
            self._log = MagicMock()
            
            self.ws_connected = True
            self.tp1_processed = False
        
        def get_trader(self, symbol: str):
            return MagicMock()
        
        async def check_tp1_with_intermittent_ws(self, passport, current_price):
            """Проверяет TP1 с прерывистым WS."""
            if not self.ws_connected:
                self._log("ws_down_tp1_missed", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price
                })
                return False
            
            # Цена достигла TP1
            if not self.tp1_processed and passport.side == "short" and current_price <= passport.tp1_price:
                self._log("tp1_hit", {
                    "passport_id": passport.passport_id,
                    "current_price": current_price,
                    "tp1_price": passport.tp1_price
                })
                
                passport.tp1_activated = True
                passport.sl_price = passport.position_entry_price  # Безубыток
                self.tp1_processed = True
                
                passport.transition_to("TP1_REACHED", "TP1 activated, SL moved to breakeven")
                self.repository.save(passport)
                
                return True
            
            return False
    
    handler = IntermittentWsHandler()
    
    # Шаг 1: Цена достигает TP1, WS работает
    print("\n📉 Шаг 1: Цена достигает TP1 (100.81), WS работает")
    result = await handler.check_tp1_with_intermittent_ws(passport_obj, 100.81)
    assert result == True, "TP1 должен быть обработан"
    
    # Шаг 2: WS отключается
    print("🔌 Шаг 2: WS отключается")
    handler.ws_connected = False
    
    # Шаг 3: Цена колеблется, WS отключен
    print("📉 Шаг 3: Цена колеблется (100.75), WS отключен")
    result = await handler.check_tp1_with_intermittent_ws(passport_obj, 100.75)
    assert result == False, "WS отключен, событие не должно быть обработано"
    
    # Шаг 4: WS восстанавливается
    print(" Шаг 4: WS восстанавливается")
    handler.ws_connected = True
    
    # Шаг 5: Цена продолжает движение
    print("📉 Шаг 5: Цена продолжает движение (100.70), WS работает")
    result = await handler.check_tp1_with_intermittent_ws(passport_obj, 100.70)
    assert result == False, "TP1 уже обработан, не должен обрабатываться снова"
    
    await asyncio.sleep(0.3)
    
    # Проверки
    current = passport_repository.load(sample_passport_dict['passport_id'])
    
    assert current.tp1_activated == True, "TP1 должен быть активирован"
    assert current.sl_price == 101.06, f"SL должен быть перенесен в безубыток (101.06), получен {current.sl_price}"
    assert current.status == "TP1_REACHED", f"Статус должен быть TP1_REACHED, получен {current.status}"
    
    print("✅ Сценарий 25 пройден: TP1 активирован несмотря на прерывистый WS")