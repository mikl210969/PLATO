"""
PLATO Sniper V6: Финальный тест Конфлюэнса (УСТ + Реальная Стена) на всех 4 днях.
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
UST_FILE = DATA_DIR / "ust_all_days_consolidated.csv"

# Параметры "Реальной Стены"
VOLUME_MULTIPLIER = 3.0       # Стена должна быть в 3 раза жирнее среднего топ-20
PERSISTENCE_N = 5             # Смотрим последние 5 обновлений (25 секунд)
PERSISTENCE_K = 3             # Стена должна быть видна минимум в 3 из 5 обновлений
PRICE_DRIFT_TICKS = 5         # Допуск на сдвиг цены: 5 тиков (0.05 для SOL)
CONFLUENCE_RADIUS_PCT = 0.005 # Стена должна быть в радиусе 0.5% от уровня УСТ
RESET_DISTANCE_PCT = 0.005    # 0.5% - цена должна отойти на это расстояние перед новым сетапом

# ==============================================================================
# 2. ПОИСК СТЕН ВОКРУГ УСТ
# ==============================================================================
def find_walls_for_usts(date_str, ust_events, df_depth):
    """Ищет аномальные стены вокруг конкретных событий УСТ за один день."""
    confluence_events = []
    active_setups = {} # Для защиты от дублирования
    
    # Предварительный расчет средних объемов для всего дня (ускоряет работу)
    bid_cols = [f'bid_v_{i}' for i in range(1, 21)]
    ask_cols = [f'ask_v_{i}' for i in range(1, 21)]
    
    # Если файлов мало, можно считать сразу. Если много - считаем по мере необходимости.
    # Для 80МБ файла это займет пару секунд.
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

    # Проходим по каждому УСТ для этого дня
    for idx, ust_row in tqdm(ust_events.iterrows(), desc=f"  Анализ УСТ ({date_str})", leave=False):
        ust_time = ust_row['time']
        ust_level = ust_row['level']
        ust_type = ust_row['type']
        
        # Окно ± 30 минут
        time_mask = (df_depth['timestamp'] >= (ust_time - pd.Timedelta(minutes=30))) & \
                    (df_depth['timestamp'] <= (ust_time + pd.Timedelta(minutes=30)))
        window_df = df_depth[time_mask].copy()
        
        if window_df.empty:
            continue
            
        # Разбиваем на блоки по 5 обновлений (25 секунд)
        window_df['local_window_id'] = np.arange(len(window_df)) // PERSISTENCE_N
        
        for w_id, group in window_df.groupby('local_window_id'):
            if len(group) < PERSISTENCE_K:
                continue
                
            for side in ['bid', 'ask']:
                is_wall, wall_price, wall_vol = check_wall_anomaly(group, side)
                
                if is_wall and wall_price is not None:
                    distance_pct = abs(wall_price - ust_level) / ust_level
                    if distance_pct <= CONFLUENCE_RADIUS_PCT:
                        
                        # Проверка на дубликаты (Reset Condition)
                        setup_key = f"{ust_level:.2f}_{side}"
                        
                        if setup_key in active_setups:
                            last_setup = active_setups[setup_key]
                            price_move = abs(wall_price - last_setup['last_price']) / last_setup['last_price']
                            if price_move < RESET_DISTANCE_PCT:
                                continue
                        
                        active_setups[setup_key] = {
                            'last_price': wall_price,
                            'last_time': group['timestamp'].max()
                        }
                        
                        confluence_events.append({
                            'date': date_str,
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
                        
    return confluence_events

# ==============================================================================
# 3. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ФИНАЛЬНЫЙ ТЕСТ SNIPER V6: Конфлюэнс на всех 4 днях")
    
    if not UST_FILE.exists():
        print(f"❌ Файл {UST_FILE} не найден. Сначала запустите test_ust_all_days.py")
        exit()
        
    df_ust_all = pd.read_csv(UST_FILE)
    df_ust_all['time'] = pd.to_datetime(df_ust_all['time'])
    
    all_confluences = []
    dates = df_ust_all['date'].unique()
    
    for date_str in dates:
        print(f"\n[1/3] Обработка дня: {date_str}")
        ust_events = df_ust_all[df_ust_all['date'] == date_str]
        
        print(f"[2/3] Загрузка стакана (depth) для {date_str}...")
        f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
        if not f_depth.exists():
            print(f"  ⚠️ Файл стакана не найден: {f_depth}")
            continue
            
        df_depth = pd.read_csv(f_depth)
        df_depth['timestamp'] = pd.to_datetime(df_depth['timestamp'])
        
        print(f"[3/3] Поиск стен вокруг {len(ust_events)} уровней УСТ...")
        confluences = find_walls_for_usts(date_str, ust_events, df_depth)
        all_confluences.extend(confluences)
        
        # Освобождаем память
        del df_depth
        
    # Итоговая сводка
    df_final = pd.DataFrame(all_confluences)
    
    print(f"\n{'='*80}")
    print(f"🏆 ИТОГОВЫЕ РЕЗУЛЬТАТЫ ЗА 4 ДНЯ")
    print(f"{'='*80}")
    print(f"Всего проверено уровней УСТ: {len(df_ust_all)}")
    print(f"Найдено идеальных конфлюэнсов: {len(df_final)}")
    print(f"Среднее количество сетапов в день: {len(df_final) / len(dates):.1f}")
    print(f"{'='*80}")
    
    if not df_final.empty:
        print(f"\n ВСЕ НАЙДЕННЫЕ СЕТАПЫ:")
        print(f"{'Дата':<12} | {'Время УСТ':<20} | {'УСТ':<7} | {'Тип':<18} | {'Время Стены':<20} | {'Ст':<4} | {'Цена':<7} | {'Объем':<8} | {'Дист':<6}")
        print("-" * 120)
        
        for i, row in df_final.iterrows():
            print(f"{row['date']:<12} | {str(row['ust_time']):<20} | {row['ust_level']:<7.2f} | {row['ust_type']:<18} | "
                  f"{str(row['wall_time']):<20} | {row['wall_side']:<4} | {row['wall_price']:<7.2f} | "
                  f"{row['wall_volume']:<8.0f} | {row['distance_pct']:<6.3f}%")
        
        output_file = DATA_DIR / "sniper_v6_final_confluences.csv"
        df_final.to_csv(output_file, index=False)
        print(f"\n✅ Полный список сохранен: {output_file}")
    else:
        print("\n️ Конфлюэнсов не найдено. Параметры слишком строгие.")
        
    print(f"\n🏁 Тест завершен!")