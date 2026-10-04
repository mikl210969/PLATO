"""
Тест поиска Уровней Смены Тренда (УСТ) на ВСЕХ доступных днях
Алгоритм: Swing High/Low с окном 12 (1 час) и фильтром закрытия свечи.
"""
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
# Прогоняем по всем дням, для которых есть данные
DATES_TO_TEST = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]

# Параметры поиска свингов (проверенные)
SWING_WINDOW = 12          # 12 свечей по 5 мин = 1 час
MIN_DISTANCE_PCT = 0.004   # 0.4% минимальное расстояние для подтверждения пробоя

# ==============================================================================
# 2. ПОИСК УСТ (Уровень Смены Тренда)
# ==============================================================================
def find_ust_levels_for_date(date_str):
    """Находит надежные уровни смены тренда за один день."""
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists():
        print(f"  ⚠️ Файл не найден: {f_depth}")
        return pd.DataFrame()
    
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    
    # Агрегация в 5-минутные свечи
    df_5m = df.set_index('timestamp').resample('5min').agg({
        'mid_price': ['first', 'max', 'min', 'last']
    }).dropna()
    df_5m.columns = ['open', 'high', 'low', 'close']
    df_5m = df_5m.reset_index()
    
    ust_events = []
    current_trend = 'unknown'
    ust_level = None
    last_break_price = None
    
    n = len(df_5m)
    for i in range(SWING_WINDOW, n - SWING_WINDOW):
        row = df_5m.iloc[i]
        close_price = row['close']
        
        # Проверка на Swing High
        if row['high'] == df_5m.iloc[i-SWING_WINDOW:i+SWING_WINDOW+1]['high'].max():
            if current_trend in ['down', 'unknown']:
                current_trend = 'up'
                ust_level = row['high']
                last_break_price = close_price
                
        # Проверка на Swing Low
        elif row['low'] == df_5m.iloc[i-SWING_WINDOW:i+SWING_WINDOW+1]['low'].min():
            if current_trend in ['up', 'unknown']:
                current_trend = 'down'
                ust_level = row['low']
                last_break_price = close_price
                
        # Проверка пробоя УСТ
        if ust_level is not None and current_trend != 'unknown' and last_break_price is not None:
            distance = abs(close_price - last_break_price) / last_break_price
            if distance >= MIN_DISTANCE_PCT:
                if current_trend == 'up' and close_price < ust_level:
                    ust_events.append({'date': date_str, 'time': row['timestamp'], 'level': ust_level, 'type': 'SUPPORT_BREAK'})
                    current_trend = 'down'
                    last_break_price = close_price
                elif current_trend == 'down' and close_price > ust_level:
                    ust_events.append({'date': date_str, 'time': row['timestamp'], 'level': ust_level, 'type': 'RESISTANCE_BREAK'})
                    current_trend = 'up'
                    last_break_price = close_price
                    
    return pd.DataFrame(ust_events)

# ==============================================================================
# 3. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 МАСШТАБНЫЙ ТЕСТ ПОИСКА УСТ")
    print(f"   Символ: {SYMBOL} | Даты: {', '.join(DATES_TO_TEST)}")
    print(f"   Правила: Окно свинга = {SWING_WINDOW} (1 час), Мин. дистанция пробоя = {MIN_DISTANCE_PCT*100}%\n")
    
    all_ust_events = []
    daily_stats = {}
    
    for date_str in tqdm(DATES_TO_TEST, desc="Обработка дней", unit="день"):
        df_ust = find_ust_levels_for_date(date_str)
        all_ust_events.append(df_ust)
        daily_stats[date_str] = len(df_ust)
        
    # Объединяем все результаты
    df_all = pd.concat(all_ust_events, ignore_index=True)
    
    print(f"\n{'='*70}")
    print(f"📊 СВОДНАЯ СТАТИСТИКА ПО ДНЯМ")
    print(f"{'='*70}")
    for date, count in daily_stats.items():
        print(f"  {date}: {count} событий УСТ")
    
    total_events = len(df_all)
    print(f"{'-'*70}")
    print(f"  ВСЕГО СОБЫТИЙ ЗА {len(DATES_TO_TEST)} ДНЯ: {total_events}")
    print(f"  СРЕДНЕЕ КОЛИЧЕСТВО В ДЕНЬ: {total_events / len(DATES_TO_TEST):.1f}")
    print(f"{'='*70}")
    
    if not df_all.empty:
        print(f"\n🔍 ПЕРВЫЕ 20 СОБЫТИЙ (для выборочной проверки на графике):")
        print(f"{'Дата':<12} | {'Время':<20} | {'Уровень':<8} | {'Тип пробоя':<18}")
        print("-" * 65)
        for i, row in df_all.head(20).iterrows():
            print(f"{row['date']:<12} | {str(row['time']):<20} | {row['level']:<8.2f} | {row['type']:<18}")
        
        # Сохраняем полный список для дальнейшего анализа
        output_file = DATA_DIR / "ust_all_days_consolidated.csv"
        df_all.to_csv(output_file, index=False)
        print(f"\n✅ Полный список сохранен: {output_file}")
    else:
        print("\n⚠️ Событий УСТ не найдено. Проверьте параметры или наличие данных.")
        
    print(f"\n🏁 Тест завершен!")