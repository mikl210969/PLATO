"""VolumeProfileFeature: HVN/LVN уровни из ленты сделок (SPOT aggTrade)."""
import time
from collections import deque
from typing import Any, Dict, List, Optional

from features.base import Feature


class VolumeProfileFeature(Feature):
    name = "volume_profile"

    def __init__(self, symbol: str, config: Dict[str, Any], log):
        super().__init__(symbol, config, log)
        
        # Параметры из конфига
        profile_cfg = config.get("profile", {})
        self._micro_min = float(profile_cfg.get("micro_min", 15))
        self._macro_hours = float(profile_cfg.get("macro_hours", 4))
        self._bin_price_pct = float(profile_cfg.get("bin_price_pct_min", 0.0005))  # 0.05%
        self._bin_atr_mult = float(profile_cfg.get("bin_atr_mult", 0.1))
        self._hvn_median_mult = float(profile_cfg.get("hvn_median_mult", 1.5))
        self._lvn_median_mult = float(profile_cfg.get("lvn_median_mult", 0.5))
        self._max_nodes = int(profile_cfg.get("max_nodes", 5))
        
        # Хранилище тиков (ts, price, volume)
        # Максимальный возраст = макро-окно + запас
        max_age_sec = self._macro_hours * 3600 + 60
        self._trades = deque()
        self._max_trades = 2000000  # Защита от переполнения памяти (SOL ~50 тр/сек * 14400 сек = 720k)
        
        # Кэш состояния
        self._state = {
            "micro_bins": {},
            "macro_bins": {},
            "nearest_hvn_above": None,
            "nearest_hvn_below": None,
            "median_volume": 0.0,
            "ts": 0.0,
        }
        self._atr_value = 0.5  # Дефолт, обновится извне или из ATR Feature

    def set_atr(self, atr: float) -> None:
        """Позволяет ATR Feature обновлять волатильность для размера бина."""
        if atr > 0:
            self._atr_value = atr

    def on_trade(self, price: float, qty: float, is_buy: bool, ts: float) -> None:
        self._last_update_ts = ts
        self._trades.append((ts, price, float(qty)))
        
        # Очистка старых тиков, если буфер переполнен
        if len(self._trades) > self._max_trades:
            self._trades.popleft()
            
        self._maybe_recalc(ts)

    def _get_bin_size(self, price: float) -> float:
        """Адаптивный размер бина: max(0.05% цены, 0.1 ATR)."""
        pct_size = price * self._bin_price_pct
        atr_size = self._atr_value * self._bin_atr_mult
        return max(pct_size, atr_size) if atr_size > 0 else pct_size

    def _recalc(self, ts: float) -> None:
        if not self._trades:
            return
            
        # 1. Ограничиваем окна по времени
        micro_cutoff = ts - (self._micro_min * 60)
        macro_cutoff = ts - (self._macro_hours * 3600)
        
        # Оптимизация: удаляем совсем старые тики из deque
        while self._trades and self._trades[0][0] < macro_cutoff:
            self._trades.popleft()
            
        # 2. Считаем размер бина по последней цене
        last_price = self._trades[-1][1]
        bin_size = self._get_bin_size(last_price)
        
        # 3. Строим карты объёмов
        micro_map = {}
        macro_map = {}
        
        for t, p, v in self._trades:
            bin_key = round(p / bin_size) * bin_size
            
            if t >= micro_cutoff:
                micro_map[bin_key] = micro_map.get(bin_key, 0.0) + v
            if t >= macro_cutoff:
                macro_map[bin_key] = macro_map.get(bin_key, 0.0) + v
                
        # 4. Находим медиану и уровни (берём макро-профиль для стабильности порогов)
        volumes = list(macro_map.values()) if macro_map else [0.0]
        # Простая медиана
        volumes_sorted = sorted(volumes)
        n = len(volumes_sorted)
        median_vol = volumes_sorted[n // 2] if n > 0 else 0.0
        
        # 5. Ищем HVN/LVN и ближайшие к текущей цене
        hvn_above = None
        hvn_below = None
        min_dist_above = float('inf')
        min_dist_below = float('inf')
        
        for price_level, vol in macro_map.items():
            if median_vol <= 0:
                continue
                
            strength = vol / median_vol
            is_hvn = strength >= self._hvn_median_mult
            
            if is_hvn:
                dist = price_level - last_price
                if dist > 0 and dist < min_dist_above:
                    min_dist_above = dist
                    hvn_above = {"price": price_level, "volume": vol, "strength": strength}
                elif dist < 0 and abs(dist) < min_dist_below:
                    min_dist_below = abs(dist)
                    hvn_below = {"price": price_level, "volume": vol, "strength": strength}
                    
        # 6. Сохраняем состояние
        self._state["micro_bins"] = micro_map
        self._state["macro_bins"] = macro_map
        self._state["nearest_hvn_above"] = hvn_above
        self._state["nearest_hvn_below"] = hvn_below
        self._state["median_volume"] = median_vol
        self._state["ts"] = ts

    def snapshot(self) -> Dict[str, Any]:
        return {
            "nearest_hvn_above": self._state["nearest_hvn_above"],
            "nearest_hvn_below": self._state["nearest_hvn_below"],
            "median_volume": self._state["median_volume"],
            "bin_count_macro": len(self._state["macro_bins"]),
            "ts": self._state["ts"],
        }