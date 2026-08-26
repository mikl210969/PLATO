"""
Шаг 7.5: Гонка состояний WS vs REST — все варианты переходов статусов (T9-T11).
"""
import pytest
from unittest.mock import MagicMock
from trading.passport import TradePassport
from trading.state_manager import StateManager
from trading.passport_manager import PassportManager


@pytest.fixture
def sm():
    pm = PassportManager()
    return StateManager(pm)


def test_t9_ws_new_before_order_sent(sm):
    """
    Сценарий: WS `NEW` пришёл ДО того, как REST-ответ обработался.
    Паспорт в SIGNAL_GENERATED → должен перейти в ORDER_ACK.
    """
    p = TradePassport(symbol="SOLUSDT", status="SIGNAL_GENERATED")
    
    # WS приходит первым
    result = sm.handle_event(p, "ORDER_ACK", {"details": "Order ACK received"})
    
    assert result is True
    assert p.status == "ORDER_ACK"


def test_t10_ws_new_after_order_sent(sm):
    """
    Сценарий: WS `NEW` пришёл ПОСЛЕ того, как REST-ответ обработался.
    Паспорт в ORDER_SENT → должен перейти в ORDER_ACK.
    """
    p = TradePassport(symbol="SOLUSDT", status="ORDER_SENT")
    
    result = sm.handle_event(p, "ORDER_ACK", {"details": "Order ACK received"})
    
    assert result is True
    assert p.status == "ORDER_ACK"


def test_t11_ws_filled_before_order_sent(sm):
    """
    Сценарий: WS `FILLED` пришёл ДО того, как REST-ответ обработался (edge case).
    Паспорт в SIGNAL_GENERATED → должен перейти в OPEN.
    """
    p = TradePassport(symbol="SOLUSDT", status="SIGNAL_GENERATED")
    
    result = sm.handle_event(p, "ORDER_FILLED", {
        "executed_qty": 7.0,
        "price": 96.89
    })
    
    assert result is True
    assert p.status == "OPEN"
    assert p.position_size == 7.0
    assert p.position_entry_price == 96.89


def test_t12_ws_filled_after_order_sent(sm):
    """
    Сценарий: WS `FILLED` пришёл ПОСЛЕ того, как REST-ответ обработался.
    Паспорт в ORDER_SENT → должен перейти в OPEN.
    """
    p = TradePassport(symbol="SOLUSDT", status="ORDER_SENT")
    
    result = sm.handle_event(p, "ORDER_FILLED", {
        "executed_qty": 7.0,
        "price": 96.89
    })
    
    assert result is True
    assert p.status == "OPEN"
    assert p.position_size == 7.0


def test_t13_ws_filled_after_order_ack(sm):
    """
    Сценарий: Нормальный путь ORDER_SENT → ORDER_ACK → FILLED → OPEN.
    """
    p = TradePassport(symbol="SOLUSDT", status="ORDER_ACK")
    
    result = sm.handle_event(p, "ORDER_FILLED", {
        "executed_qty": 7.0,
        "price": 96.89
    })
    
    assert result is True
    assert p.status == "OPEN"
    assert p.position_size == 7.0


def test_t14_ws_canceled_before_order_sent(sm):
    """
    Сценарий: WS `CANCELED` пришёл ДО того, как REST-ответ обработался.
    Паспорт в SIGNAL_GENERATED → должен перейти в CANCELED.
    """
    p = TradePassport(symbol="SOLUSDT", status="SIGNAL_GENERATED")
    
    result = sm.handle_event(p, "ORDER_CANCELED", {})
    
    assert result is True
    assert p.status == "CANCELED"