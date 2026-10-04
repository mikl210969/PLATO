"""
PLATO Sniper V6: Финальный бэктест с УМНЫМ TP2 и Breakeven
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

# Параметры УМНОГО TP2
BASE_TP2 = 2.5
MAX_TP2 = 4.0
MIN_TP2 = 2.0
BIG_ORDER_THRESHOLD = 100.0
BIG_ORDERS_MIN_COUNT = 5
DELTA_STRENGTH_THRESHOLD = 500

MIN_TIME_BETWEEN_TRADES_SEC = 120

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
    
    df['total_bid_liquidity'] = df[[f'bid_v_{i}' for i in range(1, 21)]].sum(axis=1)
    df['total_ask_liquidity'] = df[[f'ask_v_{i}' for i in range(1, 21)]].sum(axis=1)
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

def calculate_smart_tp2(entry_price, trade_side, wall_time, df_depth, df_trades):
    reasons = []
    tp2_mult = BASE_TP2
    
    depth_window = df_depth[(df_depth['timestamp'] >= wall_time - pd.Timedelta(minutes=1)) & (df_depth['timestamp'] <= wall_time)]
    if not depth_window.empty:
        current_bid = depth_window['total_bid_liquidity'].mean()
        current_ask = depth_window['total_ask_liquidity'].mean()
        avg_bid_day = depth_window['avg_bid_liq_day'].iloc[0]
        avg_ask_day = depth_window['avg_ask_liq_day'].iloc[0]
        
        if trade_side == 'long' and current_ask < (avg_ask_day * 0.6):
            tp2_mult += 0.5; reasons.append(f"Чистый путь (ask)")
        elif trade_side == 'short' and current_bid < (avg_bid_day * 0.6):
            tp2_mult += 0.5; reasons.append(f"Чистый путь (bid)")
            
    delta_window = df_trades[(df_trades['timestamp'] >= wall_time - pd.Timedelta(minutes=5)) & (df_trades['timestamp'] <= wall_time)]
    if not delta_window.empty:
        total_delta = delta_window['delta'].sum()
        if trade_side == 'long' and total_delta > DELTA_STRENGTH_THRESHOLD:
            tp2_mult += 0.5; reasons.append(f"Импульс покупок ({total_delta:.0f})")
        elif trade_side == 'short' and total_delta < -DELTA_STRENGTH_THRESHOLD:
            tp2_mult += 0.5; reasons.append(f"Импульс продаж ({total_delta:.0f})")
            
        big_orders_count = len(delta_window[delta_window['is_big']])
        if big_orders_count >= BIG_ORDERS_MIN_COUNT:
            tp2_mult += 0.5; reasons.append(f"Крупные сделки ({big_orders_count})")
            
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if not depth_at_time.empty:
        current_atr = depth_at_time.iloc[0]['atr']
        avg_atr_day = depth_at_time.iloc[0]['avg_atr_day']
        if current_atr > (avg_atr_day * 1.3):
            tp2_mult += 0.3; reasons.append(f"Высокая волатильность")
            
    tp2_mult = max(MIN_TP2, min(tp2_mult, MAX_TP2))
    risk = current_atr * SL_MULT if not depth_at_time.empty else 0.1
    tp2_price = entry_price + (risk * tp2_mult) if trade_side == 'long' else entry_price - (risk * tp2_mult)
    
    return tp2_mult, tp2_price, "; ".join(reasons) if reasons else "Стандарт"

def simulate_trade(df_depth, df_trades, confluence_row):
    wall_time = confluence_row['wall_time']
    wall_price = confluence_row['wall_price']
    wall_side = confluence_row['wall_side']
    trade_side = 'long' if wall_side == 'bid' else 'short'
    
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if depth_at_time.empty: return None
    
    atr = depth_at_time.iloc[0]['atr']
    risk = atr * SL_MULT
    entry_price = wall_price
    
    sl_price = entry_price - risk if trade_side == 'long' else entry_price + risk
    tp1_price = entry_price + (risk * TP1_MULT) if trade_side == 'long' else entry_price - (risk * TP1_MULT)
    
    # 🔥 УМНЫЙ TP2
    tp2_mult, tp2_price, tp2_reason = calculate_smart_tp2(entry_price, trade_side, wall_time, df_depth, df_trades)
    
    breakeven_sl = entry_price * (1 + 0.001) if trade_side == 'long' else entry_price * (1 - 0.001)
    
    future = df_depth[df_depth['timestamp'] > wall_time].copy()
    if future.empty: return None
    
    hit_tp1, hit_tp2, hit_sl, hit_breakeven_sl, exit_time = None, None, None, None, None
    
    for idx, row in future.iterrows():
        if trade_side == 'long':
            if row['high'] >= tp1_price and hit_tp1 is None: hit_tp1 = row['timestamp']
            if hit_tp1 is not None:
                if row['high'] >= tp2_price and hit_tp2 is None: hit_tp2 = row['timestamp']
                if row['low'] <= breakeven_sl and hit_breakeven_sl is None: hit_breakeven_sl = row['timestamp']
        else:
            if row['low'] <= tp1_price and hit_tp1 is None: hit_tp1 = row['timestamp']
            if hit_tp1 is not None:
                if row['low'] <= tp2_price and hit_tp2 is None: hit_tp2 = row['timestamp']
                if row['high'] >= breakeven_sl and hit_breakeven_sl is None: hit_breakeven_sl = row['timestamp']
                
        if hit_tp1 is None:
            if trade_side == 'long' and row['low'] <= sl_price and hit_sl is None: hit_sl = row['timestamp']
            elif trade_side == 'short' and row['high'] >= sl_price and hit_sl is None: hit_sl = row['timestamp']
            
        if (row['timestamp'] - wall_time).total_seconds() > 3600: break
    
    pnl, exit_reason = 0.0, ''
    if hit_sl and (hit_tp1 is None or hit_sl < hit_tp1):
        pnl = ((sl_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - sl_price) / entry_price)
        pnl -= COMMISSION
        exit_reason, exit_time = 'SL', hit_sl
    elif hit_tp1 and hit_tp2 and hit_tp2 > hit_tp1:
        pnl = ((tp2_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp2_price) / entry_price)
        pnl -= COMMISSION
        exit_reason, exit_time = f'TP1→TP2 (x{tp2_mult})', hit_tp2
    elif hit_tp1 and hit_breakeven_sl and hit_breakeven_sl > hit_tp1:
        pnl = ((breakeven_sl - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - breakeven_sl) / entry_price)
        pnl -= COMMISSION
        exit_reason, exit_time = 'TP1→Breakeven', hit_breakeven_sl
    elif hit_tp1:
        pnl = ((breakeven_sl - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - breakeven_sl) / entry_price)
        pnl -= COMMISSION
        exit_reason, exit_time = 'TP1→Timeout', wall_time + pd.Timedelta(hours=1)
    else:
        exit_reason, pnl, exit_time = 'Timeout', -COMMISSION, wall_time + pd.Timedelta(hours=1)
        
    return {
        'entry_time': wall_time, 'exit_time': exit_time, 'side': trade_side.upper(),
        'entry': round(entry_price, 2), 'sl': round(sl_price, 2), 'tp1': round(tp1_price, 2),
        'tp2': round(tp2_price, 2), 'tp2_mult': tp2_mult, 'pnl_pct': round(pnl * 100, 3),
        'exit': exit_reason, 'duration_min': round((exit_time - wall_time).total_seconds() / 60, 1)
    }

# ==============================================================================
# 3. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🎯 SNIPER V6: ФИНАЛЬНЫЙ ТЕСТ (Smart TP2 + Breakeven)")
    print(f"   Дата: {TARGET_DATE}\n")
    
    df_conf = load_data()
    day_conf = df_conf[df_conf['date'] == TARGET_DATE].sort_values('wall_time')
    df_depth = load_depth_data(TARGET_DATE)
    df_trades = load_trades_data(TARGET_DATE)
    
    executed_trades, skipped_trades, rejected_trades = [], [], []
    last_exit_time = None
    
    for idx, row in day_conf.iterrows():
        wall_time = row['wall_time']
        if last_exit_time is not None and (wall_time - last_exit_time).total_seconds() < MIN_TIME_BETWEEN_TRADES_SEC:
            skipped_trades.append({'time': wall_time.strftime('%H:%M:%S'), 'side': 'LONG' if row['wall_side'] == 'bid' else 'SHORT', 'reason': 'Перекрытие'})
            continue
            
        # Здесь можно добавить вызов apply_analytics_filters, если нужен фильтр дельты ДО входа. 
        # Для чистоты теста Smart TP2 пока оставим все сигналы, которые прошли базовый отбор.
        
        res = simulate_trade(df_depth, df_trades, row)
        if res:
            executed_trades.append(res)
            last_exit_time = res['exit_time']
    
    print(f"{'='*135}")
    print(f"{'Вход':<8} | {'Выход':<8} | {'Длит.':<5} | {'Напр':<5} | {'Вход':<7} | {'TP1':<7} | {'TP2 (x)':<10} | {'PnL %':<7} | {'Выход'}")
    print("-" * 135)
    
    for t in executed_trades:
        print(f"{t['entry_time'].strftime('%H:%M:%S'):<8} | {t['exit_time'].strftime('%H:%M:%S'):<8} | {t['duration_min']:>3.1f}м | "
              f"{t['side']:<5} | {t['entry']:<7.2f} | {t['tp1']:<7.2f} | {t['tp2']:<7.2f} (x{t['tp2_mult']}) | "
              f"{t['pnl_pct']:>+6.3f}% | {t['exit']}")
    
    if executed_trades:
        wins = len([t for t in executed_trades if t['pnl_pct'] > 0])
        total_pnl = sum(t['pnl_pct'] for t in executed_trades)
        print(f"{'='*135}")
        print(f"📊 ИТОГ: Сделок: {len(executed_trades)} | Побед: {wins} | WinRate: {(wins/len(executed_trades))*100:.1f}% | Total PnL: {total_pnl:+.3f}%\n")
    
    print("🏁 Анализ завершен.")