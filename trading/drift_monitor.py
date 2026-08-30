"""
DriftMonitor — периодический страж дрейфа состояния.
Каждые 30 секунд сверяет локальное состояние с биржей через REST.
При обнаружении расхождения публикует DRIFT_DETECTED и устанавливает флаг symbol_drift.
"""
import asyncio
from typing import Dict, Optional
from core.logger import get_logger


class DriftMonitor:
    """
    Периодически проверяет согласованность локального состояния с биржей.
    """

    def __init__(self, rest_client, passport_manager, event_bus, poll_interval: float = 30.0):
        self.rest = rest_client
        self.passport_manager = passport_manager
        self.bus = event_bus
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
        # Флаги дрейфа по символам (True = есть дрейф, гейт должен блокировать новые сделки)
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
        Сравнивает:
        1. Позиция на бирже vs локальный активный паспорт
        2. 🔥 НОВОЕ: Принудительное восстановление при потерянном WS-событии FILLED
        3. Открытые ордера на бирже vs локальные ордера в нетерминальных статусах
        """
        try:
            # Получаем данные с биржи
            position_data = await self.rest.get_position(symbol)
            open_orders = await self.rest.get_open_orders(symbol)
            
            exchange_position_size = 0.0
            if position_data and isinstance(position_data, dict):
                exchange_position_size = abs(float(position_data.get('size', 0) or 0))
            
            # Получаем локальный активный паспорт
            local_passport = self.passport_manager.get_active_by_symbol(symbol)
            local_position_size = 0.0
            if local_passport:
                local_position_size = abs(local_passport.position_size or 0.0)
            
            # Проверка 1: Расхождение позиции (позиция есть, паспорта нет)
            if exchange_position_size > 0.01 and not local_passport:
                self.logger.warning(
                    f"⚠️ [DRIFT_DETECTED] Position on exchange ({exchange_position_size}) "
                    f"but no local passport for {symbol}"
                )
                await self._publish_drift(symbol, "position_without_passport", {
                    "exchange_size": exchange_position_size,
                    "local_size": local_position_size
                })
                return
            
            # Проверка 1.1: Расхождение позиции (паспорт есть, позиции нет)
            if local_passport and exchange_position_size < 0.01 and local_position_size > 0.01:
                self.logger.warning(
                    f"⚠️ [DRIFT_DETECTED] Local passport ({local_position_size}) "
                    f"but no position on exchange for {symbol}"
                )
                await self._publish_drift(symbol, "passport_without_position", {
                    "exchange_size": exchange_position_size,
                    "local_size": local_position_size
                })
                return
            
            # ========================================================================
            # 🔥 ПРОВЕРКА 2: Принудительное восстановление (Force Sync)
            # Сценарий: Ордер исполнился, но событие WebSocket было потеряно (обрыв связи).
            # Признак: Паспорт существует, статус ORDER_SENT, НО на бирже уже есть позиция > 0.
            # ========================================================================
            if local_passport and local_passport.status == 'ORDER_SENT' and exchange_position_size > 0.01:
                self.logger.warning(
                    f"⚠️ [DRIFT_RECOVERY] Позиция открыта на бирже ({exchange_position_size}), "
                    f"но паспорт {local_passport.passport_id} застрял в ORDER_SENT. Принудительная синхронизация!"
                )
                
                # 1. Обновляем статус и размер позиции локально
                local_passport.status = "OPEN"
                local_passport.position_size = exchange_position_size
                # Используем entry_price как цену входа, если точная цена исполнения пока неизвестна
                local_passport.position_entry_price = local_passport.entry_price 
                
                # Сохраняем изменения
                self.passport_manager.update(local_passport)
                
                # 2. Публикуем событие, чтобы RiskManager НЕМЕДЛЕННО выставил защиту (SL/TP)
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
                
                self.logger.info(f"✅ [DRIFT_RECOVERY] Паспорт {local_passport.passport_id} успешно синхронизирован и защищен!")
                return  # Выходим, так как проблема решена, дальнейшие проверки не нужны

            # Проверка 3: Локальные ордера в нетерминальных статусах должны быть на бирже
            if local_passport and local_passport.status in ('ORDER_SENT', 'ORDER_ACK', 'LIMIT_ON_BOOK'):
                local_order_id = None
                if local_passport.orders:
                    last_order = local_passport.orders[-1]
                    local_order_id = str(last_order.get('order_id', ''))
                
                if local_order_id:
                    # Ищем этот ордер в списке открытых
                    exchange_order_ids = {str(o.get('orderId', '')) for o in open_orders}
                    
                    if local_order_id not in exchange_order_ids:
                        self.logger.warning(
                            f"⚠️ [DRIFT_DETECTED] Local order {local_order_id} "
                            f"not found in exchange open orders for {symbol}"
                        )
                        await self._publish_drift(symbol, "order_not_on_exchange", {
                            "local_order_id": local_order_id,
                            "exchange_open_orders": list(exchange_order_ids)[:5]  # Первые 5 для лога
                        })
                        return
            
            # Всё ок — сбрасываем флаг дрейфа
            if self.symbol_drift.get(symbol, False):
                self.logger.info(f"✅ [DRIFT_MONITOR] Drift resolved for {symbol}")
                self.symbol_drift[symbol] = False
                
        except Exception as e:
            self.logger.error(f"Error checking drift for {symbol}: {e}")

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