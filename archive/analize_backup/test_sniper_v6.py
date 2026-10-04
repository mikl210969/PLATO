"""
PLATO Sniper V6 Backtest: Конфлюэнс УСТ + Реальная Стена (Order Book Anomaly)
Проверка гипотезы: Ищем относительные аномалии объема в стакане, устойчивые во времени, рядом с УСТ.
"""
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data") # 🔥 Путь домашней машины
SYMBOL = "SOLUSDT"
DATE = "2026-09-28"

# Параметры УСТ (из предыдущего успешного теста)
SWING_WINDOW = 12
MIN_DISTANCE_PCT = 0.004

# Параметры "Реальной Стены" в стакане
VOLUME_MULTIPLIER = 3.5       # Стена должна быть в 3.5 раза жирнее среднего топ-20
PERSISTENCE_N = 5             # Смотрим последние 5 обновлений (25 секунд)
PERSISTENCE_K = 3             # Стена должна быть видна минимум в 3 из 5 обновлений
PRICE_DRIFT_TICKS = 2         # Допуск на сдвиг цены: 2 тика (для SOL = 0.02)
CONFLUENCE_RADIUS_PCT = 0.002 # Стена должна быть в радиусе 0.2% от уровня УСТ

# ==============================================================================
# 2. ПОИСК УСТ (Уровень Смены Тренда)
# ==============================================================================
def find_ust_levels(date_str):
    """Находит надежные уровни смены тренда за день."""
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists():
        raise FileNotFoundError(f"Файл не найден: {f_depth}")
    
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
                    ust_events.append({'time': row['timestamp'], 'level': ust_level, 'type': 'SUPPORT_BREAK'})
                    current_trend = 'down'
                    last_break_price = close_price
                elif current_trend == 'down' and close_price > ust_level:
                    ust_events.append({'time': row['timestamp'], 'level': ust_level, 'type': 'RESISTANCE_BREAK'})
                    current_trend = 'up'
                    last_break_price = close_price
                    
    return df, pd.DataFrame(ust_events)

# ==============================================================================
# 3. ПОИСК РЕАЛЬНЫХ СТЕН В СТАКАНЕ
# ==============================================================================
def find_real_walls_around_ust(df_depth, ust_events):
    """Ищет аномальные, устойчивые уровни в стакане рядом с УСТ (с защитой от дублирования)."""
    print("[INFO] Поиск реальных стен в стакане вокруг уровней УСТ...")
    
    confluence_events = []
    
    df_depth = df_depth.sort_values('timestamp').reset_index(drop=True)
    df_depth['window_id'] = np.arange(len(df_depth)) // PERSISTENCE_N
    
    bid_cols = [f'bid_v_{i}' for i in range(1, 21)]
    ask_cols = [f'ask_v_{i}' for i in range(1, 21)]
    
    print("[INFO] Расчет средних объемов стакана...")
    df_depth['avg_bid_vol'] = df_depth[bid_cols].mean(axis=1)
    df_depth['avg_ask_vol'] = df_depth[ask_cols].mean(axis=1)
    
    def check_wall_anomaly(window_group, side):
        price_cols = [f'{side}_p_{i}' for i in range(1, 21)]
        vol_cols = [f'{side}_v_{i}' for i in range(1, 21)]
        avg_vol_col = f'avg_{side}_vol'
        
        for level_idx in range(20):
            price_col = price_cols[level_idx]
            vol_col = vol_cols[level_idx]
            
            anomaly_mask = window_group[vol_col] >= (window_group[avg_vol_col] * VOLUME_MULTIPLIER)
            anomaly_rows = window_group[anomaly_mask]
            
            if len(anomaly_rows) >= PERSISTENCE_K:
                price_drift = anomaly_rows[price_col].max() - anomaly_rows[price_col].min()
                if price_drift <= (PRICE_DRIFT_TICKS * 0.01):
                    wall_price = float(anomaly_rows[price_col].mean())
                    wall_vol = float(anomaly_rows[vol_col].mean())
                    return True, wall_price, wall_vol
        return False, None, None

    # 🔥 СЛОВАРЬ ДЛЯ ОТСЛЕЖИВАНИЯ АКТИВНЫХ СЕТАПОВ
    # Ключ: "Уровень_УСТ", Значение: {'last_price': цена стены, 'last_time': время}
    active_setups = {}
    RESET_DISTANCE_PCT = 0.005  # 0.5% - цена должна отойти на это расстояние перед новым сетапом

    for idx, ust_row in ust_events.iterrows():
        ust_time = ust_row['time']
        ust_level = ust_row['level']
        ust_type = ust_row['type']
        
        time_mask = (df_depth['timestamp'] >= (ust_time - pd.Timedelta(minutes=30))) & \
                    (df_depth['timestamp'] <= (ust_time + pd.Timedelta(minutes=30)))
        window_df = df_depth[time_mask]
        
        if window_df.empty:
            continue
            
        for w_id, group in window_df.groupby('window_id'):
            if len(group) < PERSISTENCE_K:
                continue
                
            for side in ['bid', 'ask']:
                is_wall, wall_price, wall_vol = check_wall_anomaly(group, side)
                
                if is_wall and wall_price is not None:
                    distance_pct = abs(wall_price - ust_level) / ust_level
                    if distance_pct <= CONFLUENCE_RADIUS_PCT:
                        
                        # 🔥 ПРОВЕРКА: Это новый сетап или дубликат?
                        setup_key = f"{ust_level:.2f}_{side}"
                        
                        if setup_key in active_setups:
                            last_setup = active_setups[setup_key]
                            # Проверяем, ушла ли цена достаточно далеко от последнего сетапа
                            price_move = abs(wall_price - last_setup['last_price']) / last_setup['last_price']
                            
                            if price_move < RESET_DISTANCE_PCT:
                                # Цена не ушла достаточно далеко — это тот же сетап, пропускаем
                                continue
                        
                        # Это новый сетап (или цена ушла достаточно далеко)
                        active_setups[setup_key] = {
                            'last_price': wall_price,
                            'last_time': group['timestamp'].max()
                        }
                        
                        confluence_events.append({
                            'ust_time': ust_time,
                            'ust_level': ust_level,
                            'ust_type': ust_type,
                            'wall_time': group['timestamp'].max(),
                            'wall_side': side,
                            'wall_price': round(wall_price, 2),
                            'wall_volume': round(wall_vol, 2),
                            'distance_pct': round(distance_pct * 100, 3)
                        })
                        break
    return pd.DataFrame(confluence_events)

# ==============================================================================
# 4. ЗАПУСК И АНАЛИЗ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ТЕСТ SNIPER V6: Конфлюэнс УСТ + Реальная Стена")
    print(f"   Символ: {SYMBOL} | Дата: {DATE}")
    print(f"   Правила: Объем > {VOLUME_MULTIPLIER}x от среднего, Устойчивость {PERSISTENCE_K}/{PERSISTENCE_N}, Дрейф <= {PRICE_DRIFT_TICKS} тиков\n")
    
    try:
        # 1. Находим УСТ
        print("[1/3] Поиск уровней смены тренда (УСТ)...")
        df_depth_full, df_ust = find_ust_levels(DATE)
        print(f"    Найдено {len(df_ust)} событий УСТ за день.")
        
        if len(df_ust) == 0:
            print("    Нет событий УСТ для анализа.")
        else:
            # 2. Ищем стены вокруг УСТ
            print("[2/3] Анализ стакана вокруг уровней УСТ...")
            df_confluence = find_real_walls_around_ust(df_depth_full, df_ust)
            
            # 3. Вывод результатов
            print("\n[3/3] Результаты:")
            print(f"{'='*80}")
            print(f"ВСЕГО НАЙДЕНО КОНФЛЮЭНСОВ (УСТ + Реальная Стена): {len(df_confluence)}")
            print(f"{'='*80}")
            
            if not df_confluence.empty:
                # Показываем первые 15 событий для ручной проверки на графике
                print("\n🔍 ПЕРВЫЕ 15 СОБЫТИЙ (для проверки на графике Binance):")
                print(f"{'Время УСТ':<20} | {'УСТ':<7} | {'Тип':<18} | {'Время Стены':<20} | {'Сторона':<7} | {'Цена Стены':<10} | {'Объем':<10} | {'Дистанция':<10}")
                print("-" * 120)
                
                for i, row in df_confluence.head(15).iterrows():
                    print(f"{str(row['ust_time']):<20} | {row['ust_level']:<7.2f} | {row['ust_type']:<18} | "
                          f"{str(row['wall_time']):<20} | {row['wall_side']:<7} | {row['wall_price']:<10.2f} | "
                          f"{row['wall_volume']:<10.0f} | {row['distance_pct']:<10.3f}%")
                
                # Сохраняем для детального разбора
                output_file = DATA_DIR / f"sniper_v6_confluence_{DATE}.csv"
                df_confluence.to_csv(output_file, index=False)
                print(f"\n✅ Полные результаты сохранены: {output_file}")
            else:
                print("\n⚠️ Конфлюэнсов не найдено. Возможно, параметры слишком строгие для этого дня.")
                
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()