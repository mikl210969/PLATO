"""Volatility Filter — расчёт реального ATR и режимов волатильности."""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VolatilityFilter:
    def __init__(self, rest_client: Optional[Any] = None):
        """
        :param rest_client: Экземпляр BinanceRestClient для получения свечей.
        """
        self.rest_client = rest_client
        self._cached_atr = {}  # Кэш ATR по символам: {symbol: atr_value}
        self._cache_ttl = 60   # Обновлять ATR не чаще чем раз в 60 секунд
        self._last_update = {}

    async def calculate_real_atr(self, symbol: str, period: int = 14, interval: str = "1m") -> float:
        """
        Рассчитывает реальный ATR на основе последних свечей с Binance Spot REST API.
        """
        import time
        
        now = time.time()
        # Возвращаем кэш, если он свежий
        if symbol in self._cached_atr and (now - self._last_update.get(symbol, 0)) < self._cache_ttl:
            return self._cached_atr[symbol]

        if not self.rest_client:
            logger.warning("REST client not provided. Falling back to default ATR=0.5")
            return 0.5

        # Получаем свечи (берем period + 1 для расчета первого TR)
        klines = await self.rest_client.get_klines(symbol=symbol, interval=interval, limit=period + 1)
        
        if not klines or len(klines) < period + 1:
            logger.warning(f"Недостаточно данных для расчета ATR ({symbol}). Fallback to 0.5")
            return 0.5

        # Формат Binance kline: [0]time, [1]open, [2]high, [3]low, [4]close, [5]volume, ...
        try:
            tr_sum = 0.0
            prev_close = float(klines[0][4])
            
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                close = float(klines[i][4])
                
                # True Range (TR)
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                tr_sum += tr
                prev_close = close
            
            # Average True Range (ATR)
            atr = tr_sum / period
            
            # Сохраняем в кэш
            self._cached_atr[symbol] = atr
            self._last_update[symbol] = now
            
            logger.info(f"✅ Реальный ATR для {symbol} ({interval}): {atr:.4f}")
            return atr
            
        except Exception as e:
            logger.error(f"Ошибка при расчете ATR для {symbol}: {e}")
            return 0.5

    def get_volatility_mode(self, atr: float, current_price: float) -> str:
        """
        Определяет режим волатильности на основе ATR относительно цены.
        """
        if current_price <= 0:
            return "normal"
            
        atr_pct = (atr / current_price) * 100
        
        if atr_pct < 0.3:
            return "low"
        elif atr_pct > 1.5:
            return "high"
        else:
            return "normal"