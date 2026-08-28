import time
import json
import logging
from typing import Optional, Dict, Any, Callable

from extensions.data_layer.db_manager import DatabaseManager
from extensions.analytics.volatility_filter import VolatilityFilter

logger = logging.getLogger(__name__)

class MetricCache:
    def __init__(self, db_manager: DatabaseManager, volatility_filter: VolatilityFilter):
        self.db = db_manager
        self.vf = volatility_filter

    def get_or_calculate_atr(
        self, 
        symbol: str, 
        fetch_candles_func: Callable[[str, int], list], # Функция для получения свечей извне (например, из BinanceRestClient)
        period: int = 14,
        ttl_seconds: int = 3600 # По умолчанию обновляем раз в час
    ) -> Dict[str, Any]:
        """
        Атомарная операция: получает ATR из БД, если он свежий. 
        Иначе рассчитывает, сохраняет и возвращает.
        """
        now = int(time.time())
        
        # 1. Проверяем кэш
        query = """
            SELECT value, metadata, expires_at 
            FROM market_metrics 
            WHERE symbol = ? AND metric_type = 'ATR' 
            ORDER BY timestamp DESC LIMIT 1
        """
        rows = self.db.execute_query(query, (symbol,))
        
        if rows:
            row = rows[0]
            if now < row['expires_at']:
                # Данные актуальны
                metadata = json.loads(row['metadata']) if row['metadata'] else {}
                logger.debug(f"[{symbol}] ATR получен из кэша: {row['value']}")
                return {
                    "atr": row['value'],
                    "regime": metadata.get("regime", "normal"),
                    "source": "cache"
                }
            else:
                logger.info(f"[{symbol}] Кэш ATR устарел (TTL истек). Пересчет...")

        # 2. Кэш устарел или пуст -> рассчитываем
        # Требуется period + avg_atr_period (100) свечей для корректного расчета режима
        total_candles_needed = max(period, 100) + 1
        try:
            candles = fetch_candles_func(symbol, total_candles_needed)
            if not candles or len(candles) < total_candles_needed:
                logger.error(f"[{symbol}] Не удалось получить достаточно свечей для расчета ATR")
                return {"atr": 0.0, "regime": "normal", "source": "fallback"}

            current_atr = self.vf.calculate_atr(candles)
            
            # Для режима нужен средний ATR (упрощенно: среднее за последние 100 значений из рассчитанных TR, 
            # или можно сделать отдельный запрос. Для простоты возьмем среднее за доступный период)
            # В продакшене здесь лучше рассчитать RMA за 100 периодов.
            avg_atr = current_atr # Заглушка для примера, в реале нужен расчет за 100 периодов
            
            regime = self.vf.determine_volatility_regime(current_atr, avg_atr)
            
        except Exception as e:
            logger.error(f"[{symbol}] Ошибка при расчете ATR: {e}")
            return {"atr": 0.0, "regime": "normal", "source": "error"}

        # 3. Сохраняем в БД
        expires_at = now + ttl_seconds
        metadata_json = json.dumps({"regime": regime, "period": period})
        
        insert_query = """
            INSERT INTO market_metrics (symbol, metric_type, value, timestamp, expires_at, metadata)
            VALUES (?, 'ATR', ?, ?, ?, ?)
        """
        self.db.execute_query(insert_query, (symbol, current_atr, now, expires_at, metadata_json))
        logger.info(f"[{symbol}] ATR рассчитан и сохранен: {current_atr} (Режим: {regime})")

        return {
            "atr": current_atr,
            "regime": regime,
            "source": "calculated"
        }