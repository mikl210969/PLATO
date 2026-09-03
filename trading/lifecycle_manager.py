"""
LifecycleManager — управление временем жизни заявок (TTL).
Следит за лимитными ордерами и инициирует их отмену или конвертацию по истечении времени.

🔥 ИСПРАВЛЕНО:
1. TTL-задача теперь сохраняется в self._timers → отмена по ORDER_FILLED/POSITION_CLOSED работает.
2. Белый список статусов: TTL срабатывает ТОЛЬКО пока паспорт реально в ожидании исполнения.
   Частично исполненный (PARTIAL_CLOSE) или открытый (OPEN) паспорт TTL больше не трогает.
"""

import asyncio
import time
from typing import Dict, Optional, Any

from core.event_bus import EventBus, Event
from trading.passport_manager import PassportManager
from core.logger import get_logger
logger = get_logger(__name__)

class LifecycleManager:
    """
    Управляет временем жизни заявок.
    - Запускает TTL-таймеры для лимитных ордеров
    - По истечении TTL отправляет событие Оркестратору
    - Отменяет таймеры при исполнении ордера / закрытии позиции
    """

    # 🔥 БЕЛЫЙ СПИСОК: только эти статусы означают "ордер ещё висит на бирже"
    PENDING_STATUSES = {
        "ORDER_SENT", "ORDER_ACK", "LIMIT_ON_BOOK", "NEW", "PENDING",
    }

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

        self._subscribe_to_events()
        self._log("init", {"message": "LifecycleManager initialized"})

    def _log(self, event: str, data: Optional[Dict] = None):
        if self.json_logger:
            self.json_logger.log(
                module="lifecycle_manager",
                event=event,
                data=data or {}
            )
        else:
            logger.info(f"⏱️ [LIFECYCLE] {event}: {data}")

    def _subscribe_to_events(self):
        self.bus.subscribe("PASSPORT_CREATED", self._on_passport_created)
        self.bus.subscribe("ORDER_FILLED", self._on_order_filled)
        self.bus.subscribe("ORDER_CANCELED", self._on_order_canceled)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)

    async def _on_passport_created(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        order_type = payload.get('order_type', 'market')
        client_order_id = payload.get('client_order_id', '')

        self._log("ttl_event_received", {
            "passport_id": passport_id,
            "order_type": order_type,
            "client_order_id": client_order_id
        })

        if str(order_type).lower() != 'limit':
            self._log("ttl_skip_not_limit", {"order_type": order_type})
            return

        if client_order_id and isinstance(client_order_id, str):
            if 'SL_' in client_order_id or 'TP_' in client_order_id:
                self._log("ttl_skip_sl_tp_order", {"client_order_id": client_order_id})
                return

        if not passport_id or not isinstance(passport_id, str):
            self._log("ttl_skip_invalid_passport_id", {"passport_id": passport_id})
            return

        passport = self.passport_manager.get(passport_id)
        if not passport:
            active_ids = [p.passport_id for p in self.passport_manager.get_active()]
            self._log("ttl_passport_not_found", {
                "passport_id": passport_id,
                "active_ids": active_ids
            })
            return

        self._log("ttl_starting_timer", {"passport_id": passport_id})
        await self._start_ttl_timer(passport)

    async def _start_ttl_timer(self, passport):
        """Запустить TTL-таймер для паспорта. 🔥 Задача сохраняется в self._timers."""
        passport_id = passport.passport_id

        trading_config = self.config.get('trading', {})
        ttl_seconds = trading_config.get('ttl_seconds', 300)

        async def ttl_task():
            try:
                await asyncio.sleep(ttl_seconds)

                current_passport = self.passport_manager.get(passport_id)
                if not current_passport:
                    return

                # 🔥 ЗАЩИТА №1 (белый список): стреляем TTL только если паспорт
                # всё ещё в состоянии "ордер висит". OPEN / PARTIAL_CLOSE / CLOSED
                # и любые другие статусы = выходим, позицию НЕ трогаем.
                if current_passport.status not in self.PENDING_STATUSES:
                    self._log("ttl_skip_not_pending", {
                        "passport_id": passport_id,
                        "status": current_passport.status
                    })
                    return

                # 🔥 ЗАЩИТА №2: если последний ордер уже исполнен/отменен — выходим
                order_info = current_passport.orders[-1] if current_passport.orders else {}
                order_status = str(order_info.get('status', '')).upper()
                if order_status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED"):
                    self._log("ttl_skip_order_finished", {
                        "passport_id": passport_id,
                        "order_status": order_status
                    })
                    return

                self._log("ttl_expired_triggering", {
                    "passport_id": passport_id,
                    "order_id": order_info.get('order_id'),
                    "status": current_passport.status
                })

                await self.bus.publish(
                    event_type="TTL_EXPIRED",
                    source="lifecycle_manager",
                    payload={
                        "passport_id": passport_id,
                        "symbol": current_passport.symbol,
                        "order_id": order_info.get('order_id')
                    },
                    symbol=current_passport.symbol
                )

            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._log("ttl_timer_error", {"passport_id": passport_id, "error": str(e)})

        # 🔥 ГЛАВНОЕ ИСПРАВЛЕНИЕ: сохраняем задачу, чтобы её можно было отменить
        task = asyncio.create_task(ttl_task())
        self._timers[passport_id] = task

        # Автоочистка словаря при завершении задачи
        def _cleanup(t, pid=passport_id):
            self._timers.pop(pid, None)
        task.add_done_callback(_cleanup)

        self._log("ttl_timer_started", {
            "passport_id": passport_id,
            "ttl_seconds": ttl_seconds
        })

    async def _on_order_filled(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        if passport_id:
            await self._cancel_timer(passport_id, "ORDER_FILLED")

    async def _on_order_canceled(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        if passport_id:
            await self._cancel_timer(passport_id, "ORDER_CANCELED")

    async def _on_position_closed(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        if passport_id:
            await self._cancel_timer(passport_id, "POSITION_CLOSED")

    async def _cancel_timer(self, passport_id: str, reason: str):
        if passport_id not in self._timers:
            return

        task = self._timers[passport_id]
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._timers.pop(passport_id, None)
        self._log("ttl_timer_canceled", {
            "passport_id": passport_id,
            "reason": reason
        })

    async def cancel_all_timers(self):
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
        await self.cancel_all_timers()
        self._log("stopped", {"message": "LifecycleManager stopped"})