import os
import requests
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

# === НАСТРОЙКИ (ИСПРАВЛЕН ГОД НА 2024) ===
SYMBOL = "SOLUSDT"
DATA_DIR = f"data/history/{SYMBOL}/depth"
os.makedirs(DATA_DIR, exist_ok=True)

# 7 дней реальных исторических данных (сентябрь 2024)
START_DATE = datetime(2024, 9, 13)
END_DATE = datetime(2024, 9, 20)

# Базовый URL для ежедневных снапшотов стакана (1000ms)
BASE_URL = "https://data.binance.vision/data/spot/daily/depth/{symbol}/{symbol}-depth-{date}.zip"

def download_and_extract(url, filename, dest_dir):
    print(f"⬇️  Скачиваю {filename}...")
    try:
        # Увеличиваем таймаут, так как файлы могут быть большими (100-400 МБ)
        response = requests.get(url, timeout=300)
        if response.status_code == 200:
            with zipfile.ZipFile(BytesIO(response.content)) as z:
                for name in z.namelist():
                    if name.endswith('.csv'):
                        z.extract(name, dest_dir)
                        print(f"   ✅ {name} распакован")
            return True
        else:
            print(f"   ⚠️  Не найдено ({response.status_code})")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

def main():
    print(f"🚀 Загрузка исторического стакана (Depth) для {SYMBOL}")
    print(f"📅 Период: {START_DATE.strftime('%Y-%m-%d')} — {END_DATE.strftime('%Y-%m-%d')}")
    print("⚠️  Внимание: файлы могут быть большими. Общий объем за 8 дней: ~1-3 ГБ.\n")

    dates = []
    current = START_DATE
    while current <= END_DATE:
        dates.append(current)
        current += timedelta(days=1)

    success_count = 0
    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        url = BASE_URL.format(symbol=SYMBOL, date=date_str)
        
        if download_and_extract(url, f"Depth {date_str}", DATA_DIR):
            success_count += 1

    print("\n" + "=" * 60)
    print("🎉 ЗАГРУЗКА ЗАВЕРШЕНА!")
    print("=" * 60)
    print(f"✅ Успешно скачано дней: {success_count} из {len(dates)}")
    print(f"📁 Папка: {os.path.abspath(DATA_DIR)}")
    
    # Подсчет размера
    if os.path.exists(DATA_DIR):
        total_size = sum(f.stat().st_size for f in os.scandir(DATA_DIR) if f.is_file())
        print(f"💾 Общий размер: {total_size / (1024*1024*1024):.2f} ГБ")
    else:
        print("💾 Общий размер: 0.00 ГБ")


if __name__ == "__main__":
    main()