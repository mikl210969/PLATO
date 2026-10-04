"""
Скрипт для загрузки недостающих файлов aggTrades с официального архива Binance
"""
import requests
import zipfile
import os
from pathlib import Path

DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
DATES_TO_DOWNLOAD = ["2026-10-02", "2026-10-03"]

print(f"🚀 Загрузка данных aggTrades для {SYMBOL}")
print(f"📁 Целевая папка: {DATA_DIR}\n")

for date_str in DATES_TO_DOWNLOAD:
    filename = f"{SYMBOL}-aggTrades-{date_str}.zip"
    csv_filename = f"{SYMBOL}_aggTrades_{date_str}.csv"
    url = f"https://data.binance.vision/data/spot/daily/aggTrades/{SYMBOL}/{filename}"
    
    zip_path = DATA_DIR / filename
    csv_path = DATA_DIR / csv_filename
    
    if csv_path.exists():
        print(f"✅ {csv_filename} уже существует, пропускаем.")
        continue
        
    print(f"⬇️  Загружаю {filename}...")
    try:
        # Скачиваем zip-архив
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f"📦 Распаковываю {filename}...")
        # Распаковываем CSV
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(DATA_DIR)
            
        # Переименовываем извлеченный файл в наш формат, если нужно
        # Binance обычно называет файл внутри так же, как zip, но с .csv
        extracted_csv = DATA_DIR / f"{SYMBOL}-aggTrades-{date_str}.csv"
        if extracted_csv.exists() and not csv_path.exists():
            extracted_csv.rename(csv_path)
            
        # Удаляем zip для экономии места
        if zip_path.exists():
            zip_path.unlink()
            
        print(f"✅ Успешно сохранено: {csv_filename}")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Ошибка загрузки {date_str}: {e}")
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")

print("\n🏁 Загрузка завершена! Теперь можно запускать финальный тест.")