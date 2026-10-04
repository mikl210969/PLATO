"""
PLATO Sniper V6: Этап 1, Вариант А.
Адаптивный SL (1.0 ATR) + Механика Реверса (Stop & Reverse) при срабатывании стопа.
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
CONFLUENCE_FILE = DATA_DIR / "sniper_v6_final_confluences.csv"

# Параметры управления позицией (Этап 1: Агрессивные, но обоснованные)
ATR_PERIOD = 14
SL_MULT_MAIN = 1.0      # Стоп-лосс основной сделки = 1.0 * ATR (Идеальный сетап)
TP1_MULT = 1.0          # Тейк-профт 1 (Сейф) = 1.0 * Риск (закрываем 50%)
TP2_MULT = 2.5          # Тейк-профт 2 = 2.5 * Риск

# Параметры РЕВЕРСА (Stop & Reverse)
REVERSE_ENABLED = True
SL_MULT_REVERSE = 0.8   # Стоп-лосс реверса = 0.8 * ATR (еще уже, так как импульс есть)
TP_MULT_REVERSE = 1.5   # Тейк-профт реверса = 1.5 * Риск (быстрая фиксация)

COMMISSION = 0.0007     # 0.07% за сделку (вход + выход)

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_confluences():
    if not CONFLUENCE_FILE.exists():
        raise FileNotFoundError(f"Файл не найден: {CONFLUENCE_FILE}")
    df = pd.read_csv(CONFLUENCE_FILE)
    df['ust_time'] = pd.to_datetime(df['ust_time'])
    df['wall_time'] = pd.to_datetime(df['wall_time'])
    return df

def load_depth_data(date_str):
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists(): return None
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['prev_close']),
                                     abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=ATR_PERIOD).mean().fillna(df['tr'].mean())
    return df

def load_trades_data(date_str):
    f_trades = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}.csv"
    if not f_trades.exists(): return None
    df = pd.read_csv(f_trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['is_buy'] = df['is_buyer_maker'] == False
    df['delta'] = np.where(df['is_buy'], df['quantity'], -df['quantity'])
    return df

def check_delta_reversal(df_trades, wall_time, wall_side):
    if df_trades is None: return True
    start_time = wall_time - pd.Timedelta(seconds=60)
    mask = (df_trades['timestamp'] >= start_time) & (df_trades['timestamp'] <= wall_time)
    total_delta = df_trades[mask]['delta'].sum()
    if wall_side == 'bid': return total_delta < 0
    else: return total_delta > 0

# ==============================================================================
# 3. СИМУЛЯЦИЯ ТОРГОВЛИ С РЕВЕРСОМ
# ==============================================================================
def simulate_trade_with_reverse(df_depth, df_trades, confluence_row):
    wall_time = confluence_row['wall_time']
    wall_price = confluence_row['wall_price']
    wall_side = confluence_row['wall_side']
    ust_type = confluence_row['ust_type']
    
    if wall_side == 'bid' and ust_type == 'SUPPORT_BREAK':
        trade_side = 'long'
    elif wall_side == 'ask' and ust_type == 'RESISTANCE_BREAK':
        trade_side = 'short'
    else:
        return []
    
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if depth_at_time.empty: return []
    
    atr = depth_at_time.iloc[0]['atr']
    
    # --- ОСНОВНАЯ СДЕЛКА ---
    entry_price = wall_price
    risk = atr * SL_MULT_MAIN
    
    if trade_side == 'long':
        sl_price = entry_price - risk
        tp1_price = entry_price + (risk * TP1_MULT)
        tp2_price = entry_price + (risk * TP2_MULT)
    else:
        sl_price = entry_price + risk
        tp1_price = entry_price - (risk * TP1_MULT)
        tp2_price = entry_price - (risk * TP2_MULT)
    
    if not check_delta_reversal(df_trades, wall_time, wall_side):
        return [{'type': 'main', 'side': trade_side, 'status': 'filtered', 'pnl': 0.0, 'reason': 'delta_filter'}]
    
    future = df_depth[df_depth['timestamp'] > wall_time].copy()
    if future.empty: return []
    
    # Поиск исхода основной сделки
    hit_tp1, hit_tp2, hit_sl = None, None, None
    for idx, row in future.iterrows():
        if trade_side == 'long':
            if row['high'] >= tp1_price and hit_tp1 is None: hit_tp1 = row['timestamp']
            if row['high'] >= tp2_price and hit_tp2 is None: hit_tp2 = row['timestamp']
            if row['low'] <= sl_price and hit_sl is None: hit_sl = row['timestamp']
        else:
            if row['low'] <= tp1_price and hit_tp1 is None: hit_tp1 = row['timestamp']
            if row['low'] <= tp2_price and hit_tp2 is None: hit_tp2 = row['timestamp']
            if row['high'] >= sl_price and hit_sl is None: hit_sl = row['timestamp']
            
        if (row['timestamp'] - wall_time).total_seconds() > 3600: break

    results = []
    main_pnl = 0.0
    main_exit = ''
    sl_hit_time = None

    # Расчет PnL основной сделки
    if hit_sl and (not hit_tp1 or hit_sl < hit_tp1):
        main_pnl = ((sl_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - sl_price) / entry_price)
        main_pnl -= COMMISSION
        main_exit = 'SL'
        sl_hit_time = hit_sl
    elif hit_tp1:
        pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
        if hit_tp2 and hit_tp2 > hit_tp1:
            pnl_2 = ((tp2_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp2_price) / entry_price)
            main_pnl = (pnl_1 * 0.5 + pnl_2 * 0.5) - COMMISSION
            main_exit = 'TP1+TP2'
        else:
            main_pnl = (pnl_1 * 0.5) - COMMISSION # Вторая половина в 0 (упрощенно)
            main_exit = 'TP1_only'
    else:
        main_exit = 'Timeout'

    results.append({'type': 'main', 'side': trade_side, 'status': 'completed' if main_exit != 'Timeout' else 'timeout', 
                    'pnl': main_pnl, 'exit_reason': main_exit, 'entry': entry_price})

    # --- РЕВЕРС (Если сработал SL и функция включена) ---
    if REVERSE_ENABLED and main_exit == 'SL' and sl_hit_time is not None:
        rev_side = 'short' if trade_side == 'long' else 'long'
        rev_entry = sl_price # Входим по цене срабатывания стопа
        rev_risk = atr * SL_MULT_REVERSE
        
        if rev_side == 'long':
            rev_sl = rev_entry - rev_risk
            rev_tp = rev_entry + (rev_risk * TP_MULT_REVERSE)
        else:
            rev_sl = rev_entry + rev_risk
            rev_tp = rev_entry - (rev_risk * TP_MULT_REVERSE)
            
        future_rev = df_depth[df_depth['timestamp'] > sl_hit_time].copy()
        hit_rev_tp, hit_rev_sl = None, None
        
        for idx, row in future_rev.iterrows():
            if rev_side == 'long':
                if row['high'] >= rev_tp and hit_rev_tp is None: hit_rev_tp = row['timestamp']
                if row['low'] <= rev_sl and hit_rev_sl is None: hit_rev_sl = row['timestamp']
            else:
                if row['low'] <= rev_tp and hit_rev_tp is None: hit_rev_tp = row['timestamp']
                if row['high'] >= rev_sl and hit_rev_sl is None: hit_rev_sl = row['timestamp']
                
            if (row['timestamp'] - sl_hit_time).total_seconds() > 1800: break # 30 минут на реверс
            
        rev_pnl = 0.0
        rev_exit = ''
        if hit_rev_sl and (not hit_rev_tp or hit_rev_sl < hit_rev_tp):
            rev_pnl = ((rev_sl - rev_entry) / rev_entry) if rev_side == 'long' else ((rev_entry - rev_sl) / rev_entry)
            rev_pnl -= COMMISSION
            rev_exit = 'Rev_SL'
        elif hit_rev_tp:
            rev_pnl = ((rev_tp - rev_entry) / rev_entry) if rev_side == 'long' else ((rev_entry - rev_tp) / rev_entry)
            rev_pnl -= COMMISSION
            rev_exit = 'Rev_TP'
        else:
            rev_exit = 'Rev_Timeout'
            
        results.append({'type': 'reverse', 'side': rev_side, 'status': 'completed' if rev_exit != 'Rev_Timeout' else 'timeout',
                        'pnl': rev_pnl, 'exit_reason': rev_exit, 'entry': rev_entry})

    return results

# ==============================================================================
# 4. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ЭТАП 1, ВАРИАНТ А: Адаптивный SL (1.0 ATR) + Реверс при SL")
    
    df_confluences = load_confluences()
    all_results = []
    dates = df_confluences['date'].unique()
    
    for date_str in tqdm(dates, desc="Дни", unit="день"):
        df_depth = load_depth_data(date_str)
        df_trades = load_trades_data(date_str)
        if df_depth is None: continue
        
        day_conf = df_confluences[df_confluences['date'] == date_str]
        for idx, row in tqdm(day_conf.iterrows(), desc=f"  {date_str}", leave=False):
            res = simulate_trade_with_reverse(df_depth, df_trades, row)
            for r in res:
                r['date'] = date_str
                r['ust_time'] = row['wall_time']
            all_results.extend(res)
            
    df_res = pd.DataFrame(all_results)
    df_main = df_res[df_res['type'] == 'main']
    df_rev = df_res[df_res['type'] == 'reverse']
    
    print(f"\n{'='*80}")
    print(f"📊 ИТОГИ ЭТАПА 1 (ВАРИАНТ А)")
    print(f"{'='*80}")
    print(f"Всего конфлюэнсов: {len(df_confluences)}")
    print(f"Отфильтровано (дельта): {len(df_main[df_main['status'] == 'filtered'])}")
    
    # Анализ основных сделок
    df_main_completed = df_main[df_main['status'] == 'completed']
    if not df_main_completed.empty:
        wr_main = (len(df_main_completed[df_main_completed['pnl'] > 0]) / len(df_main_completed)) * 100
        pnl_main = df_main_completed['pnl'].sum() * 100
        print(f"\n📈 ОСНОВНЫЕ СДЕЛКИ (SL = 1.0 ATR):")
        print(f"  Сделок: {len(df_main_completed)} | WinRate: {wr_main:.1f}% | PnL: {pnl_main:.2f}%")
        
    # Анализ реверсов
    if REVERSE_ENABLED and not df_rev.empty:
        df_rev_completed = df_rev[df_rev['status'] == 'completed']
        if not df_rev_completed.empty:
            wr_rev = (len(df_rev_completed[df_rev_completed['pnl'] > 0]) / len(df_rev_completed)) * 100
            pnl_rev = df_rev_completed['pnl'].sum() * 100
            print(f"\n🔄 РЕВЕРСЫ (После срабатывания SL):")
            print(f"  Сделок: {len(df_rev_completed)} | WinRate: {wr_rev:.1f}% | PnL: {pnl_rev:.2f}%")
            
    # Общий итог
    total_pnl = df_res['pnl'].sum() * 100
    print(f"\n💰 ОБЩИЙ ИТОГОВЫЙ PnL (Основная + Реверс): {total_pnl:.2f}%")
    print(f"{'='*80}")
    
    if not df_res.empty:
        out_file = DATA_DIR / "sniper_v6_stage1_results.csv"
        df_res.to_csv(out_file, index=False)
        print(f"✅ Детали: {out_file}")