"""
LifecycleManager — управление временем жизни заявок (TTL).
Следит за лимитными ордерами и инициирует их отмену или конвертацию по истечении времени.
"""

import asyncio
import time
from typing import Dict, Optional, Any
from datetime import datetime, timezone

from core.types import PassportStatus
from core.event_bus import EventBus, Event
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.trader import Trader


class LifecycleManager:
    """
    Управляет временем жизни заявок.
    - Запускает TTL-таймеры для лимитных ордеров
    - По истечении TTL отправляет событие Оркестратору
    - Поддерживает отмену таймеров при исполнении ордера
    """

    def __init__(
        self,
        event_bus: EventBus,
        passport_manager: PassportManager,
        config: Dict,
        json_logger: Any = None
    ):
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.config = config
        self.json_logger = json_logger

        # Активные таймеры: {passport_id: asyncio.Task}
        self._timers: Dict[str, asyncio.Task] = {}

        # Подписываемся на события
        self._subscribe_to_events()
        self._log("init", {"message": "LifecycleManager initialized"})

    def _log(self, event: str, data: Optional[Dict] = None):
        """Логирование."""
        if self.json_logger:
            self.json_logger.log(
                module="lifecycle_manager",
                event=event,
                data=data or {}
            )
        else:
            print(f"⏱️ [LIFECYCLE] {event}: {data}")

    def _subscribe_to_events(self):
        """Подписка на события шины."""
        self.bus.subscribe("PASSPORT_CREATED", self._on_passport_created)
        self.bus.subscribe("ORDER_FILLED", self._on_order_filled)
        self.bus.subscribe("ORDER_CANCELED", self._on_order_canceled)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)

    async def _on_passport_created(self, event: Event):
        """Обработка создания паспорта."""
        payload = event.payload
        passport_id = payload.get('passport_id')
        order_type = payload.get('order_type', 'market')
        client_order_id = payload.get('client_order_id', '')

        # Только для лимитных ордеров
        if order_type != 'limit':
            return

        # 🔥 Проверяем, что client_order_id — это строка
        if client_order_id and isinstance(client_order_id, str):
            if 'SL_' in client_order_id:
                self._log("ttl_skip_sl_order", {
                    "passport_id": passport_id,
                    "client_order_id": client_order_id
                })
                return

        # 🔥 Проверяем, что passport_id — это строка
        if not passport_id or not isinstance(passport_id, str):
            self._log("ttl_skip_invalid_passport_id", {"passport_id": passport_id})
            return

        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("passport_not_found", {"passport_id": passport_id})
            return

        self._log("ttl_started_for_limit_order", {
            "passport_id": passport_id,
            "order_type": order_type
        })

        await self._start_ttl_timer(passport)

    async def _start_ttl_timer(self, passport: TradePassport):
        """Запустить TTL-таймер для паспорта."""
        passport_id = passport.passport_id

        # Проверяем, нет ли уже активного таймера
        if passport_id in self._timers:
            self._log("timer_already_running", {"passport_id": passport_id})
            return

        # Получаем TTL из конфига (по умолчанию 300 секунд = 5 минут)
        ttl_seconds = self.config.get('trading', {}).get('ttl_seconds', 300)

        self._log("ttl_timer_started", {
            "passport_id": passport_id,
            "ttl_seconds": ttl_seconds,
            "symbol": passport.symbol,
            "side": passport.side,
            "entry_price": passport.entry_price
        })

        # Создаём задачу-таймер
        task = asyncio.create_task(self._ttl_timer_task(passport, ttl_seconds))
        self._timers[passport_id] = task

    async def _ttl_timer_task(self, passport: TradePassport, ttl_seconds: int):
        """Задача таймера."""
        passport_id = passport.passport_id

        try:
            # Ждём TTL секунд
            await asyncio.sleep(ttl_seconds)

            # Проверяем, что паспорт всё ещё существует и активен
            current_passport = self.passport_manager.get(passport_id)
            if not current_passport:
                self._log("ttl_passport_not_found", {"passport_id": passport_id})
                return

            # Проверяем статус
            if current_passport.status != PassportStatus.LIMIT_ON_BOOK.value:
                self._log("ttl_skip_not_limit", {
                    "passport_id": passport_id,
                    "status": current_passport.status
                })
                return

            self._log("ttl_expired", {
                "passport_id": passport_id,
                "symbol": current_passport.symbol,
                "entry_price": current_passport.entry_price,
                "ttl_seconds": ttl_seconds
            })

            # Отправляем событие Оркестратору
            await self.bus.publish(
                event_type="TTL_EXPIRED",
                source="lifecycle_manager",
                payload={
                    "passport_id": passport_id,
                    "symbol": current_passport.symbol,
                    "entry_price": current_passport.entry_price,
                    "order_id": self._get_order_id(current_passport)
                },
                symbol=current_passport.symbol
            )

        except asyncio.CancelledError:
            # Таймер отменён — нормальная ситуация
            self._log("ttl_timer_cancelled", {"passport_id": passport_id})
        except Exception as e:
            self._log("ttl_timer_error", {
                "passport_id": passport_id,
                "error": str(e)
            })
        finally:
            # Удаляем таймер из словаря
            if passport_id in self._timers:
                del self._timers[passport_id]

    def _get_order_id(self, passport: TradePassport) -> Optional[str]:
        """Получить order_id из паспорта."""
        if passport.orders:
            return passport.orders[-1].get('order_id')
        return None

    async def _on_order_filled(self, event: Event):
        """Обработка исполнения ордера."""
        payload = event.payload
        passport_id = payload.get('passport_id')
        if not passport_id:
            return

        # Отменяем таймер, если он есть
        await self._cancel_timer(passport_id, "ORDER_FILLED")

    async def _on_order_canceled(self, event: Event):
        """Обработка отмены ордера."""
        payload = event.payload
        passport_id = payload.get('passport_id')
        if not passport_id:
            return

        # Отменяем таймер
        await self._cancel_timer(passport_id, "ORDER_CANCELED")

    async def _on_position_closed(self, event: Event):
        """Обработка закрытия позиции."""
        payload = event.payload
        passport_id = payload.get('passport_id')
        if not passport_id:
            return

        # Отменяем таймер
        await self._cancel_timer(passport_id, "POSITION_CLOSED")

    async def _cancel_timer(self, passport_id: str, reason: str):
        """Отменить активный таймер."""
        if passport_id not in self._timers:
            return

        task = self._timers[passport_id]
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        del self._timers[passport_id]
        self._log("ttl_timer_canceled", {
            "passport_id": passport_id,
            "reason": reason
        })

    async def cancel_all_timers(self):
        """Отменить все активные таймеры (при остановке)."""
        if not self._timers:
            return

        self._log("cancelling_all_timers", {"count": len(self._timers)})
        for passport_id, task in list(self._timers.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._timers.clear()

    async def stop(self):
        """Остановка менеджера."""
        await self.cancel_all_timers()
        self._log("stopped", {"message": "LifecycleManager stopped"})