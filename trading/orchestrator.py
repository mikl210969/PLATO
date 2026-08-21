"""
Оркестратор — управляющий автомат платформы.
Слушает шину, принимает решения, пишет в паспорт.
"""

from typing import Dict, Optional, List, Any
import asyncio
import time

from core.types import Signal, PassportStatus
from core.event_bus import EventBus, Event
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.state_manager import StateManager
from trading.trader import Trader


class Orchestrator:
    """Управляющий автомат платформы."""

    def __init__(
        self,
        event_bus: EventBus,
        passport_manager: PassportManager,
        passport_repository: PassportRepository,
        state_manager: StateManager,
        config: Dict,
        json_logger: Any = None
    ):
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.repository = passport_repository
        self.state_manager = state_manager
        self.config = config
        self.json_logger = json_logger

        self.risk_manager = None  # будет установлен из main.py

        self.traders: Dict[str, Trader] = {}
        self._running = False
        
        # 🔥 Защита от повторных сигналов
        self._last_signal_time: Dict[str, float] = {}
        self._signal_cooldown = 10  # секунд

        self._subscribe_to_events()
        self._log("init", {"message": "Orchestrator initialized"})
        # RiskManager для отмены ордеров
        self.risk_manager = None

        self._stuck_orders_task = None  # ссылка на фоновую задачу

    def _log(self, event: str, data: Optional[Dict] = None, level: str = "INFO"):
        """Удобный метод для логирования с поддержкой correlation_id (passport_id)."""
        if data is None:
            data = {}
        
        # 🔥 АВТОМАТИЧЕСКОЕ ИЗВЛЕЧЕНИЕ correlation_id для трассировки
        correlation_id = data.get('passport_id')

        if self.json_logger:
            self.json_logger.log(
                module="orchestrator",
                event=event,
                data=data,
                level=level,
                correlation_id=correlation_id
            )
        else:
            # Fallback в консоль, если json_logger не инициализирован
            prefix = f"[{correlation_id}] " if correlation_id else ""
            print(f"📋 [ORCHESTRATOR] {prefix}{event}: {data}")

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

    def register_trader(self, symbol: str, trader: Trader):
        """Зарегистрировать трейдера."""
        self.traders[symbol] = trader
        self._log("trader_registered", {"symbol": symbol})

    def get_trader(self, symbol: str) -> Optional[Trader]:
        """Получить трейдера по символу."""
        return self.traders.get(symbol)

    async def _on_signal(self, event: Event):
        """Обработка сигнала от стратегии."""
        self._log("signal_received", {"event": event.type})
        payload = event.payload
        signal = payload.get('signal')
        if not signal:
            self._log("signal_rejected", {"reason": "signal_is_none"})
            return

        self._log("signal_processing", {
            "signal_id": signal.signal_id,
            "side": signal.side,
            "entry_price": signal.entry_price,
            "symbol": signal.symbol,
            "strategy": signal.strategy
        })

        # Защита от повторных сигналов
        current_time = time.time()
        last_time = self._last_signal_time.get(signal.symbol, 0)
        if current_time - last_time < self._signal_cooldown:
            self._log("signal_cooldown", {
                "symbol": signal.symbol,
                "seconds_since_last": round(current_time - last_time, 1),
                "cooldown": self._signal_cooldown
            })
            return
        self._last_signal_time[signal.symbol] = current_time

        # 1. Проверяем, занят ли символ
        if self.passport_manager.is_symbol_busy(signal.symbol):
            self._log("symbol_busy", {
                "symbol": signal.symbol,
                "signal_id": signal.signal_id
            })
            await self.bus.publish(
                event_type="SIGNAL_REJECTED",
                source="orchestrator",
                payload={
                    "symbol": signal.symbol,
                    "reason": "symbol_busy",
                    "signal_id": signal.signal_id
                },
                symbol=signal.symbol
            )
            return

        # 2. Создаём паспорт
        passport = self.passport_manager.create(
            symbol=signal.symbol,
            signal_id=signal.signal_id,
            strategy=signal.strategy,
            side=signal.side,
            entry_price=signal.entry_price,
            confidence=signal.confidence
        )
        self._log("passport_created", {"passport_id": passport.passport_id})

        # 3. Рассчитываем уровни выхода (SL, TP1, TP2)
        trader = self.get_trader(signal.symbol)
        if trader:
            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
            levels = trader.calculate_exit_levels(side=signal.side, entry_price=signal.entry_price, atr_value=atr_value)  # type: ignore
            passport.sl_price = levels.get('sl_price', 0)      # 🔥 ДОБАВЛЕНО
            passport.tp1_price = levels.get('tp1_price', 0)
            passport.tp2_price = levels.get('tp2_price', 0)
            self._log("levels_calculated", {
                "passport_id": passport.passport_id,
                "sl": passport.sl_price,                       # 🔥 ДОБАВЛЕНО
                "tp1": passport.tp1_price,
                "tp2": passport.tp2_price
            })

        # 4. Сохраняем паспорт
        self.repository.save(passport)
        self._log("passport_saved", {"passport_id": passport.passport_id})

        # 5. Получаем трейдера
        trader = self.get_trader(signal.symbol)
        if not trader:
            self._log("trader_not_found", {"symbol": signal.symbol})
            return

        # 6. Отправляем команду трейдеру
        quantity = self.config.get('trading', {}).get('lot_size', 7.0)
        order_type = self.config.get('trading', {}).get('entry_order_type', 'market')
        
        self._log("sending_order", {
            "symbol": signal.symbol,
            "side": signal.side,
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": signal.entry_price if order_type == 'limit' else None # 🔥 Добавили для отладки
        })

        result = await trader.execute_order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=quantity,
            order_type=order_type,
            client_order_id=signal.signal_id,
            passport_id=passport.passport_id,
            limit_price=signal.entry_price if order_type == 'limit' else None  # 🔥 ВОТ ЭТОГО НЕ ХВАТАЛО!
        )

        self._log("order_result", {
            "passport_id": passport.passport_id,
            "success": result.get('success'),
            "order_id": result.get('order_id'),
            "error": result.get('error') # 🔥 Добавили, чтобы видеть ошибку, если она есть
        })

        # 7. Обновляем паспорт по результату
        if result.get('success'):
            self.state_manager.transition(
                passport,
                PassportStatus.ORDER_SENT.value,
                "Order sent to exchange"
            )
            self.repository.save(passport)
            self._log("passport_updated_to_order_sent", {"passport_id": passport.passport_id})

            passport.add_order({
                "order_id": result.get('order_id'),
                "client_order_id": result.get('client_order_id'),
                "status": result.get('status', 'NEW'),
                "type": result.get('order_type', 'MARKET'),
                "side": signal.side,
                "price": signal.entry_price,
                "quantity": result.get('quantity', 0)
            })
            self.repository.save(passport)
            self._log("order_added_to_passport", {
                "passport_id": passport.passport_id,
                "order_id": result.get('order_id')
            })

            if order_type == 'limit':
                await self.bus.publish(
                    event_type="PASSPORT_CREATED",
                    source="orchestrator",
                    payload={
                        "passport_id": passport.passport_id,
                        "order_type": order_type,
                        "symbol": signal.symbol
                    },
                    symbol=signal.symbol
                )
                self._log("lifecycle_manager_started", {
                    "passport_id": passport.passport_id,
                    "order_type": order_type
                })

            self._log("order_sent_success", {"passport_id": passport.passport_id})
        else:
            self.state_manager.transition(
                passport,
                PassportStatus.FAILED.value,
                f"Order failed: {result.get('error', 'unknown')}"
            )
            self.repository.save(passport)
            self._log("order_failed", {
                "passport_id": passport.passport_id,
                "error": result.get('error')
            })

    async def _on_order_update(self, event: Event):
        """Обработка обновления ордера от биржи."""
        payload = event.payload
        client_order_id = payload.get('client_order_id')
        status = payload.get('status')
        symbol = payload.get('symbol')
        
        if not client_order_id or not symbol or not status:
            return

        passport = self._find_passport_by_client_order_id(client_order_id)
        if not passport:
            # Это может быть ордер, созданный вручную или старой версией платформы
            self._log("passport_not_found_for_order", {"client_order_id": client_order_id})
            return

        # =====================================================================
        # 1. ОБРАБОТКА ВНУТРЕННИХ СТОПОВ (RiskManager закрыл позицию маркетом)
        # =====================================================================
        if status == 'FILLED':
            if client_order_id.startswith('CLOSE_TP1_HIT_'):
                self._log("internal_tp1_filled", {"passport_id": passport.passport_id})
                passport.add_timeline_event("TP1_FILLED", "Internal stop triggered (partial)")
                self.repository.save(passport)
                
                await self.bus.publish(
                    event_type="TP1_FILLED",
                    source="orchestrator",
                    payload={"passport_id": passport.passport_id, "symbol": symbol},
                    symbol=symbol
                )
                return # Размер позиции корректно обновит ACCOUNT_UPDATE

            elif client_order_id.startswith('CLOSE_TP2_HIT_') or client_order_id.startswith('CLOSE_SL_HIT_'):
                reason = 'TP2_HIT' if 'TP2' in client_order_id else 'SL_HIT'
                self._log(f"internal_{reason.lower()}_filled", {"passport_id": passport.passport_id})
                
                self.state_manager.handle_event(passport, "POSITION_CLOSED", {'exit_reason': reason})
                passport.position_size = 0.0
                self.repository.save(passport)
                
                await self.bus.publish(
                    event_type="POSITION_CLOSED",
                    source="orchestrator",
                    payload={"passport_id": passport.passport_id, "exit_reason": reason},
                    symbol=symbol
                )
                return

        # =====================================================================
        # 2. ОБРАБОТКА ВХОДНОГО ОРДЕРА (например, WallFade_...)
        # =====================================================================
        event_mapping = {
            'NEW': 'ORDER_ACK',
            'PARTIALLY_FILLED': 'ORDER_PARTIAL',
            'FILLED': 'ORDER_FILLED',
            'CANCELED': 'ORDER_CANCELED',
            'REJECTED': 'ORDER_FAILED',
            'EXPIRED': 'ORDER_CANCELED'
        }

        evt = event_mapping.get(status)
        if not evt:
            self._log("order_update_unknown_status", {"status": status})
            return

        # ВАЖНО: Мы НЕ обновляем passport.position_size здесь!
        # Источником истины для размера позиции является ТОЛЬКО ACCOUNT_UPDATE.
        # ORDER_TRADE_UPDATE говорит только о статусе конкретного ордера.

        success = self.state_manager.handle_event(
            passport,
            evt,
            {
                'price': float(payload.get('price', 0)),
                'order_type': payload.get('order_type', 'MARKET')
            }
        )

        if success:
            self.repository.save(passport)
            self._log("passport_updated", {
                "passport_id": passport.passport_id,
                "new_status": passport.status,
                "event": evt
            })

            # 🔥 Если позиция открыта — отправляем событие для RiskManager
            if passport.status == PassportStatus.OPEN.value:
                await self.bus.publish(
                    event_type="POSITION_OPENED",
                    source="orchestrator",
                    payload={
                        "passport_id": passport.passport_id,
                        "symbol": symbol,
                        "side": passport.side,
                        "entry_price": passport.position_entry_price or passport.entry_price,
                        "position_size": passport.position_size # Будет взято из последнего ACCOUNT_UPDATE
                    },
                    symbol=symbol
                )
        else:
            self._log("state_manager_rejected_transition", {
                "passport_id": passport.passport_id,
                "event": evt,
                "current_status": passport.status
            })

    async def _on_account_update(self, event: Event):
        """Обработка обновления счёта."""
        print(f"🔥 [ORCHESTRATOR] _on_account_update CALLED! event={event}")
        self._log("account_update_received", {"event": event.type})
        payload = event.payload
        symbol = payload.get('symbol')
        position_size = float(payload.get('size', 0))
        
        print(f"🔥 [ORCHESTRATOR] symbol={symbol}, position_size={position_size}")
        print(f"🔥 [ORCHESTRATOR] position_size type: {type(position_size)}, value: {position_size}")
        print(f"🔥 [ORCHESTRATOR] abs(position_size) < 0.01: {abs(position_size) < 0.01}")        

        if not symbol:
            self._log("account_update_missing_symbol")
            return

        # 🔥 ЕСЛИ ПОЗИЦИЯ = 0 (или очень близка к 0) — ОТМЕНЯЕМ ОРДЕРА
        if abs(position_size) < 0.01:
            print("🔥 [ORCHESTRATOR] INSIDE IF!")
            
            # 🔥 Проверяем risk_manager
            print(f"🔥 [ORCHESTRATOR] hasattr(self, 'risk_manager'): {hasattr(self, 'risk_manager')}")
            print(f"🔥 [ORCHESTRATOR] self.risk_manager: {self.risk_manager}")
            
            self._log("account_update_zero_position", {
                "symbol": symbol,
                "position_size": position_size
            })
            print("🔥 [ORCHESTRATOR] AFTER LOG!")

            # Ищем ВСЕ паспорта по символу
            all_passports = self.passport_manager.get_by_symbol(symbol)
            if all_passports:
                # Берём последний паспорт
                passport = sorted(all_passports, key=lambda p: p.created_at, reverse=True)[0]
                self._log("found_passport_for_zero_position", {
                    "passport_id": passport.passport_id,
                    "status": passport.status,
                    "orders_count": len(passport.orders)
                })
                
                # Отменяем ордера, если они есть
                if hasattr(self, 'risk_manager') and self.risk_manager:
                    print("🔥 [ORCHESTRATOR] CALLING cancel_all_orders!")  # 🔥 ДОБАВИТЬ
                    await self.risk_manager.cancel_all_orders(passport)
                    print("🔥 [ORCHESTRATOR] cancel_all_orders FINISHED!")  # 🔥 ДОБАВИТЬ
                
                # Закрываем паспорт, если он ещё не закрыт
                if passport.status != PassportStatus.CLOSED.value:
                    self.state_manager.handle_event(
                        passport,
                        "POSITION_CLOSED",
                        {
                            'exit_reason': 'EXTERNAL_CLOSE',
                            'exit_price': 0,
                            'gross_pnl': 0,
                            'commission': 0
                        }
                    )
                    self.repository.save(passport)
                    self._log("passport_closed_by_zero_position", {
                        "passport_id": passport.passport_id
                    })
            else:
                self._log("no_passport_for_zero_position", {"symbol": symbol})
            return

        # 🔥 Если позиция НЕ равна 0 — продолжаем обычную синхронизацию
        passport = self.passport_manager.get_active_by_symbol(symbol)
        if not passport:
            self._log("no_active_passport_for_symbol", {"symbol": symbol})
            return
        
        self._log("account_update_data", {
            "symbol": symbol,
            "position_size": position_size
        })

        # Обычная синхронизация (если позиция есть)
        success = self.state_manager.sync_with_exchange(
            passport,
            payload.get('status', ''),
            position_size
        )

        if success:
            self.repository.save(passport)
            self._log("passport_synced", {
                "passport_id": passport.passport_id,
                "new_status": passport.status,
                "position_size": position_size
            })

            if passport.status == PassportStatus.CLOSED.value:
                self._log("position_closed_externally", {"passport_id": passport.passport_id})
                await self.bus.publish(
                    event_type="POSITION_CLOSED",
                    source="orchestrator",
                    payload={
                        "passport_id": passport.passport_id,
                        "exit_reason": "EXTERNAL_CLOSE",
                        "exit_price": payload.get('price', 0)
                    },
                    symbol=symbol
                )

    async def _on_position_closed(self, event: Event):
        """Обработка закрытия позиции."""
        self._log("position_closed_event_received", {"event": event.type})
        payload = event.payload
        passport_id = payload.get('passport_id')
        if not passport_id:
            self._log("position_closed_missing_passport_id")
            return

        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("passport_not_found_for_close", {"passport_id": passport_id})
            return

        self.state_manager.handle_event(
            passport,
            "POSITION_CLOSED",
            {
                'exit_reason': payload.get('exit_reason', 'MANUAL_CLOSE'),
                'exit_price': payload.get('exit_price', 0),
                'gross_pnl': payload.get('gross_pnl', 0),
                'commission': payload.get('commission', 0)
            }
        )

        self.repository.save(passport)
        self._log("position_closed", {
            "passport_id": passport.passport_id,
            "exit_reason": payload.get('exit_reason', 'MANUAL_CLOSE')
        })

    async def _on_sync_request(self, event: Event):
        """Синхронизация паспорта с биржей (после реконнекта или принудительно)."""
        payload = event.payload
        symbol = payload.get('symbol')
        if not symbol:
            return
        
        self._log("sync_request_received", {"symbol": symbol})
        
        trader = self.get_trader(symbol)
        if not trader:
            self._log("sync_trader_not_found", {"symbol": symbol})
            return
        
        try:
            position = await trader.get_position_from_exchange(symbol)
        except Exception as e:
            self._log("sync_position_fetch_failed", {"symbol": symbol, "error": str(e)})
            return
        
        # 🔥 КРИТИЧЕСКАЯ ПРОВЕРКА: различаем три состояния
        if position is None:
            # REST недоступен (бан, таймаут, сеть) — НЕ закрываем паспорт!
            self._log("sync_position_unavailable", {
                "symbol": symbol,
                "reason": "REST returned None (banned/timeout/network)"
            })
            return
        
        size = float(position.get('size', 0) or 0)
        passport = self.passport_manager.get_active_by_symbol(symbol)
        
        if passport:
            if abs(size) < 0.01:
                # Реальный ноль → позиция закрыта на бирже
                self._log("sync_position_closed_on_exchange", {
                    "passport_id": passport.passport_id,
                    "size": size
                })
                self.state_manager.handle_event(
                    passport,
                    "POSITION_CLOSED",
                    {'exit_reason': 'EXTERNAL_CLOSE', 'exit_price': 0.0, 'gross_pnl': 0, 'commission': 0}
                )
                passport.position_size = 0
                self.repository.save(passport)
                await self.bus.publish(
                    event_type="POSITION_CLOSED",
                    source="orchestrator",
                    payload={"passport_id": passport.passport_id, "exit_reason": "EXTERNAL_CLOSE"},
                    symbol=symbol
                )
            else:
                # Позиция есть → синхронизируем размер и цену входа
                # Позиция есть → синхронизируем размер и цену входа
                self._log("sync_position_updated", {
                    "passport_id": passport.passport_id,
                    "size": size
                })
                self.state_manager.sync_with_exchange(passport, 'FILLED', abs(size))
                passport.position_size = abs(size)
                
                # 🔥 Pylance-safe извлечение цены
                entry_price = position.get('entry_price')
                if entry_price is not None:
                    passport.position_entry_price = float(entry_price)
                    
                self.repository.save(passport)
        else:
            if abs(size) >= 0.01:
                # Позиция есть, но активного паспорта нет → позиция вне платформы
                self._log("sync_orphan_position_detected", {
                    "symbol": symbol,
                    "size": size
                })


    async def _on_ttl_expired(self, event: Event):
        """Обработка истечения TTL для лимитного ордера."""
        self._log("ttl_expired_received", {"event": event.type})
        payload = event.payload
        passport_id = payload.get('passport_id')
        symbol = payload.get('symbol')

        if not passport_id:
            self._log("ttl_expired_missing_passport_id")
            return

        if not symbol or not isinstance(symbol, str):
            self._log("ttl_expired_invalid_symbol", {"symbol": symbol})
            return

        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("ttl_expired_passport_not_found", {"passport_id": passport_id})
            return

        self._log("ttl_expired_processing", {
            "passport_id": passport_id,
            "symbol": symbol,
            "current_status": passport.status
        })

        if passport.status != PassportStatus.LIMIT_ON_BOOK.value:
            self._log("ttl_expired_skip_not_limit", {
                "passport_id": passport_id,
                "status": passport.status
            })
            return

        trader = self.get_trader(symbol)
        if not trader:
            self._log("ttl_expired_trader_not_found", {"symbol": symbol})
            return

        action = self.config.get('trading', {}).get('ttl_action', 'convert_to_market')
        
        if action == 'convert_to_market':
            self._log("ttl_expired_convert_to_market", {
                "passport_id": passport_id,
                "symbol": symbol
            })

            order_id = None
            if passport.orders:
                order_id = passport.orders[-1].get('order_id')

            if order_id:
                cancel_result = await trader.cancel_order(symbol, str(order_id))
                self._log("ttl_expired_cancel_order", {
                    "passport_id": passport_id,
                    "order_id": order_id,
                    "success": cancel_result
                })

            quantity = passport.position_size or self.config.get('trading', {}).get('lot_size', 7.0)
            result = await trader.execute_order(
                symbol=symbol,
                side=passport.side,
                quantity=quantity,
                order_type='market',
                client_order_id=f"CONVERT_{passport_id}",
                passport_id=passport_id
            )

            if result.get('success'):
                self.state_manager.transition(
                    passport,
                    PassportStatus.ORDER_SENT.value,
                    "Converted from limit to market"
                )
                self.repository.save(passport)
                self._log("ttl_expired_converted_to_market_success", {
                    "passport_id": passport_id,
                    "order_id": result.get('order_id')
                })
            else:
                self.state_manager.transition(
                    passport,
                    PassportStatus.CANCELED.value,
                    f"TTL expired, convert failed: {result.get('error')}"
                )
                self.repository.save(passport)
                self._log("ttl_expired_convert_failed", {
                    "passport_id": passport_id,
                    "error": result.get('error')
                })

        else:
            self._log("ttl_expired_cancel_only", {
                "passport_id": passport_id,
                "symbol": symbol
            })

            order_id = None
            if passport.orders:
                order_id = passport.orders[-1].get('order_id')

            if order_id:
                cancel_result = await trader.cancel_order(symbol, str(order_id))
                if cancel_result:
                    self.state_manager.transition(
                        passport,
                        PassportStatus.CANCELED.value,
                        "TTL expired, order canceled"
                    )
                    self.repository.save(passport)
                    self._log("ttl_expired_order_canceled", {"passport_id": passport_id})

    def _find_passport_by_client_order_id(self, client_order_id: str) -> Optional[TradePassport]:
        """Найти паспорт по client_order_id."""
        for passport in self.passport_manager.get_all():
            for order in passport.orders:
                if order.get('client_order_id') == client_order_id:
                    return passport
        return None

    async def close_position(self, symbol: str, exit_reason: str, exit_price: float = 0.0) -> bool:
        """Закрыть позицию по символу."""
        self._log("close_position_called", {
            "symbol": symbol,
            "exit_reason": exit_reason
        })
        
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

        self._log("close_order_result", {
            "success": result.get('success')
        })

        if result.get('success'):
            self.state_manager.handle_event(
                passport,
                "POSITION_CLOSING",
                {'exit_reason': exit_reason}
            )
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

    def set_risk_manager(self, risk_manager):
        """Установить RiskManager для отмены ордеров."""
        self.risk_manager = risk_manager
        self._log("risk_manager_set", {"status": "success"})

    async def start(self):
        """Запуск оркестратора."""
        self._running = True
        self._log("started", {"message": "Orchestrator started"})

    async def start_stuck_orders_monitor(self):
        """Запуск мониторинга зависших ордеров."""
        self._stuck_orders_task = asyncio.create_task(self._check_stuck_orders_loop())

    async def _check_stuck_orders_loop(self):
        """Фоновая проверка зависших ордеров в ORDER_SENT (полная, типобезопасная версия)."""
        import time
        
        while getattr(self, '_running', True):
            await asyncio.sleep(5)
            
            for passport in self.passport_manager.get_active():
                if passport.status != PassportStatus.ORDER_SENT.value:
                    continue
                
                # Безопасное получение времени создания
                created_at = getattr(passport, 'created_at', None)
                if not created_at:
                    continue
                
                try:
                    age = time.time() - created_at.timestamp()
                except AttributeError:
                    continue
                    
                if age < 10:
                    continue
                
                self._log("stuck_order_detected", {
                    "passport_id": passport.passport_id,
                    "age_sec": round(age, 1)
                })
                
                # 1. Форсируем реконнект WS
                await self.bus.publish(
                    event_type="WS_RECONNECT_FORCED",
                    source="orchestrator",
                    payload={"passport_id": passport.passport_id},
                    symbol=passport.symbol
                )
                
                # 2. Ждём 3 секунды, давая WS шанс доставить событие
                await asyncio.sleep(3)
                
                # 3. Безопасно перечитываем паспорт
                current_passport = self.passport_manager.get(passport.passport_id)
                if current_passport is None or current_passport.status != PassportStatus.ORDER_SENT.value:
                    continue # Статус изменился, проблема решена
                
                # 4. Безопасно получаем client_order_id
                orders = getattr(current_passport, 'orders', [])
                if not orders:
                    continue
                
                last_order = orders[-1]
                client_order_id = last_order.get('client_order_id') if isinstance(last_order, dict) else None
                if not client_order_id:
                    continue
                
                # 5. Безопасно получаем трейдера
                trader = self.get_trader(current_passport.symbol)
                if not trader:
                    continue

                # 6. REST-fallback
                order_status = await trader.get_order_status(
                    symbol=current_passport.symbol,
                    client_order_id=client_order_id
                )
                
                # 7. 🔥 ЖЕСТКАЯ ПРОВЕРКА ТИПА (гарантированно убирает ошибку Pylance про None)
                if order_status is None or not isinstance(order_status, dict):
                    self._log("order_status_invalid_or_none", {
                        "passport_id": current_passport.passport_id,
                        "client_order_id": client_order_id
                    })
                    self.state_manager.handle_event(current_passport, "ORDER_FAILED", {})
                    self.repository.save(current_passport)
                    continue
                
                # Теперь Pylance на 100% знает, что order_status это dict
                exchange_status: str = str(order_status.get('status', ''))
                
                if exchange_status == 'FILLED':
                    price_val = order_status.get('price')
                    qty_val = order_status.get('executedQty')
                    
                    self.state_manager.handle_event(current_passport, "ORDER_FILLED", {
                        'price': float(price_val) if price_val is not None else 0.0,
                        'quantity': float(qty_val) if qty_val is not None else 0.0
                    })
                    self.repository.save(current_passport)
                    
                    await self.bus.publish(
                        event_type="POSITION_OPENED",
                        source="orchestrator",
                        payload={
                            "passport_id": current_passport.passport_id,
                            "symbol": current_passport.symbol,
                            "side": current_passport.side,
                            "entry_price": float(price_val) if price_val is not None else 0.0,
                            "position_size": float(qty_val) if qty_val is not None else 0.0
                        },
                        symbol=current_passport.symbol
                    )
                elif exchange_status in ('CANCELED', 'EXPIRED', 'REJECTED'):
                    self.state_manager.handle_event(current_passport, "ORDER_CANCELED", {})
                    self.repository.save(current_passport)

    async def perform_startup_recovery(self, symbol: str):
        """
        Принудительная синхронизация при старте.
        ГАРАНТИРУЕТ блокировку торговли, если состояние биржи неизвестно.
        """
        self._log("startup_recovery_started", {"symbol": symbol})
        trader = self.get_trader(symbol)
        
        # 1. ПРОВЕРКА ЛОКАЛЬНОГО СОСТОЯНИЯ
        active_passport = self.passport_manager.get_active_by_symbol(symbol)
        
        if active_passport and active_passport.status in (PassportStatus.OPEN.value, PassportStatus.ORDER_SENT.value):
            self._log("startup_recovery_local_position_found", {"passport_id": active_passport.passport_id})
            if trader:
                try:
                    position = await trader.get_position_from_exchange(symbol)
                    if position:
                        size = float(position.get('size', 0) or 0)
                        if abs(size) < 0.01:
                            self._log("startup_recovery_zombie_cleanup", {"passport_id": active_passport.passport_id})
                            self.state_manager.handle_event(active_passport, "POSITION_CLOSED", {'exit_reason': 'STARTUP_ZOMBIE_CLEANUP'})
                            self.repository.save(active_passport)
                        else:
                            active_passport.position_size = abs(size)
                            active_passport.position_entry_price = float(position.get('entry_price', 0))
                            self.repository.save(active_passport)
                except Exception as e:
                    self._log("startup_recovery_sync_failed", {"error": str(e)})
            return

        # 2. ЛОКАЛЬНЫХ ПАСПОРТОВ НЕТ. Проверяем биржу через REST (до 3 попыток по 5 сек)
        position = None
        side = None
        entry_price = 0.0
        
        if trader:
            for attempt in range(3):
                try:
                    raw_result = await trader.rest._request('GET', '/fapi/v2/positionRisk', {'symbol': symbol}, signed=True)
                    
                    if isinstance(raw_result, list):
                        for pos in raw_result:
                            pos_amt = float(pos.get('positionAmt', 0) or 0)
                            if abs(pos_amt) > 0.001:
                                position = {
                                    'symbol': symbol,
                                    'size': abs(pos_amt),
                                    'entry_price': float(pos.get('entryPrice', 0) or 0),
                                    'unrealized_pnl': float(pos.get('unRealizedProfit', 0) or 0)
                                }
                                side = 'short' if pos.get('positionSide', '').upper() == 'SHORT' else 'long'
                                entry_price = float(pos.get('entryPrice', 0) or 0)
                                break
                    
                    if position:
                        break
                except Exception as e:
                    self._log("startup_recovery_fetch_failed", {"attempt": attempt + 1, "error": str(e)})
                
                if attempt < 2:
                    await asyncio.sleep(5)

        # 3. КРИТИЧЕСКАЯ ЗАЩИТА: Если REST не ответил, БЛОКИРУЕМ СИМВОЛ В ПАМЯТИ И НА ДИСКЕ
        if position is None:
            self._log("startup_recovery_critical_rest_failed", {
                "symbol": symbol, 
                "reason": "Cannot verify exchange state. BLOCKING trading to be safe."
            })
            block_passport = TradePassport(
                passport_id=f"BLOCKED_{symbol}_{int(time.time())}",
                symbol=symbol,
                status=PassportStatus.OPEN.value,
                signal_id="REST_UNAVAILABLE_BLOCK",
                strategy="SystemBlock",
                side="unknown",
                entry_price=0.0,
                position_size=0.0,
                position_entry_price=0.0
            )
            
            # 🔥 ИСПРАВЛЕНИЕ: Регистрируем в памяти, чтобы is_symbol_busy() сразу вернул True
            if hasattr(self.passport_manager, '_passports'):
                self.passport_manager._passports[block_passport.passport_id] = block_passport
            
            self.repository.save(block_passport)
            self._log("startup_recovery_system_blocked", {"passport_id": block_passport.passport_id})
            return

        # 4. REST ОТВЕТИЛ УСПЕШНО. Создаем Recovery-паспорт.
        size = position['size']
        
        if abs(size) >= 0.01:
            self._log("startup_recovery_orphan_position_found", {
                "symbol": symbol, 
                "size": size,
                "side": side
            })
            
            recovery_passport = TradePassport(
                passport_id=f"RECOVERY_{symbol}_{int(time.time())}",
                symbol=symbol,
                status=PassportStatus.OPEN.value,
                signal_id="STARTUP_RECOVERY",
                strategy="Recovery",
                side=side,
                entry_price=entry_price,
                position_size=size,
                position_entry_price=entry_price
            )
            
            # 🔥 ИСПРАВЛЕНИЕ: Регистрируем в памяти ДО сохранения на диск
            if hasattr(self.passport_manager, '_passports'):
                self.passport_manager._passports[recovery_passport.passport_id] = recovery_passport
                self._log("startup_recovery_passport_registered_in_memory", {"passport_id": recovery_passport.passport_id})
            
            # Явный расчет уровней защиты
            if trader:
                try:
                    levels = trader.calculate_exit_levels(side=side, entry_price=entry_price)
                    recovery_passport.sl_price = float(levels.get('sl_price', 0.0))
                    recovery_passport.tp1_price = float(levels.get('tp1_price', 0.0))
                    recovery_passport.tp2_price = float(levels.get('tp2_price', 0.0))
                    self._log("startup_recovery_levels_calculated", {
                        "passport_id": recovery_passport.passport_id,
                        "sl": recovery_passport.sl_price,
                        "tp1": recovery_passport.tp1_price,
                        "tp2": recovery_passport.tp2_price
                    })
                except Exception as e:
                    self._log("startup_recovery_levels_calculation_failed", {"error": str(e)})

            self.repository.save(recovery_passport)
            
            await self.bus.publish(
                event_type="POSITION_OPENED",
                source="orchestrator_recovery",
                payload={
                    "passport_id": recovery_passport.passport_id,
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "position_size": size
                },
                symbol=symbol
            )
            self._log("startup_recovery_passport_created_and_protected", {
                "passport_id": recovery_passport.passport_id,
                "side": side,
                "entry_price": entry_price
            })
        else:
            self._log("startup_recovery_clean_start", {"symbol": symbol, "size": 0})

        self._log("startup_recovery_completed", {"symbol": symbol, "final_exchange_size": size})

    async def stop(self):
        """Остановка оркестратора."""
        self._running = False
        if self._stuck_orders_task:
            self._stuck_orders_task.cancel()
        for trader in self.traders.values():
            await trader.stop()
        self._log("stopped", {"message": "Orchestrator stopped"})