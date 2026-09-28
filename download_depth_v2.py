import os
import requests
import zipfile
from io import BytesIO
from datetime import datetime, timedelta

# === НАСТРОЙКИ ===
SYMBOL = "SOLUSDT"
DATA_DIR = f"data/history/{SYMBOL}/depth"
os.makedirs(DATA_DIR, exist_ok=True)

# 7 дней (сентябрь 2024)
START_DATE = datetime(2024, 9, 13)
END_DATE = datetime(2024, 9, 20)

# Разные варианты URL для проверки
URL_PATTERNS = [
    # Daily варианты
    "https://data.binance.vision/data/spot/daily/depth/{symbol}/{symbol}-depth-{date}.zip",
    "https://data.binance.vision/data/spot/daily/depth/{symbol}/{date}/{symbol}-depth-{date}.zip",
    # Monthly вариант
    "https://data.binance.vision/data/spot/monthly/depth/{symbol}/{symbol}-depth-{month}.zip",
]

def check_url(url):
    """Проверяет доступность URL."""
    try:
        response = requests.head(url, timeout=10, allow_redirects=True)
        return response.status_code == 200
    except:
        return False

def download_and_extract(url, filename, dest_dir):
    print(f"️  Скачиваю {filename}...")
    try:
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
    print(f"📅 Период: {START_DATE.strftime('%Y-%m-%d')} — {END_DATE.strftime('%Y-%m-%d')}\n")

    # Сначала проверяем, какие URL вообще работают
    print("🔍 Проверяю доступность данных на Binance Vision...")
    working_pattern = None
    
    for pattern in URL_PATTERNS:
        # Тестируем на первой дате
        test_date = START_DATE.strftime("%Y-%m-%d")
        test_month = START_DATE.strftime("%Y-%m")
        test_url = pattern.format(symbol=SYMBOL, date=test_date, month=test_month)
        
        if check_url(test_url):
            print(f"   ✅ Найден рабочий URL: {test_url}")
            working_pattern = pattern
            break
        else:
            print(f"   ❌ Не работает: {test_url}")
    
    if not working_pattern:
        print("\n⚠️  Binance Vision не предоставляет depth данные в ожидаемом формате.")
        print("\n📋 АЛЬТЕРНАТИВНЫЕ РЕШЕНИЯ:")
        print("1. Использовать Binance API для получения текущего стакана (но не исторического)")
        print("2. Скачать данные через сторонние сервисы (например, CryptoDataDownload.com)")
        print("3. Использовать данные из Binance Futures (там могут быть depth archives)")
        print("4. Записывать стакан самостоятельно в реальном времени для будущих тестов")
        return

    # Скачиваем данные
    dates = []
    current = START_DATE
    while current <= END_DATE:
        dates.append(current)
        current += timedelta(days=1)

    success_count = 0
    for date in dates:
        date_str = date.strftime("%Y-%m-%d")
        month_str = date.strftime("%Y-%m")
        url = working_pattern.format(symbol=SYMBOL, date=date_str, month=month_str)
        
        if download_and_extract(url, f"Depth {date_str}", DATA_DIR):
            success_count += 1

    print("\n" + "=" * 60)
    print(" ЗАГРУЗКА ЗАВЕРШЕНА!")
    print("=" * 60)
    print(f"✅ Успешно скачано дней: {success_count} из {len(dates)}")
    print(f"📁 Папка: {os.path.abspath(DATA_DIR)}")
    
    if os.path.exists(DATA_DIR):
        total_size = sum(f.stat().st_size for f in os.scandir(DATA_DIR) if f.is_file())
        print(f"💾 Общий размер: {total_size / (1024*1024*1024):.2f} ГБ")

if __name__ == "__main__":
    main()