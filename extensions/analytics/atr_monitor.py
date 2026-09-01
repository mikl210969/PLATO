"""
AtrMonitor — фоновый пересчёт ATR каждые 5 минут.
Публикует событие ATR_UPDATED для живого обновления SL/TP.
"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional

from extensions.analytics.volatility_filter import VolatilityFilter

logger = logging.getLogger(__name__)


class AtrMonitor:
    """Фоновый монитор ATR с периодическим обновлением."""

    def __init__(self, symbol: str, event_bus, volatility_filter: VolatilityFilter, 
                 update_interval_sec: int = 300):
        """
        :param symbol: Торговая пара (например, 'SOLUSDT')
        :param event_bus: Шина событий для публикации ATR_UPDATED
        :param volatility_filter: Экземпляр VolatilityFilter для расчёта ATR
        :param update_interval_sec: Период обновления в секундах (по умолчанию 5 минут)
        """
        self.symbol = symbol
        self.bus = event_bus
        self.volatility_filter = volatility_filter
        self.update_interval_sec = update_interval_sec
        
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        self._current_atr: float = 0.5  # Fallback значение
        self._last_update_time: float = 0.0

    async def start(self):
        """Запустить фоновый мониторинг ATR."""
        print(f"▶️  [AtrMonitor {self.symbol}] Starting...")
        self._is_running = True
        
        # Первый расчёт сразу
        await self._update_atr()
        
        # Запускаем фоновую задачу
        self._task = asyncio.create_task(self._monitor_loop())
        print(f"✅ [AtrMonitor {self.symbol}] Started (interval: {self.update_interval_sec}s)")

    async def stop(self):
        """Остановить мониторинг."""
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        print(f"🛑 [AtrMonitor {self.symbol}] Stopped")

    async def _monitor_loop(self):
        """Фоновый цикл: каждые N секунд пересчитываем ATR."""
        while self._is_running:
            try:
                await asyncio.sleep(self.update_interval_sec)
                await self._update_atr()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ [AtrMonitor {self.symbol}] Error in monitor loop: {e}")
                await asyncio.sleep(10)  # Пауза перед повтором

    async def _update_atr(self):
        """Пересчитать ATR и опубликовать событие."""
        try:
            # Используем VolatilityFilter для расчёта
            new_atr = await self.volatility_filter.calculate_real_atr(
                symbol=self.symbol,
                period=14,
                interval="1m"
            )
            
            # Проверяем, изменился ли ATR существенно (>5%)
            if self._current_atr > 0:
                change_pct = abs(new_atr - self._current_atr) / self._current_atr * 100
                if change_pct > 5.0:
                    logger.info(
                        f"📈 [AtrMonitor {self.symbol}] ATR изменился: "
                        f"{self._current_atr:.4f} → {new_atr:.4f} ({change_pct:+.1f}%)"
                    )
            
            # Обновляем текущее значение
            self._current_atr = new_atr
            self._last_update_time = time.time()
            
            # Получаем текущую цену для определения режима волатильности
            # (используем кэш из VolatilityFilter или fallback)
            current_price = self._get_current_price()
            volatility_mode = self.volatility_filter.get_volatility_mode(new_atr, current_price)
            
            # Публикуем событие
            await self.bus.publish(
                event_type="ATR_UPDATED",
                source="atr_monitor",
                payload={
                    "symbol": self.symbol,
                    "atr": new_atr,
                    "volatility_mode": volatility_mode,
                    "timestamp": self._last_update_time
                },
                symbol=self.symbol
            )
            
            logger.debug(
                f"📊 [AtrMonitor {self.symbol}] ATR: {new_atr:.4f} | "
                f"Mode: {volatility_mode} | Price: {current_price:.2f}"
            )
            
        except Exception as e:
            logger.error(f"❌ [AtrMonitor {self.symbol}] Failed to update ATR: {e}")

    def _get_current_price(self) -> float:
        """Получить текущую цену (fallback для определения режима волатильности)."""
        # TODO: Можно подключить к SpotPriceProvider, если он есть
        # Пока возвращаем примерную цену для SOLUSDT
        if "SOL" in self.symbol:
            return 103.0
        elif "BTC" in self.symbol:
            return 79000.0
        elif "ETH" in self.symbol:
            return 2500.0
        else:
            return 100.0

    def get_current_atr(self) -> float:
        """Вернуть текущее значение ATR (для синхронного доступа)."""
        return self._current_atr