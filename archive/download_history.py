import os
import requests
import pandas as pd
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
SYMBOL = "SOLUSDT"
DATA_DIR = f"data/history/{SYMBOL}"
os.makedirs(DATA_DIR, exist_ok=True)

# Период: с 13 по 21 сентября 2026 (включительно)
START_DATE = datetime(2026, 9, 13)
END_DATE = datetime(2026, 9, 21)

# Базовые URL Binance Vision (daily данные)
KLINE_BASE_URL = "https://data.binance.vision/data/spot/daily/klines/1m"
TRADES_BASE_URL = "https://data.binance.vision/data/spot/daily/trades"

# === ФУНКЦИИ ===
def download_and_extract(url, filename, dest_dir):
    """Скачивает ZIP и распаковывает CSV в dest_dir."""
    print(f"⬇️  Скачиваю {filename}...")
    try:
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            with zipfile.ZipFile(BytesIO(response.content)) as z:
                # Распаковываем только CSV файлы
                for name in z.namelist():
                    if name.endswith('.csv'):
                        z.extract(name, dest_dir)
                        print(f"   ✅ {name} -> {dest_dir}")
            return True
        else:
            print(f"   ⚠️  Не найдено ({response.status_code}) — возможно, данные еще не опубликованы")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False


def main():
    print(f"🚀 Загрузка исторических данных для {SYMBOL}")
    print(f"📅 Период: {START_DATE.strftime('%Y-%m-%d')} — {END_DATE.strftime('%Y-%m-%d')}\n")

    # Генерируем список дат
    dates = []
    current = START_DATE
    while current <= END_DATE:
        dates.append(current)
        current += timedelta(days=1)

    print(f" Всего дней для загрузки: {len(dates)}\n")

    # === 1. Свечи (Klines) 1m ===
    print("=" * 60)
    print("📈 ЗАГРУЗКА СВЕЧЕЙ (1m Klines)")
    print("=" * 60)
    klines_downloaded = 0
    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        # Формат URL: .../klines/1m/SOLUSDT/1m/SOLUSDT-1m-2026-09-13.zip
        url = f"{KLINE_BASE_URL}/{SYMBOL}/1m/{SYMBOL}-1m-{date_str}.zip"
        if download_and_extract(url, f"Klines 1m {date_str}", DATA_DIR):
            klines_downloaded += 1

    # === 2. Тики (Trades) ===
    print("\n" + "=" * 60)
    print(" ЗАГРУЗКА ТИКОВ (Trades)")
    print("=" * 60)
    print("⚠️  Файлы trades могут быть большими (50-200 МБ в день)")
    print("⚠️  Общий объем за 9 дней может достигать 1-2 ГБ\n")
    
    trades_downloaded = 0
    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        # Формат URL: .../trades/SOLUSDT/SOLUSDT-trades-2026-09-13.zip
        url = f"{TRADES_BASE_URL}/{SYMBOL}/{SYMBOL}-trades-{date_str}.zip"
        if download_and_extract(url, f"Trades {date_str}", DATA_DIR):
            trades_downloaded += 1

    # === ИТОГ ===
    print("\n" + "=" * 60)
    print("🎉 ЗАГРУЗКА ЗАВЕРШЕНА!")
    print("=" * 60)
    print(f"✅ Свечей (1m): {klines_downloaded} из {len(dates)} дней")
    print(f"✅ Тиков (trades): {trades_downloaded} из {len(dates)} дней")
    print(f"📁 Данные сохранены в: {os.path.abspath(DATA_DIR)}")
    
    # Показываем размер папки
    total_size = sum(f.stat().st_size for f in os.scandir(DATA_DIR) if f.is_file())
    print(f"💾 Общий размер: {total_size / (1024*1024):.2f} МБ")


if __name__ == "__main__":
    main()