from binance.client import Client
import pandas as pd
import os

SYMBOL = "BTCUSDT"
DATA_DIR = "data/history/BTCUSDT"
os.makedirs(DATA_DIR, exist_ok=True)

START_DATE = "13 Sep 2026"
END_DATE = "21 Sep 2026"

print(f"🚀 Загрузка свечей BTCUSDT за период {START_DATE} — {END_DATE}...")

client = Client() # Публичные данные, ключи не нужны

klines = client.get_historical_klines(
    symbol=SYMBOL,
    interval=Client.KLINE_INTERVAL_1MINUTE,
    start_str=START_DATE,
    end_str=END_DATE
)

df = pd.DataFrame(klines, columns=[
    'timestamp', 'open', 'high', 'low', 'close', 'volume',
    'close_time', 'quote_volume', 'trades', 'taker_buy_base',
    'taker_buy_quote', 'ignore'
])

df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
file_path = f"{DATA_DIR}/{SYMBOL}_1m_klines.csv"
df.to_csv(file_path, index=False)

print(f"✅ Успешно! Скачано {len(df)} свечей.")
print(f"📁 Сохранено в: {os.path.abspath(file_path)}")