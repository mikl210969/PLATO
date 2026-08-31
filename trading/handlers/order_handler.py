import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from core.event_bus import EventBus
    from trading.passport_manager import PassportManager
    from trading.passport_repository import PassportRepository
    from trading.state_manager import StateManager
    from trading.order_verifier import OrderVerifier

class OrderHandlerMixin:
    # ========================================================================
    # ОБЪЯВЛЕНИЯ ОБЩИХ АТРИБУТОВ ДЛЯ PYLANCE
    # ========================================================================
    bus: "EventBus"
    passport_manager: "PassportManager"
    repository: "PassportRepository"
    state_manager: "StateManager"
    verifier: "OrderVerifier"
    config: Dict[str, Any]
    _log: Any

    def get_trader(self, symbol: str) -> Any:
        raise NotImplementedError("get_trader must be implemented by the main class")
    # ========================================================================

    async def _on_order_update(self, event):
        payload = getattr(event, 'payload', event)
        if isinstance(payload, str):
            try: payload = json.loads(payload)
            except Exception:
                self._log("order_update_payload_invalid_string", {"payload": payload})
                return
        if not isinstance(payload, dict):
            self._log("order_update_payload_not_dict", {"type": str(type(payload))})
            return

        order_data = payload.get('o', payload)
        client_order_id = str(order_data.get('client_order_id') or order_data.get('c') or '')
        order_status = str(order_data.get('status') or order_data.get('X') or '')
        symbol = str(order_data.get('symbol') or order_data.get('s') or '')
        
        self._log("order_update_received", {"client_order_id": client_order_id, "status": order_status, "symbol": symbol})
        if not client_order_id: return

        close_match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
        if close_match:
            passport = self.passport_manager.get(close_match.group(2))
            if passport and order_status in ('PARTIALLY_FILLED', 'FILLED'):
                await self.bus.publish(event_type="ORDER_FILLED", source="ws_adapter", payload={
                    "client_order_id": client_order_id, "executed_qty": float(order_data.get('executed_qty') or order_data.get('z') or 0.0),
                    "avg_price": float(order_data.get('price') or order_data.get('ap') or 0.0), "close_level": close_match.group(1)
                }, symbol=symbol)
                return

        passport = next((p for p in self.passport_manager.get_active() if any(isinstance(o, dict) and str(o.get('client_order_id')) == client_order_id for o in getattr(p, 'orders', []))), None)
        if not passport:
            self._log("passport_not_found_for_order", {"client_order_id": client_order_id})
            return

        self._log("passport_found_for_order_update", {"passport_id": passport.passport_id, "new_status": order_status})

        if order_status == 'NEW':
            self.state_manager.handle_event(passport, "ORDER_ACK", {"details": "Order ACK received"})
            self.repository.save(passport)
        elif order_status in ('PARTIALLY_FILLED', 'FILLED'):
            executed_qty = float(order_data.get('executed_qty') or order_data.get('z') or 0.0)
            avg_price = float(order_data.get('price') or order_data.get('ap') or 0.0)
            self.state_manager.handle_event(passport, "ORDER_FILLED", {'price': avg_price, 'quantity': executed_qty})
            if executed_qty > 0:
                passport.position_size = executed_qty
                # 🔥 Сверяем фактический размер с биржей (защита от чанков)
                await self._reconcile_position_from_exchange(passport, symbol)                
                passport.position_entry_price = avg_price if avg_price > 0.0 else passport.entry_price
                await self.bus.publish(event_type="POSITION_OPENED", source="orchestrator", payload={
                    "passport_id": passport.passport_id, "symbol": passport.symbol, "side": passport.side,
                    "entry_price": passport.position_entry_price, "position_size": passport.position_size
                }, symbol=passport.symbol)
            self.repository.save(passport)
        elif order_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
            self.state_manager.handle_event(passport, "ORDER_CANCELED", {"details": f"Order {order_status}"})
            self.repository.save(passport)

    async def _on_order_filled(self, event):
        payload = event.payload
        client_order_id = payload.get('client_order_id')
        executed_qty = float(payload.get('executed_qty', 0) or 0)
        avg_price = float(payload.get('avg_price', 0) or 0)
        source = event.source

        if not client_order_id:
            self._log("filled_missing_client_order_id", {"payload": payload})
            return

        passport = None
        close_level = payload.get('close_level')
        
        if close_level or re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id):
            match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
            if match:
                passport = self.passport_manager.get(match.group(2))
                if passport:
                    close_level = close_level or match.group(1)

        if not passport and 'PASS_' in client_order_id:
            match = re.search(r'(PASS_\d{8}_\d{6}_[a-f0-9]+)', client_order_id)
            if match: passport = self.passport_manager.get(match.group(1))

        if not passport:
            for p in self.passport_manager.get_all():
                if p.status not in ("CLOSED", "CANCELED", "FAILED") and any(o.get('client_order_id') == client_order_id for o in p.orders):
                    passport = p
                    break

        if not passport:
            self._log("filled_passport_not_found", {"client_order_id": client_order_id, "source": source})
            return

        if close_level:
            passport.position_size -= executed_qty
            exit_reason = {'C1': 'TP1_HIT', 'C2': 'TP2_HIT', 'CS': 'SL_HIT', 'CE': 'EXTERNAL_CLOSE'}.get(close_level, 'MANUAL_CLOSE')
            
            if passport.position_size < 0.01:
                passport.close(exit_reason=exit_reason, exit_price=avg_price, gross_pnl=self._calculate_pnl(passport, avg_price, executed_qty), commission=0.0)
                self.repository.save(passport)
                await self.bus.publish(event_type="POSITION_CLOSED", source="order_filled", payload={"passport_id": passport.passport_id, "symbol": passport.symbol, "exit_reason": exit_reason, "gross_pnl": passport.gross_pnl}, symbol=passport.symbol)
                self._log("position_fully_closed", {"passport_id": passport.passport_id, "exit_reason": exit_reason, "closed_qty": executed_qty, "gross_pnl": passport.gross_pnl})
            else:
                self.state_manager.handle_event(passport, "PARTIAL_CLOSE", {'closed_qty': executed_qty, 'exit_price': avg_price, 'exit_reason': exit_reason})
                self.repository.save(passport)
                self._log("position_partially_closed", {"passport_id": passport.passport_id, "exit_reason": exit_reason, "closed_qty": executed_qty, "remaining_size": passport.position_size})
            return

        transitioned = self.state_manager.handle_event(passport, "ORDER_FILLED", {'executed_qty': executed_qty, 'price': avg_price})
        if hasattr(self, 'verifier'): await self.verifier.cancel_verification(passport.passport_id)

        if not transitioned and passport.status == "OPEN" and executed_qty > 0:
            old_size = passport.position_size
            if abs(executed_qty - old_size) > 0.001:
                passport.position_size = executed_qty
                # 🔥 Сверяем фактический размер с биржей (защита от чанков)
                await self._reconcile_position_from_exchange(passport, passport.symbol)                
                if avg_price > 0: passport.position_entry_price = avg_price
                self.repository.save(passport)
                self._log("volume_reconciled", {"passport_id": passport.passport_id, "old_size": old_size, "new_size": executed_qty, "source": source})
                await self.bus.publish(event_type="POSITION_OPENED", source=source, payload={"passport_id": passport.passport_id, "symbol": passport.symbol, "side": passport.side, "entry_price": passport.position_entry_price, "position_size": passport.position_size}, symbol=passport.symbol)
                return

        self.repository.save(passport)
        self._log("order_filled_processed", {"passport_id": passport.passport_id, "client_order_id": client_order_id, "executed_qty": executed_qty, "avg_price": avg_price, "source": source, "new_status": passport.status})
        await self.bus.publish(event_type="POSITION_OPENED", source=source, payload={"passport_id": passport.passport_id, "symbol": passport.symbol, "side": passport.side, "entry_price": passport.position_entry_price, "position_size": passport.position_size}, symbol=passport.symbol)

    async def _on_order_partial(self, event):
        payload = event.payload
        client_order_id = payload.get('client_order_id')
        executed_qty = float(payload.get('executed_qty', 0) or 0)
        avg_price = float(payload.get('avg_price', 0) or 0)
        if not client_order_id: return

        passport = next((p for p in self.passport_manager.get_all() if p.status not in ("CLOSED", "CANCELED", "FAILED") and any(o.get('client_order_id') == client_order_id for o in p.orders)), None)
        if not passport: return

        self.state_manager.handle_event(passport, "ORDER_PARTIAL", {'executed_qty': executed_qty, 'price': avg_price})
        self.repository.save(passport)
        self._log("order_partial_processed", {"passport_id": passport.passport_id, "client_order_id": client_order_id, "executed_qty": executed_qty})

    async def _on_ttl_expired(self, event):
        payload = event.payload
        passport_id, symbol, order_id = payload.get('passport_id'), payload.get('symbol'), payload.get('order_id')
        self._log("ttl_expired_handler_started", {"passport_id": passport_id, "symbol": symbol, "order_id": order_id})

        passport = self.passport_manager.get(passport_id)
        if not passport:
            return
        trader = self.get_trader(symbol)
        if not trader:
            return

        # ====================================================================
        # 🔥 РЕКОНСИЛИАЦИЯ С БИРЖЕЙ: перед ЛЮБЫМ решением спрашиваем реальную позицию
        # ====================================================================
        exchange_size = 0.0
        try:
            pos = await trader.get_position_from_exchange(symbol)
            exchange_size = abs(float(pos.get('size', 0) or 0)) if pos else 0.0
        except Exception as e:
            self._log("ttl_reconciliation_fetch_failed", {"passport_id": passport_id, "error": str(e)})

        local_size = abs(float(passport.position_size or 0))
        has_live_position = exchange_size > 0.001 or local_size > 0.001

        # ====================================================================
        # 🔥 СЛУЧАЙ 1: позиция жива → паспорт НЕ закрываем ни при каком статусе
        # ====================================================================
        if passport.status in ("OPEN", "PARTIAL_CLOSE", "PARTIALLY_FILLED") or has_live_position:
            self._log("ttl_skip_position_open", {
                "passport_id": passport_id,
                "status": passport.status,
                "exchange_size": exchange_size,
                "local_size": local_size,
            })

            # Best-effort отменяем висящий остаток входного ордера, позицию оставляем под защитой
            try:
                await trader.cancel_order(symbol, order_id)
            except Exception as e:
                self._log("ttl_cancel_residual_failed", {"passport_id": passport_id, "error": str(e)})

            # Возвращаем паспорт в OPEN, если он застрял в промежуточном статусе
            if passport.status != "OPEN":
                passport.status = "OPEN"
                passport.timeline.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "TTL_KEEP_OPEN",
                    "details": f"TTL expired, but live position detected (exchange={exchange_size}). Passport kept OPEN."
                })
                self.repository.save(passport)

            # RiskManager восстановит/удержит guard на позиции
            await self.bus.publish(
                event_type="POSITION_OPENED",
                source="lifecycle_manager",
                payload={
                    "passport_id": passport_id,
                    "symbol": symbol,
                    "side": passport.side,
                    "entry_price": passport.position_entry_price,
                    "position_size": local_size or exchange_size,
                },
                symbol=symbol
            )
            return

        # ====================================================================
        # 🔥 СЛУЧАЙ 2: позиции нет нигде → безопасно отменяем и закрываем
        # ====================================================================
        cancel_result = await trader.cancel_order(symbol, order_id)
        if cancel_result.get('success') or cancel_result.get('code') == -2011:
            await self._close_passport_after_ttl(passport, symbol, order_id)
        else:
            self._log("ttl_cancel_failed", {"passport_id": passport_id, "error": cancel_result})

    async def _close_passport_after_ttl(self, passport, symbol, order_id):
        # ====================================================================
        # 🔥 ФИНАЛЬНАЯ ПРЕДОХРАНКА: никогда не закрываем паспорт при живой позиции
        # ====================================================================
        try:
            trader = self.get_trader(symbol)
            if trader:
                pos = await trader.get_position_from_exchange(symbol)
                exchange_size = abs(float(pos.get('size', 0) or 0)) if pos else 0.0
                if exchange_size > 0.001:
                    self._log("ttl_close_aborted_position_alive", {
                        "passport_id": passport.passport_id,
                        "exchange_size": exchange_size,
                    })
                    passport.status = "OPEN"
                    passport.timeline.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "event": "TTL_CLOSE_ABORTED",
                        "details": f"Passport close aborted: live position {exchange_size} on exchange."
                    })
                    self.repository.save(passport)
                    await self.bus.publish(
                        event_type="POSITION_OPENED",
                        source="lifecycle_manager",
                        payload={
                            "passport_id": passport.passport_id,
                            "symbol": symbol,
                            "side": passport.side,
                            "entry_price": passport.position_entry_price,
                            "position_size": exchange_size,
                        },
                        symbol=symbol
                    )
                    return
        except Exception as e:
            self._log("ttl_close_reconciliation_error", {
                "passport_id": passport.passport_id,
                "error": str(e)
            })

        # Позиции нет — закрываем паспорт как раньше
        passport.status = "CLOSED"
        passport.exit_reason = "TTL_EXPIRED"
        passport.closed_at = datetime.now(timezone.utc).isoformat()
        passport.timeline.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "STATUS: CLOSED",
            "details": f"Limit order canceled after TTL. Order ID: {order_id}"
        })
        self.repository.save(passport)
        await self.bus.publish(
            event_type="POSITION_CLOSED",
            source="lifecycle_manager",
            payload={"passport_id": passport.passport_id, "symbol": symbol, "exit_reason": "TTL_EXPIRED", "gross_pnl": 0.0},
            symbol=symbol
        )

    async def _reconcile_position_from_exchange(self, passport, symbol: str):
        """🔥 Сверка размера и цены входа паспорта с РЕАЛЬНОЙ позицией на бирже."""
        try:
            trader = self.get_trader(symbol)
            if not trader:
                return
            pos = await trader.get_position_from_exchange(symbol)
            if not pos:
                return
            exchange_size = abs(float(pos.get('size', 0) or 0))
            exchange_entry = float(pos.get('entry_price', 0) or 0)

            if exchange_size > 0.001:
                old_size = float(passport.position_size or 0)
                if abs(exchange_size - old_size) > 0.001:
                    passport.position_size = exchange_size
                    self._log("position_size_reconciled", {
                        "passport_id": passport.passport_id,
                        "old_size": old_size,
                        "new_size": exchange_size,
                    })
                if exchange_entry > 0 and abs(exchange_entry - float(passport.position_entry_price or 0)) > 1e-9:
                    passport.position_entry_price = exchange_entry
                    self._log("entry_price_reconciled", {
                        "passport_id": passport.passport_id,
                        "new_entry": exchange_entry,
                    })
                self.repository.save(passport)
        except Exception as e:
            self._log("position_reconcile_failed", {
                "passport_id": passport.passport_id,
                "error": str(e)
            })

    def _calculate_pnl(self, passport, exit_price: float, quantity: float) -> float:
        if not passport.position_entry_price or passport.position_entry_price == 0: return 0.0
        return (passport.position_entry_price - exit_price) * quantity if passport.side == 'short' else (exit_price - passport.position_entry_price) * quantity