"""
OrderVerifier — асинхронная проверка исполнения ордера через REST.
Запускается после ORDER_ACK/LIMIT_ON_BOOK и опрашивает биржу до терминального статуса.
"""
import asyncio
from typing import Dict, Optional
from core.logger import get_logger


class OrderVerifier:
    """
    Проверяет статус ордера через REST API.
    Используется как fallback при потере WS-событий.
    """

    def __init__(self, rest_client, event_bus, poll_interval: float = 3.0, max_attempts: int = 20):
        self.rest = rest_client
        self.bus = event_bus
        self.poll_interval = poll_interval
        self.max_attempts = max_attempts
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self.logger = get_logger(__name__)

    async def start_verification(self, passport_id: str, order_id: str, symbol: str, client_order_id: str):
        """
        Запустить фоновую проверку ордера.
        Если задача для этого passport_id уже запущена — игнорируем.
        """
        if passport_id in self._active_tasks:
            self.logger.debug(f"Verification already running for {passport_id}")
            return

        task = asyncio.create_task(
            self._verify_loop(passport_id, order_id, symbol, client_order_id)
        )
        self._active_tasks[passport_id] = task
        self.logger.info(f"🔍 [VERIFIER] Started for {passport_id} (order_id={order_id})")

    async def _verify_loop(self, passport_id: str, order_id: str, symbol: str, client_order_id: str):
        """
        Цикл опроса REST до терминального статуса или лимита попыток.
        """
        for attempt in range(1, self.max_attempts + 1):
            try:
                await asyncio.sleep(self.poll_interval)

                # Запрашиваем статус ордера
                order_data = await self.rest.get_order_status(
                    symbol=symbol,
                    order_id=order_id
                )

                if not order_data:
                    self.logger.debug(f"Attempt {attempt}/{self.max_attempts}: no data")
                    continue

                status = order_data.get('status', '')
                executed_qty = float(order_data.get('executedQty', 0) or 0)
                avg_price = float(order_data.get('avgPrice', 0) or 0)

                self.logger.debug(
                    f"Attempt {attempt}/{self.max_attempts}: status={status}, "
                    f"executed_qty={executed_qty}, avg_price={avg_price}"
                )

                # Терминальные статусы
                if status == 'FILLED':
                    dedup_key = f"REST:{order_id}:FILLED:{executed_qty}"
                    await self.bus.publish(
                        event_type="ORDER_FILLED",
                        source="rest_verifier",
                        payload={
                            "client_order_id": client_order_id,
                            "executed_qty": executed_qty,
                            "price": avg_price,
                            "dedup_key": dedup_key,
                        },
                        symbol=symbol,
                    )
                    self.logger.info(f"✅ [VERIFIER] {passport_id} → FILLED")
                    break

                elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                    dedup_key = f"REST:{order_id}:{status}"
                    await self.bus.publish(
                        event_type="ORDER_CANCELED",
                        source="rest_verifier",
                        payload={
                            "client_order_id": client_order_id,
                            "status": status,
                            "dedup_key": dedup_key,
                        },
                        symbol=symbol,
                    )
                    self.logger.info(f"⚠️ [VERIFIER] {passport_id} → {status}")
                    break

                elif status == 'PARTIALLY_FILLED':
                    # Частичное исполнение — продолжаем опрос
                    dedup_key = f"REST:{order_id}:PARTIAL:{executed_qty}"
                    await self.bus.publish(
                        event_type="ORDER_PARTIAL",
                        source="rest_verifier",
                        payload={
                            "client_order_id": client_order_id,
                            "executed_qty": executed_qty,
                            "price": avg_price,
                            "dedup_key": dedup_key,
                        },
                        symbol=symbol,
                    )

            except Exception as e:
                self.logger.warning(f"Attempt {attempt}/{self.max_attempts} failed: {e}")

        # Завершаем задачу
        if passport_id in self._active_tasks:
            del self._active_tasks[passport_id]

    async def cancel_verification(self, passport_id: str):
        """
        Отменить активную проверку (например, при закрытии позиции).
        """
        if passport_id in self._active_tasks:
            task = self._active_tasks[passport_id]
            task.cancel()
            del self._active_tasks[passport_id]
            self.logger.info(f"🛑 [VERIFIER] Cancelled for {passport_id}")

    async def stop_all(self):
        """Отменить все активные проверки."""
        for passport_id in list(self._active_tasks.keys()):
            await self.cancel_verification(passport_id)