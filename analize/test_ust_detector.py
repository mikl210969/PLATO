"""
Детектор УСТ v3.0: Swing Point + Breakout + Clustering
Финальная версия перед интеграцией в платформу
"""
import pandas as pd
import numpy as np
from pathlib import Path

DATA_FILE = Path(r"data\history\SOLUSDT\SOLUSDT_1m_klines.csv")

def calculate_atr(df, period=14):
    """Расчет ATR"""
    df['prev_close'] = df['close'].shift(1)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['prev_close']), abs(df['low'] - df['prev_close']))
    )
    df['atr'] = df['tr'].rolling(window=period).mean()
    return df

def find_swing_points(df, lookback=5):
    """Находит локальные минимумы и максимумы"""
    df['swing_low'] = False
    df['swing_high'] = False
    
    for i in range(lookback, len(df) - lookback):
        if df['low'].iloc[i] == df['low'].iloc[i-lookback:i+lookback+1].min():
            df.loc[df.index[i], 'swing_low'] = True
        if df['high'].iloc[i] == df['high'].iloc[i-lookback:i+lookback+1].max():
            df.loc[df.index[i], 'swing_high'] = True
    
    return df

def is_valid_breakout(df, level_price, current_index, direction):
    """
    Проверяет пробой уровня по правилам: Импульс ИЛИ 3 свечи подряд с объемом
    """
    if current_index < 2:
        return False, ""
    
    current = df.iloc[current_index]
    prev_1 = df.iloc[current_index - 1]
    prev_2 = df.iloc[current_index - 2]
    
    avg_volume = df['volume'].rolling(window=20).mean().iloc[current_index]
    atr = df['atr'].iloc[current_index]
    
    if direction == 'bull':  # Пробой сопротивления вверх
        if current['low'] <= level_price:
            return False, ""
        
        # Триггер 1: Импульсная свеча
        body_size = abs(current['close'] - current['open'])
        is_impulse = (body_size > 1.5 * atr) and (current['volume'] > 1.5 * avg_volume)
        
        # Триггер 2: 3 свечи подряд вверх выше уровня + объем
        is_3_candles = (current['close'] > prev_1['close'] > prev_2['close']) and \
                       (prev_2['low'] > level_price) and \
                       (max(current['volume'], prev_1['volume'], prev_2['volume']) > 1.2 * avg_volume)
        
        if is_impulse:
            return True, "Impulse"
        elif is_3_candles:
            return True, "3 Candles"
        else:
            return False, ""
            
    elif direction == 'bear':  # Пробой поддержки вниз
        if current['high'] >= level_price:
            return False, ""
        
        # Триггер 1: Импульсная свеча вниз
        body_size = abs(current['close'] - current['open'])
        is_impulse = (body_size > 1.5 * atr) and (current['volume'] > 1.5 * avg_volume)
        
        # Триггер 2: 3 свечи подряд вниз ниже уровня + объем
        is_3_candles = (current['close'] < prev_1['close'] < prev_2['close']) and \
                       (prev_2['high'] < level_price) and \
                       (max(current['volume'], prev_1['volume'], prev_2['volume']) > 1.2 * avg_volume)
        
        if is_impulse:
            return True, "Impulse"
        elif is_3_candles:
            return True, "3 Candles"
        else:
            return False, ""
    
    return False, ""

def find_ust_levels(df_5m, min_distance_pct=0.005):
    """
    Ищет УСТ: swing point + подтвержденный пробой + clustering
    min_distance_pct: минимальное расстояние между уровнями (0.5%)
    """
    df_5m = find_swing_points(df_5m, lookback=3)
    df_5m = calculate_atr(df_5m)
    
    ust_levels = []
    last_level_price = None
    
    # Собираем все swing points
    swings = []
    for i in range(len(df_5m)):
        if df_5m['swing_low'].iloc[i]:
            swings.append({
                'time': df_5m.index[i],
                'price': df_5m['low'].iloc[i],
                'type': 'low',
                'index': i
            })
        elif df_5m['swing_high'].iloc[i]:
            swings.append({
                'time': df_5m.index[i],
                'price': df_5m['high'].iloc[i],
                'type': 'high',
                'index': i
            })
    
    # Для каждого swing point ищем пробой
    for i, swing in enumerate(swings):
        swing_price = swing['price']
        swing_time = swing['time']
        swing_type = swing['type']
        swing_index = swing['index']
        
        # Проверяем минимальное расстояние от последнего уровня (clustering)
        if last_level_price and abs(swing_price - last_level_price) / last_level_price < min_distance_pct:
            continue
        
        # Ищем пробой в следующих 100 свечах (500 минут = ~8 часов)
        breakout_found = False
        breakout_time = None
        breakout_type = ""
        
        for j in range(swing_index + 5, min(swing_index + 100, len(df_5m))):
            if swing_type == 'low':
                direction = 'bear'
            else:
                direction = 'bull'
            
            is_breakout, breakout_reason = is_valid_breakout(df_5m, swing_price, j, direction)
            
            if is_breakout:
                breakout_found = True
                breakout_time = df_5m.index[j]
                breakout_type = breakout_reason
                break
        
        if breakout_found:
            # Рассчитываем коэффициент силы
            coefficient = 0.5  # База за swing + breakout
            
            # Бонус за импульс
            if breakout_type == "Impulse":
                coefficient += 0.3
            
            # Бонус за объем на пробое
            breakout_idx = df_5m.index.get_loc(breakout_time)
            if breakout_idx < len(df_5m):
                volume_at_breakout = df_5m['volume'].iloc[breakout_idx]
                avg_volume = df_5m['volume'].rolling(window=20).mean().iloc[breakout_idx]
                if volume_at_breakout > avg_volume * 2.0:
                    coefficient += 0.2
            
            coefficient = min(coefficient, 1.0)
            
            ust_levels.append({
                'time': swing_time,
                'price': round(swing_price, 2),
                'direction': 'bull' if swing_type == 'low' else 'bear',
                'breakout_time': breakout_time,
                'breakout_type': breakout_type,
                'coefficient': round(coefficient, 2),
                'type': f"{'Support' if swing_type == 'low' else 'Resistance'} Breakout"
            })
            
            last_level_price = swing_price
    
    return pd.DataFrame(ust_levels)

if __name__ == "__main__":
    print(f"🚀 Загрузка данных из: {DATA_FILE}")
    
    df_1m = pd.read_csv(DATA_FILE)
    df_1m['timestamp'] = pd.to_datetime(df_1m['timestamp'])
    df_1m.set_index('timestamp', inplace=True)
    
    print("️ Агрегация в 5-минутные свечи...")
    df_5m = df_1m.resample('5min').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    print(f"✅ Загружено {len(df_1m)} свечей 1m -> {len(df_5m)} свечей 5m")
    
    print("🔍 Поиск УСТ (swing + breakout + clustering)...")
    ust_df = find_ust_levels(df_5m, min_distance_pct=0.005)
    
    if not ust_df.empty:
        print(f"\n НАЙДЕНО УРОВНЕЙ УСТ: {len(ust_df)}")
        print("-" * 100)
        print(f"{'Время':<20} | {'Тип':<25} | {'Цена':<8} | {'Пробой':<20} | {'Тип пробоя':<12} | {'Коэфф.'}")
        print("-" * 100)
        
        for _, row in ust_df.iterrows():
            time_str = str(row['time']).split('.')[0]
            breakout_str = str(row['breakout_time']).split('.')[0]
            print(f"{time_str:<20} | {row['type']:<25} | {row['price']:<8} | {breakout_str:<20} | {row['breakout_type']:<12} | {row['coefficient']}")
        
        print(f"\n📊 Статистика:")
        print(f"  Средний коэффициент: {ust_df['coefficient'].mean():.2f}")
        print(f"  Уровней с коэфф. >= 0.7: {len(ust_df[ust_df['coefficient'] >= 0.7])}")
        print(f"  Импульсных пробоев: {len(ust_df[ust_df['breakout_type'] == 'Impulse'])}")
        print(f"  Пробоев по 3 свечам: {len(ust_df[ust_df['breakout_type'] == '3 Candles'])}")
        print(f"  Уровней в день (среднее): {len(ust_df) / 8:.1f}")
    else:
        print("❌ Уровни УСТ не найдены.")