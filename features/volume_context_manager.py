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

    async def update_context(self, recent_volumes: List[float]) -> Dict[str, Any]:
        """
        Обновляет контекст на основе последних объемов.
        Добавлены: EMA для сглаживания + гистерезис для стабильности режимов.
        """
        if not recent_volumes:
            return self.current_params

        # 1. Рассчитываем средний объем за период
        avg_vol = sum(recent_volumes) / len(recent_volumes)
        
        # 2. Рассчитываем коэффициент относительно базового объема
        vol_ratio = avg_vol / self.baseline_avg_vol if self.baseline_avg_vol > 0 else 1.0
        
        # 3. 🔥 EMA для сглаживания (коэффициент 0.3 = быстрая реакция, но без резких скачков)
        if not hasattr(self, '_ema_vol_ratio'):
            self._ema_vol_ratio = vol_ratio  # Инициализация при первом запуске
        else:
            self._ema_vol_ratio = (vol_ratio * 0.3) + (self._ema_vol_ratio * 0.7)
        
        # Используем EMA для определения режима (вместо мгновенного vol_ratio)
        smoothed_vol_ratio = self._ema_vol_ratio
        
        # 4. Определяем новый режим на основе сглаженного объема
        new_regime = self._determine_regime(smoothed_vol_ratio)
        
        # 5. 🔥 ГИСТЕРЕЗИС: Требуем подтверждение смены режима
        if not hasattr(self, '_regime_counter'):
            self._regime_counter = 0
            self._pending_regime = new_regime
        
        if new_regime == self._pending_regime:
            self._regime_counter += 1
        else:
            self._pending_regime = new_regime
            self._regime_counter = 1
        
        # Режим меняется только если продержался 5 свечей (5 минут для 1m таймфрейма)
        HYSTERESIS_THRESHOLD = 5
        if self._regime_counter >= HYSTERESIS_THRESHOLD and new_regime != self.current_regime:
            # Смена режима подтверждена
            old_regime = self.current_regime
            self.current_regime = new_regime
            self._regime_counter = 0
            
            # Получаем параметры для нового режима
            new_params = deepcopy(self.base_params)
            if new_regime in self.regimes:
                overrides = self.regimes[new_regime].get("overrides", {})
                new_params.update(overrides)
            
            if not self.dry_run:
                self.current_params = new_params
            
            # Логируем смену режима
            logger.warning(
                f"🔄 [REGIME SHIFT] {old_regime.upper()} ➔ {new_regime.upper()} | "
                f"EMA_VolRatio: {smoothed_vol_ratio:.2f} | AvgVol: {avg_vol:.1f} | "
                f"Подтверждено за {HYSTERESIS_THRESHOLD} свечей"
            )
        
        # 6. Сохраняем метрики для отладки
        self.last_metrics = {
            "vol_ratio": vol_ratio,
            "ema_vol_ratio": smoothed_vol_ratio,
            "avg_vol": avg_vol,
            "current_regime": self.current_regime,
            "pending_regime": self._pending_regime,
            "regime_counter": self._regime_counter
        }
        
        return self.current_params
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