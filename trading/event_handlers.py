import time
import datetime
import json
from typing import TYPE_CHECKING, Dict, Any, Optional

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

class EventHandlersMixin:
    # Явные аннотации типов для удовлетворения Pylance (эти атрибуты есть в Orchestrator)
    _log: Any
    bus: Any
    passport_manager: Any
    repository: Any
    state_manager: Any
    config: Any
    get_trader: Any

    def __init__(self):
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 5.0  # секунд

    def _subscribe_to_events(self):
        """Подписка на события шины."""
        self.bus.subscribe("SIGNAL_GENERATED", self._on_signal)
        self.bus.subscribe("ORDER_TRADE_UPDATE", self._on_order_update)
        self.bus.subscribe("ACCOUNT_UPDATE", self._on_account_update)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)
        self.bus.subscribe("SYNC_REQUEST", self._on_sync_request)
        self.bus.subscribe("TTL_EXPIRED", self._on_ttl_expired)
        
        self._log("subscribed_to_events", {
            "subscriptions": ["SIGNAL_GENERATED", "ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "POSITION_CLOSED", "SYNC_REQUEST", "TTL_EXPIRED"]
        })

    async def _on_signal(self, event):
        """Обработка сигнала от стратегии."""
        self._log("signal_received", {"event": event.type})
        payload = event.payload
        signal = payload.get('signal')
        if not signal:
            self._log("signal_rejected", {"reason": "signal_is_none"})
            return

        # Защита от повторных сигналов
        current_time = time.time()
        last_time = self._last_signal_time.get(signal.symbol, 0)
        if current_time - last_time < self._signal_cooldown:
            self._log("signal_cooldown", {"symbol": signal.symbol})
            return
        self._last_signal_time[signal.symbol] = current_time

        if self.passport_manager.is_symbol_busy(signal.symbol):
            self._log("symbol_busy", {"symbol": signal.symbol, "signal_id": signal.signal_id})
            return

        # Создаём паспорт
        passport = self.passport_manager.create(
            symbol=signal.symbol, signal_id=signal.signal_id, strategy=signal.strategy,
            side=signal.side, entry_price=signal.entry_price, confidence=signal.confidence
        )
        self._log("passport_created", {"passport_id": passport.passport_id})

        # Рассчитываем уровни выхода
        trader = self.get_trader(signal.symbol)
        if trader:
            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
            levels = trader.calculate_exit_levels(side=signal.side, entry_price=signal.entry_price, atr_value=atr_value)
            passport.sl_price = levels.get('sl_price', 0)
            passport.tp1_price = levels.get('tp1_price', 0)
            passport.tp2_price = levels.get('tp2_price', 0)

        self.repository.save(passport)

        # Отправляем команду трейдеру (🔥 ИСПРАВЛЕНИЕ: добавлен limit_price)
        quantity = self.config.get('trading', {}).get('lot_size', 7.0)
        order_type = self.config.get('trading', {}).get('entry_order_type', 'market')
        
        self._log("sending_order", {
            "symbol": signal.symbol, "side": signal.side, "quantity": quantity, 
            "order_type": order_type, "limit_price": signal.entry_price if order_type == 'limit' else None
        })

        result = await trader.execute_order(
            symbol=signal.symbol, side=signal.side, quantity=quantity, order_type=order_type,
            client_order_id=signal.signal_id, passport_id=passport.passport_id,
            limit_price=signal.entry_price if order_type == 'limit' else None  # 🔥 Ключевой фикс
        )

        self._log("order_result", {"passport_id": passport.passport_id, "success": result.get('success'), "error": result.get('error')})

        if result.get('success'):
            self.state_manager.handle_event(passport, "ORDER_SENT", "Order sent to exchange")
            self.repository.save(passport)
            
            passport.add_order({
                "order_id": result.get('order_id'), "client_order_id": result.get('client_order_id'),
                "status": result.get('status', 'NEW'), "type": result.get('order_type', 'MARKET'),
                "side": signal.side, "price": signal.entry_price, "quantity": result.get('quantity', 0)
            })
            self.repository.save(passport)

            if order_type == 'limit':
                await self.bus.publish(event_type="PASSPORT_CREATED", source="orchestrator", 
                                       payload={"passport_id": passport.passport_id}, symbol=signal.symbol)
        else:
            self.state_manager.handle_event(passport, "ORDER_FAILED", result.get('error', 'unknown'))
            self.repository.save(passport)

    async def _on_order_update(self, event):
        """Обработка обновлений статуса ордеров от биржи (WebSocket)."""
        # 1. Безопасно получаем payload
        payload = getattr(event, 'payload', event)
        
        # 2. Если вдруг пришла строка, пытаемся распарсить
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                self._log("order_update_payload_invalid_string", {"payload": payload})
                return
                
        # 3. Если это всё ещё не словарь, выходим
        if not isinstance(payload, dict):
            self._log("order_update_payload_not_dict", {"type": str(type(payload))})
            return

        # 4. Извлекаем данные (поддерживаем и нормализованный формат, и сырой Binance)
        order_data = payload.get('o', payload)
        
        client_order_id = str(order_data.get('client_order_id') or order_data.get('c') or '')
        order_status = str(order_data.get('status') or order_data.get('X') or '')
        symbol = str(order_data.get('symbol') or order_data.get('s') or '')
        
        self._log("order_update_received", {
            "client_order_id": client_order_id,
            "status": order_status,
            "symbol": symbol
        })

        if not client_order_id:
            return

        # 5. Находим паспорт по client_order_id
        passport = None
        for p in self.passport_manager.get_active():
            orders = getattr(p, 'orders', [])
            for order in orders:
                if isinstance(order, dict) and str(order.get('client_order_id')) == client_order_id:
                    passport = p
                    break
            if passport:
                break

        if not passport:
            self._log("passport_not_found_for_order", {"client_order_id": client_order_id})
            return

        self._log("passport_found_for_order_update", {
            "passport_id": passport.passport_id,
            "new_status": order_status
        })

        # 6. Логика перехода статусов
        if order_status == 'NEW':
            self.state_manager.handle_event(passport, "ORDER_ACK", {"details": "Order ACK received"})
            self.repository.save(passport)
            
        elif order_status in ('PARTIALLY_FILLED', 'FILLED'):
            # Используем ключи из твоего лога: 'executed_qty' и 'price'
            executed_qty = float(order_data.get('executed_qty') or order_data.get('z') or 0.0)
            avg_price = float(order_data.get('price') or order_data.get('ap') or 0.0)
            
            self.state_manager.handle_event(passport, "ORDER_FILLED", {
                'price': avg_price,
                'quantity': executed_qty
            })
            
            # 🔥 КРИТИЧЕСКИ ВАЖНО: Обновляем размеры позиции в паспорте
            if executed_qty > 0:
                passport.position_size = executed_qty
                passport.position_entry_price = avg_price if avg_price > 0.0 else passport.entry_price
                
                await self.bus.publish(
                    event_type="POSITION_OPENED",
                    source="orchestrator",
                    payload={
                        "passport_id": passport.passport_id,
                        "symbol": passport.symbol,
                        "side": passport.side,
                        "entry_price": passport.position_entry_price,
                        "position_size": passport.position_size
                    },
                    symbol=passport.symbol
                )
            self.repository.save(passport)
                
        elif order_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
            self.state_manager.handle_event(passport, "ORDER_CANCELED", {"details": f"Order {order_status}"})
            self.repository.save(passport)

    async def _on_account_update(self, event):
        """Обработка обновлений баланса и позиций от биржи (WebSocket)."""
        payload = event.payload
        
        # Структура Binance WS: данные аккаунта лежат в ключе 'a', позиции в 'P' (список)
        account_data = payload.get('a', payload)
        positions = account_data.get('P', [])
        
        if not positions:
            return

        for pos in positions:
            symbol = pos.get('s')  # Символ позиции (например, 'SOLUSDT')
            if not symbol:
                continue
                
            # pa = position amount (размер позиции). Может быть отрицательным для шорта
            pos_amt = float(pos.get('pa', 0))
            
            # Если размер позиции стал практически нулевым
            if abs(pos_amt) < 0.01:
                # Ищем локальный паспорт для этого символа
                passport = self.passport_manager.get_active_by_symbol(symbol)
                
                # Если паспорт есть и он всё ещё считается "открытым" локально
                if passport and passport.status in ["OPEN", "ORDER_ACK", "ORDER_SENT"]:
                    self._log("external_close_detected", {
                        "passport_id": passport.passport_id, 
                        "symbol": symbol,
                        "previous_size": passport.position_size
                    })
                    
                    # 🔥 Обновляем паспорт, чтобы он отражал реальность
                    passport.position_size = 0.0
                    passport.status = "EXTERNAL_CLOSE"
                    passport.exit_reason = "EXTERNAL_CLOSE"
                    
                    # Добавляем запись в timeline
                    passport.timeline.append({
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "event": "STATUS: EXTERNAL_CLOSE",
                        "details": "Position closed manually or liquidated on exchange"
                    })
                    
                    # Сохраняем изменения
                    self.repository.save(passport)
                    
                    self._log("passport_marked_as_external_close", {
                        "passport_id": passport.passport_id
                    })

    async def _on_position_closed(self, event):
        self._log("position_closed_event", {"event": event.payload})

    async def _on_sync_request(self, event):
        self._log("sync_request_received")

    async def _on_ttl_expired(self, event):
        """Обработка истечения TTL лимитного ордера."""
        import datetime
        
        payload = event.payload
        passport_id = payload.get('passport_id')
        symbol = payload.get('symbol')
        order_id = payload.get('order_id')
        
        self._log("ttl_expired_handler_started", {
            "passport_id": passport_id,
            "symbol": symbol,
            "order_id": order_id
        })
        
        # Находим паспорт
        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("ttl_passport_not_found", {"passport_id": passport_id})
            return
        
        # Получаем трейдера
        trader = self.get_trader(symbol)
        if not trader:
            self._log("ttl_trader_not_found", {"symbol": symbol})
            return
        
        # 🔥 ЛОГИКА В ЗАВИСИМОСТИ ОТ СТАТУСА
        if passport.status == "OPEN":
            # Ордер полностью исполнился, TTL не нужен
            self._log("ttl_skip_fully_filled", {
                "passport_id": passport_id,
                "status": passport.status
            })
            return
        
        elif passport.status == "PARTIALLY_FILLED":
            # 🔥 ЧАСТИЧНОЕ ИСПОЛНЕНИЕ: отменяем остаток, позиция остается
            self._log("ttl_partial_fill_detected", {
                "passport_id": passport_id,
                "position_size": passport.position_size,
                "original_quantity": passport.orders[-1].get('quantity', 0) if passport.orders else 0
            })
            
            # Отменяем остаток ордера на бирже
            cancel_result = await trader.cancel_order(symbol=symbol, order_id=order_id)
            
            if cancel_result.get('success'):
                self._log("ttl_partial_fill_order_canceled", {
                    "passport_id": passport_id,
                    "remaining_canceled": True
                })
                
                # Паспорт остается OPEN с фактическим размером позиции
                passport.status = "OPEN"
                passport.exit_reason = ""  # Позиция открыта, не закрыта
                
                # Добавляем запись в timeline
                passport.timeline.append({
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "event": "TTL_EXPIRED_PARTIAL_FILL",
                    "details": f"TTL expired. Remaining order canceled. Position size: {passport.position_size}"
                })
                
                # Сохраняем паспорт
                self.repository.save(passport)
                
                # Публикуем событие, что позиция открыта (для RiskManager и Monitor)
                await self.bus.publish(
                    event_type="POSITION_OPENED",
                    source="lifecycle_manager",
                    payload={
                        "passport_id": passport_id,
                        "symbol": symbol,
                        "side": passport.side,
                        "entry_price": passport.position_entry_price,
                        "position_size": passport.position_size
                    },
                    symbol=symbol
                )
            else:
                self._log("ttl_partial_fill_cancel_failed", {
                    "passport_id": passport_id,
                    "error": cancel_result.get('error')
                })
        
        else:
            # ORDER_SENT, ORDER_ACK или другие статусы — ордер не исполнился, отменяем полностью
            self._log("ttl_canceling_order", {
                "passport_id": passport_id,
                "order_id": order_id,
                "status": passport.status
            })
            
            cancel_result = await trader.cancel_order(symbol=symbol, order_id=order_id)
            
            if cancel_result.get('success'):
                self._log("ttl_order_canceled_success", {
                    "passport_id": passport_id,
                    "order_id": order_id
                })
                
                # Меняем статус паспорта
                passport.status = "TTL_EXPIRED"
                passport.exit_reason = "TTL_EXPIRED"
                
                # Добавляем запись в timeline
                passport.timeline.append({
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "event": "STATUS: TTL_EXPIRED",
                    "details": f"Limit order canceled after TTL. Order ID: {order_id}"
                })
                
                # Сохраняем паспорт
                self.repository.save(passport)
                
                # Публикуем событие о закрытии
                await self.bus.publish(
                    event_type="POSITION_CLOSED",
                    source="lifecycle_manager",
                    payload={
                        "passport_id": passport_id,
                        "symbol": symbol,
                        "exit_reason": "TTL_EXPIRED"
                    },
                    symbol=symbol
                )
            else:
                self._log("ttl_cancel_failed", {
                    "passport_id": passport_id,
                    "error": cancel_result.get('error')
                })