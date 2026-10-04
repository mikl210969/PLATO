"""
PLATO Sniper V6: ФИНАЛЬНЫЙ ТЕСТ НА 4 ДНЯХ
Smart TP2 + Breakeven + Защита от перекрытий
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
DATES_TO_TEST = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]

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
            tp2_mult += 0.5; reasons.append("Чистый путь (ask)")
        elif trade_side == 'short' and current_bid < (avg_bid_day * 0.6):
            tp2_mult += 0.5; reasons.append("Чистый путь (bid)")
            
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
            tp2_mult += 0.3; reasons.append("Высокая волатильность")
            
    tp2_mult = max(MIN_TP2, min(tp2_mult, MAX_TP2))
    risk = current_atr * SL_MULT if not depth_at_time.empty else 0.1
    tp2_price = entry_price + (risk * tp2_mult) if trade_side == 'long' else entry_price - (risk * tp2_mult)
    
    return tp2_mult, tp2_price

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
    tp2_mult, tp2_price = calculate_smart_tp2(entry_price, trade_side, wall_time, df_depth, df_trades)
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
        'date': wall_time.strftime('%Y-%m-%d'),
        'entry_time': wall_time, 'exit_time': exit_time, 'side': trade_side.upper(),
        'entry': round(entry_price, 2), 'tp1': round(tp1_price, 2),
        'tp2': round(tp2_price, 2), 'tp2_mult': tp2_mult, 'pnl_pct': round(pnl * 100, 3),
        'exit': exit_reason, 'duration_min': round((exit_time - wall_time).total_seconds() / 60, 1)
    }

# ==============================================================================
# 3. ГЛАВНЫЙ ЦИКЛ ПО ВСЕМ ДНЯМ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ФИНАЛЬНЫЙ ТЕСТ SNIPER V6 (Smart TP2 + Breakeven)")
    print(f"   Период: {DATES_TO_TEST[0]} - {DATES_TO_TEST[-1]}")
    print(f"   Логика: SL=1.0 ATR, TP1=1.5, Smart TP2 (2.5-4.0), Breakeven\n")
    
    df_conf = load_data()
    all_trades = []
    
    for date_str in DATES_TO_TEST:
        print(f"📅 Обработка {date_str}...")
        day_conf = df_conf[df_conf['date'] == date_str].sort_values('wall_time')
        df_depth = load_depth_data(date_str)
        df_trades = load_trades_data(date_str)
        
        if df_depth is None or df_trades is None:
            print(f"  ⚠️ Данные не найдены, пропускаем.")
            continue
            
        day_trades = []
        last_exit_time = None
        
        for idx, row in day_conf.iterrows():
            wall_time = row['wall_time']
            if last_exit_time is not None and (wall_time - last_exit_time).total_seconds() < MIN_TIME_BETWEEN_TRADES_SEC:
                continue
                
            res = simulate_trade(df_depth, df_trades, row)
            if res:
                day_trades.append(res)
                last_exit_time = res['exit_time']
                
        all_trades.extend(day_trades)
        
        # Вывод статистики за день
        if day_trades:
            wins = len([t for t in day_trades if t['pnl_pct'] > 0])
            total_pnl = sum(t['pnl_pct'] for t in day_trades)
            print(f"  ✅ Сделок: {len(day_trades)} | Побед: {wins} | WinRate: {(wins/len(day_trades))*100:.1f}% | PnL: {total_pnl:+.3f}%\n")
        else:
            print(f"  ⚠️ Сделок не найдено.\n")

    # Итоговая сводка
    df_all = pd.DataFrame(all_trades)
    print("="*80)
    print("🏆 ГРАНД-ФИНАЛ: СВОДНАЯ СТАТИСТИКА ЗА 4 ДНЯ")
    print("="*80)
    
    if not df_all.empty:
        total_trades = len(df_all)
        total_wins = len(df_all[df_all['pnl_pct'] > 0])
        total_pnl = df_all['pnl_pct'].sum()
        avg_pnl = total_pnl / total_trades
        winrate = (total_wins / total_trades) * 100
        
        print(f"  Всего сделок:      {total_trades}")
        print(f"  Прибыльных:        {total_wins}")
        print(f"  Убыточных:         {total_trades - total_wins}")
        print(f"  Общий Win Rate:    {winrate:.1f}%")
        print(f"  Общий Total PnL:   {total_pnl:+.3f}%")
        print(f"  Средний PnL/сделку:{avg_pnl:+.3f}%")
        
        print(f"\n РАСПРЕДЕЛЕНИЕ ПО ДНЯМ:")
        for date_str in DATES_TO_TEST:
            day_df = df_all[df_all['date'] == date_str]
            if not day_df.empty:
                d_wins = len(day_df[day_df['pnl_pct'] > 0])
                d_pnl = day_df['pnl_pct'].sum()
                d_wr = (d_wins / len(day_df)) * 100
                print(f"  {date_str}: {len(day_df)} сделок | WR: {d_wr:.0f}% | PnL: {d_pnl:+.3f}%")
                
        print(f"\n РАСПРЕДЕЛЕНИЕ ПО ПРИЧИНАМ ВЫХОДА:")
        for reason in df_all['exit'].unique():
            count = len(df_all[df_all['exit'] == reason])
            print(f"  {reason:<20}: {count} сделок")
            
        # Сохраняем детали
        out_file = DATA_DIR / "sniper_v6_grand_final_results.csv"
        df_all.to_csv(out_file, index=False)
        print(f"\n✅ Детальный отчет сохранен: {out_file}")
    else:
        print("  ⚠️ Нет данных для анализа.")
        
    print("="*80)
    print("🏁 Тест завершен!")