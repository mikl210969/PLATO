import time
import csv
import os
from datetime import datetime
from binance.client import Client

# === НАСТРОЙКИ ===
SYMBOL = "SOLUSDT"
INTERVAL_SEC = 5
DEPTH_LIMIT = 100
LOG_DIR = r"C:\Users\ongul\YandexDisk\Data"
os.makedirs(LOG_DIR, exist_ok=True)

client = Client()  # публичный доступ, ключи не нужны


def get_depth():
    try:
        return client.get_order_book(symbol=SYMBOL, limit=DEPTH_LIMIT)
    except Exception as e:
        print(f"❌ Ошибка стакана: {type(e).__name__}: {e!r}")
        return None


def build_header():
    header = ['timestamp']
    for i in range(1, DEPTH_LIMIT + 1):
        header.extend([f'bid_p_{i}', f'bid_v_{i}'])
    for i in range(1, DEPTH_LIMIT + 1):
        header.extend([f'ask_p_{i}', f'ask_v_{i}'])
    return header


def open_file(date_str, header):
    path = os.path.join(LOG_DIR, f"{SYMBOL}_depth_{date_str}.csv")
    f = open(path, 'a', newline='', encoding='utf-8')
    writer = csv.writer(f)
    if os.path.getsize(path) == 0:
        writer.writerow(header)
    return path, f, writer


def run_session():
    header = build_header()
    current_date = datetime.now().strftime("%Y-%m-%d")
    path, f, writer = open_file(current_date, header)
    records = 0
    print(f"🚀 Пишу в {os.path.basename(path)}")

    try:
        while True:
            # Ротация файла при смене дня — безопасная
            new_date = datetime.now().strftime("%Y-%m-%d")
            if new_date != current_date:
                try:
                    f.close()
                except Exception:
                    pass
                current_date = new_date
                path, f, writer = open_file(current_date, header)
                records = 0
                print(f"📅 Ротация: создан {os.path.basename(path)}")

            depth = get_depth()
            if depth:
                row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                for bid in depth['bids']:
                    row.extend([bid[0], bid[1]])
                for ask in depth['asks']:
                    row.extend([ask[0], ask[1]])
                writer.writerow(row)
                records += 1

                if records % 12 == 0:  # раз в минуту
                    f.flush()  # 🔥 гарантия записи на диск
                    print(f"✅ {datetime.now().strftime('%H:%M:%S')} строк: {records}")

            time.sleep(INTERVAL_SEC)
    finally:
        try:
            f.close()
        except Exception:
            pass


if __name__ == "__main__":
    # 🔥 ГЛАВНАЯ ЗАЩИТА: бесконечный цикл возобновления
    while True:
        try:
            run_session()
        except KeyboardInterrupt:
            print("⏹️ Остановлено пользователем")
            break
        except Exception as e:
            print(f"❌ Сбой: {type(e).__name__}: {e!r} — перезапуск через 10 сек")
            time.sleep(10)