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
        
        self._min_size_mult = float(walls_cfg.get("min_size_avg_mult", 1.5))
        self._min_age_sec = float(walls_cfg.get("min_age_sec", 2.0))
        self._relocate_radius_ticks = int(walls_cfg.get("relocate_radius_ticks", 3))
        self._tick_size = 0.01
        
        self._median_history = deque(maxlen=10)
        
        self._active_walls: Dict[float, Dict[str, Any]] = {}
        
        self._state = {"walls_bid": [], "walls_ask": [], "ts": 0.0}

    def on_orderbook(self, bids: List[tuple], asks: List[tuple], ts: float) -> None:
        self._last_update_ts = ts
        
        top_bids = bids[:self._depth_levels]
        top_asks = asks[:self._depth_levels]
        
        if not top_bids or not top_asks:
            return
            
        bid_sizes = sorted([q for p, q in top_bids if q > 0])
        ask_sizes = sorted([q for p, q in top_asks if q > 0])
        
        if bid_sizes and ask_sizes:
            median_bid = bid_sizes[len(bid_sizes) // 2]
            median_ask = ask_sizes[len(ask_sizes) // 2]
            median_size = (median_bid + median_ask) / 2.0
            self._median_history.append(median_size)
            
            smooth_median = sum(self._median_history) / len(self._median_history)
        else:
            smooth_median = 0.0
            
        if smooth_median <= 0:
            return
            
        threshold = smooth_median * self._min_size_mult
        
        current_wall_prices = set()
        
        for price, qty in top_bids:
            if qty == 0:
                continue
            if qty >= threshold:
                current_wall_prices.add(price)
                self._update_or_create_wall(price, "bid", qty, ts)
                
        for price, qty in top_asks:
            if qty == 0:
                continue
            if qty >= threshold:
                current_wall_prices.add(price)
                self._update_or_create_wall(price, "ask", qty, ts)
                
        disappeared_prices = set(self._active_walls.keys()) - current_wall_prices
        for price in disappeared_prices:
            self._handle_disappeared_wall(price, top_bids, top_asks, ts)
            
        self._build_snapshot(ts)

    def _update_or_create_wall(self, price: float, side: str, size: float, ts: float) -> None:
        if price in self._active_walls:
            wall = self._active_walls[price]
            wall["last_seen"] = ts
            wall["update_count"] += 1
            
            if abs(size - wall["size"]) > 0.01:
                wall["size_history"].append(size)
                wall["size"] = size
                
            if size < wall["initial_size"]:
                wall["eaten_pct"] = (wall["initial_size"] - size) / wall["initial_size"]
            else:
                wall["initial_size"] = size
        else:
            self._active_walls[price] = {
                "price": price,
                "side": side,
                "size": size,
                "initial_size": size,
                "first_seen": ts,
                "last_seen": ts,
                "eaten_pct": 0.0,
                "status": "alive",
                "update_count": 1,
                "size_history": deque([size], maxlen=50)
            }

    def _handle_disappeared_wall(self, price: float, bids: List[tuple], asks: List[tuple], ts: float) -> None:
        wall = self._active_walls[price]
        
        relocated = False
        radius = self._relocate_radius_ticks * self._tick_size
        
        for p, q in (bids if wall["side"] == "bid" else asks):
            if abs(p - price) <= radius and q >= wall["initial_size"] * 0.7:
                self._active_walls[p] = wall
                self._active_walls[p]["price"] = p
                self._active_walls[p]["last_seen"] = ts
                del self._active_walls[price]
                relocated = True
                break
                
        if not relocated:
            if wall["eaten_pct"] < 0.5:
                wall["status"] = "spoofed"
            else:
                wall["status"] = "eaten"
            del self._active_walls[price]

    def _calculate_cv(self, size_history: deque) -> float:
        if len(size_history) < 2:
            return 0.0
        
        sizes = list(size_history)
        mean_size = sum(sizes) / len(sizes)
        
        if mean_size == 0:
            return 0.0
        
        variance = sum((s - mean_size) ** 2 for s in sizes) / len(sizes)
        std_dev = variance ** 0.5
        
        return std_dev / mean_size

    def _build_snapshot(self, ts: float) -> None:
        walls_bid = []
        walls_ask = []
        
        for wall in self._active_walls.values():
            age = ts - wall["first_seen"]
            confidence = min(1.0, age / (self._min_age_sec * 3.0))
            
            cv = self._calculate_cv(wall["size_history"])
            is_stable = cv <= 0.15
            is_mature = wall["update_count"] >= 30
            
            if age >= self._min_age_sec:
                wall_info = {
                    "price": wall["price"],
                    "size": wall["size"],
                    "age_sec": age,
                    "confidence": confidence,
                    "eaten_pct": wall["eaten_pct"],
                    "status": wall["status"],
                    "update_count": wall["update_count"],
                    "cv": cv,
                    "is_stable": is_stable,
                    "is_mature": is_mature
                }
                if wall["side"] == "bid":
                    walls_bid.append(wall_info)
                else:
                    walls_ask.append(wall_info)
                    
        self._state["walls_bid"] = walls_bid
        self._state["walls_ask"] = walls_ask
        self._state["ts"] = ts

    def snapshot(self) -> Dict[str, Any]:
        walls_bid = []
        walls_ask = []
        now = time.time()
        
        max_wall_cv = self.config.get("walls", {}).get("max_wall_cv", 0.15) if isinstance(self.config.get("walls"), dict) else 0.15

        for price, wall in self._active_walls.items():
            age = now - wall["first_seen"]
            
            if age < self._min_age_sec:
                continue
                
            cv = self._calculate_cv(wall["size_history"])
            if cv > max_wall_cv:
                continue
            
            # 🔥 ИСПРАВЛЕНО: добавлено confidence!
            confidence = min(1.0, age / (self._min_age_sec * 3.0))
                
            wall_data = {
                "price": wall["price"],
                "size": wall["size"],
                "age": round(age, 1),
                "cv": round(cv, 2),
                "side": wall["side"],
                "confidence": round(confidence, 2)  # 🔥 ДОБАВЛЕНО!
            }
            
            if wall["side"] == "bid":
                walls_bid.append(wall_data)
            else:
                walls_ask.append(wall_data)

        walls_bid.sort(key=lambda x: x["size"], reverse=True)
        walls_ask.sort(key=lambda x: x["size"], reverse=True)

        self._state = {
            "walls_bid": walls_bid[:5],
            "walls_ask": walls_ask[:5],
            "ts": now
        }
        return self._state