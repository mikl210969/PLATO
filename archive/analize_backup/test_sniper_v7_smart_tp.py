"""
PLATO Sniper V7: Умный Тейк-Профит (Smart TP) на основе структурных уровней.
Реверсы отключены. SL = 1.0 ATR. TP2 ищет ближайший УСТ предыдущих дней.
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
UST_ALL_FILE = DATA_DIR / "ust_all_days_consolidated.csv"

# Параметры управления позицией
ATR_PERIOD = 14
SL_MULT = 1.0           # Узкий, но обоснованный стоп для идеального сетапа
TP1_MULT = 1.0          # Сейф: закрываем 50% на 1:1
MIN_TP2_RR = 2.0        # Минимальное R:R для структурного TP2 (иначе берем фикс)
FIXED_TP2_RR = 2.5      # Фиксированный TP2, если структурного уровня нет или он слишком близко

COMMISSION = 0.0007

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data():
    df_conf = pd.read_csv(CONFLUENCE_FILE)
    df_conf['ust_time'] = pd.to_datetime(df_conf['ust_time'])
    df_conf['wall_time'] = pd.to_datetime(df_conf['wall_time'])
    
    df_ust = pd.read_csv(UST_ALL_FILE)
    df_ust['time'] = pd.to_datetime(df_ust['time'])
    return df_conf, df_ust

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
# 3. ПОИСК УМНОГО TP2
# ==============================================================================
def find_smart_tp2(trade_side, entry_price, risk, current_date, df_ust_all):
    """Ищет ближайший структурный уровень (УСТ) предыдущих дней в направлении сделки."""
    # Фильтруем УСТ только за дни ДО текущей сделки
    past_ust = df_ust_all[df_ust_all['date'] < current_date].copy()
    
    if past_ust.empty:
        return entry_price + (risk * FIXED_TP2_RR) if trade_side == 'long' else entry_price - (risk * FIXED_TP2_RR), "Fixed_2.5"
    
    if trade_side == 'long':
        # Ищем ближайший уровень ВЫШЕ цены входа
        targets = past_ust[past_ust['level'] > entry_price]
        if targets.empty:
            return entry_price + (risk * FIXED_TP2_RR), "Fixed_2.5"
        best_target = targets.loc[targets['level'].idxmin(), 'level']
    else: # short
        # Ищем ближайший уровень НИЖЕ цены входа
        targets = past_ust[past_ust['level'] < entry_price]
        if targets.empty:
            return entry_price - (risk * FIXED_TP2_RR), "Fixed_2.5"
        best_target = targets.loc[targets['level'].idxmax(), 'level']
        
    # Проверяем, дает ли этот уровень достаточный R:R
    potential_rr = abs(best_target - entry_price) / risk
    if potential_rr >= MIN_TP2_RR:
        return best_target, f"Smart_UST_RR{potential_rr:.1f}"
    else:
        # Уровень есть, но он слишком близко (лучше взять фиксированный)
        fallback = entry_price + (risk * FIXED_TP2_RR) if trade_side == 'long' else entry_price - (risk * FIXED_TP2_RR)
        return fallback, "Fixed_2.5"

# ==============================================================================
# 4. СИМУЛЯЦИЯ ТОРГОВЛИ (Без реверсов)
# ==============================================================================
def simulate_trade_v7(df_depth, df_trades, confluence_row, df_ust_all):
    wall_time = confluence_row['wall_time']
    wall_price = confluence_row['wall_price']
    wall_side = confluence_row['wall_side']
    ust_type = confluence_row['ust_type']
    current_date = confluence_row['date']
    
    if wall_side == 'bid' and ust_type == 'SUPPORT_BREAK':
        trade_side = 'long'
    elif wall_side == 'ask' and ust_type == 'RESISTANCE_BREAK':
        trade_side = 'short'
    else:
        return None
    
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if depth_at_time.empty: return None
    
    atr = depth_at_time.iloc[0]['atr']
    risk = atr * SL_MULT
    entry_price = wall_price
    
    if not check_delta_reversal(df_trades, wall_time, wall_side):
        return {'type': 'main', 'side': trade_side, 'status': 'filtered', 'pnl': 0.0, 'reason': 'delta_filter'}
    
    # Расчет уровней
    tp1_price = entry_price + (risk * TP1_MULT) if trade_side == 'long' else entry_price - (risk * TP1_MULT)
    tp2_price, tp2_reason = find_smart_tp2(trade_side, entry_price, risk, current_date, df_ust_all)
    sl_price = entry_price - risk if trade_side == 'long' else entry_price + risk
    
    future = df_depth[df_depth['timestamp'] > wall_time].copy()
    if future.empty: return None
    
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

    pnl = 0.0
    exit_reason = ''
    
    if hit_sl and (not hit_tp1 or hit_sl < hit_tp1):
        pnl = ((sl_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - sl_price) / entry_price)
        pnl -= COMMISSION
        exit_reason = 'SL'
    elif hit_tp1:
        pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
        if hit_tp2 and hit_tp2 > hit_tp1:
            pnl_2 = ((tp2_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp2_price) / entry_price)
            pnl = (pnl_1 * 0.5 + pnl_2 * 0.5) - COMMISSION
            exit_reason = f'TP1+TP2 ({tp2_reason})'
        else:
            pnl = (pnl_1 * 0.5) - COMMISSION
            exit_reason = 'TP1_only'
    else:
        exit_reason = 'Timeout'
        
    return {
        'type': 'main', 'side': trade_side, 'status': 'completed' if exit_reason != 'Timeout' else 'timeout',
        'pnl': pnl, 'exit_reason': exit_reason, 'entry': entry_price, 'tp2_reason': tp2_reason
    }

# ==============================================================================
# 5. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print("🚀 ЭТАП 2: УМНЫЙ ТЕЙК-ПРОФИТ (Smart TP) на основе УСТ предыдущих дней")
    print("   Реверсы отключены. SL = 1.0 ATR. TP2 ищет структурные уровни.\n")
    
    df_conf, df_ust_all = load_data()
    all_results = []
    dates = df_conf['date'].unique()
    
    for date_str in tqdm(dates, desc="Дни", unit="день"):
        df_depth = load_depth_data(date_str)
        df_trades = load_trades_data(date_str)
        if df_depth is None: continue
        
        day_conf = df_conf[df_conf['date'] == date_str]
        for idx, row in tqdm(day_conf.iterrows(), desc=f"  {date_str}", leave=False):
            res = simulate_trade_v7(df_depth, df_trades, row, df_ust_all)
            if res:
                res['date'] = date_str
                all_results.append(res)
            
    df_res = pd.DataFrame(all_results)
    df_completed = df_res[df_res['status'] == 'completed']
    
    print(f"\n{'='*80}")
    print(f"📊 ИТОГИ ЭТАПА 2 (УМНЫЙ TP)")
    print(f"{'='*80}")
    print(f"Всего конфлюэнсов: {len(df_conf)}")
    print(f"Отфильтровано (дельта): {len(df_res[df_res['status'] == 'filtered'])}")
    
    if not df_completed.empty:
        wr = (len(df_completed[df_completed['pnl'] > 0]) / len(df_completed)) * 100
        total_pnl = df_completed['pnl'].sum() * 100
        avg_pnl = total_pnl / len(df_completed)
        
        print(f"\n📈 МЕТРИКИ СТРАТЕГИИ:")
        print(f"  Завершенных сделок: {len(df_completed)}")
        print(f"  Win Rate:           {wr:.1f}%")
        print(f"  Total PnL:          {total_pnl:.2f}%")
        print(f"  Avg PnL per trade:  {avg_pnl:.3f}%")
        
        print(f"\n🎯 РАСПРЕДЕЛЕНИЕ ПРИЧИН ВЫХОДА:")
        print(df_completed['exit_reason'].value_counts().to_string())
        
        print(f"\n📈 РАСПРЕДЕЛЕНИЕ ПО СТОРОНАМ:")
        for side in ['long', 'short']:
            side_df = df_completed[df_completed['side'] == side]
            if not side_df.empty:
                side_wr = (len(side_df[side_df['pnl'] > 0]) / len(side_df)) * 100
                side_pnl = side_df['pnl'].sum() * 100
                print(f"  {side.upper():5}: {len(side_df):3} сделок, WR={side_wr:.1f}%, PnL={side_pnl:.2f}%")
        
        out_file = DATA_DIR / "sniper_v7_smart_tp_results.csv"
        df_res.to_csv(out_file, index=False)
        print(f"\n✅ Детали сохранены: {out_file}")
        
    print(f"{'='*80}")