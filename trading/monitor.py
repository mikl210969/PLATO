import time
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .base_mixin import BaseMixin

class MonitorMixin(BaseMixin):
    # Все аннотации типов теперь наследуются от BaseMixin
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
        """
        Фоновая проверка зависших ордеров в ORDER_SENT или ORDER_ACK.
        🔥 ФАЗА 3: интервал увеличен с 5 до 30 сек; REST-проверка запускается
        только для ордеров старше 30 сек и когда платформа не HEALTHY
        (при HEALTHY WS-события должны сами закрыть статус).
        """
        from core.types import PassportStatus
        
        while getattr(self, '_running', True):
            await asyncio.sleep(30)  # 🔥 ФАЗА 3: было 5, стало 30
            
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
                    
                # 🔥 ФАЗА 3: даём WS-событиям 30 секунд вместо 10
                if age < 30:
                    continue
                
                # 🔥 ФАЗА 3: если платформа HEALTHY и ордеру меньше 2 минут —
                # ждём WS-событие, REST не дёргаем
                if getattr(passport, 'platform_health', 'HEALTHY') == 'HEALTHY' and age < 120:
                    self._log("stuck_order_deferred_ws_expected", {
                        "passport_id": passport.passport_id,
                        "status": passport.status,
                        "age_sec": round(age, 1)
                    })
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