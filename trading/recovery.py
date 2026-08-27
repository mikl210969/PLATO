import asyncio
import datetime
from typing import TYPE_CHECKING, Optional, Any, List, Dict

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .base_mixin import BaseMixin
from core.types import PassportStatus


class RecoveryMixin(BaseMixin):

    async def perform_startup_recovery(self, symbol: Optional[str] = None):
        """
        🔥 ШАГ 10.4.3: Полная стартовая реконсиляция.
        1. Загрузка паспортов из репозитория
        2. Replay трейдов за последние 24 часа
        3. Сверка сумм: локально vs биржа
        4. Создание RECOVERY или закрытие призраков
        """
        self._log("startup_recovery_started", {"symbol": symbol})
        
        if not symbol:
            self._log("recovery_skipped_no_symbol")
            return

        try:
            trader = self.get_trader(symbol)
            if not trader or not hasattr(trader, 'rest'):
                self._log("recovery_skipped", {"reason": "trader_or_rest_not_found", "symbol": symbol})
                return

            rest_client = trader.rest

            # ШАГ 10.4.3.1: Загрузка паспортов из репозитория в память
            loaded_count = await self._load_passports_from_repository(symbol)
            
            # ШАГ 10.4.3.2: Replay трейдов за последние 24 часа
            replayed_closes = await self._replay_user_trades(symbol, rest_client)

            # ШАГ 10.4.3.3: Получение позиции с биржи
            pos_data = await rest_client.get_position(symbol)
            
            if not pos_data or not isinstance(pos_data, dict):
                self._log("recovery_position_fetch_failed", {"symbol": symbol})
                return

            exchange_size = abs(float(pos_data.get('size', 0) or 0))
            exchange_side = pos_data.get('side', 'none')
            exchange_entry = float(pos_data.get('entry_price', 0) or 0)

            self._log("recovery_exchange_position", {
                "symbol": symbol,
                "size": exchange_size,
                "side": exchange_side,
                "entry": exchange_entry,
            })

            # ШАГ 10.4.3.4: Сумма локальных позиций
            local_passports = self.passport_manager.get_all_active_by_symbol(symbol)
            local_sum = sum(abs(p.position_size) for p in local_passports)

            self._log("recovery_position_comparison", {
                "symbol": symbol,
                "exchange_size": exchange_size,
                "local_sum": local_sum,
                "diff": round(exchange_size - local_sum, 4),
                "passports_count": len(local_passports),
                "loaded_from_repo": loaded_count,
                "replayed_closes": replayed_closes,
            })

            # ШАГ 10.4.3.5: Матрица решений
            diff = exchange_size - local_sum

            if abs(diff) < 0.01:
                self._log("recovery_positions_match", {"symbol": symbol})
                return

            if diff > 0.01:
                # Сирота на бирже: exchange > local
                # Создаём RECOVERY паспорт на разницу
                await self._create_recovery_passport(
                    symbol=symbol,
                    side=exchange_side,
                    size=diff,
                    entry_price=exchange_entry,
                    trader=trader,
                )
            else:
                # Призрак локально: local > exchange
                # Закрываем призраков на разницу
                await self._close_phantom_passports(
                    symbol=symbol,
                    excess=abs(diff),
                )

        except Exception as e:
            self._log("recovery_critical_failure", {"error": str(e)})
            import traceback
            traceback.print_exc()

        self._log("startup_recovery_completed", {"symbol": symbol})

    async def _load_passports_from_repository(self, symbol: str) -> int:
        """
        🔥 ШАГ 10.4.3.1: Загрузка паспортов из репозитория в память.
        Загружает только активные паспорта для данного символа.
        """
        loaded = 0
        try:
            all_passports = self.repository.load_all()
            
            for passport in all_passports:
                # Загружаем только для нужного символа
                if passport.symbol != symbol:
                    continue
                
                # Пропускаем уже терминальные паспорта
                if passport.status in (
                    PassportStatus.CLOSED.value,
                    PassportStatus.CANCELED.value,
                    PassportStatus.FAILED.value,
                ):
                    continue
                
                # Проверяем, есть ли уже в памяти (мало ли)
                if self.passport_manager.get(passport.passport_id):
                    continue
                
                self.passport_manager.update(passport)
                loaded += 1
                self._log("recovery_loaded_passport", {
                    "passport_id": passport.passport_id,
                    "status": passport.status,
                    "size": passport.position_size,
                })
        
        except Exception as e:
            self._log("recovery_load_passports_failed", {"error": str(e)})
        
        self._log("recovery_loaded_summary", {
            "symbol": symbol,
            "loaded_count": loaded,
        })
        return loaded

    async def _replay_user_trades(self, symbol: str, rest_client) -> int:
        """
        🔥 ШАГ 10.4.3.2: Replay трейдов за последние 24 часа.
        Применяет закрытия (C1_, C2_, CS_) к паспортам.
        """
        import re
        
        try:
            # Запрашиваем трейды за последние 24 часа
            end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
            start_time = end_time - (24 * 60 * 60 * 1000)  # 24 часа назад
            
            trades = await rest_client.get_user_trades(
                symbol=symbol,
                start_time=start_time,
                end_time=end_time,
                limit=1000
            )
            
            if not trades:
                self._log("recovery_no_trades_found", {"symbol": symbol})
                return 0
            
            # Группируем трейды по orderId
            by_order: Dict[str, List[Dict]] = {}
            for trade in trades:
                order_id = str(trade.get('orderId', ''))
                by_order.setdefault(order_id, []).append(trade)
            
            closes_applied = 0
            
            # Анализируем каждую группу
            for order_id, order_trades in by_order.items():
                # Берём первый трейд для определения типа
                sample = order_trades[0]
                client_order_id = sample.get('orderId', '')
                
                # Ищем clientOrderId через get_order_status
                order_info = await rest_client.get_order_status(
                    symbol=symbol,
                    order_id=order_id
                )
                if not order_info:
                    continue
                
                client_order_id = order_info.get('clientOrderId', '')
                
                # Определяем: это закрытие?
                close_match = re.match(r'^(C1|C2|CS|CE)_(PASS_.+)$', client_order_id)
                if not close_match:
                    # Не закрытие — пропускаем (входные ордера уже в паспортах)
                    continue
                
                close_level = close_match.group(1)
                passport_id = close_match.group(2)
                
                # Суммируем qty по всем трейдам этого ордера
                total_qty = sum(float(t.get('qty', 0) or 0) for t in order_trades)
                total_quote = sum(float(t.get('quoteQty', 0) or 0) for t in order_trades)
                avg_price = total_quote / total_qty if total_qty > 0 else 0
                
                # Применяем к паспорту
                passport = self.passport_manager.get(passport_id)
                if not passport:
                    self._log("recovery_close_passport_not_found", {
                        "passport_id": passport_id,
                        "close_level": close_level,
                        "qty": total_qty,
                    })
                    continue
                
                if passport.status in (
                    PassportStatus.CLOSED.value,
                    PassportStatus.CANCELED.value,
                    PassportStatus.FAILED.value,
                ):
                    continue  # уже закрыт
                
                # Применяем закрытие
                old_size = passport.position_size
                passport.position_size = max(0.0, passport.position_size - total_qty)
                
                exit_reason_map = {
                    'C1': 'TP1_HIT',
                    'C2': 'TP2_HIT',
                    'CS': 'SL_HIT',
                    'CE': 'EXTERNAL_CLOSE',
                }
                exit_reason = exit_reason_map.get(close_level, 'MANUAL_CLOSE')
                
                if passport.position_size < 0.01:
                    passport.close(
                        exit_reason=exit_reason,
                        exit_price=avg_price,
                        gross_pnl=self._calculate_pnl(passport, avg_price, total_qty),
                        commission=0.0
                    )
                    self._log("recovery_closed_passport", {
                        "passport_id": passport_id,
                        "exit_reason": exit_reason,
                        "old_size": old_size,
                    })
                else:
                    passport.add_timeline_event(
                        'RECOVERY_PARTIAL_CLOSE',
                        f"Replayed close {close_level}: -{total_qty} @ {avg_price}"
                    )
                    self._log("recovery_partial_close", {
                        "passport_id": passport_id,
                        "close_level": close_level,
                        "qty": total_qty,
                        "old_size": old_size,
                        "new_size": passport.position_size,
                    })
                
                self.repository.save(passport)
                closes_applied += 1
            
            self._log("recovery_replay_summary", {
                "symbol": symbol,
                "trades_analyzed": len(trades),
                "closes_applied": closes_applied,
            })
            return closes_applied
        
        except Exception as e:
            self._log("recovery_replay_failed", {"error": str(e)})
            return 0

    def _calculate_pnl(self, passport, exit_price: float, quantity: float) -> float:
        """Рассчитать PnL для закрытия."""
        if not passport.position_entry_price or passport.position_entry_price == 0:
            return 0.0
        
        if passport.side == 'short':
            return (passport.position_entry_price - exit_price) * quantity
        else:
            return (exit_price - passport.position_entry_price) * quantity

    async def _create_recovery_passport(
        self,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        trader,
    ):
        """
        🔥 ШАГ 10.4.3.5A: Создать RECOVERY-паспорт для orphan-позиции на бирже.
        """
        self._log("recovery_creating_passport", {
            "symbol": symbol,
            "side": side,
            "size": size,
            "entry": entry_price,
        })
        
        passport = self.passport_manager.create(
            symbol=symbol,
            signal_id=f"RECOVERY_{symbol}_{int(asyncio.get_event_loop().time()*1000)}",
            strategy="Recovery",
            side=side,
            entry_price=entry_price,
            confidence=1.0
        )
        
        passport.position_size = abs(size)
        passport.position_entry_price = entry_price
        passport.status = PassportStatus.OPEN.value
        
        # Пересчитываем уровни
        atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
        levels = trader.calculate_exit_levels(side=side, entry_price=entry_price, atr_value=atr_value)
        
        passport.sl_price = levels.get('sl_price', 0)
        passport.tp1_price = levels.get('tp1_price', 0)
        passport.tp2_price = levels.get('tp2_price', 0)
        
        passport.timeline.append({
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event": "STATUS: RECOVERED_OPEN",
            "details": f"Position recovered from exchange. Size: {size}, Entry: {entry_price}"
        })
        
        self.repository.save(passport)
        
        self._log("recovery_passport_created", {
            "passport_id": passport.passport_id,
            "size": passport.position_size,
            "sl": passport.sl_price,
        })
        
        # Публикуем событие для RiskManager
        await self.bus.publish(
            event_type="POSITION_OPENED",
            source="recovery",
            payload={
                "passport_id": passport.passport_id,
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "position_size": abs(size)
            },
            symbol=symbol
        )

    async def _close_phantom_passports(self, symbol: str, excess: float):
        """
        🔥 ШАГ 10.4.3.5B: Закрыть призраков локально (local > exchange).
        Берём самые старые активные паспорта и уменьшаем их размер.
        """
        self._log("recovery_closing_phantoms", {
            "symbol": symbol,
            "excess": excess,
        })
        
        # Сортируем паспорта по created_at (самые старые первые)
        local_passports = self.passport_manager.get_all_active_by_symbol(symbol)
        local_passports.sort(key=lambda p: p.created_at)
        
        remaining_excess = excess
        
        for passport in local_passports:
            if remaining_excess < 0.01:
                break
            
            close_qty = min(passport.position_size, remaining_excess)
            passport.position_size -= close_qty
            remaining_excess -= close_qty
            
            if passport.position_size < 0.01:
                passport.close(
                    exit_reason="PHANTOM_CLEANUP",
                    exit_price=0.0,
                    gross_pnl=0.0,
                    commission=0.0,
                )
                self._log("recovery_phantom_closed", {
                    "passport_id": passport.passport_id,
                    "closed_qty": close_qty,
                })
            else:
                passport.add_timeline_event(
                    'PHANTOM_CLEANUP',
                    f"Phantom cleanup: -{close_qty}"
                )
                self._log("recovery_phantom_partial", {
                    "passport_id": passport.passport_id,
                    "closed_qty": close_qty,
                    "remaining": passport.position_size,
                })
            
            self.repository.save(passport)