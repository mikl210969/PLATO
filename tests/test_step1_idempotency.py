"""
Шаг 1: Идемпотентность StateManager и детекция аномалий дрейфа (T6).
"""
import pytest
from trading.passport import TradePassport
from trading.state_manager import StateManager


@pytest.fixture
def sm():
    return StateManager(passport_manager=None)


def test_t6_duplicate_filled_is_noop(sm):
    """Второй ORDER_FILLED по уже OPEN паспорту — тихий no-op."""
    p = TradePassport(symbol="SOLUSDT", status="OPEN")
    p.position_size = 7.0
    p.position_entry_price = 96.95
    timeline_before = len(p.timeline)

    result = sm.handle_event(p, "ORDER_FILLED", {"executed_qty": 7.0, "price": 96.95})

    assert result is False
    assert p.status == "OPEN"
    assert p.position_size == 7.0
    assert len(p.timeline) == timeline_before


def test_t6_filled_on_closed_passport_is_anomaly(sm):
    """ORDER_FILLED по CLOSED паспорту — аномалия дрейфа, статус не меняется."""
    p = TradePassport(symbol="SOLUSDT", status="CLOSED")

    result = sm.handle_event(p, "ORDER_FILLED", {"executed_qty": 7.0, "price": 96.95})

    assert result is False
    assert p.status == "CLOSED"


def test_t6_normal_filled_still_works(sm):
    """Нормальный путь ORDER_ACK -> OPEN продолжает работать."""
    p = TradePassport(symbol="SOLUSDT", status="ORDER_ACK")

    result = sm.handle_event(p, "ORDER_FILLED", {"executed_qty": 7.0, "price": 96.95})

    assert result is True
    assert p.status == "OPEN"
    assert p.position_size == 7.0
    assert p.position_entry_price == 96.95