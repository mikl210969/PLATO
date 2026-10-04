"""
PLATO Sniper V6: Исправленные фильтры аналитики (Ордерфлоу логика)
Ищем поглощение: атака в стену -> истощение/разворот дельты.
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
ANALYTICS_FILE = DATA_DIR / "ust_events_full_analytics.csv"
TARGET_DATE = "2026-09-28"

# Параметры управления позицией
ATR_PERIOD = 14
SL_MULT = 1.0
TP1_MULT = 1.0
TP2_MULT = 2.5
COMMISSION = 0.0007

# Параметры фильтров (ИСПРАВЛЕННЫЕ)
FILTER_DELTA_REVERSAL_ENABLED = True
DELTA_SHIFT_MIN = 500  # Дельта должна улучшиться минимум на 500 SOL (например, с -1000 до -400 или +200)

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data():
    df_conf = pd.read_csv(CONFLUENCE_FILE)
    df_conf['ust_time'] = pd.to_datetime(df_conf['ust_time'])
    df_conf['wall_time'] = pd.to_datetime(df_conf['wall_time'])
    
    df_analytics = pd.read_csv(ANALYTICS_FILE)
    df_analytics['event_time'] = pd.to_datetime(df_analytics['event_time'])
    return df_conf, df_analytics

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
# 3. ПРИМЕНЕНИЕ ИСПРАВЛЕННЫХ ФИЛЬТРОВ
# ==============================================================================
def apply_analytics_filters(confluence_row, df_analytics):
    event_time = confluence_row['wall_time']
    wall_side = confluence_row['wall_side']
    
    # Находим ближайшее событие УСТ в аналитике (в пределах 10 минут)
    time_diff = abs((df_analytics['event_time'] - event_time).dt.total_seconds())
    closest_event = df_analytics.loc[time_diff.idxmin()]
    
    if time_diff.min() > 600:  # 10 минут
        return True, "No close analytics data"
    
    # 1. Определяем направление ПРОСТО по стороне стены
    trade_side = 'long' if wall_side == 'bid' else 'short'
    
    passed = True
    reasons = []
    
    # 2. Фильтр: Разворот/Истощение дельты (Поглощение)
    if FILTER_DELTA_REVERSAL_ENABLED:
        delta_before = closest_event['delta_before']
        delta_after = closest_event['delta_after']
        delta_shift = delta_after - delta_before  # Насколько дельта "улучшилась"
        
        if trade_side == 'long':
            # Для LONG: мы хотим, чтобы ДО были продажи (delta_before < 0), 
            # а ПОСЛЕ давление ослабло или развернулось (delta_shift > порога)
            if delta_shift < DELTA_SHIFT_MIN:
                passed = False
                reasons.append(f"Нет разворота дельты. Сдвиг: {delta_shift:.0f} (нужно > {DELTA_SHIFT_MIN})")
        else:  # short
            # Для SHORT: мы хотим, чтобы ДО были покупки (delta_before > 0),
            # а ПОСЛЕ давление ослабло (delta_shift < -порога)
            if delta_shift > -DELTA_SHIFT_MIN:
                passed = False
                reasons.append(f"Нет разворота дельты. Сдвиг: {delta_shift:.0f} (нужно < {-DELTA_SHIFT_MIN})")
    
    return passed, "; ".join(reasons) if reasons else "Absorption confirmed"

# ==============================================================================
# 4. СИМУЛЯЦИЯ ТОРГОВЛИ
# ==============================================================================
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
    
    if not check_delta_reversal(df_trades, wall_time, wall_side):
        return {'type': 'main', 'side': trade_side, 'status': 'filtered', 'pnl': 0.0, 'reason': 'delta_filter'}
    
    tp1_price = entry_price + (risk * TP1_MULT) if trade_side == 'long' else entry_price - (risk * TP1_MULT)
    tp2_price = entry_price + (risk * TP2_MULT) if trade_side == 'long' else entry_price - (risk * TP2_MULT)
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
            exit_reason = 'TP1+TP2'
        else:
            pnl = (pnl_1 * 0.5) - COMMISSION
            exit_reason = 'TP1_only'
    else:
        exit_reason = 'Timeout'
        
    return {
        'type': 'main', 'side': trade_side, 'status': 'completed' if exit_reason != 'Timeout' else 'timeout',
        'pnl': pnl, 'exit_reason': exit_reason, 'entry': entry_price
    }

# ==============================================================================
# 5. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 БЭКТЕСТ С ИСПРАВЛЕННЫМИ ФИЛЬТРАМИ (Ордерфлоу: Поглощение)")
    print(f"   Логика: Ищем сдвиг дельты >= {DELTA_SHIFT_MIN} SOL в нашу пользу\n")
    
    df_conf, df_analytics = load_data()
    day_conf = df_conf[df_conf['date'] == TARGET_DATE]
    day_analytics = df_analytics[df_analytics['date'] == TARGET_DATE]
    
    print(f"[INFO] Загрузка данных для {TARGET_DATE}...")
    df_depth = load_depth_data(TARGET_DATE)
    df_trades = load_trades_data(TARGET_DATE)
    
    if df_depth is None:
        print("❌ Данные не найдены")
        exit()
    
    # Бэктест БЕЗ фильтров
    print(f"\n{'='*80}")
    print(f"📊 БАЗОВЫЙ БЭКТЕСТ (без фильтров)")
    print(f"{'='*80}")
    results_base = []
    for idx, row in day_conf.iterrows():
        res = simulate_trade(df_depth, df_trades, row)
        if res:
            res['filter_reason'] = 'No filter'
            results_base.append(res)
    
    df_base = pd.DataFrame(results_base)
    df_base_completed = df_base[df_base['status'] == 'completed']
    
    if not df_base_completed.empty:
        wr_base = (len(df_base_completed[df_base_completed['pnl'] > 0]) / len(df_base_completed)) * 100
        pnl_base = df_base_completed['pnl'].sum() * 100
        print(f"  Сделок: {len(df_base_completed)} | Win Rate: {wr_base:.1f}% | Total PnL: {pnl_base:.2f}%")
    
    # Бэктест С фильтрами
    print(f"\n{'='*80}")
    print(f"🎯 БЭКТЕСТ С ФИЛЬТРАМИ ПОГЛОЩЕНИЯ")
    print(f"{'='*80}")
    
    results_filtered = []
    filtered_out = []
    
    for idx, row in day_conf.iterrows():
        passed, reason = apply_analytics_filters(row, day_analytics)
        
        if not passed:
            filtered_out.append({'wall_time': row['wall_time'], 'side': row['wall_side'], 'reason': reason})
            continue
        
        res = simulate_trade(df_depth, df_trades, row)
        if res:
            res['filter_reason'] = reason
            results_filtered.append(res)
    
    df_filtered = pd.DataFrame(results_filtered)
    df_filtered_completed = df_filtered[df_filtered['status'] == 'completed']
    
    if not df_filtered_completed.empty:
        wr_filtered = (len(df_filtered_completed[df_filtered_completed['pnl'] > 0]) / len(df_filtered_completed)) * 100
        pnl_filtered = df_filtered_completed['pnl'].sum() * 100
        print(f"  Сделок: {len(df_filtered_completed)} (отфильтровано: {len(filtered_out)})")
        print(f"  Win Rate: {wr_filtered:.1f}% | Total PnL: {pnl_filtered:.2f}%")
    else:
        print(f"  Все сделки отфильтрованы! ({len(filtered_out)} отклонено)")
    
    # Сравнение
    print(f"\n{'='*80}")
    print(f"📈 СРАВНЕНИЕ РЕЗУЛЬТАТОВ")
    print(f"{'='*80}")
    if not df_base_completed.empty and not df_filtered_completed.empty:
        improvement_wr = wr_filtered - wr_base
        improvement_pnl = pnl_filtered - pnl_base
        print(f"  Win Rate: {wr_base:.1f}% → {wr_filtered:.1f}% ({improvement_wr:+.1f}%)")
        print(f"  Total PnL: {pnl_base:.2f}% → {pnl_filtered:.2f}% ({improvement_pnl:+.2f}%)")
        
        if improvement_wr >= 0 and improvement_pnl >= 0:
            print(f"\n✅ ФИЛЬТРЫ УЛУЧШИЛИ или СОХРАНИЛИ результат!")
        else:
            print(f"\n⚠️ Фильтры изменили состав сделок. Нужен анализ отклоненных.")
    
    if filtered_out:
        print(f"\n{'='*80}")
        print(f"🔍 ОТФИЛЬТРОВАННЫЕ СДЕЛКИ ({len(filtered_out)})")
        print(f"{'='*80}")
        for i, f in enumerate(filtered_out, 1):
            print(f"  {i}. {f['wall_time']} | {f['side']:<4} | {f['reason']}")
    
    print(f"\n🏁 Тест завершен!")