"""
Шаг 1.5: Карта переходов StateManager реально применяется (T7).
"""
import pytest
from trading.passport import TradePassport
from trading.state_manager import StateManager


@pytest.fixture
def sm():
    return StateManager(passport_manager=None)


def test_t7_forbidden_transition_rejected(sm):
    """Запрещённый переход (CLOSED → OPEN) отклоняется."""
    p = TradePassport(symbol="SOLUSDT", status="CLOSED")
    result = sm.transition(p, "OPEN", "Should be forbidden")
    assert result is False
    assert p.status == "CLOSED"


def test_t7_normal_transition_works(sm):
    """Нормальный переход (ORDER_ACK → OPEN) работает."""
    p = TradePassport(symbol="SOLUSDT", status="ORDER_ACK")
    result = sm.transition(p, "OPEN", "Order filled")
    assert result is True
    assert p.status == "OPEN"


def test_t7_idempotent_transition_is_noop(sm):
    """Повтор того же статуса — тихий no-op, timeline не растёт."""
    p = TradePassport(symbol="SOLUSDT", status="OPEN")
    timeline_before = len(p.timeline)
    result = sm.transition(p, "OPEN", "Duplicate event")
    assert result is False
    assert p.status == "OPEN"
    assert len(p.timeline) == timeline_before


def test_t7_unknown_current_status_rejected(sm):
    """Переход из неизвестного статуса отклоняется."""
    p = TradePassport(symbol="SOLUSDT", status="NONEXISTENT")
    result = sm.transition(p, "OPEN", "Should fail")
    assert result is False
    assert p.status == "NONEXISTENT"