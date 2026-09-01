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
        if passport_id in self._active_tasks:
            self.logger.debug(f"Verification already running for {passport_id}")
            return

        task = asyncio.create_task(
            self._verify_loop(passport_id, order_id, symbol, client_order_id)
        )
        self._active_tasks[passport_id] = task
        self.logger.info(f"🔍 [VERIFIER] Started for {passport_id} (order_id={order_id})")

    async def _get_avg_price_from_fills(self, symbol: str, order_id: str) -> float:
        try:
            import time
            end_time = int(time.time() * 1000)
            start_time = end_time - (60 * 60 * 1000)
            
            trades = await self.rest.get_user_trades(symbol, start_time, end_time, 500)
            if not trades:
                return 0.0
            
            order_trades = [t for t in trades if str(t.get('orderId', '')) == str(order_id)]
            if not order_trades:
                return 0.0
            
            total_qty = sum(float(t.get('qty', 0) or 0) for t in order_trades)
            total_quote = sum(float(t.get('quoteQty', 0) or 0) for t in order_trades)
            
            if total_qty > 0:
                return total_quote / total_qty
            return 0.0
        except Exception as e:
            self.logger.warning(f"Failed to fetch fills for order {order_id}: {e}")
            return 0.0

    async def _verify_loop(self, passport_id: str, order_id: str, symbol: str, client_order_id: str):
        try:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    await asyncio.sleep(self.poll_interval)

                    order_data = await self.rest.get_order_status(symbol=symbol, order_id=order_id)
                    if not order_data:
                        self.logger.debug(f"Attempt {attempt}/{self.max_attempts}: no data")
                        continue

                    status = order_data.get('status', '')
                    executed_qty = float(order_data.get('executedQty', 0) or 0)
                    avg_price = float(order_data.get('avgPrice', 0) or 0)

                    if status == 'FILLED' and avg_price == 0 and executed_qty > 0:
                        self.logger.warning(f"⚠️ avgPrice=0 for FILLED order {order_id}, fetching from user_trades...")
                        avg_price = await self._get_avg_price_from_fills(symbol, order_id)
                        if avg_price > 0:
                            self.logger.info(f"✅ Recovered avg_price from fills: {avg_price}")

                    self.logger.debug(f"Attempt {attempt}/{self.max_attempts}: status={status}, qty={executed_qty}, avg={avg_price}")

                    if status == 'FILLED':
                        await self.bus.publish(
                            event_type="ORDER_FILLED",
                            source="rest_verifier",
                            payload={"client_order_id": client_order_id, "executed_qty": executed_qty, "avg_price": avg_price},
                            symbol=symbol,
                        )
                        self.logger.info(f"✅ [VERIFIER] {passport_id} → FILLED")
                        break  # Задача завершается естественно

                    elif status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                        await self.bus.publish(
                            event_type="ORDER_CANCELED",
                            source="rest_verifier",
                            payload={"client_order_id": client_order_id, "status": status},
                            symbol=symbol,
                        )
                        self.logger.info(f"⚠️ [VERIFIER] {passport_id} → {status}")
                        break  # Задача завершается естественно

                    elif status == 'PARTIALLY_FILLED':
                        await self.bus.publish(
                            event_type="ORDER_PARTIAL",
                            source="rest_verifier",
                            payload={"client_order_id": client_order_id, "executed_qty": executed_qty, "avg_price": avg_price},
                            symbol=symbol,
                        )

                except Exception as e:
                    self.logger.warning(f"Attempt {attempt}/{self.max_attempts} failed: {e}")
        finally:
            # 🔥 КРИТИЧЕСКИЙ ФИКС: Гарантированная очистка при естественном завершении (break)
            # Это предотвращает RecursionError при попытке отменить уже завершённую задачу
            if passport_id in self._active_tasks:
                del self._active_tasks[passport_id]
                self.logger.debug(f"🧹 [VERIFIER] Task cleaned up for {passport_id}")

    async def cancel_verification(self, passport_id: str):
        """Отменить активную проверку."""
        # 🔥 КРИТИЧЕСКИЙ ФИКС: Убираем опасный 'await task', используем pop
        task = self._active_tasks.pop(passport_id, None)
        if task and not task.done():
            task.cancel()
            self.logger.info(f"🛑 [VERIFIER] Cancelled for {passport_id}")

    async def stop_all(self):
        for passport_id in list(self._active_tasks.keys()):
            await self.cancel_verification(passport_id)