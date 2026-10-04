"""
Скрипт для скачивания исторических данных aggTrades (лента сделок) для SOLUSDT.
Сохраняет данные на Яндекс Диск для синхронизации между компьютерами.
"""

import requests
import pandas as pd
import time
from datetime import datetime, timedelta
import os
import logging

# Настройки
SYMBOL = "SOLUSDT"
OUTPUT_DIR = r"C:\Users\ongul\YandexDisk\Data"  # Яндекс Диск
START_DATE = "2026-09-27"  # Совпадает с данными стакана
END_DATE = "2026-09-29"    # Совпадает с данными стакана
RATE_LIMIT_DELAY = 0.1     # задержка между запросами (секунды)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_agg_trades(symbol: str, start_time: int, end_time: int, limit: int = 1000) -> list:
    """
    Получает агрегированные сделки с Binance REST API.
    """
    url = "https://api.binance.com/api/v3/aggTrades"
    params = {
        "symbol": symbol,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка при запросе данных: {e}")
        return []


def download_day_data(symbol: str, date_str: str, output_dir: str):
    """
    Скачивает данные за один день по часам.
    """
    # Проверяем, есть ли уже файл
    output_file = os.path.join(output_dir, f"{symbol}_aggTrades_{date_str}.csv")
    if os.path.exists(output_file):
        logger.info(f"⚠️ Файл {output_file} уже существует, пропускаем скачивание")
        return
    
    date = datetime.strptime(date_str, "%Y-%m-%d")
    start_of_day = int(date.timestamp() * 1000)
    end_of_day = start_of_day + (24 * 60 * 60 * 1000) - 1
    
    all_trades = []
    current_time = start_of_day
    
    logger.info(f"Начинаем скачивание данных за {date_str}...")
    
    while current_time < end_of_day:
        # Скачиваем по 1 часу
        hour_end = min(current_time + (60 * 60 * 1000), end_of_day)
        
        trades = get_agg_trades(symbol, current_time, hour_end, limit=1000)
        
        if not trades:
            logger.warning(f"Нет данных для периода {datetime.fromtimestamp(current_time/1000)} - {datetime.fromtimestamp(hour_end/1000)}")
            current_time = hour_end
            continue
        
        all_trades.extend(trades)
        
        # Прогресс
        progress = ((current_time - start_of_day) / (end_of_day - start_of_day)) * 100
        logger.info(f"Прогресс: {progress:.1f}% | Получено сделок: {len(all_trades)}")
        
        # Переходим к следующему часу
        current_time = hour_end
        time.sleep(RATE_LIMIT_DELAY)
    
    # Сохраняем в CSV
    if all_trades:
        df = pd.DataFrame(all_trades)
        # Binance возвращает 8 колонок
        df.columns = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "timestamp", "is_buyer_maker", "ignore"]
        
        # Удаляем служебную колонку ignore
        df = df.drop(columns=["ignore"])
        
        # Конвертируем timestamp в читаемый формат
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        df.to_csv(output_file, index=False)
        logger.info(f"✅ Сохранено {len(all_trades)} сделок в {output_file}")
    else:
        logger.warning(f"⚠️ Не удалось получить данные за {date_str}")


def main():
    # Создаем директорию для данных (если нет)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Генерируем список дат
    start = datetime.strptime(START_DATE, "%Y-%m-%d")
    end = datetime.strptime(END_DATE, "%Y-%m-%d")
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    logger.info(f"План скачивания: {dates}")
    logger.info(f"Символ: {SYMBOL}")
    logger.info(f"Директория вывода: {OUTPUT_DIR}")
    
    # Скачиваем данные для каждого дня
    for date_str in dates:
        download_day_data(SYMBOL, date_str, OUTPUT_DIR)
    
    logger.info("✅ Скачивание завершено!")
    
    # Показываем результат
    logger.info(f"\n Файлы в {OUTPUT_DIR}:")
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith('.csv'):
            path = os.path.join(OUTPUT_DIR, f)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            logger.info(f"  {f}: {size_mb:.2f} МБ")


if __name__ == "__main__":
    main()