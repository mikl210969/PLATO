"""
Тест поиска Уровня Смены Тренда (УСТ) на 5-минутном графике
Алгоритм: Swing High/Low с настраиваемым окном
"""
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
# 🔥 Укажи правильный путь (домашняя машина)
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
DATE = "2026-09-28"  # Начнем с одного дня для отладки

# Параметры поиска свингов
SWING_WINDOW = 5  # Сколько баров слева/справа проверять (для 5мин графика оптимально 3-7)

# ==============================================================================
# 2. ЗАГРУЗКА И АГРЕГАЦИЯ ДАННЫХ
# ==============================================================================
def load_and_aggregate(date_str):
    """Загружает 5-секундные данные и агрегирует в 5-минутные свечи."""
    print(f"[INFO] Загрузка данных за {date_str}...")
    
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists():
        raise FileNotFoundError(f"Файл не найден: {f_depth}")
    
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Берем mid_price из лучшего бида/аска
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    
    # Агрегация в 5-минутные свечи
    df_5m = df.set_index('timestamp').resample('5min').agg({
        'mid_price': ['first', 'max', 'min', 'last', 'count']
    }).dropna()
    
    df_5m.columns = ['open', 'high', 'low', 'close', 'bars_count']
    df_5m = df_5m.reset_index()
    
    print(f"[INFO] Получено {len(df_5m)} 5-минутных свечей")
    return df_5m

# ==============================================================================
# 3. ПОИСК SWING HIGH/LOW (УСТ)
# ==============================================================================
def find_swings(df, window=5):
    """
    Находит Swing High и Swing Low.
    Swing High: high[i] - максимум среди high[i-window:i+window+1]
    Swing Low: low[i] - минимум среди low[i-window:i+window+1]
    """
    print(f"[INFO] Поиск свингов с окном = {window}...")
    
    df = df.copy()
    df['swing_high'] = np.nan
    df['swing_low'] = np.nan
    df['ust_level'] = np.nan  # Уровень смены тренда
    df['ust_type'] = ''       # 'support' (бывший swing low) или 'resistance' (бывший swing high)
    
    n = len(df)
    swing_highs = []
    swing_lows = []
    
    # Ищем свинги
    for i in tqdm(range(window, n - window), desc="Поиск свингов", unit="свеча"):
        # Swing High
        if df.iloc[i]['high'] == df.iloc[i-window:i+window+1]['high'].max():
            df.iloc[i, df.columns.get_loc('swing_high')] = df.iloc[i]['high']
            swing_highs.append({'idx': i, 'price': df.iloc[i]['high'], 'time': df.iloc[i]['timestamp']})
        
        # Swing Low
        if df.iloc[i]['low'] == df.iloc[i-window:i+window+1]['low'].min():
            df.iloc[i, df.columns.get_loc('swing_low')] = df.iloc[i]['low']
            swing_lows.append({'idx': i, 'price': df.iloc[i]['low'], 'time': df.iloc[i]['timestamp']})
    
    print(f"[INFO] Найдено {len(swing_highs)} Swing High и {len(swing_lows)} Swing Low")
    
    return df, swing_highs, swing_lows

# ==============================================================================
# 4. ОПРЕДЕЛЕНИЕ УСТ И ПРОБОЕВ
# ==============================================================================
def find_ust_breaks(df, swing_highs, swing_lows):
    """
    Определяет УСТ и моменты его пробоя (Исправленная логика V2).
    """
    print("[INFO] Анализ пробоев УСТ (с фильтром закрытия свечи и расстояния)...")
    
    breaks = []
    current_trend = 'unknown'
    ust_level = None
    ust_type = None
    
    # Для фильтра минимального расстояния
    last_break_price = None
    MIN_DISTANCE_PCT = 0.003 # 0.3% минимальное движение для нового уровня
    
    sh_idx = {s['idx']: s['price'] for s in swing_highs}
    sl_idx = {s['idx']: s['price'] for s in swing_lows}
    
    n = len(df)
    for i in range(n):
        row = df.iloc[i]
        close_price = row['close']
        
        # 1. Обновляем тренд при формировании нового свинга
        if i in sh_idx:
            if current_trend in ['down', 'unknown']:
                current_trend = 'up'
                ust_level = sh_idx[i]
                ust_type = 'support'
                last_break_price = close_price
                
        elif i in sl_idx:
            if current_trend in ['up', 'unknown']:
                current_trend = 'down'
                ust_level = sl_idx[i]
                ust_type = 'resistance'
                last_break_price = close_price
                
        # 2. Проверяем ПРОБОЙ текущего УСТ (Только по ЗАКРЫТИЮ свечи и с учетом дистанции)
        if ust_level is not None and current_trend != 'unknown' and last_break_price is not None:
            distance = abs(close_price - last_break_price) / last_break_price
            
            if distance < MIN_DISTANCE_PCT:
                continue # Игнорируем шум, цена не ушла достаточно далеко
                
            if current_trend == 'up' and close_price < ust_level:
                # Свеча ЗАКРЫЛАСЬ ниже поддержки
                breaks.append({
                    'time': row['timestamp'],
                    'type': 'UST_BREAK_DOWN',
                    'level': ust_level,
                    'price': close_price,
                    'description': f"Свеча закрылась ниже УСТ (поддержка) {ust_level:.2f}"
                })
                current_trend = 'down'
                ust_type = 'resistance'
                last_break_price = close_price
                
            elif current_trend == 'down' and close_price > ust_level:
                # Свеча ЗАКРЫЛАСЬ выше сопротивления
                breaks.append({
                    'time': row['timestamp'],
                    'type': 'UST_BREAK_UP',
                    'level': ust_level,
                    'price': close_price,
                    'description': f"Свеча закрылась выше УСТ (сопротивление) {ust_level:.2f}"
                })
                current_trend = 'up'
                ust_type = 'support'
                last_break_price = close_price
                
    return breaks
# ==============================================================================
# 5. АНАЛИЗ И ВЫВОД РЕЗУЛЬТАТОВ
# ==============================================================================
def analyze_results(breaks, df):
    """Выводит статистику по пробоям УСТ."""
    print(f"\n{'='*70}")
    print(f"📊 РЕЗУЛЬТАТЫ АНАЛИЗА УСТ за {DATE}")
    print(f"{'='*70}")
    
    if not breaks:
        print("️ Пробоев УСТ не найдено")
        return
    
    # Группировка по типу
    df_breaks = pd.DataFrame(breaks)
    
    print(f"\nВсего событий смены/пробоя тренда: {len(breaks)}")
    print(f"\nРаспределение по типам:")
    for t in df_breaks['type'].unique():
        count = len(df_breaks[df_breaks['type'] == t])
        print(f"  {t}: {count}")
    
    # Первые 10 событий для ручной проверки
    print(f"\n{'='*70}")
    print("🔍 ПЕРВЫЕ 15 СОБЫТИЙ (для ручной проверки):")
    print(f"{'='*70}")
    for i, b in enumerate(breaks[:15]):
        print(f"{i+1:3}. [{b['time']}] {b['type']}")
        print(f"     Уровень: {b['level']:.2f} | Цена закрытия: {b['price']:.2f}")
        print(f"     {b['description']}")
        print()
    
    # Сохраняем в CSV для дальнейшего анализа
    output_file = DATA_DIR / f"ust_breaks_{DATE}.csv"
    df_breaks.to_csv(output_file, index=False)
    print(f" Результаты сохранены: {output_file}")

# ==============================================================================
# 6. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ТЕСТ ПОИСКА УСТ (Уровень Смены Тренда)")
    print(f"   Символ: {SYMBOL} | Дата: {DATE} | Окно свинга: {SWING_WINDOW}\n")
    
    try:
        # 1. Загрузка и агрегация
        df_5m = load_and_aggregate(DATE)
        
        # 2. Поиск свингов
        df_with_swings, swing_highs, swing_lows = find_swings(df_5m, window=SWING_WINDOW)
        
        # 3. Поиск пробоев УСТ
        breaks = find_ust_breaks(df_with_swings, swing_highs, swing_lows)
        
        # 4. Анализ
        analyze_results(breaks, df_with_swings)
        
        print(f"\n✅ Тест завершен!")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()