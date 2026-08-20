# tests/test_passport_statuses.py
"""
Тест для проверки переходов статусов паспорта.
"""

from core.types import PassportStatus
from trading.passport import TradePassport


def test_passport_status_transitions():
    """Проверка всех переходов статусов."""
    
    passport = TradePassport(symbol="SOLUSDT")
    
    # 1. SIGNAL_GENERATED → ORDER_SENT
    passport.transition_to(PassportStatus.ORDER_SENT.value, "Order sent")
    assert passport.status == PassportStatus.ORDER_SENT.value
    print(f"✅ SIGNAL_GENERATED → ORDER_SENT")
    
    # 2. ORDER_SENT → ORDER_ACK (MARKET)
    passport.transition_to(PassportStatus.ORDER_ACK.value, "Order ACK")
    assert passport.status == PassportStatus.ORDER_ACK.value
    print(f"✅ ORDER_SENT → ORDER_ACK")
    
    # 3. ORDER_ACK → OPEN (FILLED)
    passport.transition_to(PassportStatus.OPEN.value, "Order filled")
    assert passport.status == PassportStatus.OPEN.value
    print(f"✅ ORDER_ACK → OPEN")
    
    # 4. OPEN → CLOSED
    passport.close("EXTERNAL_CLOSE", 0.0)
    assert passport.status == PassportStatus.CLOSED.value
    print(f"✅ OPEN → CLOSED")
    
    print("\n✅ Все переходы статусов работают корректно!")


if __name__ == "__main__":
    test_passport_status_transitions()