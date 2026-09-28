import pandas as pd
import numpy as np
from collections import defaultdict

# === НАСТРОЙКИ ===
TICKS_FILE = "data/history/SOLUSDT/SOLUSDT_agg_trades.csv"
KLINE_FILE = "data/history/SOLUSDT/SOLUSDT_1m_klines.csv"

# Параметры Volume Profile
PRICE_BIN_SIZE = 0.10  # Размер ценового бина в долларах (для SOL ~$110 это ~0.09%)

# Расстояния для тестирования
DISTANCES_TO_TEST = [0.003, 0.005, 0.008]  # 0.3%, 0.5%, 0.8%

# Параметры отскока
LOOKAHEAD_MINUTES = 30       # Смотрим следующие 30 минут
MIN_REVERSAL_PCT = 0.002     # Минимальный разворот 0.2% чтобы считать отскоком успешным


def build_volume_profile(df_day):
    """Строит Volume Profile за один день."""
    df_day = df_day.copy()
    df_day['price_bin'] = (df_day['price'] // PRICE_BIN_SIZE) * PRICE_BIN_SIZE
    profile = df_day.groupby('price_bin')['quantity'].sum()
    return profile


def find_hvns(profile, top_n=3):
    """Находит топ-N HVN (уровни с максимальным объемом)."""
    if profile.empty:
        return []
    top_levels = profile.nlargest(top_n)
    return top_levels.index.tolist()


def check_bounce(kline_df, hvn_price, entry_time, distance_pct):
    """Проверяет, был ли отскок от HVN."""
    upper_zone = hvn_price * (1 + distance_pct)
    lower_zone = hvn_price * (1 - distance_pct)
    
    mask = (kline_df['timestamp'] > entry_time) & \
           (kline_df['timestamp'] <= entry_time + pd.Timedelta(minutes=LOOKAHEAD_MINUTES))
    future_candles = kline_df.loc[mask]
    
    if future_candles.empty:
        return 'inconclusive'
    
    touched = False
    for _, candle in future_candles.iterrows():
        if candle['low'] <= upper_zone and candle['high'] >= lower_zone:
            touched = True
            break
    
    if not touched:
        return 'no_touch'
    
    pre_mask = kline_df['timestamp'] <= entry_time
    pre_candles = kline_df.loc[pre_mask]
    if pre_candles.empty:
        return 'inconclusive'
    
    price_before = pre_candles.iloc[-1]['close']
    
    if price_before > hvn_price:
        approach = 'from_above'
    else:
        approach = 'from_below'
    
    min_price_after = future_candles['low'].min()
    max_price_after = future_candles['high'].max()
    last_price = future_candles.iloc[-1]['close']
    
    if approach == 'from_above':
        if last_price > hvn_price * (1 + MIN_REVERSAL_PCT):
            return 'bounce'
        elif min_price_after < hvn_price * (1 - distance_pct * 0.5):
            return 'break'
    else:
        if last_price < hvn_price * (1 - MIN_REVERSAL_PCT):
            return 'bounce'
        elif max_price_after > hvn_price * (1 + distance_pct * 0.5):
            return 'break'
    
    return 'inconclusive'


def run_analysis():
    print("🔬 HVN Analyzer: Научное обоснование price_distance_pct")
    print("=" * 70)
    
    # 1. Загрузка данных
    print(f"📂 Загрузка тиков из {TICKS_FILE}...")
    ticks_df = pd.read_csv(TICKS_FILE)
    ticks_df['time'] = pd.to_datetime(ticks_df['T'], unit='ms')
    
    # 🔥 ИСПРАВЛЕНИЕ: Переименовываем колонки Binance ('p' -> 'price', 'q' -> 'quantity')
    ticks_df = ticks_df.rename(columns={'p': 'price', 'q': 'quantity'})
    
    print(f"📂 Загрузка свечей из {KLINE_FILE}...")
    kline_df = pd.read_csv(KLINE_FILE)
    kline_df['timestamp'] = pd.to_datetime(kline_df['timestamp'])
    
    print(f"✅ Загружено {len(ticks_df):,} тиков и {len(kline_df)} свечей.\n")
    
    # 2. Группируем по дням
    ticks_df['date'] = ticks_df['time'].dt.date
    kline_df['date'] = kline_df['timestamp'].dt.date
    
    unique_dates = sorted(ticks_df['date'].unique())
    print(f"📅 Анализируем {len(unique_dates)} дней: {unique_dates[0]} — {unique_dates[-1]}\n")
    
    # 3. Статистика по расстояниям
    stats = {d: {'bounce': 0, 'break': 0, 'no_touch': 0, 'inconclusive': 0, 'total': 0} 
             for d in DISTANCES_TO_TEST}
    
    # 4. Основной цикл по дням
    for date in unique_dates:
        day_ticks = ticks_df[ticks_df['date'] == date]
        day_klines = kline_df[kline_df['date'] == date]
        
        if day_ticks.empty or day_klines.empty:
            continue
        
        profile = build_volume_profile(day_ticks)
        hvns = find_hvns(profile, top_n=3)
        
        if not hvns:
            continue
        
        for hvn_price in hvns:
            nearby_mask = (day_klines['close'] > hvn_price * 0.99) & \
                          (day_klines['close'] < hvn_price * 1.01)
            nearby_candles = day_klines.loc[nearby_mask]
            
            if nearby_candles.empty:
                continue
            
            entry_time = nearby_candles.iloc[0]['timestamp']
            
            for dist in DISTANCES_TO_TEST:
                result = check_bounce(day_klines, hvn_price, entry_time, dist)
                stats[dist][result] += 1
                stats[dist]['total'] += 1
    
    # 5. Вывод результатов
    print("=" * 70)
    print("📊 РЕЗУЛЬТАТЫ АНАЛИЗА HVN")
    print("=" * 70)
    print(f"{'Расстояние':<15} {'Всего тестов':<15} {'Отскок':<10} {'Пробой':<10} {'Винрейт':<10}")
    print("-" * 70)
    
    best_distance = None
    best_winrate = 0
    
    for dist in DISTANCES_TO_TEST:
        s = stats[dist]
        decisive = s['bounce'] + s['break']
        winrate = (s['bounce'] / decisive * 100) if decisive > 0 else 0
        
        dist_pct = dist * 100
        print(f"{dist_pct:<14.1f}% {s['total']:<15} {s['bounce']:<10} {s['break']:<10} {winrate:<9.1f}%")
        
        if winrate > best_winrate and decisive >= 10:
            best_winrate = winrate
            best_distance = dist_pct
    
    print("-" * 70)
    
    if best_distance:
        print(f"🏆 ОПТИМАЛЬНОЕ РАССТОЯНИЕ: {best_distance}% (винрейт {best_winrate:.1f}%)")
        print(f"💡 РЕКОМЕНДАЦИЯ: Установить price_distance_pct = {best_distance/100} в strategies.json")
    else:
        print("⚠️  Недостаточно данных для уверенной рекомендации")
    
    print("=" * 70)
    print("\n📝 ПОЯСНЕНИЕ:")
    print("  • Отскок: цена коснулась зоны HVN и развернулась на ≥0.2%")
    print("  • Пробой: цена пробила HVN и ушла дальше")
    print("  • Винрейт = Отскок / (Отскок + Пробой)")


if __name__ == "__main__":
    run_analysis()