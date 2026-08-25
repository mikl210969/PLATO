"""
PositionMonitor — мониторинг и управление открытыми позициями.
- Следит за ценой в реальном времени
- Закрывает позицию при достижении SL, TP1, TP2
- Сдвигает SL в Break-Even при прохождении 0.5 ATR в прибыль
- Проверяет Basis Stop (>1.5%)
"""
import asyncio
import time
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .base_mixin import BaseMixin


class PositionMonitor(BaseMixin):
    """Монитор позиций с управлением TP/SL/Basis Stop."""

    def __init__(self):
        self._monitor_task = None
        self._position_running = True
        self._last_price_check: Dict[str, float] = {}

    async def start_position_monitor(self):
        """Запуск мониторинга позиций."""
        self._monitor_task = asyncio.create_task(self._position_monitor_loop())

    async def stop_position_monitor(self):
        """Остановка мониторинга позиций."""
        self._position_running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _position_monitor_loop(self):
        """Основной цикл мониторинга позиций."""
        while getattr(self, '_position_running', True):
            await asyncio.sleep(1)
            
            for passport in self.passport_manager.get_active():
                if passport.status != "OPEN":
                    continue
                
                current_price = await self._get_current_price(passport.symbol)
                if not current_price:
                    continue
                
                # Проверяем в порядке приоритета
                if await self._check_basis_stop(passport, current_price):
                    continue
                
                if await self._check_tp1(passport, current_price):
                    continue
                
                if await self._check_tp2(passport, current_price):
                    continue
                
                if await self._check_sl(passport, current_price):
                    continue
                
                await self._check_break_even(passport, current_price)

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """Получить текущую цену из стакана биржи."""
        try:
            trader = self.get_trader(symbol)
            if not trader:
                return None
            
            orderbook = await trader.rest.get_orderbook(symbol, limit=5)
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])
            
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                return (best_bid + best_ask) / 2
            
            return None
        except Exception as e:
            self._log("price_fetch_error", {"symbol": symbol, "error": str(e)})
            return None

    async def _check_basis_stop(self, passport, current_price: float) -> bool:
        """Проверка Basis Stop (>1.5% движения против позиции)."""
        basis_threshold = 1.5
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return False
        
        price_change_pct = abs(current_price - entry_price) / entry_price * 100
        
        if price_change_pct > basis_threshold:
            self._log("basis_stop_triggered", {
                "passport_id": passport.passport_id,
                "price_change_pct": round(price_change_pct, 2),
                "threshold": basis_threshold
            })
            await self._close_position(passport, current_price, "BASIS_STOP")
            return True
        return False

    async def _check_tp1(self, passport, current_price: float) -> bool:
        """Проверка TP1: закрытие 50% позиции + сдвиг SL в Break-Even."""
        tp1_price = passport.tp1_price
        if not tp1_price or tp1_price <= 0:
            return False
        
        if passport.side == "short":
            if current_price > tp1_price:
                return False
        else:
            if current_price < tp1_price:
                return False
        
        if getattr(passport, 'tp1_closed', False):
            return False
        
        self._log("tp1_triggered", {
            "passport_id": passport.passport_id,
            "tp1_price": tp1_price,
            "current_price": current_price
        })
        
        close_quantity = passport.position_size * 0.5
        await self._close_position(passport, current_price, "TP1", quantity=close_quantity)
        await self._move_sl_to_break_even(passport)
        passport.tp1_closed = True
        return True

    async def _check_tp2(self, passport, current_price: float) -> bool:
        """Проверка TP2: закрытие оставшихся 50% позиции."""
        tp2_price = passport.tp2_price
        if not tp2_price or tp2_price <= 0:
            return False
        
        if passport.side == "short":
            if current_price > tp2_price:
                return False
        else:
            if current_price < tp2_price:
                return False
        
        if getattr(passport, 'tp2_closed', False):
            return False
        
        self._log("tp2_triggered", {
            "passport_id": passport.passport_id,
            "tp2_price": tp2_price,
            "current_price": current_price
        })
        
        await self._close_position(passport, current_price, "TP2")
        passport.tp2_closed = True
        return True

    async def _check_sl(self, passport, current_price: float) -> bool:
        """Проверка SL: полное закрытие позиции."""
        sl_price = passport.sl_price
        if not sl_price or sl_price <= 0:
            return False
        
        if passport.side == "short":
            if current_price < sl_price:
                return False
        else:
            if current_price > sl_price:
                return False
        
        self._log("sl_triggered", {
            "passport_id": passport.passport_id,
            "sl_price": sl_price,
            "current_price": current_price
        })
        
        await self._close_position(passport, current_price, "SL_HIT")
        return True

    async def _check_break_even(self, passport, current_price: float):
        """Сдвиг SL в Break-Even при прохождении 0.5 ATR в прибыль."""
        if getattr(passport, 'sl_moved_to_be', False):
            return
        
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return
        
        atr_value = self._get_atr_value()
        be_offset = 0.25 * atr_value
        
        if passport.side == "short":
            profit_distance = entry_price - current_price
            new_sl_price = current_price + be_offset
        else:
            profit_distance = current_price - entry_price
            new_sl_price = current_price - be_offset
        
        min_profit_for_be = 0.5 * atr_value
        
        if profit_distance >= min_profit_for_be:
            if passport.side == "short":
                if new_sl_price < passport.sl_price:
                    passport.sl_price = new_sl_price
                    passport.sl_moved_to_be = True
                    self._log("break_even_applied", {"passport_id": passport.passport_id, "new_sl": new_sl_price})
            else:
                if new_sl_price > passport.sl_price:
                    passport.sl_price = new_sl_price
                    passport.sl_moved_to_be = True
                    self._log("break_even_applied", {"passport_id": passport.passport_id, "new_sl": new_sl_price})
            
            self.repository.save(passport)

    async def _close_position(self, passport, price: float, reason: str, quantity: Optional[float] = None):
        """Закрыть позицию (полностью или частично) по рынку."""
        trader = self.get_trader(passport.symbol)
        if not trader:
            self._log("trader_not_found_for_close", {"passport_id": passport.passport_id})
            return False
        
        if quantity is None:
            quantity = passport.position_size
        
        is_partial = quantity < passport.position_size
        
        self._log("closing_position", {
            "passport_id": passport.passport_id,
            "quantity": quantity,
            "total_size": passport.position_size,
            "reason": reason,
            "is_partial": is_partial
        })
        
        # 🔥 Hedge Mode: передаём position_side явно, reduce_only НЕ шлётся (-1106)
        position_side = "SHORT" if passport.side == "short" else "LONG"
        
        result = await trader.execute_order(
            symbol=passport.symbol,
            side="buy" if passport.side == "short" else "sell",
            quantity=quantity,
            order_type="market",
            client_order_id=f"{reason}_{passport.passport_id}",
            passport_id=passport.passport_id,
            position_side=position_side
        )
        
        if not result.get('success'):
            self._log("close_failed", {
                "passport_id": passport.passport_id,
                "error": result.get('error')
            })
            return False
        
        if is_partial:
            passport.position_size -= quantity
            passport.exit_reason = reason
            passport.timeline.append({
                "timestamp": time.time(),
                "event": f"PARTIAL_CLOSE: {reason}",
                "details": f"Closed {quantity} @ {price}, remaining: {passport.position_size}"
            })
        else:
            passport.status = "CLOSED"
            passport.exit_reason = reason
            passport.exit_price = price
            
            if passport.side == "short":
                gross_pnl = (passport.position_entry_price - price) * quantity
            else:
                gross_pnl = (price - passport.position_entry_price) * quantity
            
            passport.gross_pnl = gross_pnl
            passport.closed_at = time.time()
            
            passport.timeline.append({
                "timestamp": time.time(),
                "event": f"CLOSED: {reason}",
                "details": f"Closed {quantity} @ {price}, PnL: {gross_pnl:.2f}"
            })
            
            await self.bus.publish(
                event_type="POSITION_CLOSED",
                source="position_monitor",
                payload={
                    "passport_id": passport.passport_id,
                    "symbol": passport.symbol,
                    "exit_reason": reason,
                    "exit_price": price,
                    "gross_pnl": gross_pnl
                },
                symbol=passport.symbol
            )
        
        self.repository.save(passport)
        return True

    async def _move_sl_to_break_even(self, passport):
        """Сдвинуть SL в точку безубытка после TP1."""
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return
        
        atr_value = self._get_atr_value()
        be_offset = 0.1 * atr_value
        
        if passport.side == "short":
            new_sl = entry_price + be_offset
        else:
            new_sl = entry_price - be_offset
        
        old_sl = passport.sl_price
        passport.sl_price = new_sl
        passport.sl_moved_to_be = True
        
        self._log("sl_moved_to_break_even", {
            "passport_id": passport.passport_id,
            "old_sl": old_sl,
            "new_sl": new_sl,
            "entry_price": entry_price
        })
        
        self.repository.save(passport)