from binance.client import Client
import pandas as pd
from datetime import datetime
import os

# === НАСТРОЙКИ ===
SYMBOL = "SOLUSDT"
DATA_DIR = f"data/history/{SYMBOL}"
os.makedirs(DATA_DIR, exist_ok=True)

# Период: с 13 по 21 сентября 2026
START_DATE = "13 Sep 2026"
END_DATE = "21 Sep 2026"

# === API КЛЮЧИ ===
API_KEY = "1LUSywhGHR1d6B9h9Qh8095dnayDuRI7FM31nWkRigCo3N1JulYAcVfyh7yfVBam"
API_SECRET = "q4IXVti28FNbHyAcouwJCAye4cYAvuHxRbtBQq6BHcx20jQ7oOb8qifz1qe0atxV"
client = Client(API_KEY, API_SECRET)

print(f"🚀 Загрузка агрегированных сделок для {SYMBOL}")
print(f"📅 Период: {START_DATE} — {END_DATE}\n")

start_time = int(datetime.strptime(START_DATE, "%d %b %Y").timestamp() * 1000)
end_time = int(datetime.strptime(END_DATE, "%d %b %Y").timestamp() * 1000)

all_trades = []
current_time = start_time
total_downloaded = 0

print("️  Скачиваю...")

while current_time < end_time:
    try:
        trades = client.get_aggregate_trades(
            symbol=SYMBOL,
            startTime=current_time,
            endTime=end_time,
            limit=1000
        )
        
        if not trades:
            print("   ✅ Завершено")
            break
        
        all_trades.extend(trades)
        total_downloaded += len(trades)
        
        # Двигаем курсор на время последней сделки + 1 мс
        last_time = trades[-1]['T']
        current_time = last_time + 1
        
        # Прогресс
        if total_downloaded % 50000 == 0:
            print(f"   ... скачано {total_downloaded:,} сделок")
            
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        break

# Сохранение
if all_trades:
    trades_df = pd.DataFrame(all_trades)
    trades_df['time'] = pd.to_datetime(trades_df['T'], unit='ms')
    
    trades_file = f"{DATA_DIR}/{SYMBOL}_agg_trades.csv"
    trades_df.to_csv(trades_file, index=False)
    
    print(f"\n✅ Скачано {len(trades_df):,} агрегированных сделок")
    print(f"📁 Сохранено в: {trades_file}")
    
    # Статистика
    file_size = os.path.getsize(trades_file) / (1024*1024)
    print(f"💾 Размер файла: {file_size:.2f} МБ")
    print(f"📊 Первая сделка: {trades_df['time'].min()}")
    print(f"📊 Последняя сделка: {trades_df['time'].max()}")
else:
    print("⚠️  Сделки не скачаны")