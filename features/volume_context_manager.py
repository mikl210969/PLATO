import asyncio
import logging
import numpy as np
from typing import Dict, Any, List, Optional
from copy import deepcopy

logger = logging.getLogger(__name__)

class VolumeContextManager:
    """
    Менеджер адаптивного контекста на основе вертикальных объемов.
    Пересчитывает режимы рынка и обновляет пороги фильтров для стратегий.
    """
    
    def __init__(self, volume_config: Dict[str, Any], base_strategy_params: Dict[str, Any]):
        self.config = volume_config
        self.base_params = base_strategy_params
        
        self.enabled = self.config.get("enabled", False)
        self.lookback = self.config.get("lookback_candles", 20)
        self.baseline_avg_vol = self.config.get("baseline_avg_vol", 100.0)
        self.regimes = self.config.get("regimes", {})
        
        # 🔥 Используем asyncio.Lock для безопасной работы в асинхронном окружении
        self._lock = asyncio.Lock()
        self.current_regime = "normal"
        self.current_params = deepcopy(self.base_params)
        self.last_metrics = {}
        
        self.dry_run = self.config.get("dry_run", False)
        self.force_regime = self.config.get("force_regime", None)

    async def update_context(self, recent_volumes: List[float]) -> None:
        # 🔥 ГАРАНТИРОВАННЫЙ ВЫВОД
        print(f"🔍 [VCM DEBUG] Вызван update_context. Получено объемов: {len(recent_volumes)}")
        
        if not self.enabled:
            print("️ [VCM DEBUG] Менеджер выключен!")
            return

        if len(recent_volumes) < self.lookback:
            print(f"⚠️ [VCM DEBUG] Мало данных: {len(recent_volumes)} < {self.lookback}")
            return

        try:
            volumes_array = np.array(recent_volumes[-self.lookback:])
            avg_vol = float(np.mean(volumes_array))
            std_vol = float(np.std(volumes_array))
            
            vol_ratio = avg_vol / self.baseline_avg_vol if self.baseline_avg_vol > 0 else 1.0
            # Clamp для защиты от аномалий
            vol_ratio = max(0.1, min(vol_ratio, 10.0))

            new_regime = self._determine_regime(vol_ratio)
            
        except Exception as e:
            logger.error(f"❌ Ошибка при расчете метрик контекста: {e}")
            return

        # Применяем overrides для нового режима
        new_params = deepcopy(self.base_params)
        if new_regime in self.regimes:
            overrides = self.regimes[new_regime].get("overrides", {})
            new_params.update(overrides)

        # Thread-safe обновление состояния
        async with self._lock:
            regime_changed = (new_regime != self.current_regime)
            
            if regime_changed:
                # 🔥 RUNTIME ЛОГИРОВАНИЕ: Сообщаем об изменении параметров
                old_wall_vol = self.current_params.get("min_wall_volume", "N/A") if hasattr(self, 'current_params') else "N/A"
                new_wall_vol = new_params.get("min_wall_volume", "N/A")
                old_price_dist = self.current_params.get("price_distance_pct", "N/A") if hasattr(self, 'current_params') else "N/A"
                new_price_dist = new_params.get("price_distance_pct", "N/A")
                
                logger.warning(
                    f"🔄 [REGIME SHIFT] {self.current_regime.upper()} ➔ {new_regime.upper()} | "
                    f"VolRatio: {vol_ratio:.2f} | AvgVol: {avg_vol:.1f} | "
                    f"MinWallVol: {old_wall_vol} ➔ {new_wall_vol} | "
                    f"PriceDist%: {old_price_dist} ➔ {new_price_dist}"
                )
                
                self.current_regime = new_regime

            if not self.dry_run:
                self.current_params = new_params

            self.last_metrics = {
                "avg_vol": avg_vol,
                "std_vol": std_vol,
                "vol_ratio": vol_ratio,
                "regime": self.current_regime
            }

    async def get_active_params(self) -> Dict[str, Any]:
        """
        Возвращает текущие активные параметры (для AdaptiveStrategy).
        Если dry_run=True, возвращает базовые параметры, чтобы не ломать логику.
        """
        async with self._lock:
            # Если мы в режиме наблюдения (dry_run), отдаем base_params, 
            # чтобы стратегии работали со стандартными настройками из конфига.
            if self.dry_run:
                return self.base_params
            
            return self.current_params

    def _determine_regime(self, vol_ratio: float) -> str:
        """Определяет режим на основе соотношения объемов."""
        if self.force_regime and self.force_regime in self.regimes:
            return self.force_regime

        # Сортируем режимы по vol_ratio_max, чтобы найти первый подходящий
        sorted_regimes = sorted(self.regimes.items(), key=lambda x: x[1].get("vol_ratio_max", 999))
        
        for regime_name, regime_data in sorted_regimes:
            if vol_ratio <= regime_data["vol_ratio_max"]:
                return regime_name
                
        return "normal" # Fallback

    async def get_metrics(self) -> Dict[str, Any]:
        """Возвращает последние рассчитанные метрики (для логов/вебморды)."""
        async with self._lock:
            return deepcopy(self.last_metrics)