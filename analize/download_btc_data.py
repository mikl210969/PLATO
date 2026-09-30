# analize/download_btc_data.py
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(r"C:\Users\m.ongudushev\YandexDisk\Data")
SYMBOL = "BTCUSDT"
DATES = ["2026-09-28", "2026-09-29"]

def download_klines(symbol, date_str):
    start_time = int(datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    end_time = int(datetime.strptime(f"{date_str} 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": "5m",
        "startTime": start_time,
        "endTime": end_time,
        "limit": 1000
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    df = pd.DataFrame(data, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['datetime'] = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)
    
    # Оставляем только нужные колонки для контекста
    df = df[['timestamp', 'datetime', 'close', 'volume']]
    
    file_path = DATA_DIR / f"{symbol}_5m_{date_str}.csv"
    df.to_csv(file_path, index=False)
    print(f"[OK] Сохранено: {file_path.name} ({len(df)} строк)")

if __name__ == "__main__":
    for date in DATES:
        download_klines(SYMBOL, date)
    print("Готово! Данные BTC загружены.")