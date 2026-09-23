"""
Синтетический тест для VolumeRollingWindow и DatabaseManager.
Проверяет:
1. Агрегацию тиков в 1-минутные свечи.
2. Корректное обновление High/Low/Close и суммирование Volume.
3. Сохранение завершенных свечей в историю и в БД.
4. Расчет baseline объема.
"""
import os
import time
import logging
from extensions.data_layer.db_manager import DatabaseManager
from features.volume_rolling_window import VolumeRollingWindow

# Настраиваем логгер
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("test_rolling_window")

def run_test():
    # 1. Создаем временную БД для теста, чтобы не трогать основную
    test_db_path = "test_temp_metrics.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    logger.info("🚀 Инициализация DatabaseManager и VolumeRollingWindow...")
    db = DatabaseManager(db_path=test_db_path)
    # lookback=3 означает, что мы будем хранить последние 3 минуты в памяти
    rolling_window = VolumeRollingWindow(db_manager=db, lookback_candles=3)

    # 🔥 Используем время, кратное 60, чтобы избежать перескока минут
    base_time = int(time.time()) // 60 * 60
    t_minute_1 = float(base_time)
    t_minute_2 = t_minute_1 + 60.0 
    t_minute_3 = t_minute_1 + 120.0 

    logger.info(f"📥 Подача тиков для Минуты 1 (base={int(t_minute_1)})...")
    rolling_window.on_trade("SOLUSDT", price=100.0, quantity=10.0, timestamp=t_minute_1 + 1, is_buyer_maker=False)
    rolling_window.on_trade("SOLUSDT", price=105.0, quantity=5.0, timestamp=t_minute_1 + 20, is_buyer_maker=False)
    rolling_window.on_trade("SOLUSDT", price=95.0, quantity=15.0, timestamp=t_minute_1 + 50, is_buyer_maker=True)

    logger.info("📥 Подача тиков для Минуты 2 (должна завершить свечу Минуты 1)...")
    rolling_window.on_trade("SOLUSDT", price=102.0, quantity=20.0, timestamp=t_minute_2 + 5, is_buyer_maker=False)
    rolling_window.on_trade("SOLUSDT", price=103.0, quantity=8.0, timestamp=t_minute_2 + 30, is_buyer_maker=False)

    logger.info("📥 Подача тиков для Минуты 3 (должна завершить свечу Минуты 2)...")
    rolling_window.on_trade("SOLUSDT", price=101.0, quantity=12.0, timestamp=t_minute_3 + 10, is_buyer_maker=True)

    # 3. Проверка результатов в памяти
    logger.info("🔍 Проверка истории объемов в памяти...")
    volumes = rolling_window.get_recent_volumes("SOLUSDT")
    expected_volumes = [30.0, 28.0]
    
    if volumes == expected_volumes:
        logger.info(f"✅ Объемы в памяти корректны: {volumes}")
    else:
        logger.error(f"❌ Ошибка! Ожидалось {expected_volumes}, получено {volumes}")
        return

    # 4. Проверка БД
    logger.info("🔍 Проверка записей в SQLite...")
    rows = db.execute("SELECT * FROM candles_1m WHERE symbol = 'SOLUSDT' ORDER BY timestamp ASC")
    
    if len(rows) == 2:
        row1 = rows[0]
        if (row1['open'] == 100.0 and row1['high'] == 105.0 and 
            row1['low'] == 95.0 and row1['close'] == 95.0 and row1['volume'] == 30.0):
            logger.info(f"✅ Свеча Минуты 1 в БД корректна: O={row1['open']} H={row1['high']} L={row1['low']} C={row1['close']} V={row1['volume']}")
        else:
            logger.error(f"❌ Данные свечи Минуты 1 в БД неверны: {dict(row1)}")
            return
        
        row2 = rows[1]
        if (row2['open'] == 102.0 and row2['high'] == 103.0 and 
            row2['low'] == 102.0 and row2['close'] == 103.0 and row2['volume'] == 28.0):
            logger.info(f"✅ Свеча Минуты 2 в БД корректна: O={row2['open']} H={row2['high']} L={row2['low']} C={row2['close']} V={row2['volume']}")
        else:
            logger.error(f"❌ Данные свечи Минуты 2 в БД неверны: {dict(row2)}")
            return
    else:
        logger.error(f"❌ Ожидалось 2 записи в БД, найдено {len(rows)}")
        return

    # 5. Проверка расчета Baseline
    logger.info("🔍 Проверка calculate_baseline_avg_vol...")
    avg = rolling_window.calculate_baseline_avg_vol("SOLUSDT", lookback_minutes=1440)
    if abs(avg - 29.0) < 0.1:
        logger.info(f"✅ Средний объем (Baseline) рассчитан верно: {avg}")
    else:
        logger.error(f"❌ Baseline неверен: {avg}")
        return

    # Очистка
    db.close()
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        if os.path.exists(test_db_path + "-shm"):
            os.remove(test_db_path + "-shm")
        if os.path.exists(test_db_path + "-wal"):
            os.remove(test_db_path + "-wal")

    logger.info("🎉 ТЕСТ ПРОЙДЕН УСПЕШНО! VolumeRollingWindow работает корректно.")

if __name__ == "__main__":
    run_test()