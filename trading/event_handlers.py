import time
import datetime
import json
from typing import TYPE_CHECKING, Dict, Any, Optional
from datetime import datetime, timezone
from core.types import PassportStatus

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .base_mixin import BaseMixin

class EventHandlersMixin(BaseMixin):
    # Все аннотации типов теперь наследуются от BaseMixin

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
        self.bus.subscribe("ORDER_FILLED", self._on_order_filled)
        self.bus.subscribe("ORDER_PARTIAL", self._on_order_partial)        
        self._log("subscribed_to_events", {
            "subscriptions": ["SIGNAL_GENERATED", "ORDER_TRADE_UPDATE", "ACCOUNT_UPDATE", "POSITION_CLOSED", "SYNC_REQUEST", "TTL_EXPIRED"]
        })

    async def _on_signal(self, event):
        """Обработка сигнала от стратегии с Pre-Trade Gate."""
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

        # Проверка 1: Символ занят?
        if self.passport_manager.is_symbol_busy(signal.symbol):
            self._log("symbol_busy", {"symbol": signal.symbol, "signal_id": signal.signal_id})
            return

        # 🔥 Проверка 2: Активный дрейф?
        if hasattr(self, 'drift_monitor') and self.drift_monitor.is_drift_active(signal.symbol):
            self._log("signal_rejected_drift_active", {
                "symbol": signal.symbol,
                "signal_id": signal.signal_id,
                "reason": "drift_monitor_active"
            })
            return

        # 🔥 Проверка 3: Активный верификатор для этого символа?
        if hasattr(self, 'verifier'):
            # Проверяем, есть ли активная задача верификации для любого паспорта по этому символу
            active_passports = self.passport_manager.get_by_symbol(signal.symbol)
            for passport in active_passports:
                if passport.passport_id in self.verifier._active_tasks:
                    self._log("signal_rejected_verifier_active", {
                        "symbol": signal.symbol,
                        "signal_id": signal.signal_id,
                        "passport_id": passport.passport_id,
                        "reason": "verifier_active"
                    })
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

            # 🔥 ШАГ 6: Запускаем REST-верификатор СРАЗУ после отправки ордера.
            # Это гарантирует, что даже при потере WS-события NEW мы узнаем об исполнении.
            if hasattr(self, 'verifier'):
                await self.verifier.start_verification(
                    passport_id=passport.passport_id,
                    order_id=str(result.get('order_id', '')),
                    symbol=signal.symbol,
                    client_order_id=signal.signal_id
                )
                self._log("verifier_started_on_send", {
                    "passport_id": passport.passport_id,
                    "order_id": result.get('order_id'),
                })
            
            passport.add_order({
                "order_id": result.get('order_id'), "client_order_id": result.get('client_order_id'),
                "status": result.get('status', 'NEW'), "type": result.get('order_type', 'MARKET'),
                "side": signal.side, "price": signal.entry_price, "quantity": result.get('quantity', 0)
            })
            self.repository.save(passport)

            # 🔥 ОТЛАДКА TTL: Проверяем, пытаемся ли мы вообще опубликовать событие
            print(f"🔥 [DEBUG TTL] Конфиг order_type: '{order_type}' (тип: {type(order_type)})")
            
            if str(order_type).lower() == 'limit':
                print(f"🔥 [DEBUG TTL] Пытаемся опубликовать PASSPORT_CREATED для {passport.passport_id}")
                await self.bus.publish(
                    event_type="PASSPORT_CREATED",
                    source="orchestrator",
                    payload={
                        "passport_id": passport.passport_id,
                        "order_type": str(order_type).lower()
                    },
                    symbol=signal.symbol
                )
                print(f"✅ [DEBUG TTL] PASSPORT_CREATED успешно опубликован!")
            else:
                print(f"⚠️ [DEBUG TTL] Пропускаем публикацию PASSPORT_CREATED, так как order_type = '{order_type}' (ожидался 'limit')")
                
        else:
            # 🔥 ИСПРАВЛЕНО: передаём словарь, так как state_manager ожидает Dict[str, Any]
            self.state_manager.handle_event(passport, "ORDER_FAILED", {'error': result.get('error', 'unknown')})
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

        # 🔥 ШАГ 10.1: Парсинг коротких ID закрытий (C1_, C2_, CS_, CE_)
        import re
        close_match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
        if close_match:
            close_level = close_match.group(1)  # C1/C2/CS/CE
            passport_id = close_match.group(2)    # PASS_YYYYMMDD_HHMMSS_XXXXXX
            
            passport = self.passport_manager.get(passport_id)
            if passport and order_status in ('PARTIALLY_FILLED', 'FILLED'):
                # Публикуем ORDER_FILLED для закрытия — _on_order_filled обработает
                executed_qty = float(order_data.get('executed_qty') or order_data.get('z') or 0.0)
                avg_price = float(order_data.get('price') or order_data.get('ap') or 0.0)
                
                await self.bus.publish(
                    event_type="ORDER_FILLED",
                    source="ws_adapter",
                    payload={
                        "client_order_id": client_order_id,
                        "executed_qty": executed_qty,
                        "avg_price": avg_price,
                        "close_level": close_level,  # C1/C2/CS/CE
                    },
                    symbol=symbol
                )
                return

        # 5. Находим паспорт по client_order_id (для входных ордеров)
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

        # 6. Логика перехода статусов (для входных ордеров)
        if order_status == 'NEW':
            self.state_manager.handle_event(passport, "ORDER_ACK", {"details": "Order ACK received"})
            self.repository.save(passport)
            
        elif order_status in ('PARTIALLY_FILLED', 'FILLED'):
            executed_qty = float(order_data.get('executed_qty') or order_data.get('z') or 0.0)
            avg_price = float(order_data.get('price') or order_data.get('ap') or 0.0)
            
            self.state_manager.handle_event(passport, "ORDER_FILLED", {
                'price': avg_price,
                'quantity': executed_qty
            })
            
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
                        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        """Синхронизация с биржей: проверяем реальное состояние позиции."""
        payload = event.payload
        symbol = payload.get('symbol')
        
        if not symbol:
            self._log("sync_request_no_symbol")
            return
        
        self._log("sync_request_received", {"symbol": symbol})
        
        # Получаем активный паспорт
        passport = self.passport_manager.get_active_by_symbol(symbol)
        if not passport:
            self._log("sync_no_active_passport", {"symbol": symbol})
            return
        
        # Получаем позицию с биржи
        trader = self.get_trader(symbol)
        if not trader:
            self._log("sync_trader_not_found", {"symbol": symbol})
            return
        
        position = await trader.get_position_from_exchange(symbol)
        if not position:
            self._log("sync_position_fetch_failed", {"symbol": symbol})
            return
        
        position_size = abs(float(position.get('size', 0) or 0))
        
        self._log("sync_position_check", {
            "symbol": symbol,
            "passport_status": passport.status,
            "exchange_position_size": position_size
        })
        
        # Если на бирже позиции нет, а паспорт OPEN → закрываем паспорт
        if position_size < 0.01 and passport.status in ["OPEN", "PARTIAL_CLOSE"]:
            self._log("sync_external_close_detected", {
                "passport_id": passport.passport_id,
                "symbol": symbol
            })
            
            passport.status = PassportStatus.CLOSED.value
            passport.exit_reason = "EXTERNAL_CLOSE"
            passport.position_size = 0.0
            passport.closed_at = datetime.now(timezone.utc).isoformat()
            
            self.repository.save(passport)
            
            await self.bus.publish(
                event_type="POSITION_CLOSED",
                source="sync",
                payload={
                    "passport_id": passport.passport_id,
                    "symbol": symbol,
                    "exit_reason": "EXTERNAL_CLOSE"
                },
                symbol=symbol
            )

    async def _on_ttl_expired(self, event):
        """Обработка истечения TTL лимитного ордера с REST-верификацией при -2011."""
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

        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("ttl_passport_not_found", {"passport_id": passport_id})
            return

        trader = self.get_trader(symbol)
        if not trader:
            self._log("ttl_trader_not_found", {"symbol": symbol})
            return

        # Если ордер уже полностью исполнился, TTL не нужен
        if passport.status == "OPEN":
            self._log("ttl_skip_fully_filled", {"passport_id": passport_id, "status": passport.status})
            return

        elif passport.status == "PARTIALLY_FILLED":
            # Частичное исполнение: отменяем остаток, позиция остаётся OPEN
            self._log("ttl_partial_fill_detected", {
                "passport_id": passport_id,
                "position_size": passport.position_size,
                "original_quantity": passport.orders[-1].get('quantity', 0) if passport.orders else 0
            })

            cancel_result = await trader.cancel_order(symbol, order_id)

            if cancel_result.get('success') or cancel_result.get('code') == -2011:
                passport.timeline.append({
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "event": "TTL_EXPIRED_PARTIAL_FILL",
                    "details": f"TTL expired. Remaining order canceled. Position size: {passport.position_size}"
                })
                self.repository.save(passport)

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
                    "error": cancel_result
                })

        else:
            # ORDER_SENT, ORDER_ACK, LIMIT_ON_BOOK: ордер не исполнился — отменяем
            cancel_result = await trader.cancel_order(symbol, order_id)

            if cancel_result.get('success'):
                # Реальная отмена → закрываем паспорт
                await self._close_passport_after_ttl(passport, symbol, order_id, datetime)
                return

            if cancel_result.get('code') == -2011:
                # 🔥 КЛЮЧЕВОЙ ФИКС ШАГА 3: ордер не найден — выясняем его реальную судьбу
                self._log("ttl_order_not_found_verifying", {
                    "passport_id": passport_id,
                    "order_id": order_id
                })

                client_order_id = (passport.orders[-1].get('client_order_id')
                                   if passport.orders else None)

                order_status_data = await trader.rest.get_order_status(
                    symbol=symbol,
                    order_id=order_id,
                    client_order_id=client_order_id
                )

                if order_status_data:
                    real_status = order_status_data.get('status', '')
                    executed_qty = float(order_status_data.get('executedQty', 0) or 0)
                    avg_price = float(order_status_data.get('avgPrice', 0) or 0)

                    self._log("ttl_verification_result", {
                        "passport_id": passport_id,
                        "real_status": real_status,
                        "executed_qty": executed_qty,
                        "avg_price": avg_price
                    })

                    if real_status == 'FILLED':
                        # Ордер уже исполнен — публикуем ORDER_FILLED,
                        # дедупликация защитит от дубля, если WS-событие всё-таки придёт
                        dedup_key = f"REST:{order_id}:FILLED:{executed_qty}"
                        await self.bus.publish(
                            event_type="ORDER_FILLED",
                            source="ttl_verifier",
                            payload={
                                "client_order_id": client_order_id,
                                "status": "FILLED",
                                "symbol": symbol,
                                "executed_qty": executed_qty,
                                "avg_price": avg_price,
                                "dedup_key": dedup_key,
                            },
                            symbol=symbol
                        )
                        return

                    elif real_status == 'PARTIALLY_FILLED':
                        dedup_key = f"REST:{order_id}:PARTIAL:{executed_qty}"
                        await self.bus.publish(
                            event_type="ORDER_PARTIAL",
                            source="ttl_verifier",
                            payload={
                                "client_order_id": client_order_id,
                                "status": "PARTIALLY_FILLED",
                                "symbol": symbol,
                                "executed_qty": executed_qty,
                                "avg_price": avg_price,
                                "dedup_key": dedup_key,
                            },
                            symbol=symbol
                        )
                        # Закрываем паспорт как неисполненный остаток
                        await self._close_passport_after_ttl(passport, symbol, order_id, datetime)
                        return

                    # CANCELED, EXPIRED, REJECTED или реально нет ордера — закрываем паспорт
                    await self._close_passport_after_ttl(passport, symbol, order_id, datetime)
                    return

                # REST тоже не вернул данных — считаем ордер потерянным
                self._log("ttl_verification_failed", {
                    "passport_id": passport_id,
                    "order_id": order_id
                })
                await self._close_passport_after_ttl(passport, symbol, order_id, datetime)
                return

            # Другая ошибка отмены — не закрываем паспорт, пусть попробует TTL ещё раз
            self._log("ttl_cancel_failed", {
                "passport_id": passport_id,
                "error": cancel_result
            })

    async def _close_passport_after_ttl(self, passport, symbol, order_id, datetime):
        """Вспомогательный метод: закрыть паспорт после истечения TTL."""
        passport.status = "CLOSED"
        passport.exit_reason = "TTL_EXPIRED"
        passport.closed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        passport.timeline.append({
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "STATUS: CLOSED",
            "details": f"Limit order canceled after TTL. Order ID: {order_id}"
        })

        self.repository.save(passport)

        await self.bus.publish(
            event_type="POSITION_CLOSED",
            source="lifecycle_manager",
            payload={
                "passport_id": passport.passport_id,
                "symbol": symbol,
                "exit_reason": "TTL_EXPIRED",
                "gross_pnl": 0.0
            },
            symbol=symbol
        )

    async def _on_order_filled(self, event):
        """
        Обработка события исполнения ордера (WS или REST-верификатор).
        Поддерживает как исходные ордера, так и TP/SL ордера закрытия.
        """
        payload = event.payload
        client_order_id = payload.get('client_order_id')
        executed_qty = float(payload.get('executed_qty', 0) or 0)
        avg_price = float(payload.get('avg_price', 0) or 0)
        source = event.source

        if not client_order_id:
            self._log("filled_missing_client_order_id", {"payload": payload})
            return

        # 🔥 ШАГ 10.1: Парсинг коротких ID закрытий (C1_, C2_, CS_, CE_)
        passport = None
        close_order_detected = False
        close_level = payload.get('close_level')  # C1/C2/CS/CE (если есть)
        
        import re
        
        # Приоритет 1: короткий формат C1_PASS_...
        if close_level or re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id):
            match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
            if match:
                if not close_level:
                    close_level = match.group(1)
                passport_id = match.group(2)
                passport = self.passport_manager.get(passport_id)
                if passport:
                    close_order_detected = True
                    self._log("close_order_detected_short", {
                        "passport_id": passport_id,
                        "close_level": close_level,
                        "client_order_id": client_order_id,
                        "executed_qty": executed_qty,
                    })
        
        # Приоритет 2: legacy формат PASS_... (для обратной совместимости)
        if not passport and 'PASS_' in client_order_id:
            match = re.search(r'(PASS_\d{8}_\d{6}_[a-f0-9]+)', client_order_id)
            if match:
                extracted_passport_id = match.group(1)
                passport = self.passport_manager.get(extracted_passport_id)
                if passport:
                    close_order_detected = True
                    self._log("close_order_detected_legacy", {
                        "passport_id": extracted_passport_id,
                        "client_order_id": client_order_id,
                        "executed_qty": executed_qty,
                    })

        # Приоритет 3: ищем по client_order_id среди активных (для входных ордеров)
        if not passport:
            for p in self.passport_manager.get_all():
                if p.status in ("CLOSED", "CANCELED", "FAILED"):
                    continue
                for order in p.orders:
                    if order.get('client_order_id') == client_order_id:
                        passport = p
                        break
                if passport:
                    break

        if not passport:
            self._log("filled_passport_not_found", {
                "client_order_id": client_order_id,
                "source": source,
            })
            return

        # Если это ордер закрытия, обрабатываем как PARTIAL_CLOSE
        if close_order_detected:
            # Обновляем размер позиции
            passport.position_size -= executed_qty
            
            # Определяем exit_reason из close_level
            exit_reason_map = {
                'C1': 'TP1_HIT',
                'C2': 'TP2_HIT',
                'CS': 'SL_HIT',
                'CE': 'EXTERNAL_CLOSE',
            }
            exit_reason = exit_reason_map.get(close_level, 'MANUAL_CLOSE')
            
            # Если позиция полностью закрыта
            if passport.position_size < 0.01:
                passport.close(
                    exit_reason=exit_reason,
                    exit_price=avg_price,
                    gross_pnl=self._calculate_pnl(passport, avg_price, executed_qty),
                    commission=0.0
                )
                self.repository.save(passport)
                
                await self.bus.publish(
                    event_type="POSITION_CLOSED",
                    source="order_filled",
                    payload={
                        "passport_id": passport.passport_id,
                        "symbol": passport.symbol,
                        "exit_reason": exit_reason,
                        "gross_pnl": passport.gross_pnl,
                    },
                    symbol=passport.symbol
                )
                
                self._log("position_fully_closed", {
                    "passport_id": passport.passport_id,
                    "exit_reason": exit_reason,
                    "closed_qty": executed_qty,
                    "gross_pnl": passport.gross_pnl,
                })
            else:
                # Частичное закрытие
                self.state_manager.handle_event(passport, "PARTIAL_CLOSE", {
                    'closed_qty': executed_qty,
                    'exit_price': avg_price,
                    'exit_reason': exit_reason,
                })
                self.repository.save(passport)
                
                self._log("position_partially_closed", {
                    "passport_id": passport.passport_id,
                    "exit_reason": exit_reason,
                    "closed_qty": executed_qty,
                    "remaining_size": passport.position_size,
                })
            return

        # Обычный ордер открытия — применяем переход
        transitioned = self.state_manager.handle_event(passport, "ORDER_FILLED", {
            'executed_qty': executed_qty,
            'price': avg_price,
        })

        # Отменяем активный верификатор
        if hasattr(self, 'verifier'):
            await self.verifier.cancel_verification(passport.passport_id)

        if not transitioned:
            # 🔥 ШАГ 10.2: Умная рекonsиляция объёма
            # Если статус уже OPEN, но объём отличается от реального — 
            # значит WS потерял часть partial fill'ов, rest_verifier принёс правду.
            # Реконсилируем к истине (биржа = источник истины).
            if passport.status == "OPEN" and executed_qty > 0:
                old_size = passport.position_size
                if abs(executed_qty - old_size) > 0.001:
                    passport.position_size = executed_qty
                    if avg_price > 0:
                        passport.position_entry_price = avg_price
                    
                    self.repository.save(passport)
                    
                    self._log("volume_reconciled", {
                        "passport_id": passport.passport_id,
                        "old_size": old_size,
                        "new_size": executed_qty,
                        "source": source,
                    })
                    
                    # Перепубликуем POSITION_OPENED для обновления RiskManager
                    await self.bus.publish(
                        event_type="POSITION_OPENED",
                        source=source,
                        payload={
                            "passport_id": passport.passport_id,
                            "symbol": passport.symbol,
                            "side": passport.side,
                            "entry_price": passport.position_entry_price,
                            "position_size": passport.position_size,
                        },
                        symbol=passport.symbol,
                    )
                    return
            
            # Обычный noop — ничего не делаем
            self._log("order_filled_noop", {
                "passport_id": passport.passport_id,
                "client_order_id": client_order_id,
                "status": passport.status,
                "source": source,
            })
            return

        self.repository.save(passport)

        self._log("order_filled_processed", {
            "passport_id": passport.passport_id,
            "client_order_id": client_order_id,
            "executed_qty": executed_qty,
            "avg_price": avg_price,
            "source": source,
            "new_status": passport.status,
        })

        # Публикуем POSITION_OPENED для RiskManager
        await self.bus.publish(
            event_type="POSITION_OPENED",
            source=source,
            payload={
                "passport_id": passport.passport_id,
                "symbol": passport.symbol,
                "side": passport.side,
                "entry_price": passport.position_entry_price,
                "position_size": passport.position_size,
            },
            symbol=passport.symbol,
        )

    async def _on_order_partial(self, event):
        """Обработка частичного исполнения."""
        payload = event.payload
        client_order_id = payload.get('client_order_id')
        executed_qty = float(payload.get('executed_qty', 0) or 0)
        avg_price = float(payload.get('avg_price', 0) or 0)

        if not client_order_id:
            return

        passport = None
        for p in self.passport_manager.get_all():
            if p.status in ("CLOSED", "CANCELED", "FAILED"):
                continue
            for order in p.orders:
                if order.get('client_order_id') == client_order_id:
                    passport = p
                    break
            if passport:
                break

        if not passport:
            return

        self.state_manager.handle_event(passport, "ORDER_PARTIAL", {
            'executed_qty': executed_qty,
            'price': avg_price,
        })
        self.repository.save(passport)

        self._log("order_partial_processed", {
            "passport_id": passport.passport_id,
            "client_order_id": client_order_id,
            "executed_qty": executed_qty,
        })

    def _calculate_pnl(self, passport, exit_price: float, quantity: float) -> float:
        """Рассчитать PnL для закрытия."""
        if not passport.position_entry_price or passport.position_entry_price == 0:
            return 0.0
        
        if passport.side == 'short':
            return (passport.position_entry_price - exit_price) * quantity
        else:
            return (exit_price - passport.position_entry_price) * quantity