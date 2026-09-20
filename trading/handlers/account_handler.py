from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from trading.passport_manager import PassportManager
    from trading.passport_repository import PassportRepository
    from trading.state_manager import StateManager

class AccountHandlerMixin:
    # ========================================================================
    # ОБЪЯВЛЕНИЯ ОБЩИХ АТРИБУТОВ ДЛЯ PYLANCE
    # ========================================================================
    bus: "EventBus"
    passport_manager: "PassportManager"
    repository: "PassportRepository"
    state_manager: "StateManager"
    config: Dict[str, Any]
    _log: Any

    def get_trader(self, symbol: str) -> Any:
        raise NotImplementedError("get_trader must be implemented by the main class")
    # ========================================================================

    async def _on_account_update(self, event):
        payload = event.payload
        account_data = payload.get('a', payload)
        positions = account_data.get('P', [])
        if not positions: return

        for pos in positions:
            symbol = pos.get('s')
            if not symbol: continue
            pos_amt = float(pos.get('pa', 0))
            if abs(pos_amt) < 0.01:
                passport = self.passport_manager.get_active_by_symbol(symbol)
                # 🔥 «Нет позиции» ≠ «позицию закрыли»: ORDER_SENT/ORDER_ACK без филлов
                # не получают внешнее закрытие (входной ордер ещё работает в стакане)
                _had_pos = float(getattr(passport, 'filled_qty', 0) or 0) > 0.001 if passport else False
                if passport and (
                    passport.status in ("OPEN", "PARTIAL_CLOSE")
                    or (passport.status in ("ORDER_ACK", "ORDER_SENT") and _had_pos)
                ):
                    # 🔥 КОНТРОЛЬ: событие в шторм реконнектов может врать —
                    # перед внешним закрытием подтверждаем отсутствие позиции
                    # прямым вопросом бирже (пункт 3)
                    _trader = self.get_trader(symbol)
                    if _trader:
                        _pos = await _trader.get_position_from_exchange(symbol)
                        _ex_size = abs(float(_pos.get('size', 0) or 0)) if _pos else 0.0
                        if _ex_size > 0.001:
                            self._log("external_close_cancelled_position_exists", {
                                "passport_id": passport.passport_id,
                                "symbol": symbol,
                                "exchange_size": _ex_size
                            })
                            continue

                    # 🔥 НЕ перезаписываем уже закрытые паспорта:
                    # exit_reason != "" = паспорт уже закрыт другим путём (SL/TP1/TP2)
                    if getattr(passport, 'exit_reason', '') and passport.exit_reason != "":
                        self._log("external_close_ignored_already_closed", {
                            "passport_id": passport.passport_id,
                            "existing_exit_reason": passport.exit_reason
                        })
                        continue

                    self._log("external_close_detected", {"passport_id": passport.passport_id, "symbol": symbol, "previous_size": passport.position_size})
                    passport.position_size = 0.0
                    passport.status = "CLOSED"
                    passport.exit_reason = "EXTERNAL_CLOSE"
                    passport.closed_at = datetime.now(timezone.utc).isoformat()
                    passport.timeline.append({"timestamp": datetime.now(timezone.utc).isoformat(), "event": "STATUS: EXTERNAL_CLOSE", "details": "Position closed manually or liquidated on exchange"})
                    self.repository.save(passport)
                    self._log("passport_marked_as_external_close", {"passport_id": passport.passport_id})
                    
    async def _on_position_closed(self, event):
        self._log("position_closed_event", {"event": event.payload})

    async def _on_sync_request(self, event):
        payload = event.payload
        symbol = payload.get('symbol')
        if not symbol:
            self._log("sync_request_no_symbol")
            return
        
        self._log("sync_request_received", {"symbol": symbol})
        passport = self.passport_manager.get_active_by_symbol(symbol)
        if not passport:
            self._log("sync_no_active_passport", {"symbol": symbol})
            return
        
        trader = self.get_trader(symbol)
        if not trader:
            self._log("sync_trader_not_found", {"symbol": symbol})
            return
        
        position = await trader.get_position_from_exchange(symbol)
        if not position:
            self._log("sync_position_fetch_failed", {"symbol": symbol})
            return
        
        position_size = abs(float(position.get('size', 0) or 0))
        self._log("sync_position_check", {"symbol": symbol, "passport_status": passport.status, "exchange_position_size": position_size})
        
        if position_size < 0.01 and passport.status in ["OPEN", "PARTIAL_CLOSE"]:
            self._log("sync_external_close_detected", {"passport_id": passport.passport_id, "symbol": symbol})
            passport.status = "CLOSED"
            passport.exit_reason = "EXTERNAL_CLOSE"
            passport.position_size = 0.0
            passport.closed_at = datetime.now(timezone.utc).isoformat()
            self.repository.save(passport)
            await self.bus.publish(event_type="POSITION_CLOSED", source="sync", payload={"passport_id": passport.passport_id, "symbol": symbol, "exit_reason": "EXTERNAL_CLOSE"}, symbol=symbol)