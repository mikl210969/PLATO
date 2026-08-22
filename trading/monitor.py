import time
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

class MonitorMixin:
    # Явные аннотации типов для удовлетворения Pylance (эти атрибуты есть в Orchestrator)
    _log: Any
    passport_manager: Any
    repository: Any
    state_manager: Any
    bus: Any
    get_trader: Any

    def __init__(self):
        self._stuck_orders_task = None
        self._running = True

    async def start_stuck_orders_monitor(self):
        """Запуск мониторинга зависших ордеров."""
        self._stuck_orders_task = asyncio.create_task(self._check_stuck_orders_loop())

    async def stop_monitors(self):
        self._running = False
        if self._stuck_orders_task:
            self._stuck_orders_task.cancel()
            try:
                await self._stuck_orders_task
            except asyncio.CancelledError:
                pass

    async def _check_stuck_orders_loop(self):
        """Фоновая проверка зависших ордеров в ORDER_SENT или ORDER_ACK."""
        from core.types import PassportStatus
        
        while getattr(self, '_running', True):
            await asyncio.sleep(5)
            
            for passport in self.passport_manager.get_active():
                # Проверяем и ORDER_SENT, и ORDER_ACK
                if passport.status not in [PassportStatus.ORDER_SENT.value, PassportStatus.ORDER_ACK.value]:
                    continue
                
                created_at = getattr(passport, 'created_at', None)
                if not created_at:
                    continue
                
                try:
                    age = time.time() - created_at.timestamp()
                except AttributeError:
                    continue
                    
                # Проверяем ордеры, которые висят дольше 10 секунд
                if age < 10:
                    continue
                
                self._log("stuck_order_check", {
                    "passport_id": passport.passport_id,
                    "status": passport.status,
                    "age_sec": round(age, 1)
                })
                
                orders = getattr(passport, 'orders', [])
                if not orders:
                    continue
                
                last_order = orders[-1]
                client_order_id = last_order.get('client_order_id') if isinstance(last_order, dict) else None
                if not client_order_id:
                    continue
                
                trader = self.get_trader(passport.symbol)
                if not trader:
                    continue

                # REST-fallback: запрашиваем реальный статус ордера на бирже
                order_status = await trader.get_order_status(
                    symbol=passport.symbol, 
                    client_order_id=client_order_id
                )
                
                if order_status is None or not isinstance(order_status, dict):
                    self._log("order_status_check_failed", {"passport_id": passport.passport_id})
                    continue
                
                exchange_status = str(order_status.get('status', ''))
                self._log("rest_order_status_check", {
                    "passport_id": passport.passport_id,
                    "exchange_status": exchange_status
                })
                
                if exchange_status == 'FILLED':
                    price_val = order_status.get('price') or order_status.get('avgPrice')
                    qty_val = order_status.get('executedQty') or order_status.get('qty')
                    
                    self.state_manager.handle_event(passport, "ORDER_FILLED", {
                        'price': float(price_val) if price_val else 0.0,
                        'quantity': float(qty_val) if qty_val else 0.0
                    })
                    self.repository.save(passport)
                    
                    # Публикуем событие открытия позиции
                    await self.bus.publish(
                        event_type="POSITION_OPENED",
                        source="orchestrator",
                        payload={
                            "passport_id": passport.passport_id,
                            "symbol": passport.symbol,
                            "side": passport.side,
                            "entry_price": float(price_val) if price_val else 0.0,
                            "position_size": float(qty_val) if qty_val else 0.0
                        },
                        symbol=passport.symbol
                    )
                    self._log("rest_fallback_success_filled", {"passport_id": passport.passport_id})
                    
                elif exchange_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                    self.state_manager.handle_event(passport, "ORDER_CANCELED", {"details": f"REST fallback: {exchange_status}"})
                    self.repository.save(passport)
                    self._log("rest_fallback_success_canceled", {"passport_id": passport.passport_id})