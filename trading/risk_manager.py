"""
RiskManager — внутренняя защита позиции (Internal Stop).

Режим: ВНУТРЕННИЙ СТОП. TP1/TP2/SL НЕ выставляются на биржу как ордера.
RiskManager следит за ценой (PRICE_UPDATE из WS) и при пересечении уровней
закрывает позицию рыночными ордерами.

Поведение:
- TP1: закрыть 50% остатка, перенести SL в безубыток.
- TP2: закрыть остаток полностью.
- SL: закрыть остаток полностью.
- Идемпотентность: каждый уровень стреляет ровно один раз (флаги *_done).
- Свежесть цены: цена старше max_price_age_sec → проверки пропускаются (warning).
- Авторегистрация защиты: активный паспорт без guard → guard создаётся из паспорта.
- cancel_all_orders: отменяет только активные ордера паспорта (входные лимитки).

Hedge Mode: market-закрытие = side противоположная + positionSide = сторона позиции,
reduceOnly НЕ шлётся (запрещён в Hedge Mode, -1106).
"""

import time
from typing import Dict, Optional, Any
import asyncio

from core.types import PassportStatus
from core.event_bus import EventBus, Event
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.trader import Trader
from datetime import datetime, timezone
from core.logger import get_logger

logger = get_logger(__name__)


class RiskManager:
    """Внутренняя защита позиции (Internal Stop)."""

    def __init__(
        self,
        event_bus: EventBus,
        passport_manager: PassportManager,
        trader: Trader,
        config: Dict,
        json_logger: Any = None,
        passport_repository: Any = None
    ):
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.trader = trader
        self.config = config
        self.json_logger = json_logger
        self.repository = passport_repository
        self._guards: Dict[str, Dict[str, Any]] = {}

        self._max_price_age = float(
            self.config.get('risk', {}).get('max_price_age_sec', 3.0)
        )

        self._subscribe_to_events()
        self._log("init", {
            "message": "RiskManager initialized (internal stop mode)",
            "max_price_age_sec": self._max_price_age
        })
        self._guard_lock = asyncio.Lock()  # 🔥 НОВОЕ: Lock для синхронизации
    # ============================================================
    # СЛУЖЕБНОЕ
    # ============================================================

    def _log(self, event: str, data: Optional[Dict] = None, level: str = "INFO"):
        if self.json_logger:
            self.json_logger.log(
                module="risk_manager",
                event=event,
                data=data or {},
                level=level
            )
        else:
            logger.info(f"🛡️ [RISK] {event}: {data}")

    def _subscribe_to_events(self):
        self.bus.subscribe("POSITION_OPENED", self._on_position_opened)
        self.bus.subscribe("PRICE_UPDATE", self._on_price_update)
        self.bus.subscribe("ACCOUNT_UPDATE", self._on_account_update)
        self.bus.subscribe("POSITION_CLOSED", self._on_position_closed)

    # ============================================================
    # РЕГИСТРАЦИЯ ЗАЩИТЫ
    # ============================================================

    async def _on_position_opened(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        if not passport_id:
            return

        passport = self.passport_manager.get(passport_id)
        if not passport:
            self._log("passport_not_found", {"passport_id": passport_id})
            return

        # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Если уровни не заданы, рассчитываем их принудительно
        if passport.sl_price == 0 or passport.tp1_price == 0:
            self._log("levels_missing_calculating", {
                "passport_id": passport_id,
                "message": "Уровни отсутствуют, рассчитываем принудительно перед регистрацией guard"
            })
            
            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
            levels = self.trader.calculate_exit_levels(
                side=passport.side, 
                entry_price=passport.position_entry_price or passport.entry_price, 
                atr_value=atr_value
            )
            
            # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ: PassportManager сам обновит поля, timeline и сохранит на диск
            await self.passport_manager.apply_change(
                passport_id=passport_id,
                change_type="LEVELS_UPDATED",
                payload={
                    "sl_price": levels.get('sl_price', 0),
                    "tp1_price": levels.get('tp1_price', 0),
                    "tp2_price": levels.get('tp2_price', 0)
                }
            )

        # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ для регистрации guard
        await self.passport_manager.apply_change(
            passport_id=passport_id,
            change_type="GUARD_REGISTERED",
            payload={
                "sl_price": passport.sl_price,
                "tp1_price": passport.tp1_price,
                "tp2_price": passport.tp2_price,
                "remaining": passport.position_size
            }
        )
        self._register_guard_in_memory(passport)

    def _register_guard_in_memory(self, passport: TradePassport, remaining: Optional[float] = None):
        """Создать внутреннюю защиту по паспорту (только в памяти, без мутации паспорта)."""
        if remaining is None:
            remaining = passport.position_size

        lot = float(passport.position_size or remaining or 0)
        tp1_done = False
        sl_price = passport.sl_price

        if lot > 0 and float(remaining) < lot * 0.99:
            tp1_done = True
            sl_price = passport.position_entry_price or passport.entry_price

        self._guards[passport.passport_id] = {
            "passport_id": passport.passport_id,
            "symbol": passport.symbol,
            "side": passport.side,
            "lot": lot,
            "remaining": float(remaining),
            "tp1_price": passport.tp1_price,
            "tp2_price": passport.tp2_price,
            "sl_price": sl_price,
            "tp1_done": tp1_done,
            "tp2_done": False,
            "sl_done": False,
        }
        
        self._log("guard_registered_in_memory", {
            "passport_id": passport.passport_id,
            "side": passport.side,
            "remaining": float(remaining),
            "tp1": passport.tp1_price,
            "tp2": passport.tp2_price,
            "sl": sl_price,
            "tp1_done": tp1_done
        })

    async def ensure_guard_registered(self, passport):
        """Гарантированная регистрация guard через REST-фоллбэк."""
        if passport.passport_id in self._guards:
            return

        self._log("guard_fallback_triggered", {
            "passport_id": passport.passport_id,
            "message": "DriftMonitor detected open position without guard. Forcing registration."
        })

        if passport.sl_price == 0 or passport.tp1_price == 0:
            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
            levels = self.trader.calculate_exit_levels(
                side=passport.side,
                entry_price=passport.position_entry_price or passport.entry_price,
                atr_value=atr_value
            )
            await self.passport_manager.apply_change(
                passport_id=passport.passport_id,
                change_type="LEVELS_UPDATED",
                payload={
                    "sl_price": levels.get('sl_price', 0),
                    "tp1_price": levels.get('tp1_price', 0),
                    "tp2_price": levels.get('tp2_price', 0)
                }
            )

        await self.passport_manager.apply_change(
            passport_id=passport.passport_id,
            change_type="GUARD_REGISTERED",
            payload={
                "sl_price": passport.sl_price,
                "tp1_price": passport.tp1_price,
                "tp2_price": passport.tp2_price,
                "remaining": abs(passport.position_size or 0)
            }
        )
        self._register_guard_in_memory(passport)

    # ============================================================
    # СИНХРОНИЗАЦИЯ С БИРЖЕЙ
    # ============================================================

    async def _on_account_update(self, event: Event):
        payload = event.payload or {}

        updates = []
        if payload.get('symbol') and 'size' in payload:
            updates.append((payload['symbol'], abs(float(payload.get('size', 0) or 0))))
        else:
            account = payload.get('a', {}) or {}
            for pos in (account.get('P', []) or []):
                sym = pos.get('s')
                if not sym:
                    continue
                updates.append((sym, abs(float(pos.get('pa', 0) or 0))))

        if not updates:
            return

        for symbol, size in updates:
            for passport_id in list(self._guards.keys()):
                guard = self._guards[passport_id]
                if guard['symbol'] != symbol:
                    continue

                if size < 0.01:
                    self._guards.pop(passport_id, None)
                    self._log("guard_removed_zero_position", {
                        "passport_id": passport_id,
                        "symbol": symbol
                    })
                    continue

                guard['remaining'] = size
                if guard['lot'] > 0 and size < guard['lot'] * 0.99 and not guard['tp1_done']:
                    guard['tp1_done'] = True
                    passport = self.passport_manager.get(passport_id)
                    if passport:
                        be = passport.position_entry_price or passport.entry_price or 0.0
                        if be > 0:
                            guard['sl_price'] = be
                            # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ для переноса SL
                            await self.passport_manager.apply_change(
                                passport_id=passport_id,
                                change_type="SL_MOVED_TO_BREAKEVEN",
                                payload={
                                    "price": be,
                                    "reason": "TP1_HIT"
                                }
                            )
                            self._log("tp1_derived_from_exchange", {
                                "passport_id": passport_id,
                                "remaining": size,
                                "sl_breakeven": be
                            })

    async def _on_position_closed(self, event: Event):
        payload = event.payload
        passport_id = payload.get('passport_id')
        if passport_id and passport_id in self._guards:
            self._guards.pop(passport_id, None)
            self._log("guard_removed_position_closed", {"passport_id": passport_id})

    # ============================================================
    # ВНУТРЕННИЙ СТОП: ПРОВЕРКА УРОВНЕЙ
    # ============================================================

    async def _on_price_update(self, event: Event):
        payload = event.payload
        symbol = payload.get('symbol')
        price = float(payload.get('price', 0))
        ts = float(payload.get('ts', 0))

        if price <= 0:
            return

        age = time.time() - ts
        if age > self._max_price_age:
            self._log("price_stale_skip", {
                "symbol": symbol,
                "price": price,
                "age_sec": round(age, 2)
            })
            return

        # Авторегистрация: активный паспорт есть, а защиты нет
        active_ids = [g['passport_id'] for g in self._guards.values() if g['symbol'] == symbol]
        if not active_ids:
            if not symbol:
                return
            passport = self.passport_manager.get_active_by_symbol(symbol)
            if not passport:
                return
            
            if passport.status in (PassportStatus.OPEN.value, PassportStatus.PARTIAL_CLOSE.value):
                # 🔥 АТОМАРНАЯ РЕГИСТРАЦИЯ: используем lock
                async with self._guard_lock:
                    # Повторная проверка внутри lock
                    if passport.passport_id not in self._guards:
                        await self.passport_manager.apply_change(
                            passport_id=passport.passport_id,
                            change_type="GUARD_REGISTERED",
                            payload={
                                "sl_price": passport.sl_price,
                                "tp1_price": passport.tp1_price,
                                "tp2_price": passport.tp2_price,
                                "remaining": passport.position_size
                            }
                        )
                        self._register_guard_in_memory(passport)
                        active_ids = [passport.passport_id]

        for passport_id in active_ids:
            guard = self._guards.get(passport_id)
            if not guard:
                continue
            passport = self.passport_manager.get(passport_id)
            if not passport:
                self._guards.pop(passport_id, None)
                continue
            await self._check_guard(passport, guard, price)

    async def _check_guard(self, passport: TradePassport, guard: Dict, price: float):
        is_short = guard['side'] == 'short'

        # 🔥 TP1: частичное закрытие + SL в безубыток
        if not guard['tp1_done'] and guard['tp1_price'] > 0:
            hit = price <= guard['tp1_price'] if is_short else price >= guard['tp1_price']
            if hit:
                guard['tp1_done'] = True
                
                qty = round(guard['remaining'] * 0.5, 2)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'TP1_HIT')
                    if ok:
                        guard['remaining'] = round(guard['remaining'] - qty, 2)
                        be = passport.position_entry_price or passport.entry_price
                        
                        # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ
                        await self.passport_manager.apply_change(
                            passport_id=passport.passport_id,
                            change_type="TP1_HIT",
                            payload={
                                "price": price,
                                "closed_qty": qty,
                                "sl_breakeven": be
                            }
                        )
                        
                        self._log("tp1_triggered", {
                            "passport_id": passport.passport_id,
                            "price": price,
                            "closed_qty": qty,
                            "remaining": guard['remaining'],
                            "sl_breakeven": be
                        })
                    else:
                        guard['tp1_done'] = False

        # 🔥 TP2: полное закрытие остатка
        if not guard['tp2_done'] and guard['tp2_price'] > 0:
            hit = price <= guard['tp2_price'] if is_short else price >= guard['tp2_price']
            if hit:
                guard['tp2_done'] = True
                
                qty = await self._full_close_qty(passport, guard)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'TP2_HIT')
                    if ok:
                        guard['remaining'] = 0.0
                        # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ
                        await self.passport_manager.apply_change(
                            passport_id=passport.passport_id,
                            change_type="TP2_HIT",
                            payload={
                                "price": price,
                                "closed_qty": qty
                            }
                        )
                        
                        self._log("tp2_triggered", {
                            "passport_id": passport.passport_id,
                            "price": price,
                            "closed_qty": qty
                        })
                    else:
                        guard['tp2_done'] = False

        # 🔥 SL: полное закрытие остатка
        if not guard['sl_done'] and guard['sl_price'] > 0:
            hit = price >= guard['sl_price'] if is_short else price <= guard['sl_price']
            if hit:
                guard['sl_done'] = True
                
                qty = await self._full_close_qty(passport, guard)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'SL_HIT')
                    if ok:
                        guard['remaining'] = 0.0
                        # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ
                        await self.passport_manager.apply_change(
                            passport_id=passport.passport_id,
                            change_type="SL_HIT",
                            payload={
                                "price": price,
                                "closed_qty": qty
                            }
                        )
                        
                        self._log("sl_triggered", {
                            "passport_id": passport.passport_id,
                            "price": price,
                            "closed_qty": qty
                        })
                    else:
                        guard['sl_done'] = False

    # ============================================================
    # ИСПОЛНЕНИЕ: MARKET-ЗАКРЫТИЕ (Hedge Mode)
    # ============================================================

    async def _close_market(self, passport: TradePassport, guard: Dict, quantity: float, reason: str) -> bool:
        if quantity <= 0:
            return False

        try:
            pos = await self.trader.get_position_from_exchange(passport.symbol)
            real_size = abs(float(pos.get('size', 0) or 0)) if pos else 0.0
            
            if real_size < 0.01:
                self._log("close_aborted_position_already_zero", {
                    "passport_id": passport.passport_id,
                    "reason": reason,
                    "message": "Позиция уже закрыта, отмена отправки ордера во избежание ошибки -2022/-1106"
                })
                self._guards.pop(passport.passport_id, None)
                return False
                
            if real_size < quantity:
                self._log("close_qty_adjusted_to_exchange", {
                    "passport_id": passport.passport_id,
                    "requested": quantity,
                    "real_exchange_size": real_size
                })
                quantity = real_size

        except Exception as e:
            self._log("close_exchange_check_failed", {
                "passport_id": passport.passport_id,
                "error": str(e),
                "message": "Не удалось проверить размер позиции, отмена закрытия для безопасности"
            })
            return False

        is_short = guard['side'] == 'short'
        close_side = 'long' if is_short else 'short'

        prefix_map = {
            'TP1_HIT': 'C1',
            'TP2_HIT': 'C2',
            'SL_HIT': 'CS',
        }
        prefix = prefix_map.get(reason, 'CE')
        short_client_order_id = f"{prefix}_{passport.passport_id}"

        result = await self.trader.execute_order(
            symbol=passport.symbol,
            side=close_side,
            quantity=quantity,
            order_type='market',
            client_order_id=short_client_order_id,
            passport_id=passport.passport_id,
            position_side='SHORT' if is_short else 'LONG'
        )

        self._log("internal_close_sent", {
            "passport_id": passport.passport_id,
            "reason": reason,
            "side": close_side,
            "position_side": 'SHORT' if is_short else 'LONG',
            "quantity": quantity,
            "client_order_id": short_client_order_id,
            "success": result.get('success'),
            "error": result.get('error')
        })
        
        return bool(result.get('success'))

    async def _full_close_qty(self, passport: TradePassport, guard: Dict) -> float:
        qty = round(float(guard['remaining']), 2)
        try:
            pos = await self.trader.get_position_from_exchange(passport.symbol)
            if pos:
                real = abs(float(pos.get('size', 0) or 0))
                if real > 0.001 and abs(real - qty) > 0.001:
                    self._log("close_qty_reconciled", {
                        "passport_id": passport.passport_id,
                        "guard_remaining": qty,
                        "exchange_size": real,
                    })
                    qty = round(real, 2)
        except Exception as e:
            self._log("close_qty_reconcile_failed", {
                "passport_id": passport.passport_id,
                "error": str(e)
            })
        return qty

    # ============================================================
    # ОТМЕНА ОРДЕРОВ
    # ============================================================

    async def cancel_all_orders(self, passport: TradePassport) -> bool:
        symbol = passport.symbol
        cancelled = 0

        for order in passport.orders:
            order_id = order.get('order_id')
            if order_id and order.get('status') in ('NEW', 'PARTIALLY_FILLED'):
                result = await self.trader.cancel_order(symbol, order_id)
                if result:
                    # 🔥 ЕДИНЫЙ МЕТОД ИЗМЕНЕНИЯ для отмены ордера
                    await self.passport_manager.apply_change(
                        passport_id=passport.passport_id,
                        change_type="ORDER_CANCELLED",
                        payload={"order_id": order_id}
                    )
                    cancelled += 1
                    self._log("order_cancelled", {
                        "passport_id": passport.passport_id,
                        "order_id": order_id,
                        "client_order_id": order.get('client_order_id')
                    })

        self._log("all_orders_cancelled", {
            "passport_id": passport.passport_id,
            "cancelled_count": cancelled
        })
        return cancelled > 0

    async def force_guard_registration(self, symbol: str):
        open_passports = self.passport_manager.get_all_active_by_symbol(symbol)
        
        for passport in open_passports:
            if passport.passport_id in self._guards:
                continue
            
            if passport.sl_price == 0 or passport.tp1_price == 0:
                atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
                levels = self.trader.calculate_exit_levels(
                    side=passport.side,
                    entry_price=passport.position_entry_price or passport.entry_price,
                    atr_value=atr_value
                )
                await self.passport_manager.apply_change(
                    passport_id=passport.passport_id,
                    change_type="LEVELS_UPDATED",
                    payload={
                        "sl_price": levels.get('sl_price', 0),
                        "tp1_price": levels.get('tp1_price', 0),
                        "tp2_price": levels.get('tp2_price', 0)
                    }
                )
            
            await self.passport_manager.apply_change(
                passport_id=passport.passport_id,
                change_type="GUARD_REGISTERED",
                payload={
                    "sl_price": passport.sl_price,
                    "tp1_price": passport.tp1_price,
                    "tp2_price": passport.tp2_price,
                    "remaining": passport.position_size
                }
            )
            self._register_guard_in_memory(passport)

    async def stop(self):
        self._log("stopped", {"guards_active": len(self._guards)})