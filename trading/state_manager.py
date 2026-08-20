"""
StateManager — управление переходами состояний паспорта.
"""

from typing import Optional, Dict, Any
from core.types import PassportStatus
from trading.passport import TradePassport
from trading.passport_manager import PassportManager


class StateManager:
    """Управляет переходами состояний паспорта."""

    def __init__(self, passport_manager: PassportManager):
        self.passport_manager = passport_manager
        self._allowed_transitions = self._build_transition_map()

    def _build_transition_map(self) -> Dict[str, set]:
        """Карта разрешённых переходов."""
        return {
            PassportStatus.SIGNAL_GENERATED.value: {
                PassportStatus.ORDER_SENT.value,
                PassportStatus.CANCELED.value,
                PassportStatus.CLOSED.value
            },
            PassportStatus.ORDER_SENT.value: {
                PassportStatus.ORDER_ACK.value,
                PassportStatus.OPEN.value,  # MARKET-ордер может сразу стать OPEN
                PassportStatus.CANCELED.value,
                PassportStatus.FAILED.value
            },
            PassportStatus.ORDER_ACK.value: {
                PassportStatus.LIMIT_ON_BOOK.value,
                PassportStatus.OPEN.value,
                PassportStatus.CANCELED.value,
                PassportStatus.FAILED.value
            },
            PassportStatus.LIMIT_ON_BOOK.value: {
                PassportStatus.OPEN.value,
                PassportStatus.CANCELED.value,
                PassportStatus.FAILED.value
            },
            PassportStatus.OPEN.value: {
                PassportStatus.PARTIAL_CLOSE.value,
                PassportStatus.CLOSING.value,
                PassportStatus.CLOSED.value,
                PassportStatus.CANCELED.value
            },
            PassportStatus.PARTIAL_CLOSE.value: {
                PassportStatus.OPEN.value,
                PassportStatus.CLOSING.value,
                PassportStatus.CLOSED.value
            },
            PassportStatus.CLOSING.value: {
                PassportStatus.CLOSED.value,
                PassportStatus.FAILED.value
            },
            PassportStatus.CANCELED.value: set(),  # Терминальное
            PassportStatus.CLOSED.value: set(),    # Терминальное
            PassportStatus.FAILED.value: set(),    # Терминальное
        }

    def can_transition(self, current_status: str, new_status: str) -> bool:
        """Проверить, разрешён ли переход."""
        allowed = self._allowed_transitions.get(current_status, set())
        return new_status in allowed

    def transition(self, passport: TradePassport, new_status: str, reason: str = "") -> bool:
        """
        Выполнить переход, если он разрешён.
        Возвращает True, если переход выполнен.
        """
        current = passport.status

        if not self.can_transition(current, new_status):
            # Просто игнорируем, без вывода в консоль
            pass
            return False

        # Выполняем переход
        passport.transition_to(new_status, reason)

        # Логируем
        emoji = self._get_emoji(new_status)
        print(f"{emoji} [STATE] {current} → {new_status} ({reason})")

        return True

    def _get_emoji(self, status: str) -> str:
        """Получить эмодзи для статуса."""
        emojis = {
            PassportStatus.SIGNAL_GENERATED.value: "📌",
            PassportStatus.ORDER_SENT.value: "📤",
            PassportStatus.ORDER_ACK.value: "✅",
            PassportStatus.LIMIT_ON_BOOK.value: "📚",
            PassportStatus.OPEN.value: "🔓",
            PassportStatus.PARTIAL_CLOSE.value: "🔻",
            PassportStatus.CLOSING.value: "🔒",
            PassportStatus.CLOSED.value: "✔️",
            PassportStatus.CANCELED.value: "❌",
            PassportStatus.FAILED.value: "💀",
        }
        return emojis.get(status, "🔄")

    def handle_event(self, passport: TradePassport, event_type: str, event_data: Dict[str, Any]) -> bool:
        """
        Обработать событие и выполнить соответствующий переход.
        """
        status = passport.status
        new_status = None
        reason = ""

        # Определяем целевой статус на основе события
        if event_type == "ORDER_SENT":
            new_status = PassportStatus.ORDER_SENT.value
            reason = "Order sent to exchange"

        elif event_type == "ORDER_ACK":
            order_type = event_data.get('order_type', 'MARKET')
            if order_type == 'LIMIT':
                new_status = PassportStatus.LIMIT_ON_BOOK.value
                reason = "Limit order on book"
            else:
                new_status = PassportStatus.ORDER_ACK.value
                reason = "Order ACK received"

        elif event_type == "ORDER_FILLED":
            new_status = PassportStatus.OPEN.value
            reason = "Order filled"
            # Обновляем данные позиции
            passport.position_size = event_data.get('executed_qty', 0)
            passport.position_entry_price = event_data.get('price', 0)

        elif event_type == "ORDER_PARTIAL":
            new_status = PassportStatus.OPEN.value
            reason = f"Partial fill: {event_data.get('executed_qty', 0)}"
            # Обновляем данные позиции
            passport.position_size = event_data.get('executed_qty', 0)
            passport.position_entry_price = event_data.get('price', 0)

        elif event_type == "ORDER_CANCELED":
            new_status = PassportStatus.CANCELED.value
            reason = "Order canceled"

        elif event_type == "ORDER_FAILED":
            new_status = PassportStatus.FAILED.value
            reason = event_data.get('error', "Order failed")

        elif event_type == "POSITION_CLOSED":
            new_status = PassportStatus.CLOSED.value
            reason = event_data.get('exit_reason', "Position closed")
            # Обновляем данные закрытия
            passport.exit_price = event_data.get('exit_price', 0)
            passport.gross_pnl = event_data.get('gross_pnl', 0)
            passport.commission = event_data.get('commission', 0)
            passport.net_pnl = passport.gross_pnl - passport.commission

        elif event_type == "POSITION_CLOSING":
            new_status = PassportStatus.CLOSING.value
            reason = "Closing position"

        elif event_type == "PARTIAL_CLOSE":
            new_status = PassportStatus.PARTIAL_CLOSE.value
            reason = f"Partial close: {event_data.get('closed_qty', 0)}"

        else:
            print(f"⚠️ [STATE] Unknown event: {event_type}")
            return False

        if not new_status:
            return False

        # Выполняем переход
        return self.transition(passport, new_status, reason)

    def sync_with_exchange(self, passport: TradePassport, exchange_status: str, position_size: float = 0) -> bool:
        """
        Синхронизировать паспорт с состоянием на бирже.
        """
        current = passport.status

        # Если на бирже позиции нет, а паспорт говорит OPEN
        if position_size == 0 and current == PassportStatus.OPEN.value:
            return self.transition(passport, PassportStatus.CLOSED.value, "External close (sync)")

        # Если на бирже позиция есть, а паспорт говорит CLOSED
        if position_size > 0 and current == PassportStatus.CLOSED.value:
            return self.transition(passport, PassportStatus.OPEN.value, "External open (sync)")

        # Если статусы совпадают — ничего не делаем
        return True