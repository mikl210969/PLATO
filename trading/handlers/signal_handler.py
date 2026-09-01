import time
from typing import TYPE_CHECKING, Dict, Any, Optional

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from trading.passport_manager import PassportManager
    from trading.passport_repository import PassportRepository
    from trading.state_manager import StateManager
    from trading.order_verifier import OrderVerifier
    from trading.drift_monitor import DriftMonitor
    from extensions.risk.position_sizer import PositionSizer
    from typing import TYPE_CHECKING, Dict, Any, Optional

class SignalHandlerMixin:
    # ========================================================================
    # ОБЪЯВЛЕНИЯ ОБЩИХ АТРИБУТОВ ДЛЯ PYLANCE
    # ========================================================================
    bus: "EventBus"
    passport_manager: "PassportManager"
    repository: "PassportRepository"
    state_manager: "StateManager"
    verifier: "OrderVerifier"
    drift_monitor: "DriftMonitor"
    position_sizer: Optional["PositionSizer"] = None
    config: Dict[str, Any]
    _log: Any

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
        # 🔥 УРОВЕНЬ 1: Хранилище BTC-контекста для Smart Sizing
        self._btc_context: Dict[str, Any] = {
            "trend": "FLAT",
            "regime": "NORMAL",
            "delta_strength": 0.0,
            "price": 0.0
        }

    def _subscribe_to_events(self):
        self.bus.subscribe("SIGNAL_GENERATED", self._on_signal)
        self.bus.subscribe("ORDER_TRADE_UPDATE", self._on_order_update)
        self.bus.subscribe("ACCOUNT_UPDATE", self._on_account_update)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)
        self.bus.subscribe("SYNC_REQUEST", self._on_sync_request)
        self.bus.subscribe("TTL_EXPIRED", self._on_ttl_expired)
        self.bus.subscribe("ORDER_FILLED", self._on_order_filled)
        self.bus.subscribe("ORDER_PARTIAL", self._on_order_partial)
        # 🔥 УРОВЕНЬ 1: Подписка на BTC-контекст для Smart Sizing
        self.bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        self._log("subscribed_to_events", {
            "subscriptions": [
                "SIGNAL_GENERATED", "ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE",
                "POSITION_CLOSED", "SYNC_REQUEST", "TTL_EXPIRED",
                "ORDER_FILLED", "ORDER_PARTIAL", "BTC_CONTEXT_UPDATED"
            ]
        })

    async def _on_btc_context_updated(self, event):
        """🔥 УРОВЕНЬ 1: Обновляем локальное хранилище BTC-контекста."""
        payload = getattr(event, 'payload', {})
        if not isinstance(payload, dict):
            return
        self._btc_context = {
            "trend": payload.get("trend", "FLAT"),
            "regime": payload.get("regime", "NORMAL"),
            "delta_strength": payload.get("delta_strength", 0.0),
            "price": payload.get("current_price", 0.0)
        }

    def _calculate_smart_risk(self, base_risk: float, signal_side: str) -> tuple:
        """
        🔥 УРОВЕНЬ 1: Smart Sizing — корректирует риск под BTC-тренд.
        
        Логика:
        - BTC UP + LONG → ×1.5 (бонус за подтверждение)
        - BTC DOWN + SHORT → ×1.5 (бонус за подтверждение)
        - BTC FLAT → ×1.0 (нейтрально)
        - Против тренда → ×0.5 (штраф)
        - IMPULSIVE режим → ×0.7 (защита от "ловли ножей")
        
        Возвращает: (adjusted_risk, multiplier, reason)
        """
        btc_trend = self._btc_context.get("trend", "FLAT")
        btc_regime = self._btc_context.get("regime", "NORMAL")
        
        # Защита от импульсных движений (IMPULSIVE)
        if btc_regime == "IMPULSIVE":
            multiplier = 0.7
            reason = f"IMPULSIVE regime (delta={self._btc_context.get('delta_strength', 0):.1f}) — защита от ловли ножей"
            return base_risk * multiplier, multiplier, reason
        
        # Совпадение тренда BTC с направлением сигнала
        if signal_side == "long" and btc_trend == "UP":
            multiplier = 1.5
            reason = "BTC UP + LONG → подтверждение трендом (×1.5)"
        elif signal_side == "short" and btc_trend == "DOWN":
            multiplier = 1.5
            reason = "BTC DOWN + SHORT → подтверждение трендом (×1.5)"
        elif btc_trend == "FLAT":
            multiplier = 1.0
            reason = "BTC FLAT → нейтральный режим"
        else:
            # Против тренда — штраф
            multiplier = 0.5
            reason = f"BTC {btc_trend} vs {signal_side.upper()} → контртренд (×0.5)"
        
        return base_risk * multiplier, multiplier, reason

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
        if not trader:
            self._log("trader_not_found", {"symbol": signal.symbol})
            return

        atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
        levels = trader.calculate_exit_levels(side=signal.side, entry_price=signal.entry_price, atr_value=atr_value)
        passport.sl_price = levels.get('sl_price', 0)
        passport.tp1_price = levels.get('tp1_price', 0)
        passport.tp2_price = levels.get('tp2_price', 0)

        self.repository.save(passport)

        # ========================================================================
        # 🔥 УРОВЕНЬ 1: SMART SIZING — адаптивный риск под BTC-тренд
        # ========================================================================
        base_risk_usdt = self.config.get('risk', {}).get('risk_per_trade_usdt', 30.0)
        
        # Рассчитываем скорректированный риск
        risk_usdt, smart_multiplier, smart_reason = self._calculate_smart_risk(
            base_risk=base_risk_usdt,
            signal_side=signal.side
        )
        
        # Громкий лог для мониторинга
        print(f"💰 [SMART SIZING] BTC: {self._btc_context.get('trend')} ({self._btc_context.get('regime')}) | "
              f"Signal: {signal.side.upper()} | "
              f"Риск: {base_risk_usdt}$ → {risk_usdt:.2f}$ (×{smart_multiplier}) | "
              f"{smart_reason}")
        
        self._log("smart_sizing_calculated", {
            "base_risk_usdt": base_risk_usdt,
            "adjusted_risk_usdt": risk_usdt,
            "multiplier": smart_multiplier,
            "reason": smart_reason,
            "btc_trend": self._btc_context.get("trend"),
            "btc_regime": self._btc_context.get("regime"),
            "signal_side": signal.side,
            "symbol": signal.symbol
        })
        # ========================================================================

        sl_price = passport.sl_price
        
        # 1. ЗАЩИТА: Проверяем, инициализирован ли сайзер
        if self.position_sizer is None:
            self._log("position_sizer_missing", {
                "reason": "PositionSizer not initialized, falling back to default lot size"
            })
            quantity = 7.0
        else:
            # 2. Вызываем расчет с СКОРРЕКТИРОВАННЫМ риском
            safe_quantity = await self.position_sizer.calculate(
                symbol=signal.symbol,
                entry_price=signal.entry_price,
                sl_price=sl_price,
                risk_usdt=risk_usdt  # ← используем smart_risk
            )

            # 3. Если рассчитанный лот меньше минимума биржи, отменяем сигнал
            if safe_quantity is None:
                self._log("signal_rejected_position_sizing", {
                    "reason": "Calculated quantity is below exchange minimums or risk is too small",
                    "symbol": signal.symbol,
                    "base_risk_usdt": base_risk_usdt,
                    "adjusted_risk_usdt": risk_usdt,
                    "smart_multiplier": smart_multiplier,
                    "entry": signal.entry_price,
                    "sl": sl_price
                })
                self.state_manager.handle_event(passport, "ORDER_FAILED", {'error': 'Position sizing failed: quantity too small'})
                self.repository.save(passport)
                return

            quantity = safe_quantity

        # 🔥 НОВОЕ: Добавляем информацию о Smart Sizing в паспорт
        passport.sizing_info = {
            "base_risk_usdt": base_risk_usdt,
            "adjusted_risk_usdt": risk_usdt,
            "smart_multiplier": smart_multiplier,
            "smart_reason": smart_reason,
            "btc_trend": self._btc_context.get("trend"),
            "btc_regime": self._btc_context.get("regime"),
            "sl_distance": round(abs(signal.entry_price - sl_price), 4),
            "final_quantity": quantity,
            "max_size_cap": self.position_sizer.max_position_size if self.position_sizer else None,
            "fallback_used": self.position_sizer is None
        }

        order_type = self.config.get('trading', {}).get('entry_order_type', 'market')
        
        # Генерация богатого client_order_id
        short_symbol = signal.symbol[:4]
        short_strategy = signal.strategy[:10]
        short_passport_id = passport.passport_id.split('_')[-1] if '_' in passport.passport_id else passport.passport_id[-8:]
        rich_client_order_id = f"{short_symbol}_{short_strategy}_{short_passport_id}_{int(time.time())}"[:35]
        
        self._log("sending_order", {
            "symbol": signal.symbol, 
            "side": signal.side, 
            "quantity": quantity, 
            "order_type": order_type, 
            "limit_price": signal.entry_price if order_type == 'limit' else None,
            "client_order_id": rich_client_order_id,
            "base_risk_usdt": base_risk_usdt,
            "adjusted_risk_usdt": risk_usdt,
            "smart_multiplier": smart_multiplier
        })

        print(f"🚀 [ПЕРЕД ОТПРАВКОЙ] Символ: {signal.symbol} | Количество (quantity): {quantity} | Цена: {signal.entry_price}")
        
        result = await trader.execute_order(
            symbol=signal.symbol,  
            side=signal.side, 
            quantity=quantity, 
            order_type=order_type,
            client_order_id=rich_client_order_id,
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
                    client_order_id=rich_client_order_id
                )
                self._log("verifier_started_on_send", {"passport_id": passport.passport_id, "order_id": result.get('order_id')})
            
            passport.add_order({
                "order_id": result.get('order_id'), 
                "client_order_id": rich_client_order_id,
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
                
                await self.bus.publish(
                    event_type="PASSPORT_CREATED", 
                    source="orchestrator", 
                    payload={
                        "passport_id": passport.passport_id, 
                        "order_type": str(order_type).lower(),
                        "client_order_id": rich_client_order_id
                    }, 
                    symbol=signal.symbol
                )
                print(f"✅ [DEBUG TTL] PASSPORT_CREATED успешно опубликован! client_order_id: {rich_client_order_id}")
            else:
                print(f"⚠️ [DEBUG TTL] Пропускаем публикацию PASSPORT_CREATED, так как order_type = '{order_type}' (ожидался 'limit')")
        else:
            self.state_manager.handle_event(passport, "ORDER_FAILED", {'error': result.get('error', 'unknown')})
            self.repository.save(passport)