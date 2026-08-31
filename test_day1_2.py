import logging
from extensions.data_layer.db_manager import DatabaseManager
from extensions.analytics.volatility_filter import VolatilityFilter
from extensions.analytics.metric_cache import MetricCache

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Заглушка для имитации запроса к BinanceRestClient
def mock_fetch_candles(symbol: str, limit: int) -> list:
    import random
    # Генерируем фейковые свечи для теста
    candles = []
    price = 140.0
    for _ in range(limit):
        high = price + random.uniform(0.5, 2.0)
        low = price - random.uniform(0.5, 2.0)
        close = random.uniform(low, high)
        candles.append({"high": high, "low": low, "close": close, "open": price})
        price = close
    return candles

if __name__ == "__main__":
    # 1. Инициализация
    db = DatabaseManager()
    vf = VolatilityFilter(atr_period=14, avg_atr_period=100)
    cache = MetricCache(db, vf)

    symbol = "SOLUSDT"
    
    # 2. Первый вызов (должен рассчитать и сохранить)
    print("\n--- Первый вызов (Расчет) ---")
    result1 = cache.get_or_calculate_atr(symbol, mock_fetch_candles, period=14, ttl_seconds=60)
    print(f"Результат: {result1}")

    # 3. Второй вызов (должен мгновенно вернуть из кэша)
    print("\n--- Второй вызов (Кэш) ---")
    result2 = cache.get_or_calculate_atr(symbol, mock_fetch_candles, period=14, ttl_seconds=60)
    print(f"Результат: {result2}")
    
    db.close()