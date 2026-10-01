"""
Скрипт для скачивания ПОЛНЫХ исторических агрегированных сделок (aggTrades) с Binance
Использует пагинацию для обхода лимита в 1000 записей на запрос.
"""
import pandas as pd
from binance.client import Client
from datetime import datetime
import os
import time

# === НАСТРОЙКИ ===
SYMBOL = "SOLUSDT"
DATE_STR = "2026-09-30"
OUTPUT_DIR = r"C:\Users\ongul\YandexDisk\Data"
os.makedirs(OUTPUT_DIR, exist_ok=True)
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"{SYMBOL}_aggTrades_{DATE_STR}.csv")

client = Client()

def download_agg_trades_robust():
    print(f"🚀 Начинаю загрузку ПОЛНЫХ aggTrades для {SYMBOL} за {DATE_STR}...")
    
    start_time = int(datetime.strptime(f"{DATE_STR} 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    end_time = int(datetime.strptime(f"{DATE_STR} 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    
    all_trades = []
    from_id = None
    batch_count = 0
    
    try:
        while True:
            if from_id:
                # Запрашиваем следующие 1000 записей после последнего ID
                trades = client.get_aggregate_trades(symbol=SYMBOL, fromId=from_id, limit=1000)
            else:
                # Первый запрос по времени
                trades = client.get_aggregate_trades(symbol=SYMBOL, startTime=start_time, limit=1000)
            
            if not trades:
                print("⚠️ Больше нет данных для загрузки.")
                break
                
            all_trades.extend(trades)
            batch_count += 1
            
            # Проверяем, достигли ли мы конца дня
            last_trade_time = trades[-1]['T']
            if last_trade_time >= end_time:
                print(f"✅ Достигнут конец дня ({DATE_STR}).")
                break
                
            # Следующий запрос начнется с ID последней сделки + 1
            from_id = trades[-1]['a'] + 1
            
            # Защита от бесконечного цикла (SOLUSDT редко превышает 500к-1М сделок в день)
            if len(all_trades) > 1500000:
                print("⚠️ Достигнут лимит 1.5 млн сделок, остановка для безопасности.")
                break
                
            # Небольшая пауза, чтобы не получить бан от API за спам запросами
            time.sleep(0.1)
            
            if batch_count % 10 == 0:
                print(f"  Загружено пакетов: {batch_count}, всего сделок: {len(all_trades)}...")

        if not all_trades:
            print("❌ Сделок не найдено.")
            return

        print(f"\n✅ Загрузка завершена! Всего получено {len(all_trades)} записей.")
        print("Конвертирую в DataFrame...")
        
        df = pd.DataFrame(all_trades)
        
        df = df.rename(columns={
            'a': 'agg_trade_id',
            'p': 'price',
            'q': 'quantity',
            'f': 'first_trade_id',
            'l': 'last_trade_id',
            'T': 'timestamp',
            'm': 'is_buyer_maker',
            'M': 'is_best_match'
        })
        
        df.to_csv(OUTPUT_FILE, index=False)
        print(f"💾 Успешно сохранено в: {OUTPUT_FILE}")
        print(f"📊 Размер файла: {os.path.getsize(OUTPUT_FILE) / (1024*1024):.2f} МБ")
        
    except Exception as e:
        print(f"❌ Критическая ошибка при загрузке: {e}")

if __name__ == "__main__":
    download_agg_trades_robust()