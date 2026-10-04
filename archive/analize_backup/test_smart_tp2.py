"""
PLATO Sniper V6: Тест УМНОГО TP2 (Исправленные, строгие пороги)
Анализирует стакан, дельту и агрессивные сделки для определения оптимального TP2
"""
import pandas as pd
import numpy as np
from pathlib import Path

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
CONFLUENCE_FILE = DATA_DIR / "sniper_v6_final_confluences.csv"
TARGET_DATE = "2026-09-28"

ATR_PERIOD = 14
SL_MULT = 1.0
TP1_MULT = 1.5
COMMISSION = 0.0007

# Параметры для умного TP2
BASE_TP2 = 2.5
MAX_TP2 = 4.0
MIN_TP2 = 2.0

# 🔥 ИСПРАВЛЕННЫЕ ПОРОГИ (Более строгие)
BIG_ORDER_THRESHOLD = 100.0      # Крупный ордер теперь > 100 SOL (было 50)
BIG_ORDERS_MIN_COUNT = 5         # Нужно минимум 5 таких ордеров в окне (было 3)
DELTA_STRENGTH_THRESHOLD = 500   # Сильная дельта > 500 SOL (было 200)

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data():
    df_conf = pd.read_csv(CONFLUENCE_FILE)
    df_conf['wall_time'] = pd.to_datetime(df_conf['wall_time'])
    return df_conf

def load_depth_data(date_str):
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists(): return None
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['prev_close']), abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=ATR_PERIOD).mean().fillna(df['tr'].mean())
    
    # Суммарная ликвидность в стакане (топ-20 уровней)
    df['total_bid_liquidity'] = df[[f'bid_v_{i}' for i in range(1, 21)]].sum(axis=1)
    df['total_ask_liquidity'] = df[[f'ask_v_{i}' for i in range(1, 21)]].sum(axis=1)
    
    # Средние значения за день для сравнения
    df['avg_bid_liq_day'] = df['total_bid_liquidity'].mean()
    df['avg_ask_liq_day'] = df['total_ask_liquidity'].mean()
    df['avg_atr_day'] = df['atr'].mean()
    
    return df

def load_trades_data(date_str):
    f_trades = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}.csv"
    if not f_trades.exists(): return None
    df = pd.read_csv(f_trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['is_buy'] = df['is_buyer_maker'] == False
    df['delta'] = np.where(df['is_buy'], df['quantity'], -df['quantity'])
    df['is_big'] = df['quantity'] >= BIG_ORDER_THRESHOLD
    return df

# ==============================================================================
# 3. РАСЧЕТ УМНОГО TP2
# ==============================================================================
def calculate_smart_tp2(entry_price, trade_side, wall_time, df_depth, df_trades):
    reasons = []
    tp2_mult = BASE_TP2
    
    # 1. Анализ ликвидности в стакане (сопротивление на пути)
    depth_window = df_depth[(df_depth['timestamp'] >= wall_time - pd.Timedelta(minutes=1)) & 
                            (df_depth['timestamp'] <= wall_time)]
    
    if not depth_window.empty:
        current_bid = depth_window['total_bid_liquidity'].mean()
        current_ask = depth_window['total_ask_liquidity'].mean()
        avg_bid_day = depth_window['avg_bid_liq_day'].iloc[0]
        avg_ask_day = depth_window['avg_ask_liq_day'].iloc[0]
        
        if trade_side == 'long':
            # Для LONG: путь чистый, если ask ликвидность меньше 60% от средней дневной
            if current_ask < (avg_ask_day * 0.6):
                tp2_mult += 0.5
                reasons.append(f"Чистый путь (ask={current_ask:.0f} < 60% ср.)")
        else:  # short
            if current_bid < (avg_bid_day * 0.6):
                tp2_mult += 0.5
                reasons.append(f"Чистый путь (bid={current_bid:.0f} < 60% ср.)")
    
    # 2. Анализ дельты (импульс) за последние 5 минут
    delta_window = df_trades[(df_trades['timestamp'] >= wall_time - pd.Timedelta(minutes=5)) & 
                             (df_trades['timestamp'] <= wall_time)]
    
    if not delta_window.empty:
        total_delta = delta_window['delta'].sum()
        
        if trade_side == 'long' and total_delta > DELTA_STRENGTH_THRESHOLD:
            tp2_mult += 0.5
            reasons.append(f"Сильный импульс покупок ({total_delta:.0f} SOL)")
        elif trade_side == 'short' and total_delta < -DELTA_STRENGTH_THRESHOLD:
            tp2_mult += 0.5
            reasons.append(f"Сильный импульс продаж ({total_delta:.0f} SOL)")
    
    # 3. Анализ крупных сделок (агрессия)
    if not delta_window.empty:
        big_orders = delta_window[delta_window['is_big']]
        big_orders_count = len(big_orders)
        
        if big_orders_count >= BIG_ORDERS_MIN_COUNT:
            tp2_mult += 0.5  # Увеличили бонус, так как условие теперь строгое
            reasons.append(f"Кластер крупных сделок ({big_orders_count} x >{BIG_ORDER_THRESHOLD} SOL)")
    
    # 4. Анализ волатильности (ATR)
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if not depth_at_time.empty:
        current_atr = depth_at_time.iloc[0]['atr']
        avg_atr_day = depth_at_time.iloc[0]['avg_atr_day']
        
        if current_atr > (avg_atr_day * 1.3):  # Волатильность на 30% выше средней
            tp2_mult += 0.3
            reasons.append(f"Повышенная волатильность (ATR={current_atr:.3f})")
    
    # Ограничиваем TP2
    tp2_mult = max(MIN_TP2, min(tp2_mult, MAX_TP2))
    
    # Рассчитываем цену TP2
    risk = current_atr * SL_MULT if not depth_at_time.empty else 0.1
    if trade_side == 'long':
        tp2_price = entry_price + (risk * tp2_mult)
    else:
        tp2_price = entry_price - (risk * tp2_mult)
    
    return tp2_mult, tp2_price, "; ".join(reasons) if reasons else "Стандартный TP2 (2.5)"

# ==============================================================================
# 4. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🎯 ТЕСТ УМНОГО TP2 (ИСПРАВЛЕННЫЕ ПОРОГИ)")
    print(f"   Базовый TP2: {BASE_TP2}, Диапазон: {MIN_TP2} - {MAX_TP2}")
    print(f"   Порог крупного ордера: >{BIG_ORDER_THRESHOLD} SOL, Мин. кол-во: {BIG_ORDERS_MIN_COUNT}\n")
    
    df_conf = load_data()
    day_conf = df_conf[df_conf['date'] == TARGET_DATE].sort_values('wall_time')
    
    df_depth = load_depth_data(TARGET_DATE)
    df_trades = load_trades_data(TARGET_DATE)
    
    if df_depth is None:
        print("❌ Данные не найдены")
        exit()
    
    print(f"{'='*120}")
    print(f"АНАЛИЗ УМНОГО TP2 ДЛЯ КАЖДОЙ СДЕЛКИ")
    print(f"{'='*120}")
    print(f"{'Время':<8} | {'Напр':<5} | {'Вход':<7} | {'ATR':<6} | {'TP2_mult':<9} | {'TP2_price':<9} | {'Причины'}")
    print("-" * 120)
    
    tp2_multipliers = []
    
    for idx, row in day_conf.iterrows():
        wall_time = row['wall_time']
        wall_price = row['wall_price']
        wall_side = row['wall_side']
        trade_side = 'long' if wall_side == 'bid' else 'short'
        
        depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
        atr = depth_at_time.iloc[0]['atr'] if not depth_at_time.empty else 0.1
        
        tp2_mult, tp2_price, reasons = calculate_smart_tp2(wall_price, trade_side, wall_time, df_depth, df_trades)
        tp2_multipliers.append(tp2_mult)
        
        print(f"{wall_time.strftime('%H:%M:%S'):<8} | {trade_side.upper():<5} | {wall_price:<7.2f} | {atr:<6.3f} | "
              f"{tp2_mult:<9.2f} | {tp2_price:<9.2f} | {reasons}")
    
    print(f"\n{'='*120}")
    print(f"📊 СТАТИСТИКА УМНОГО TP2")
    print(f"{'='*120}")
    print(f"  Средний TP2 множитель: {np.mean(tp2_multipliers):.2f}")
    print(f"  Минимальный TP2: {min(tp2_multipliers):.2f}")
    print(f"  Максимальный TP2: {max(tp2_multipliers):.2f}")
    print(f"  Сделок с TP2 > 3.0: {len([x for x in tp2_multipliers if x > 3.0])} (из {len(tp2_multipliers)})")
    print(f"  Сделок со стандартным TP2 (2.5): {len([x for x in tp2_multipliers if x == 2.5])}")
    
    print(f"\n🏁 Анализ завершен.")