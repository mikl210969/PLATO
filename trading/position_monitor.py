"""
PositionMonitor — мониторинг и управление открытыми позициями.
- Следит за ценой в реальном времени
- Закрывает позицию при достижении SL, TP1, TP2
- Сдвигает SL в Break-Even при прохождении 1R
- Проверяет Basis Stop (>1.5%)
"""
import asyncio
import time
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

class PositionMonitor:
    # Аннотации типов для Pylance
    _log: Any
    passport_manager: Any
    repository: Any
    state_manager: Any
    bus: Any
    get_trader: Any
    config: Any

    def __init__(self):
        self._monitor_task = None
        self._position_running = True  # ✅ Уникальное имя
        self._last_price_check: Dict[str, float] = {}

    async def start_position_monitor(self):
        """Запуск мониторинга позиций."""
        self._monitor_task = asyncio.create_task(self._position_monitor_loop())

    async def stop_position_monitor(self):
        """Остановка мониторинга позиций."""
        self._position_running = False  # ✅ Останавливаем только PositionMonitor
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def _position_monitor_loop(self):
        """Основной цикл мониторинга позиций."""
        while getattr(self, '_position_running', True):  # ✅ Используем новое имя
            await asyncio.sleep(1)  # Проверяем каждую секунду
            
            # Получаем все открытые позиции
            for passport in self.passport_manager.get_active():
                if passport.status != "OPEN":
                    continue
                
                # Получаем текущую цену (из WS или REST)
                current_price = await self._get_current_price(passport.symbol)
                if not current_price:
                    continue
                
                # Проверяем Basis Stop
                if await self._check_basis_stop(passport, current_price):
                    continue  # Если Basis Stop сработал, остальные проверки пропускаем
                
                # Проверяем TP1 (закрытие 50% + сдвиг SL в BE)
                if await self._check_tp1(passport, current_price):
                    continue
                
                # Проверяем TP2 (закрытие оставшихся 50%)
                if await self._check_tp2(passport, current_price):
                    continue
                
                # Проверяем SL (полное закрытие)
                if await self._check_sl(passport, current_price):
                    continue
                
                # Проверяем сдвиг в Break-Even (если прошли 1R)
                await self._check_break_even(passport, current_price)

    async def _get_current_price(self, symbol: str) -> Optional[float]:
        """Получить текущую цену (в будущем — из WS, сейчас — из REST)."""
        try:
            trader = self.get_trader(symbol)
            if not trader:
                return None
            
            orderbook = await trader.rest.get_orderbook(symbol, limit=1)
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
        """
        Проверка Basis Stop (>1.5% расхождения между фьючерсом и спотом).
        В упрощённой версии — просто проверяем сильное движение против позиции.
        """
        # Получаем ATR для расчёта порога
        atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
        basis_threshold = 1.5  # 1.5% — порог Basis Stop
        
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return False
        
        # Рассчитываем процентное изменение
        price_change_pct = abs(current_price - entry_price) / entry_price * 100
        
        if price_change_pct > basis_threshold:
            self._log("basis_stop_triggered", {
                "passport_id": passport.passport_id,
                "price_change_pct": round(price_change_pct, 2),
                "threshold": basis_threshold
            })
            
            # Закрываем позицию полностью по рынку
            await self._close_position(passport, current_price, "BASIS_STOP")
            return True
        
        return False

    async def _check_tp1(self, passport, current_price: float) -> bool:
        """
        Проверка TP1: закрытие 50% позиции + сдвиг SL в Break-Even.
        """
        tp1_price = passport.tp1_price
        if not tp1_price or tp1_price <= 0:
            return False
        
        # Проверяем, достигнута ли цена TP1
        if passport.side == "short":
            if current_price > tp1_price:  # Для шорта TP1 ниже цены
                return False
        else:
            if current_price < tp1_price:  # Для лонга TP1 выше цены
                return False
        
        # Проверяем, не закрыли ли мы уже TP1
        if getattr(passport, 'tp1_closed', False):
            return False
        
        self._log("tp1_triggered", {
            "passport_id": passport.passport_id,
            "tp1_price": tp1_price,
            "current_price": current_price
        })
        
        # Закрываем 50% позиции
        close_quantity = passport.position_size * 0.5
        await self._close_partial_position(passport, current_price, close_quantity, "TP1")
        
        # Сдвигаем SL в Break-Even
        await self._move_sl_to_break_even(passport)
        
        # Помечаем, что TP1 закрыт
        passport.tp1_closed = True
        
        return True

    async def _check_tp2(self, passport, current_price: float) -> bool:
        """
        Проверка TP2: закрытие оставшихся 50% позиции.
        """
        tp2_price = passport.tp2_price
        if not tp2_price or tp2_price <= 0:
            return False
        
        # Проверяем, достигнута ли цена TP2
        if passport.side == "short":
            if current_price > tp2_price:
                return False
        else:
            if current_price < tp2_price:
                return False
        
        # Проверяем, не закрыли ли мы уже TP2
        if getattr(passport, 'tp2_closed', False):
            return False
        
        self._log("tp2_triggered", {
            "passport_id": passport.passport_id,
            "tp2_price": tp2_price,
            "current_price": current_price
        })
        
        # Закрываем оставшуюся часть
        await self._close_position(passport, current_price, "TP2")
        passport.tp2_closed = True
        
        return True

    async def _check_sl(self, passport, current_price: float) -> bool:
        """
        Проверка SL: полное закрытие позиции.
        """
        sl_price = passport.sl_price
        if not sl_price or sl_price <= 0:
            return False
        
        # Проверяем, достигнута ли цена SL
        if passport.side == "short":
            if current_price < sl_price:  # Для шорта SL выше цены
                return False
        else:
            if current_price > sl_price:  # Для лонга SL ниже цены
                return False
        
        self._log("sl_triggered", {
            "passport_id": passport.passport_id,
            "sl_price": sl_price,
            "current_price": current_price
        })
        
        # Закрываем позицию полностью
        await self._close_position(passport, current_price, "SL_HIT")
        return True

    async def _check_break_even(self, passport, current_price: float):
        """
        Сдвиг SL в Break-Even при прохождении 1R (или 0.25 ATR).
        """
        # Проверяем, не сдвинут ли уже SL в BE
        if getattr(passport, 'sl_moved_to_be', False):
            return
        
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return
        
        # Получаем ATR
        atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
        be_offset = 0.25 * atr_value  # Смещение BE на 0.25 ATR
        
        # Рассчитываем, сколько прошла цена в нашу пользу
        if passport.side == "short":
            profit_distance = entry_price - current_price
            new_sl_price = current_price + be_offset
        else:
            profit_distance = current_price - entry_price
            new_sl_price = current_price - be_offset
        
        # Проверяем, прошли ли мы достаточно для сдвига в BE (например, 1R или 0.5 ATR)
        min_profit_for_be = 0.5 * atr_value
        
        if profit_distance >= min_profit_for_be:
            # Проверяем, что новый SL лучше текущего
            if passport.side == "short":
                if new_sl_price < passport.sl_price:  # Для шорта SL должен уменьшаться
                    passport.sl_price = new_sl_price
                    passport.sl_moved_to_be = True
                    self._log("break_even_applied", {
                        "passport_id": passport.passport_id,
                        "old_sl": passport.sl_price,
                        "new_sl": new_sl_price
                    })
            else:
                if new_sl_price > passport.sl_price:  # Для лонга SL должен увеличиваться
                    passport.sl_price = new_sl_price
                    passport.sl_moved_to_be = True
                    self._log("break_even_applied", {
                        "passport_id": passport.passport_id,
                        "old_sl": passport.sl_price,
                        "new_sl": new_sl_price
                    })
            
            # Сохраняем изменения
            self.repository.save(passport)

    async def _close_partial_position(self, passport, price: float, quantity: float, reason: str):
        """Закрыть часть позиции по рынку."""
        trader = self.get_trader(passport.symbol)
        if not trader:
            self._log("trader_not_found_for_close", {"passport_id": passport.passport_id})
            return
        
        self._log("closing_partial_position", {
            "passport_id": passport.passport_id,
            "quantity": quantity,
            "reason": reason,
            "price": price
        })
        
        # Отправляем рыночный ордер на закрытие части позиции
        result = await trader.execute_order(
            symbol=passport.symbol,
            side="buy" if passport.side == "short" else "sell",
            quantity=quantity,
            order_type="market",
            reduce_only=True,
            client_order_id=f"{reason}_{passport.passport_id}",
            passport_id=passport.passport_id
        )
        
        if result.get('success'):
            # Обновляем размер позиции
            passport.position_size -= quantity
            passport.exit_reason = reason
            passport.timeline.append({
                "timestamp": time.time(),
                "event": f"PARTIAL_CLOSE: {reason}",
                "details": f"Closed {quantity} @ {price}"
            })
            self.repository.save(passport)
        else:
            self._log("partial_close_failed", {
                "passport_id": passport.passport_id,
                "error": result.get('error')
            })

    async def _close_position(self, passport, price: float, reason: str):
        """Закрыть всю позицию по рынку."""
        trader = self.get_trader(passport.symbol)
        if not trader:
            self._log("trader_not_found_for_close", {"passport_id": passport.passport_id})
            return
        
        self._log("closing_position", {
            "passport_id": passport.passport_id,
            "quantity": passport.position_size,
            "reason": reason,
            "price": price
        })
        
        # Отправляем рыночный ордер на полное закрытие
        result = await trader.execute_order(
            symbol=passport.symbol,
            side="buy" if passport.side == "short" else "sell",
            quantity=passport.position_size,
            order_type="market",
            reduce_only=True,
            client_order_id=f"{reason}_{passport.passport_id}",
            passport_id=passport.passport_id
        )
        
        if result.get('success'):
            # Обновляем статус паспорта
            passport.status = "CLOSED"
            passport.exit_reason = reason
            passport.exit_price = price
            
            # Рассчитываем PnL
            if passport.side == "short":
                gross_pnl = (passport.position_entry_price - price) * passport.position_size
            else:
                gross_pnl = (price - passport.position_entry_price) * passport.position_size
            
            passport.gross_pnl = gross_pnl
            passport.closed_at = time.time()
            
            passport.timeline.append({
                "timestamp": time.time(),
                "event": f"CLOSED: {reason}",
                "details": f"Closed {passport.position_size} @ {price}, PnL: {gross_pnl}"
            })
            
            self.repository.save(passport)
            
            # Публикуем событие о закрытии
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
        else:
            self._log("full_close_failed", {
                "passport_id": passport.passport_id,
                "error": result.get('error')
            })

    async def _move_sl_to_break_even(self, passport):
        """Сдвинуть SL в точку безубытка."""
        entry_price = passport.position_entry_price or passport.entry_price
        if not entry_price:
            return
        
        # Получаем ATR для небольшого смещения (чтобы избежать случайного срабатывания)
        atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
        be_offset = 0.1 * atr_value  # Небольшое смещение
        
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