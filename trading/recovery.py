import asyncio
import datetime
from typing import TYPE_CHECKING, Optional, Any

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .base_mixin import BaseMixin

class RecoveryMixin(BaseMixin):
    # Все аннотации типов теперь наследуются от BaseMixin

    async def perform_startup_recovery(self, symbol: Optional[str] = None):
        """Блокирующее восстановление состояния при старте платформы."""
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

            try:
                pos_data = await rest_client.get_position(symbol)
                
                if pos_data and isinstance(pos_data, dict):
                    pos_size = float(pos_data.get('size', 0))
                    
                    if abs(pos_size) > 0.001:
                        side = pos_data.get('side', 'none')
                        entry_price = float(pos_data.get('entry_price', 0))
                        
                        self._log("recovery_open_position_found", {
                            "symbol": symbol, "side": side, "size": pos_size, "entry": entry_price
                        })
                        
                        existing_passport = self.passport_manager.get_active_by_symbol(symbol)
                        
                        if not existing_passport:
                            self._log("recovery_creating_passport", {"symbol": symbol})
                            
                            # 1. Создаем паспорт
                            passport = self.passport_manager.create(
                                symbol=symbol,
                                signal_id=f"RECOVERY_{symbol}_{int(asyncio.get_event_loop().time()*1000)}",
                                strategy="Recovery",
                                side=side,
                                entry_price=entry_price,
                                confidence=1.0
                            )
                            
                            # 2. 🔥 ЖЕСТКО ЗАДАЕМ РАЗМЕР И ЦЕНУ
                            passport.position_size = abs(pos_size)
                            passport.position_entry_price = entry_price

                            # 3. ПЕРЕСЧИТЫВАЕМ УРОВНИ ВЫХОДА
                            atr_value = self.config.get('trading', {}).get('atr_value', 0.5)
                            levels = trader.calculate_exit_levels(side=side, entry_price=entry_price, atr_value=atr_value)
                            
                            passport.sl_price = levels.get('sl_price', 0)
                            passport.tp1_price = levels.get('tp1_price', 0)
                            passport.tp2_price = levels.get('tp2_price', 0)

                            # 4. 🔥 ПРЯМОЕ ИЗМЕНЕНИЕ СТАТУСА НА OPEN (в обход строгого State Manager для Recovery)
                            passport.status = "OPEN"
                            
                            # 5. Добавляем событие в timeline вручную
                            passport.timeline.append({
                                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                "event": "STATUS: RECOVERED_OPEN",
                                "details": f"Position recovered from exchange. Size: {abs(pos_size)}, Entry: {entry_price}"
                            })
                            
                            # 6. Сохраняем обновленный паспорт
                            self.repository.save(passport)
                            
                            self._log("recovery_position_successfully_restored", {
                                "passport_id": passport.passport_id,
                                "status": passport.status,
                                "size": passport.position_size,
                                "sl": passport.sl_price
                            })
                            
                            # 7. Публикуем событие, чтобы запустить мониторинг
                            await self.bus.publish(
                                event_type="POSITION_OPENED",
                                source="recovery",
                                payload={
                                    "passport_id": passport.passport_id,
                                    "symbol": symbol,
                                    "side": side,
                                    "entry_price": entry_price,
                                    "position_size": abs(pos_size)
                                },
                                symbol=symbol
                            )
                        else:
                            self._log("recovery_position_already_tracked", {
                                "passport_id": existing_passport.passport_id
                            })
            except Exception as e:
                self._log("recovery_position_check_failed", {"error": str(e)})

        except Exception as e:
            self._log("recovery_critical_failure", {"error": str(e)})
        
        self._log("startup_recovery_completed", {"symbol": symbol})