import time
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from trading.passport_manager import PassportManager
    from trading.passport_repository import PassportRepository
    from trading.state_manager import StateManager
    from trading.order_verifier import OrderVerifier
    from trading.drift_monitor import DriftMonitor

class SignalHandlerMixin:
    # ========================================================================
    # ОБЪЯВЛЕНИЯ ОБЩИХ АТРИБУТОВ ДЛЯ PYLANCE (чтобы видеть атрибуты из других миксинов)
    # ========================================================================
    bus: "EventBus"
    passport_manager: "PassportManager"
    repository: "PassportRepository"
    state_manager: "StateManager"
    verifier: "OrderVerifier"
    drift_monitor: "DriftMonitor"
    config: Dict[str, Any]
    _log: Any

    # Методы, определенные в других миксинах, но на которые мы подписываемся здесь
    _on_order_update: Any
    _on_account_update: Any
    _on_position_closed: Any
    _on_sync_request: Any
    _on_ttl_expired: Any
    _on_order_filled: Any
    _on_order_partial: Any

    def get_trader(self, symbol: str) -> Any:
        raise NotImplementedError("get_trader must be implemented by the main class")
    # ========================================================================

    def __init__(self):
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 5.0

    def _subscribe_to_events(self):
        self.bus.subscribe("SIGNAL_GENERATED", self._on_signal)
        self.bus.subscribe("ORDER_TRADE_UPDATE", self._on_order_update)
        self.bus.subscribe("ACCOUNT_UPDATE", self._on_account_update)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)
        self.bus.subscribe("SYNC_REQUEST", self._on_sync_request)
        self.bus.subscribe("TTL_EXPIRED", self._on_ttl_expired)
        self.bus.subscribe("ORDER_FILLED", self._on_order_filled)
        self.bus.subscribe("ORDER_PARTIAL", self._on_order_partial)        
        self._log("subscribed_to_events", {
            "subscriptions": ["SIGNAL_GENERATED", "ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "POSITION_CLOSED", "SYNC_REQUEST", "TTL_EXPIRED"]
        })

    def _generate_rich_client_order_id(self, symbol: str, strategy: str, passport_id: str) -> str:
        """
        Генерирует богатый client_order_id с мета-данными (символ, стратегия, паспорт, время).
        Формат: {SYMBOL}_{STRATEGY}_{PASSPORT_SHORT}_{TIMESTAMP}
        Максимум 35 символов (лимит Binance).
        """
        # Берем первые 4 символа символа (например, "SOL" из "SOLUSDT")
        short_symbol = symbol[:4]
        
        # Берем первые 10 символов стратегии (например, "Absorption" из "AbsorptionV2")
        short_strategy = strategy[:10]
        
        # Берем последние 8 символов passport_id (например, "1ff4a4" из "PASS_20260829_225901_1ff4a4")
        short_passport_id = passport_id.split('_')[-1] if '_' in passport_id else passport_id[-8:]
        
        # Генерируем ID и жестко обрезаем до 35 символов
        rich_id = f"{short_symbol}_{short_strategy}_{short_passport_id}_{int(time.time())}"
        return rich_id[:35]

    async def _on_signal(self, event):
        self._log("signal_received", {"event": event.type})
        payload = event.payload
        signal = payload.get('signal')
        if not signal:
            self._log("signal_rejected", {"reason": "signal_is_none"})
            return

        current_time = time.time()
        last_time = self._last_signal_time.get(signal.symbol, 0)
        if current_time - last_time < self._signal_cooldown:
            self._log("signal_cooldown", {"symbol": signal.symbol})
            return
        self._last_signal_time[signal.symbol] = current_time

        if self.passport_manager.is_symbol_busy(signal.symbol):
            self._log("symbol_busy", {"symbol": signal.symbol, "signal_id": signal.signal_id})
            return

        if hasattr(self, 'drift_monitor') and self.drift_monitor.is_drift_active(signal.symbol):
            self._log("signal_rejected_drift_active", {"symbol": signal.symbol, "signal_id": signal.signal_id, "reason": "drift_monitor_active"})
            return

        if hasattr(self, 'verifier'):
            active_passports = self.passport_manager.get_by_symbol(signal.symbol)
            for passport in active_passports:
                if passport.passport_id in self.verifier._active_tasks:
                    self._log("signal_rejected_verifier_active", {"symbol": signal.symbol, "signal_id": signal.signal_id, "passport_id": passport.passport_id, "reason": "verifier_active"})
                    return

        passport = self.passport_manager.create(
            symbol=signal.symbol, signal_id=signal.signal_id, strategy=signal.strategy,
            side=signal.side, entry_price=signal.entry_price, confidence=signal.confidence
        )
        self._log("passport_created", {"passport_id": passport.passport_id})

        trader = self.get_trader(signal.symbol)
        if trader:
            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
            levels = trader.calculate_exit_levels(side=signal.side, entry_price=signal.entry_price, atr_value=atr_value)
            passport.sl_price = levels.get('sl_price', 0)
            passport.tp1_price = levels.get('tp1_price', 0)
            passport.tp2_price = levels.get('tp2_price', 0)

        self.repository.save(passport)

        quantity = self.config.get('trading', {}).get('lot_size', 7.0)
        order_type = self.config.get('trading', {}).get('entry_order_type', 'market')
        
        # 🔥 ГЕНЕРАЦИЯ БОГАТОГО client_order_id (строго до 35 символов для Binance)
        # Пример результата: "SOL_Absorption_1ff4a4_1788044341" (35 символов)
        rich_client_order_id = self._generate_rich_client_order_id(
            symbol=signal.symbol,
            strategy=signal.strategy,
            passport_id=passport.passport_id
        )
        
        self._log("sending_order", {
            "symbol": signal.symbol, 
            "side": signal.side, 
            "quantity": quantity, 
            "order_type": order_type, 
            "limit_price": signal.entry_price if order_type == 'limit' else None,
            "client_order_id": rich_client_order_id
        })

        result = await trader.execute_order(
            symbol=signal.symbol, 
            side=signal.side, 
            quantity=quantity, 
            order_type=order_type,
            client_order_id=rich_client_order_id,  # 🔥 Передаем богатый ID
            passport_id=passport.passport_id,
            limit_price=signal.entry_price if order_type == 'limit' else None
        )

        self._log("order_result", {"passport_id": passport.passport_id, "success": result.get('success'), "error": result.get('error')})

        if result.get('success'):
            self.state_manager.handle_event(passport, "ORDER_SENT", {"details": "Order sent to exchange"})
            self.repository.save(passport)

            if hasattr(self, 'verifier'):
                await self.verifier.start_verification(
                    passport_id=passport.passport_id, 
                    order_id=str(result.get('order_id', '')), 
                    symbol=signal.symbol, 
                    client_order_id=rich_client_order_id  # 🔥 Передаем богатый ID
                )
                self._log("verifier_started_on_send", {"passport_id": passport.passport_id, "order_id": result.get('order_id')})
            
            passport.add_order({
                "order_id": result.get('order_id'), 
                "client_order_id": rich_client_order_id,  # 🔥 Сохраняем богатый ID
                "status": result.get('status', 'NEW'), 
                "type": result.get('order_type', 'MARKET'), 
                "side": signal.side, 
                "price": signal.entry_price, 
                "quantity": result.get('quantity', 0)
            })
            self.repository.save(passport)

            print(f"🔥 [DEBUG TTL] Конфиг order_type: '{order_type}' (тип: {type(order_type)})")
            if str(order_type).lower() == 'limit':
                print(f"🔥 [DEBUG TTL] Пытаемся опубликовать PASSPORT_CREATED для {passport.passport_id}")
                
                # 🔥 Передаем богатый client_order_id в LifecycleManager
                await self.bus.publish(
                    event_type="PASSPORT_CREATED", 
                    source="orchestrator", 
                    payload={
                        "passport_id": passport.passport_id, 
                        "order_type": str(order_type).lower(),
                        "client_order_id": rich_client_order_id  # 🔥 Богатый ID
                    }, 
                    symbol=signal.symbol
                )
                print(f"✅ [DEBUG TTL] PASSPORT_CREATED успешно опубликован! client_order_id: {rich_client_order_id}")
            else:
                print(f"⚠️ [DEBUG TTL] Пропускаем публикацию PASSPORT_CREATED, так как order_type = '{order_type}' (ожидался 'limit')")
        else:
            self.state_manager.handle_event(passport, "ORDER_FAILED", {'error': result.get('error', 'unknown')})
            self.repository.save(passport)