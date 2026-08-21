import asyncio
from typing import Dict, Any, Optional
from .event_handlers import EventHandlersMixin
from .monitor import MonitorMixin
from .recovery import RecoveryMixin

class Orchestrator(EventHandlersMixin, MonitorMixin, RecoveryMixin):
    """Главный оркестратор платформы. Координирует модули через миксины."""

    def __init__(
        self, 
        config: Dict[str, Any], 
        event_bus, 
        passport_manager, 
        passport_repository,  
        state_manager, 
        json_logger
    ):
        self.config = config
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.repository = passport_repository  
        self.state_manager = state_manager
        self.json_logger = json_logger
        
        # Менеджеры (будут установлены из main.py)
        self.risk_manager = None
        self.lifecycle_manager = None
        
        self.traders: Dict[str, Any] = {}
        self._running = False

        # Инициализация миксинов
        EventHandlersMixin.__init__(self)
        MonitorMixin.__init__(self)
        
        self._subscribe_to_events()

    def _log(self, event: str, data: Optional[Dict] = None):
        """Унифицированный метод логирования с защитой от разных сигнатур."""
        safe_data = data or {}
        try:
            self.json_logger.log(event, safe_data)
        except TypeError:
            try:
                self.json_logger.log(event, data=safe_data)
            except TypeError:
                try:
                    self.json_logger.log("INFO", event, safe_data)
                except Exception:
                    print(f"[LOG] {event}: {safe_data}")

    def get_trader(self, symbol: str):
        return self.traders.get(symbol)

    def register_trader(self, symbol: str, trader_instance):
        self.traders[symbol] = trader_instance

    def set_risk_manager(self, risk_manager):
        """Установка менеджера рисков (вызывается из main.py)."""
        self.risk_manager = risk_manager
        self._log("risk_manager_set_in_orchestrator")

    def set_lifecycle_manager(self, lifecycle_manager):
        """Установка менеджера жизненного цикла (если потребуется)."""
        self.lifecycle_manager = lifecycle_manager
        self._log("lifecycle_manager_set_in_orchestrator")

    async def start(self):
        self._running = True
        self._log("orchestrator_starting")
        await self.perform_startup_recovery()
        await self.start_stuck_orders_monitor()
        self._log("orchestrator_started")

    async def stop(self):
        self._running = False
        self._log("orchestrator_stopping")
        await self.stop_monitors()
        self._log("orchestrator_stopped")

    async def close_position(self, symbol: str, exit_reason: str, exit_price: float = 0.0) -> bool:
        """Закрыть позицию по символу."""
        self._log("close_position_called", {"symbol": symbol, "exit_reason": exit_reason})
        
        if symbol is None:
            self._log("close_position_symbol_is_none")
            return False

        passport = self.passport_manager.get_active_by_symbol(symbol)
        if not passport:
            self._log("no_active_position_for_close", {"symbol": symbol})
            return False
        
        self._log("passport_found_for_close", {
            "passport_id": passport.passport_id,
            "size": passport.position_size
        })

        trader = self.get_trader(symbol)
        if not trader:
            self._log("trader_not_found_for_close", {"symbol": symbol})
            return False

        self._log("sending_close_order", {
            "symbol": symbol,
            "quantity": passport.position_size
        })
        
        result = await trader.close_position(
            symbol=symbol,
            quantity=passport.position_size,
            exit_reason=exit_reason,
            exit_price=exit_price
        )

        self._log("close_order_result", {"success": result.get('success')})

        if result.get('success'):
            self.state_manager.handle_event(passport, "POSITION_CLOSING", {'exit_reason': exit_reason})
            self.repository.save(passport)
            self._log("position_closing_initiated", {"passport_id": passport.passport_id})

            await self.bus.publish(
                event_type="POSITION_CLOSING",
                source="orchestrator",
                payload={"passport_id": passport.passport_id, "exit_reason": exit_reason},
                symbol=symbol
            )
            return True

        self._log("close_position_failed")
        return False