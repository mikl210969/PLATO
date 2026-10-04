"""
Сбор полной аналитики вокруг событий УСТ
До/Во время/После: дельта, агрессивные ордера, лимитные стены
"""
import pandas as pd
import numpy as np
from pathlib import Path

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
UST_FILE = DATA_DIR / "ust_all_days_consolidated.csv"

# Временные окна (в минутах)
WINDOW_BEFORE = 10   # Смотрим за 10 минут ДО события
WINDOW_AFTER = 10    # Смотрим за 10 минут ПОСЛЕ события

# Порог "крупного" ордера для ленты
BIG_ORDER_THRESHOLD = 50.0  # SOL

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_ust_events():
    """Загружает события УСТ."""
    df = pd.read_csv(UST_FILE)
    df['time'] = pd.to_datetime(df['time'])
    return df

def load_depth_data(date_str):
    """Загружает стакан."""
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists():
        return None
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['bid_total'] = df[[f'bid_v_{i}' for i in range(1, 21)]].sum(axis=1)
    df['ask_total'] = df[[f'ask_v_{i}' for i in range(1, 21)]].sum(axis=1)
    return df

def load_trades_data(date_str):
    """Загружает ленту сделок."""
    f_trades = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}.csv"
    if not f_trades.exists():
        return None
    df = pd.read_csv(f_trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['is_buy'] = df['is_buyer_maker'] == False
    df['delta'] = np.where(df['is_buy'], df['quantity'], -df['quantity'])
    df['is_big'] = df['quantity'] >= BIG_ORDER_THRESHOLD
    return df

# ==============================================================================
# 3. АНАЛИТИКА ВОКРУГ СОБЫТИЯ
# ==============================================================================
def analyze_event(event_time, df_depth, df_trades, event_type):
    """
    Собирает полную аналитику вокруг события.
    Возвращает dict с метриками до/во время/после.
    """
    # Определяем временные окна
    time_before_start = event_time - pd.Timedelta(minutes=WINDOW_BEFORE)
    time_before_end = event_time
    time_after_start = event_time
    time_after_end = event_time + pd.Timedelta(minutes=WINDOW_AFTER)
    
    # --- ДО СОБЫТИЯ ---
    depth_before = df_depth[(df_depth['timestamp'] >= time_before_start) & 
                            (df_depth['timestamp'] < time_before_end)]
    trades_before = df_trades[(df_trades['timestamp'] >= time_before_start) & 
                              (df_trades['timestamp'] < time_before_end)]
    
    # --- ПОСЛЕ СОБЫТИЯ ---
    depth_after = df_depth[(df_depth['timestamp'] >= time_after_start) & 
                           (df_depth['timestamp'] <= time_after_end)]
    trades_after = df_trades[(df_trades['timestamp'] >= time_after_start) & 
                             (df_trades['timestamp'] <= time_after_end)]
    
    # Рассчитываем метрики
    analytics = {
        'event_time': event_time,
        'event_type': event_type,
        
        # ДО: Дельта и объем
        'delta_before': trades_before['delta'].sum() if not trades_before.empty else 0,
        'big_orders_before': len(trades_before[trades_before['is_big']]) if not trades_before.empty else 0,
        'big_volume_before': trades_before[trades_before['is_big']]['quantity'].sum() if not trades_before.empty else 0,
        
        # ДО: Ликвидность в стакане
        'avg_bid_liquidity_before': depth_before['bid_total'].mean() if not depth_before.empty else 0,
        'avg_ask_liquidity_before': depth_before['ask_total'].mean() if not depth_before.empty else 0,
        
        # ПОСЛЕ: Дельта и объем
        'delta_after': trades_after['delta'].sum() if not trades_after.empty else 0,
        'big_orders_after': len(trades_after[trades_after['is_big']]) if not trades_after.empty else 0,
        'big_volume_after': trades_after[trades_after['is_big']]['quantity'].sum() if not trades_after.empty else 0,
        
        # ПОСЛЕ: Ликвидность в стакане
        'avg_bid_liquidity_after': depth_after['bid_total'].mean() if not depth_after.empty else 0,
        'avg_ask_liquidity_after': depth_after['ask_total'].mean() if not depth_after.empty else 0,
        
        # Изменение ликвидности (после - до)
        'bid_liquidity_change': (depth_after['bid_total'].mean() if not depth_after.empty else 0) - 
                                (depth_before['bid_total'].mean() if not depth_before.empty else 0),
        'ask_liquidity_change': (depth_after['ask_total'].mean() if not depth_after.empty else 0) - 
                                (depth_before['ask_total'].mean() if not depth_before.empty else 0),
    }
    
    return analytics

# ==============================================================================
# 4. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 СБОР ПОЛНОЙ АНАЛИТИКИ ВОКРУГ СОБЫТИЙ УСТ")
    print(f"   Окна: {WINDOW_BEFORE} мин до / {WINDOW_AFTER} мин после\n")
    
    df_ust = load_ust_events()
    all_analytics = []
    
    dates = df_ust['date'].unique()
    
    for date_str in dates:
        print(f"\n[INFO] Обработка {date_str}...")
        df_depth = load_depth_data(date_str)
        df_trades = load_trades_data(date_str)
        
        if df_depth is None or df_trades is None:
            print(f"  ⚠️ Данные не найдены")
            continue
        
        day_events = df_ust[df_ust['date'] == date_str]
        
        for idx, event in day_events.iterrows():
            print(f"   Анализ события: {event['time']} | {event['type']} | Уровень: {event['level']:.2f}")
            
            analytics = analyze_event(event['time'], df_depth, df_trades, event['type'])
            analytics['date'] = date_str
            analytics['ust_level'] = event['level']
            all_analytics.append(analytics)
    
    # Сохраняем результаты
    df_analytics = pd.DataFrame(all_analytics)
    output_file = DATA_DIR / "ust_events_full_analytics.csv"
    df_analytics.to_csv(output_file, index=False)
    
    print(f"\n{'='*80}")
    print(f"✅ АНАЛИТИКА СОБРАНА")
    print(f"{'='*80}")
    print(f"Всего событий проанализировано: {len(df_analytics)}")
    print(f"Файл сохранен: {output_file}")
    
    # Показываем сводную статистику
    print(f"\n📈 СВОДНАЯ СТАТИСТИКА:")
    print(f"  Средняя дельта ДО события: {df_analytics['delta_before'].mean():.2f} SOL")
    print(f"  Средняя дельта ПОСЛЕ события: {df_analytics['delta_after'].mean():.2f} SOL")
    print(f"  Среднее кол-во крупных ордеров ДО: {df_analytics['big_orders_before'].mean():.1f}")
    print(f"  Среднее кол-во крупных ордеров ПОСЛЕ: {df_analytics['big_orders_after'].mean():.1f}")
    print(f"  Изменение.bid ликвидности: {df_analytics['bid_liquidity_change'].mean():.0f} SOL")
    print(f"  Изменение.ask ликвидности: {df_analytics['ask_liquidity_change'].mean():.0f} SOL")
    
    print(f"\n🔍 ПЕРВЫЕ 5 СОБЫТИЙ (детально):")
    for i, row in df_analytics.head(5).iterrows():
        print(f"\n  Событие #{i+1}: {row['event_time']}")
        print(f"  Тип: {row['event_type']} | Уровень: {row['ust_level']:.2f}")
        print(f"  ДО: Дельта={row['delta_before']:.1f}, Крупных ордеров={row['big_orders_before']}, Bid ликвидность={row['avg_bid_liquidity_before']:.0f}")
        print(f"  ПОСЛЕ: Дельта={row['delta_after']:.1f}, Крупных ордеров={row['big_orders_after']}, Bid ликвидность={row['avg_bid_liquidity_after']:.0f}")
        print(f"  Изменение ликвидности: Bid={row['bid_liquidity_change']:.0f}, Ask={row['ask_liquidity_change']:.0f}")
    
    print(f"\n🏁 Готово! Теперь можно анализировать паттерны вокруг каждого события.")