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

# === ИНИЦИАЛИЗАЦИЯ КЛИЕНТА ===
API_KEY = "1LUSywhGHR1d6B9h9Qh8095dnayDuRI7FM31nWkRigCo3N1JulYAcVfyh7yfVBam"
API_SECRET = "q4IXVti28FNbHyAcouwJCAye4cYAvuHxRbtBQq6BHcx20jQ7oOb8qifz1qe0atxV"

if API_KEY and API_SECRET:
    client = Client(API_KEY, API_SECRET)
    HAS_API_KEY = True
    print("🔑 API ключи найдены")
else:
    client = Client()
    HAS_API_KEY = False
    print("⚠️  API ключи не указаны")

print(f"\n🚀 Загрузка данных для {SYMBOL}")
print(f"📅 Период: {START_DATE} — {END_DATE}\n")

# === 1. Свечи (Klines) 1m ===
print("=" * 60)
print("📈 СВЕЧИ (1m Klines)")
print("=" * 60)

klines = client.get_historical_klines(
    symbol=SYMBOL,
    interval=Client.KLINE_INTERVAL_1MINUTE,
    start_str=START_DATE,
    end_str=END_DATE
)

klines_df = pd.DataFrame(klines, columns=[
    'timestamp', 'open', 'high', 'low', 'close', 'volume',
    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
    'taker_buy_quote', 'ignore'
])

klines_df['timestamp'] = pd.to_datetime(klines_df['timestamp'], unit='ms')
klines_df['close_time'] = pd.to_datetime(klines_df['close_time'], unit='ms')

klines_file = f"{DATA_DIR}/{SYMBOL}_1m_klines.csv"
klines_df.to_csv(klines_file, index=False)
print(f"✅ Скачано {len(klines_df)} свечей")
print(f"📁 Сохранено в: {klines_file}")

# === 2. Тики (Trades) ===
print("\n" + "=" * 60)
print(" ТИКИ (Trades)")
print("=" * 60)

if HAS_API_KEY:
    print("🔑 Скачиваю тики с API ключами...")
    
    all_trades = []
    start_time = int(datetime.strptime(START_DATE, "%d %b %Y").timestamp() * 1000)
    end_time = int(datetime.strptime(END_DATE, "%d %b %Y").timestamp() * 1000)
    
    from_id = None
    batch_size = 1000
    total_downloaded = 0
    
    while start_time < end_time:
        try:
            if from_id:
                trades = client.get_historical_trades(
                    symbol=SYMBOL, 
                    limit=batch_size, 
                    fromId=from_id
                )
            else:
                trades = client.get_historical_trades(
                    symbol=SYMBOL, 
                    limit=batch_size, 
                    startTime=start_time
                )
            
            if not trades:
                break
            
            all_trades.extend(trades)
            total_downloaded += len(trades)
            
            from_id = trades[-1]['id'] + 1
            
            last_trade_time = trades[-1]['time']
            if last_trade_time >= end_time:
                break
            
            if total_downloaded % 10000 == 0:
                print(f"   ... скачано {total_downloaded} тиков")
                
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            break
    
    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_df['time'] = pd.to_datetime(trades_df['time'], unit='ms')
        
        trades_df = trades_df[trades_df['time'] >= pd.Timestamp(START_DATE)]
        trades_df = trades_df[trades_df['time'] <= pd.Timestamp(END_DATE)]
        
        trades_file = f"{DATA_DIR}/{SYMBOL}_trades.csv"
        trades_df.to_csv(trades_file, index=False)
        print(f"✅ Скачано {len(trades_df)} тиков")
        print(f" Сохранено в: {trades_file}")
    else:
        print("⚠️  Тики не скачаны")
else:
    print("⚠️  API ключи не указаны — пропускаю скачивание тиков")

# === ИТОГ ===
print("\n" + "=" * 60)
print("🎉 ЗАВЕРШЕНО!")
print("=" * 60)
print(f"📁 Папка: {os.path.abspath(DATA_DIR)}")

files = os.listdir(DATA_DIR)
print(f"📄 Файлов: {len(files)}")
for f in files:
    size = os.path.getsize(os.path.join(DATA_DIR, f))
    print(f"   - {f}: {size / (1024*1024):.2f} МБ")