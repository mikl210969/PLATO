"""Breakout Detector — детектирует возможность пробоя уровня через поедание стены."""
import logging
import time
from collections import deque
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)


class BreakoutDetector:
    def __init__(
        self,
        event_bus: Any,
        symbol: str,
        consumption_threshold_pct: float = 60.0,      # 60% съеденности
        min_consumption_rate_pct: float = 5.0,        # 5% в секунду
        refill_threshold_pct: float = 50.0,           # 50% подлив = отмена
        check_interval_sec: float = 0.5,              # Проверка каждые 500мс
        cooldown_sec: float = 1800.0                  # 30 минут cooldown на уровень
    ):
        self.event_bus = event_bus
        self.symbol = symbol
        
        self.consumption_threshold_pct = consumption_threshold_pct
        self.min_consumption_rate_pct = min_consumption_rate_pct
        self.refill_threshold_pct = refill_threshold_pct
        self.check_interval_sec = check_interval_sec
        self.cooldown_sec = cooldown_sec
        
        # Отслеживаемые стены: {price: {initial_volume, first_seen, last_check_time, last_volume}}
        self._tracked_walls: Dict[float, Dict[str, Any]] = {}
        
        # Cooldown: {price: last_signal_time}
        self._cooldown: Dict[float, float] = {}
        
        # Подписка на события
        self.event_bus.subscribe("WALL_CONFIRMED", self._on_wall_confirmed)
        self.event_bus.subscribe("SPOT_ORDERBOOK_UPDATE", self._on_orderbook_update)
        
        logger.info(f"✅ BreakoutDetector initialized for {symbol}")
        print("🚨 [ПРЯМОЙ PRINT] BreakoutDetector класс создан и подписан на события!")        

    async def _on_wall_confirmed(self, event: Any):
        """Запоминаем новую подтвержденную стену для отслеживания."""
        try:
            payload = getattr(event, "payload", {})
            price = float(payload.get("price", 0.0))
            volume = float(payload.get("volume", 0.0))
            side = payload.get("side", "BID")
            
            if price <= 0 or volume <= 0:
                return
            
            # Запоминаем стену
            self._tracked_walls[price] = {
                "side": side,
                "initial_volume": volume,
                "first_seen": time.time(),
                "last_check_time": time.time(),
                "last_volume": volume,
                "consumption_start_time": None,
                "consumption_start_volume": None
            }
            
            logger.debug(f"🎯 [BreakoutDetector] Отслеживаем стену @ {price:.2f} | Vol: {volume:.0f}")
            
        except Exception as e:
            logger.error(f"Error in BreakoutDetector wall tracking: {e}")

    async def _on_orderbook_update(self, event: Any):
        """Проверяем состояние отслеживаемых стен при обновлении стакана."""
        try:
            payload = getattr(event, "payload", {})
            orderbook = payload.get("orderbook", {})
            
            if not orderbook:
                return
            
            current_time = time.time()
            
            # Проверяем каждую отслеживаемую стену
            for wall_price, wall_data in list(self._tracked_walls.items()):
                # Проверка cooldown
                if wall_price in self._cooldown:
                    if current_time - self._cooldown[wall_price] < self.cooldown_sec:
                        continue
                
                # Получаем текущий объем на этом уровне
                current_volume = self._get_volume_at_price(orderbook, wall_price, wall_data["side"])
                
                # Проверяем, существует ли еще стена
                if current_volume <= 0:
                    # Стена полностью исчезла — это может быть пробой или спуфинг
                    # Для простоты удаляем из отслеживания
                    del self._tracked_walls[wall_price]
                    continue
                
                # Считаем процент съеденности
                initial_volume = wall_data["initial_volume"]
                consumption_pct = ((initial_volume - current_volume) / initial_volume) * 100.0
                
                # Проверяем, достигли ли порога съеденности
                if consumption_pct >= self.consumption_threshold_pct:
                    # Считаем скорость поедания
                    if wall_data["consumption_start_time"] is None:
                        # Начинаем отсчет скорости
                        wall_data["consumption_start_time"] = current_time
                        wall_data["consumption_start_volume"] = current_volume
                        continue
                    
                    time_elapsed = current_time - wall_data["consumption_start_time"]
                    if time_elapsed > 0:
                        volume_consumed = wall_data["consumption_start_volume"] - current_volume
                        consumption_rate_pct = (volume_consumed / initial_volume * 100.0) / time_elapsed
                        
                        # Проверяем минимальную скорость
                        if consumption_rate_pct >= self.min_consumption_rate_pct:
                            # Проверяем подлив ликвидности
                            last_volume = wall_data["last_volume"]
                            refill_pct = ((current_volume - last_volume) / last_volume * 100.0) if last_volume > 0 else 0.0
                            
                            if refill_pct > self.refill_threshold_pct:
                                # Подлив ликвидности — отменяем отслеживание
                                logger.info(f"🚫 [BreakoutDetector] Подлив ликвидности @ {wall_price:.2f} | Refill: {refill_pct:.1f}%")
                                del self._tracked_walls[wall_price]
                                continue
                            
                            # ✅ УСЛОВИЯ ВЫПОЛНЕНЫ — публикуем событие
                            self._cooldown[wall_price] = current_time
                            
                            logger.info(
                                f"🚀 [BreakoutDetector] BREAKOUT_OPPORTUNITY @ {wall_price:.2f} | "
                                f"Consumption: {consumption_pct:.1f}% | Rate: {consumption_rate_pct:.1f}%/s"
                            )
                            
                            await self.event_bus.publish(
                                event_type="BREAKOUT_OPPORTUNITY",
                                source="breakout_detector",
                                payload={
                                    "wall_price": wall_price,
                                    "side": wall_data["side"],
                                    "consumption_pct": consumption_pct,
                                    "consumption_rate_pct": consumption_rate_pct,
                                    "initial_volume": initial_volume,
                                    "current_volume": current_volume,
                                    "timestamp": current_time
                                },
                                symbol=self.symbol
                            )
                            
                            # Удаляем из отслеживания (cooldown активен)
                            del self._tracked_walls[wall_price]
                            continue
                
                # Обновляем last_volume для следующего цикла
                wall_data["last_volume"] = current_volume
                wall_data["last_check_time"] = current_time
                
        except Exception as e:
            logger.error(f"Error in BreakoutDetector orderbook check: {e}")

    def _get_volume_at_price(self, orderbook: Dict, price: float, side: str) -> float:
        """Получает объем на определенном ценовом уровне из стакана."""
        try:
            levels = orderbook.get("bids" if side == "BID" else "asks", [])
            
            # Ищем уровень с нужной ценой (с допуском 0.01)
            for level_price, level_volume in levels:
                if abs(float(level_price) - price) < 0.01:
                    return float(level_volume)
            
            return 0.0
            
        except Exception as e:
            logger.error(f"Error getting volume at price: {e}")
            return 0.0