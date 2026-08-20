"""
RecoveryManager — восстановление состояния платформы после перезапуска.
Синхронизирует паспорта с реальным состоянием на бирже.
"""

import asyncio
from typing import Dict, Optional, List, Any
from pathlib import Path

from core.types import PassportStatus
from core.event_bus import EventBus
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.trader import Trader


class RecoveryManager:
    """
    Восстановление платформы после перезапуска.
    - Загружает все паспорта из репозитория
    - Синхронизирует их с реальным состоянием на бирже
    - Корректирует статусы паспортов
    """

    def __init__(
        self,
        passport_manager: PassportManager,
        passport_repository: PassportRepository,
        trader: Trader,
        config: Dict,
        json_logger: Any = None
    ):
        self.passport_manager = passport_manager
        self.repository = passport_repository
        self.trader = trader
        self.config = config
        self.json_logger = json_logger

    def _log(self, event: str, data: Optional[Dict] = None):
        """Логирование."""
        if self.json_logger:
            self.json_logger.log(
                module="recovery_manager",
                event=event,
                data=data or {}
            )
        else:
            print(f"♻️ [RECOVERY] {event}: {data}")

    async def recover(self) -> Dict[str, Any]:
        """
        Основной метод восстановления.
        Возвращает статистику восстановления.
        """
        self._log("recovery_started", {})

        stats = {
            "total_passports": 0,
            "active_passports": 0,
            "synced": 0,
            "corrected": 0,
            "errors": 0
        }

        # 1. Загружаем все паспорта из репозитория
        passports = await self._load_all_passports()
        stats["total_passports"] = len(passports)

        if not passports:
            self._log("no_passports_found", {})
            return stats

        self._log("passports_loaded", {"count": len(passports)})

        # 2. Загружаем паспорта в менеджер
        for passport in passports:
            self.passport_manager.update(passport)

        # 3. Получаем активные паспорта
        active_passports = self.passport_manager.get_active()
        stats["active_passports"] = len(active_passports)

        if not active_passports:
            self._log("no_active_passports", {})
            return stats

        self._log("active_passports_found", {
            "count": len(active_passports),
            "ids": [p.passport_id for p in active_passports]
        })

        # 4. Синхронизируем каждый активный паспорт с биржей
        for passport in active_passports:
            try:
                corrected = await self._sync_passport(passport)
                if corrected:
                    stats["corrected"] += 1
                stats["synced"] += 1
            except Exception as e:
                stats["errors"] += 1
                self._log("sync_error", {
                    "passport_id": passport.passport_id,
                    "error": str(e)
                })

        self._log("recovery_completed", stats)
        return stats

    async def _load_all_passports(self) -> List[TradePassport]:
        """
        Загрузить все паспорта из репозитория.
        """
        passports = []
        logs_dir = Path("logs")
        
        if not logs_dir.exists():
            return passports

        # Ищем все файлы паспортов
        for file_path in logs_dir.glob("passport_*.json"):
            try:
                passport_id = file_path.stem.replace("passport_", "")
                passport = await self._load_passport_from_file(file_path)
                if passport:
                    passports.append(passport)
            except Exception as e:
                self._log("load_file_error", {
                    "file": str(file_path),
                    "error": str(e)
                })

        return passports

    async def _load_passport_from_file(self, file_path: Path) -> Optional[TradePassport]:
        """Загрузить паспорт из файла."""
        import json
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return TradePassport(**data)
        except Exception as e:
            self._log("passport_load_error", {
                "file": str(file_path),
                "error": str(e)
            })
            return None

    async def _sync_passport(self, passport: TradePassport) -> bool:
        """
        Синхронизировать паспорт с биржей.
        Возвращает True, если состояние было скорректировано.
        """
        symbol = passport.symbol
        status = passport.status

        self._log("sync_passport_start", {
            "passport_id": passport.passport_id,
            "status": status,
            "symbol": symbol
        })

        corrected = False

        try:
            # 1. Получаем позицию с биржи
            position = await self.trader.get_position_from_exchange(symbol)
            position_size = position.get('size', 0) if position else 0

            # 2. Получаем статус ордера с биржи (если есть order_id)
            order_id = None
            if passport.orders:
                order_id = passport.orders[-1].get('order_id')

            order_status = None
            if order_id:
                order_info = await self.trader.get_order_status(symbol, str(order_id))
                if order_info:
                    order_status = order_info.get('status')

            self._log("sync_exchange_data", {
                "passport_id": passport.passport_id,
                "position_size": position_size,
                "order_status": order_status
            })

            # 3. Корректируем состояние паспорта

            # Случай A: Паспорт говорит OPEN, а позиции нет
            if status == PassportStatus.OPEN.value and position_size == 0:
                self._log("sync_correction_close", {
                    "passport_id": passport.passport_id,
                    "reason": "Position closed externally"
                })
                passport.close("EXTERNAL_CLOSE", 0.0)
                self.repository.save(passport)
                corrected = True

            # Случай B: Паспорт говорит ORDER_SENT/ORDER_ACK/LIMIT_ON_BOOK, а ордер уже FILLED
            elif status in (PassportStatus.ORDER_SENT.value, PassportStatus.ORDER_ACK.value, PassportStatus.LIMIT_ON_BOOK.value):
                if order_status == 'FILLED' and position_size != 0:
                    self._log("sync_correction_open", {
                        "passport_id": passport.passport_id,
                        "reason": "Order filled, but passport not updated"
                    })
                    # Обновляем паспорт
                    passport.position_size = position_size
                    passport.position_entry_price = position.get('entry_price', passport.entry_price)
                    passport.transition_to(PassportStatus.OPEN.value, "Recovered: order filled")
                    self.repository.save(passport)
                    corrected = True

                elif order_status == 'CANCELED' or order_status == 'EXPIRED':
                    self._log("sync_correction_cancel", {
                        "passport_id": passport.passport_id,
                        "reason": f"Order {order_status}"
                    })
                    passport.transition_to(PassportStatus.CANCELED.value, f"Recovered: order {order_status}")
                    self.repository.save(passport)
                    corrected = True

                elif order_status is None and position_size == 0:
                    # Ордера нет и позиции нет — скорее всего, ордер не создавался или был удалён
                    self._log("sync_correction_cancel_no_order", {
                        "passport_id": passport.passport_id,
                        "reason": "No order found on exchange"
                    })
                    passport.transition_to(PassportStatus.CANCELED.value, "Recovered: no order on exchange")
                    self.repository.save(passport)
                    corrected = True

            # Случай C: Паспорт говорит OPEN, а позиция есть — просто подтверждаем
            elif status == PassportStatus.OPEN.value and position_size != 0:
                self._log("sync_confirmed_open", {
                    "passport_id": passport.passport_id,
                    "position_size": position_size
                })

            # Случай D: Паспорт говорит CLOSED, а позиция есть
            elif status == PassportStatus.CLOSED.value and position_size != 0:
                self._log("sync_correction_reopen", {
                    "passport_id": passport.passport_id,
                    "reason": "Position reopened externally"
                })
                passport.transition_to(PassportStatus.OPEN.value, "Recovered: position reopened")
                passport.position_size = position_size
                passport.position_entry_price = position.get('entry_price', 0)
                self.repository.save(passport)
                corrected = True

        except Exception as e:
            self._log("sync_error", {
                "passport_id": passport.passport_id,
                "error": str(e)
            })
            raise

        return corrected