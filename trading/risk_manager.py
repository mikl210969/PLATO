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
- Авторегистрация защиты: активный паспорт без guard → guard создаётся из паспорта,
  tp1_done выводится из фактического размера позиции (биржа = источник истины).
- cancel_all_orders: отменяет только активные ордера паспорта (входные лимитки),
  история НЕ очищается — ордера помечаются CANCELED.

Hedge Mode: market-закрытие = side противоположная + positionSide = сторона позиции,
reduceOnly НЕ шлётся (запрещён в Hedge Mode, -1106).
"""

import time
from typing import Dict, Optional, Any

from core.types import PassportStatus
from core.event_bus import EventBus, Event
from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.trader import Trader


class RiskManager:
    """Внутренняя защита позиции (Internal Stop)."""

    def __init__(
        self,
        event_bus: EventBus,
        passport_manager: PassportManager,
        trader: Trader,
        config: Dict,
        json_logger: Any = None
    ):
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.trader = trader
        self.config = config
        self.json_logger = json_logger

        # Внутренняя защита: passport_id -> guard
        self._guards: Dict[str, Dict[str, Any]] = {}

        # Свежесть цены (сек). Конфиг: risk.max_price_age_sec, дефолт 3.0
        self._max_price_age = float(
            self.config.get('risk', {}).get('max_price_age_sec', 3.0)
        )

        self._subscribe_to_events()
        self._log("init", {
            "message": "RiskManager initialized (internal stop mode)",
            "max_price_age_sec": self._max_price_age
        })

    # ============================================================
    # СЛУЖЕБНОЕ
    # ============================================================

    def _log(self, event: str, data: Optional[Dict] = None):
        if self.json_logger:
            self.json_logger.log(
                module="risk_manager",
                event=event,
                data=data or {}
            )
        else:
            print(f"🛡️ [RISK] {event}: {data}")

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

        if passport.sl_price == 0 or passport.tp1_price == 0:
            self._log("levels_not_set", {
                "passport_id": passport_id,
                "sl": passport.sl_price,
                "tp1": passport.tp1_price
            })
            return

        self._register_guard(passport, remaining=passport.position_size)

    def _register_guard(self, passport: TradePassport, remaining: Optional[float] = None):
        """Создать внутреннюю защиту по паспорту."""
        if remaining is None:
            remaining = passport.position_size

        lot = float(passport.position_size or remaining or 0)
        tp1_done = False
        sl_price = passport.sl_price

        # Биржа = источник истины: если размер меньше лота,
        # консервативно считаем, что TP1 уже сработал → SL в безубыток.
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
        self._log("guard_registered", {
            "passport_id": passport.passport_id,
            "side": passport.side,
            "remaining": float(remaining),
            "tp1": passport.tp1_price,
            "tp2": passport.tp2_price,
            "sl": sl_price,
            "tp1_done": tp1_done
        })

    # ============================================================
    # СИНХРОНИЗАЦИЯ С БИРЖЕЙ
    # ============================================================

    async def _on_account_update(self, event: Event):
        payload = event.payload
        symbol = payload.get('symbol')
        if not symbol:
            return

        size = abs(float(payload.get('size', 0)))

        for passport_id in list(self._guards.keys()):
            guard = self._guards[passport_id]
            if guard['symbol'] != symbol:
                continue

            if size < 0.01:
                # Позиция закрыта — защита не нужна
                self._guards.pop(passport_id, None)
                self._log("guard_removed_zero_position", {
                    "passport_id": passport_id,
                    "symbol": symbol
                })
                continue

            # Синхронизация остатка и флага TP1 из реального размера
            guard['remaining'] = size
            if guard['lot'] > 0 and size < guard['lot'] * 0.99 and not guard['tp1_done']:
                guard['tp1_done'] = True
                passport = self.passport_manager.get(passport_id)
                if passport:
                    be = passport.position_entry_price or passport.entry_price
                    guard['sl_price'] = be
                    passport.sl_price = be
                    passport.add_timeline_event('SL_MOVED_TO_BREAKEVEN', {'price': be, 'reason': 'ACCOUNT_UPDATE_SYNC'})
                    self.passport_manager.update(passport)
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

        # Контроль свежести цены: протухшая цена → не принимаем решений
        age = time.time() - ts
        if age > self._max_price_age:
            self._log("price_stale_skip", {
                "symbol": symbol,
                "price": price,
                "age_sec": round(age, 2)
            })
            return

        # Авторегистрация: активный паспорт есть, а защиты нет (рестарт/сбой)
        active_ids = [g['passport_id'] for g in self._guards.values() if g['symbol'] == symbol]
        if not active_ids:
            passport = self.passport_manager.get_active_by_symbol(symbol)
            if passport and passport.status in (
                PassportStatus.OPEN.value,
                PassportStatus.PARTIAL_CLOSE.value
            ):
                self._register_guard(passport, remaining=passport.position_size)
                active_ids = [passport.passport_id]

        # Проверка уровней по каждому guard символа
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
                guard['tp1_done'] = True  # ставим ДО отправки — защита от дублей
                qty = round(guard['remaining'] * 0.5, 2)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'TP1_HIT')
                    if ok:
                        guard['remaining'] = round(guard['remaining'] - qty, 2)
                        be = passport.position_entry_price or passport.entry_price
                        guard['sl_price'] = be
                        passport.sl_price = be
                        passport.add_timeline_event('SL_MOVED_TO_BREAKEVEN', {'price': be, 'reason': 'TP1_HIT'})
                        self.passport_manager.update(passport)
                        self._log("tp1_triggered", {
                            "passport_id": passport.passport_id,
                            "price": price,
                            "closed_qty": qty,
                            "remaining": guard['remaining'],
                            "sl_breakeven": be
                        })
                    else:
                        guard['tp1_done'] = False  # retry на следующем тике

        # 🔥 TP2: полное закрытие остатка
        if not guard['tp2_done'] and guard['tp2_price'] > 0:
            hit = price <= guard['tp2_price'] if is_short else price >= guard['tp2_price']
            if hit:
                guard['tp2_done'] = True
                qty = round(guard['remaining'], 2)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'TP2_HIT')
                    if ok:
                        guard['remaining'] = 0.0
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
                qty = round(guard['remaining'], 2)
                if qty >= 0.1:
                    ok = await self._close_market(passport, guard, qty, 'SL_HIT')
                    if ok:
                        guard['remaining'] = 0.0
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
        """Закрыть часть позиции маркетом. Hedge Mode: positionSide = сторона позиции, без reduceOnly."""
        if quantity <= 0:
            return False

        is_short = guard['side'] == 'short'
        close_side = 'long' if is_short else 'short'  # execute_order: 'long' -> BUY

        result = await self.trader.execute_order(
            symbol=passport.symbol,
            side=close_side,
            quantity=quantity,
            order_type='market',
            #client_order_id=f"{reason}_{passport.passport_id}",
            client_order_id=f"CLOSE_{reason}_{passport.passport_id}",            
            passport_id=passport.passport_id,
            position_side='SHORT' if is_short else 'LONG'  # сторона ПОЗИЦИИ, не ордера
        )

        self._log("internal_close_sent", {
            "passport_id": passport.passport_id,
            "reason": reason,
            "side": close_side,
            "position_side": 'SHORT' if is_short else 'LONG',
            "quantity": quantity,
            "success": result.get('success'),
            "error": result.get('error')
        })
        return bool(result.get('success'))

    # ============================================================
    # ОТМЕНА ОРДЕРОВ (только входные лимитки; история сохраняется)
    # ============================================================

    async def cancel_all_orders(self, passport: TradePassport) -> bool:
        """Отменить все активные ордера паспорта (входные лимитки). История НЕ очищается."""
        symbol = passport.symbol
        cancelled = 0

        for order in passport.orders:
            order_id = order.get('order_id')
            if order_id and order.get('status') in ('NEW', 'PARTIALLY_FILLED'):
                result = await self.trader.cancel_order(symbol, order_id)
                if result:
                    order['status'] = 'CANCELED'  # помечаем, не удаляем
                    cancelled += 1
                    self._log("order_cancelled", {
                        "passport_id": passport.passport_id,
                        "order_id": order_id,
                        "client_order_id": order.get('client_order_id')
                    })

        self.passport_manager.update(passport)
        self._log("all_orders_cancelled", {
            "passport_id": passport.passport_id,
            "cancelled_count": cancelled
        })
        return cancelled > 0

    async def stop(self):
        self._log("stopped", {"guards_active": len(self._guards)})