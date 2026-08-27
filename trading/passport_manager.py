"""
Менеджер паспортов — хранит все активные паспорта в памяти.
Только CRUD. Без логики изменения статусов.
"""

from typing import Dict, Optional, List
from core.types import PassportStatus
from trading.passport import TradePassport


class PassportManager:
    """Менеджер паспортов (кэш в памяти)."""

    def __init__(self):
        self._passports: Dict[str, TradePassport] = {}

    def create(self, symbol: str, signal_id: str, strategy: str, side: str, entry_price: float, confidence: float = 0.5) -> TradePassport:
        """Создать новый паспорт."""
        passport = TradePassport(
            symbol=symbol,
            status=PassportStatus.SIGNAL_GENERATED.value,
            signal_id=signal_id,
            strategy=strategy,
            side=side,
            entry_price=entry_price,
            confidence=confidence,
            sl_price=0.0,
            tp1_price=0.0,
            tp2_price=0.0,
            position_size=0.0,
            position_entry_price=0.0
        )
        self._passports[passport.passport_id] = passport
        return passport

    def get(self, passport_id: str) -> Optional[TradePassport]:
        """Получить паспорт по ID."""
        return self._passports.get(passport_id)

    def get_all(self) -> List[TradePassport]:
        """Получить все паспорта."""
        return list(self._passports.values())

    def get_active(self) -> List[TradePassport]:
        """Получить все активные паспорта."""
        return [p for p in self._passports.values() if p.status not in (PassportStatus.CLOSED.value, PassportStatus.CANCELED.value, PassportStatus.FAILED.value)]

    def get_by_symbol(self, symbol: str) -> List[TradePassport]:
        """Получить все паспорта по символу."""
        return [p for p in self._passports.values() if p.symbol == symbol]

    def get_active_by_symbol(self, symbol: str) -> Optional[TradePassport]:
        """Получить активный паспорт по символу."""
        for p in self._passports.values():
            if p.symbol == symbol and p.status not in (PassportStatus.CLOSED.value, PassportStatus.CANCELED.value, PassportStatus.FAILED.value):
                return p
        return None

    def get_all_active_by_symbol(self, symbol: str) -> List[TradePassport]:
        """
        🔥 ШАГ 10.4.3: Получить ВСЕ активные паспорта по символу.
        Используется при реконсиляции для подсчёта суммы локальных позиций.
        """
        return [
            p for p in self._passports.values()
            if p.symbol == symbol and p.status not in (
                PassportStatus.CLOSED.value,
                PassportStatus.CANCELED.value,
                PassportStatus.FAILED.value
            )
        ]

    def is_symbol_busy(self, symbol: str) -> bool:
        """Проверить, занят ли символ."""
        return self.get_active_by_symbol(symbol) is not None

    def update(self, passport: TradePassport):
        """Обновить паспорт в кэше."""
        self._passports[passport.passport_id] = passport

    def remove(self, passport_id: str):
        """Удалить паспорт из менеджера."""
        if passport_id in self._passports:
            del self._passports[passport_id]