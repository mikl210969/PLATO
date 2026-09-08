"""
DriftMonitor — периодический страж дрейфа состояния.
Каждые 30 секунд сверяет локальное состояние с биржей через REST.
При обнаружении расхождения публикует DRIFT_DETECTED и устанавливает флаг symbol_drift.
"""
import asyncio
from typing import Dict, Optional
from core.logger import get_logger
from core.types import PassportStatus


class DriftMonitor:
    """
    Периодически проверяет согласованность локального состояния с биржей.
    """

    def __init__(self, rest_client, passport_manager, passport_repository, event_bus, risk_manager, poll_interval: float = 30.0):
        self.rest = rest_client
        self.passport_manager = passport_manager
        self.repository = passport_repository
        self.bus = event_bus
        self.risk_manager = risk_manager
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
        self.symbol_drift: Dict[str, bool] = {}
        
        self.logger = get_logger(__name__)

    async def start(self, symbols: list):
        """Запустить периодическую проверку для списка символов."""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(symbols))
        self.logger.info(f"🔍 [DRIFT_MONITOR] Started for symbols: {symbols}")

    async def stop(self):
        """Остановить мониторинг."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.info("🛑 [DRIFT_MONITOR] Stopped")

    async def _monitor_loop(self, symbols: list):
        """Основной цикл проверки дрейфа."""
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                
                for symbol in symbols:
                    await self._check_drift(symbol)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"DriftMonitor error: {e}")
                await asyncio.sleep(5)

    async def _check_drift(self, symbol: str):
        """
        Проверить дрейф для одного символа.
        🔥 ИСПРАВЛЕНО: get_open_orders вызывается ТОЛЬКО после успешного get_position.
        """
        try:
            # 1. Получаем данные о позиции с биржи
            position_data = await self.rest.get_position(symbol)
            
            # 🔥 КРИТИЧЕСКАЯ ЗАЩИТА: Если position_data == None (бан или ошибка),
            # НЕ делаем второй запрос get_open_orders, чтобы не продлевать бан!
            if position_data is None:
                self.logger.debug(f"️ [DRIFT] {symbol}: REST недоступен, проверка пропущена")
                return
            
            # 2. 🔥 ОПТИМИЗАЦИЯ: открытые ордера запрашиваем ТОЛЬКО если есть активные паспорта
            open_orders = []
            if self.passport_manager.get_active_by_symbol(symbol):
                open_orders = await self.rest.get_open_orders(symbol)

            exchange_position_size = 0.0
            if isinstance(position_data, dict):
                exchange_position_size = abs(float(position_data.get('size', 0) or 0))
            
            local_passport = self.passport_manager.get_active_by_symbol(symbol)
            local_position_size = 0.0
            if local_passport:
                local_position_size = abs(local_passport.position_size or 0.0)
            
            # Проверка 1: Расхождение позиции (позиция есть, паспорта нет)
            if exchange_position_size > 0.01 and not local_passport:
                self.logger.warning(
                    f"️ [DRIFT_DETECTED] Position on exchange ({exchange_position_size}) "
                    f"but no local passport for {symbol}"
                )
                await self._publish_drift(symbol, "position_without_passport", {
                    "exchange_size": exchange_position_size,
                    "local_size": local_position_size
                })
                return
            
            # Проверка 1.1: Паспорт есть, позиции нет (внешнее закрытие)
            if local_passport and exchange_position_size < 0.01 and local_position_size > 0.01:
                self.logger.warning(
                    f"️ [DRIFT_DETECTED] Local passport ({local_position_size}) "
                    f"but no position on exchange for {symbol}. "
                    f"Recovering EXTERNAL_CLOSE data..."
                )
                
                await self._recover_external_close(local_passport, symbol)
                return
            
            # Проверка 2: Принудительное восстановление (Force Sync)
            if local_passport and local_passport.status == 'ORDER_SENT' and exchange_position_size > 0.01:
                self.logger.warning(
                    f"⚠️ [DRIFT_RECOVERY] Позиция открыта на бирже ({exchange_position_size}), "
                    f"но паспорт {local_passport.passport_id} застрял в ORDER_SENT. Принудительная синхронизация!"
                )
                
                local_passport.status = "OPEN"
                local_passport.position_size = exchange_position_size
                local_passport.position_entry_price = local_passport.entry_price 
                
                self.passport_manager.update(local_passport)
                self.repository.save(local_passport)
                
                if hasattr(self, 'risk_manager') and self.risk_manager is not None:
                    try:
                        await self.risk_manager.ensure_guard_registered(local_passport)
                        self.logger.info(f"✅ [DRIFT_RECOVERY] Guard успешно зарегистрирован для {local_passport.passport_id} через REST-фоллбэк. Позиция под защитой!")
                    except Exception as e:
                        self.logger.error(f"❌ [DRIFT_RECOVERY] Ошибка при принудительной регистрации guard: {e}")
                else:
                    self.logger.warning("⚠️ [DRIFT_RECOVERY] RiskManager не передан в DriftMonitor. Используем публикацию события.")
                    await self.bus.publish(
                        event_type="POSITION_OPENED",
                        source="drift_monitor_recovery",
                        payload={
                            "passport_id": local_passport.passport_id,
                            "symbol": symbol,
                            "side": local_passport.side,
                            "entry_price": local_passport.position_entry_price,
                            "position_size": exchange_position_size
                        },
                        symbol=symbol
                    )
                
                return

            # Проверка 3: Локальные ордера в нетерминальных статусах должны быть на бирже
            if local_passport and local_passport.status in ('ORDER_SENT', 'ORDER_ACK', 'LIMIT_ON_BOOK'):
                local_order_id = None
                if local_passport.orders:
                    last_order = local_passport.orders[-1]
                    local_order_id = str(last_order.get('order_id', ''))
                
                if local_order_id:
                    exchange_order_ids = {str(o.get('orderId', '')) for o in open_orders}
                    
                    if local_order_id not in exchange_order_ids:
                        self.logger.warning(
                            f"⚠️ [DRIFT_DETECTED] Local order {local_order_id} "
                            f"not found in exchange open orders for {symbol}"
                        )
                        await self._publish_drift(symbol, "order_not_on_exchange", {
                            "local_order_id": local_order_id,
                            "exchange_open_orders": list(exchange_order_ids)[:5]
                        })
                        return
            
            # Всё ок — сбрасываем флаг дрейфа
            if self.symbol_drift.get(symbol, False):
                self.logger.info(f"✅ [DRIFT_MONITOR] Drift resolved for {symbol}")
                self.symbol_drift[symbol] = False
                
        except Exception as e:
            self.logger.error(f"Error checking drift for {symbol}: {e}")

    async def _recover_external_close(self, passport, symbol: str):
        """Восстанавливает данные о закрытии позиции при EXTERNAL_CLOSE."""
        try:
            import time
            
            # 🔥 Circuit Breaker: если бан активен, не делаем запрос
            if hasattr(self.rest, '_ban_active') and self.rest._ban_active():
                self.logger.warning(f"⏸️ [DRIFT_RECOVERY] {symbol}: REST забанен, восстановление отложено")
                return
            
            end_time = int(time.time() * 1000)
            start_time = end_time - (24 * 60 * 60 * 1000)
            
            self.logger.info(f"🔍 [DRIFT_RECOVERY] Запрашиваем историю сделок для {symbol}...")
            trades = await self.rest.get_user_trades(symbol, start_time, end_time, 1000)
            
            if not trades:
                self.logger.warning(f"️ [DRIFT_RECOVERY] История сделок пуста для {symbol} (возможная ошибка сети или таймаут).")
                self.logger.warning(f"   Паспорт {passport.passport_id} ОСТАЕТСЯ в статусе {passport.status}.")
                return
            
            passport_order_ids = {str(o.get('order_id', '')) for o in passport.orders}
            
            passport_trades = [
                t for t in trades 
                if str(t.get('orderId', '')) in passport_order_ids
            ]
            
            if not passport_trades:
                self.logger.warning(
                    f"⚠️ [DRIFT_RECOVERY] Не найдено сделок для ордера {passport_order_ids}. "
                    f"Пытаемся найти по client_order_id..."
                )
                
                passport_client_ids = {str(o.get('client_order_id', '')) for o in passport.orders}
                passport_trades = [
                    t for t in trades 
                    if str(t.get('clientOrderId', '')) in passport_client_ids
                ]
            
            if not passport_trades:
                self.logger.warning(f"⚠️ [DRIFT_RECOVERY] Не удалось найти сделки для {passport.passport_id} в истории биржи.")
                self.logger.warning(f"   Паспорт ОСТАЕТСЯ в статусе {passport.status}.")
                return
            
            closing_side = "BUY" if passport.side == "short" else "SELL"
            
            closing_trades = [
                t for t in passport_trades 
                if t.get('side') == closing_side
            ]
            
            if not closing_trades:
                self.logger.warning(
                    f"️ [DRIFT_RECOVERY] Не найдено закрывающих сделок ({closing_side}) "
                    f"для {passport.passport_id}. Используем все сделки."
                )
                closing_trades = passport_trades
            
            total_qty = sum(float(t.get('qty', 0)) for t in closing_trades)
            total_value = sum(float(t.get('quoteQty', 0)) for t in closing_trades)
            
            if total_qty > 0:
                exit_price = total_value / total_qty
            else:
                exit_price = float(closing_trades[-1].get('price', 0))
            
            entry_price = passport.entry_price
            position_qty = abs(passport.position_size or 0)
            
            if passport.side == "long":
                gross_pnl = (exit_price - entry_price) * position_qty
            else:
                gross_pnl = (entry_price - exit_price) * position_qty
            
            total_commission = sum(
                float(t.get('commission', 0)) for t in closing_trades
            )
            
            passport.status = PassportStatus.CLOSED.value
            passport.exit_reason = "EXTERNAL_CLOSE"
            
            if exit_price == 0.0:
                self.logger.warning(f"⚠️ [DRIFT_RECOVERY] exit_price=0.0 для {passport.passport_id}. Используем fallback.")
                if passport.sl_price > 0:
                    exit_price = passport.sl_price
                elif passport.tp1_price > 0:
                    exit_price = passport.tp1_price
                elif passport.tp2_price > 0:
                    exit_price = passport.tp2_price
                else:
                    exit_price = passport.entry_price
                    
                gross_pnl = 0.0
                self.logger.warning(f"   PnL сброшен в 0.0. Fallback exit_price: {exit_price}")

            passport.exit_price = exit_price
            passport.gross_pnl = gross_pnl
            passport.commission = total_commission
            passport.net_pnl = gross_pnl - total_commission
            passport.position_size = 0.0
            passport.closed_at = passport.updated_at
            
            passport.add_timeline_event(
                "EXTERNAL_CLOSE",
                f"Recovered: exit_price={exit_price:.4f}, pnl={gross_pnl:+.2f}, commission={total_commission:.4f}"
            )
            
            self.passport_manager.update(passport)
            self.repository.save(passport)
            
            self.logger.info(
                f"✅ [DRIFT_RECOVERY] Паспорт {passport.passport_id} восстановлен и СОХРАНЕН на диск: "
                f"exit_price={exit_price:.4f}, PnL={gross_pnl:+.2f} USDT, commission={total_commission:.4f}"
            )
            
        except Exception as e:
            self.logger.error(f"❌ [DRIFT_RECOVERY] Ошибка восстановления {passport.passport_id}: {e}")
            self.logger.warning(f"   Паспорт {passport.passport_id} ОСТАЕТСЯ в статусе {passport.status} до успешной синхронизации.")
            return

    async def _publish_drift(self, symbol: str, drift_type: str, details: dict):
        """Опубликовать событие дрейфа и установить флаг."""
        self.symbol_drift[symbol] = True
        
        await self.bus.publish(
            event_type="DRIFT_DETECTED",
            source="drift_monitor",
            payload={
                "symbol": symbol,
                "drift_type": drift_type,
                **details
            },
            symbol=symbol
        )

    def is_drift_active(self, symbol: str) -> bool:
        """Проверить, есть ли активный дрейф для символа."""
        return self.symbol_drift.get(symbol, False)