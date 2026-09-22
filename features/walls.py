"""WallsFeature: детектор крупных лимитных ордеров (стен) в стакане."""
import time
from collections import deque
from typing import Any, Dict, List, Optional

from features.base import Feature


class WallsFeature(Feature):
    name = "walls"

    def __init__(self, symbol: str, config: Dict[str, Any], log):
        super().__init__(symbol, config, log)
        
        walls_cfg = config.get("walls", {})
        self._depth_levels = int(config.get("orderbook_depth", 50))
        self._min_size_mult = float(walls_cfg.get("min_size_avg_mult", 5.0))
        self._min_age_sec = float(walls_cfg.get("min_age_sec", 10.0))
        self._relocate_radius_ticks = int(walls_cfg.get("relocate_radius_ticks", 3))
        self._tick_size = 0.01  # Для SOLUSDT
        
        # История размеров уровней для расчета медианы (сглаживание)
        self._median_history = deque(maxlen=10)
        
        # Активные стены: ключ = цена, значение = словарь состояния
        self._active_walls: Dict[float, Dict[str, Any]] = {}
        
        self._state = {"walls_bid": [], "walls_ask": [], "ts": 0.0}

    def on_orderbook(self, bids: List[tuple], asks: List[tuple], ts: float) -> None:
        self._last_update_ts = ts
        
        # 1. Берем верхние N уровней
        top_bids = bids[:self._depth_levels]
        top_asks = asks[:self._depth_levels]
        
        if not top_bids or not top_asks:
            return
            
        # 2. Считаем медиану размера уровня (для динамического порога)
        bid_sizes = sorted([q for p, q in top_bids if q > 0])
        ask_sizes = sorted([q for p, q in top_asks if q > 0])
        
        if bid_sizes and ask_sizes:
            median_bid = bid_sizes[len(bid_sizes) // 2]
            median_ask = ask_sizes[len(ask_sizes) // 2]
            median_size = (median_bid + median_ask) / 2.0
            self._median_history.append(median_size)
            
            # Сглаженная медиана (если история есть)
            smooth_median = sum(self._median_history) / len(self._median_history)
        else:
            smooth_median = 0.0
            
        if smooth_median <= 0:
            return
            
        threshold = smooth_median * self._min_size_mult
        
        # 3. Ищем кандидатов в стены
        current_wall_prices = set()
        
        for price, qty in top_bids:
            if qty >= threshold:
                current_wall_prices.add(price)
                self._update_or_create_wall(price, "bid", qty, ts)
                
        for price, qty in top_asks:
            if qty >= threshold:
                current_wall_prices.add(price)
                self._update_or_create_wall(price, "ask", qty, ts)
                
        # 4. Проверяем исчезнувшие стены (спойфинг или съедение)
        disappeared_prices = set(self._active_walls.keys()) - current_wall_prices
        for price in disappeared_prices:
            self._handle_disappeared_wall(price, top_bids, top_asks, ts)
            
        # 5. Формируем снимок (только зрелые стены)
        self._build_snapshot(ts)

    def _update_or_create_wall(self, price: float, side: str, size: float, ts: float) -> None:
        if price in self._active_walls:
            wall = self._active_walls[price]
            wall["last_seen"] = ts
            wall["size"] = size
            # Если размер уменьшился — считаем % съедения
            if size < wall["initial_size"]:
                wall["eaten_pct"] = (wall["initial_size"] - size) / wall["initial_size"]
            else:
                wall["initial_size"] = size  # Если выросла — обновляем базу
        else:
            self._active_walls[price] = {
                "price": price,
                "side": side,
                "size": size,
                "initial_size": size,
                "first_seen": ts,
                "last_seen": ts,
                "eaten_pct": 0.0,
                "status": "alive"
            }

    def _handle_disappeared_wall(self, price: float, bids: List[tuple], asks: List[tuple], ts: float) -> None:
        wall = self._active_walls[price]
        
        # Проверяем релокацию (переезд на соседний тик)
        relocated = False
        radius = self._relocate_radius_ticks * self._tick_size
        
        # Ищем в текущем стакане уровень в радиусе
        for p, q in (bids if wall["side"] == "bid" else asks):
            if abs(p - price) <= radius and q >= wall["initial_size"] * 0.7:
                # Нашли переезд! Переносим стену
                self._active_walls[p] = wall
                self._active_walls[p]["price"] = p
                self._active_walls[p]["last_seen"] = ts
                del self._active_walls[price]
                relocated = True
                break
                
        if not relocated:
            # Если стена исчезла и не переехала — это спойфинг или полное съедение
            # Помечаем как spoofed, если eaten_pct маленький
            if wall["eaten_pct"] < 0.5:
                wall["status"] = "spoofed"
            else:
                wall["status"] = "eaten"
            # Удаляем из активных (она уже обработана)
            del self._active_walls[price]

    def _build_snapshot(self, ts: float) -> None:
        walls_bid = []
        walls_ask = []
        
        for wall in self._active_walls.values():
            age = ts - wall["first_seen"]
            # Зрелость: от 0 до 1.0
            confidence = min(1.0, age / (self._min_age_sec * 3.0))
            
            # В снимок отдаем только зрелые стены (возраст > min_age)
            if age >= self._min_age_sec:
                wall_info = {
                    "price": wall["price"],
                    "size": wall["size"],
                    "age_sec": age,
                    "confidence": confidence,
                    "eaten_pct": wall["eaten_pct"],
                    "status": wall["status"]
                }
                if wall["side"] == "bid":
                    walls_bid.append(wall_info)
                else:
                    walls_ask.append(wall_info)
                    
        # Сортируем по близости к рынку (опционально, но удобно)
        # Для простоты оставим как есть, стратегии сами отфильтруют
        
        self._state["walls_bid"] = walls_bid
        self._state["walls_ask"] = walls_ask
        self._state["ts"] = ts

    def snapshot(self) -> Dict[str, Any]:
        return {
            "walls_bid": self._state["walls_bid"],
            "walls_ask": self._state["walls_ask"],
            "ts": self._state["ts"]
        }