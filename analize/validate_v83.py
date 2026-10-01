"""
PLATO Validation Script v8.3
Валидация ТОП-1 конфигурации на out-of-sample данных (30 сентября)
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ==============================================================================
# 1. КОНФИГУРАЦИЯ (ТОП-1 из оптимизации v8.3)
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")  # 🔥 Домашний путь
SYMBOL = "SOLUSDT"
DATES_TO_TEST = ["2026-09-30"]  # 🔥 Теперь с полными данными!

BIG_ORDER_THRESHOLD = 30.0
COMMISSION_PER_TRADE = 0.0007

#  ТОП-1 ПАРАМЕТРЫ ИЗ ОПТИМИЗАЦИИ
VALIDATION_PARAMS = {
    'delta_long_thresh': 200,
    'delta_short_thresh': 30,
    'imb_tf': 5,
    'hvn_tf': 60,
    'hvn_max_dist': 0.35,
    'r_mult': 2.0
}

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data(dates) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f"[INFO] Загрузка спотовых данных за {dates}...")
    depth_frames, trade_frames = [], []
    for date_str in dates:
        for ext in ['.csv']:
            f = DATA_DIR / f"{SYMBOL}_depth_{date_str}{ext}"
            if f.exists():
                df_depth = pd.read_csv(f)
                df_depth['timestamp'] = pd.to_datetime(df_depth['timestamp'])
                depth_frames.append(df_depth)
                print(f"  ✅ Загружен стакан: {f.name} ({len(df_depth)} строк)")
                break
        for ext in ['.csv']:
            f = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}{ext}"
            if f.exists():
                df_trade = pd.read_csv(f)
                if 'datetime' in df_trade.columns:
                    df_trade['timestamp'] = pd.to_datetime(df_trade['datetime'])
                else:
                    df_trade['timestamp'] = pd.to_datetime(df_trade['timestamp'], unit='ms')
                trade_frames.append(df_trade)
                print(f"  ✅ Загружена лента: {f.name} ({len(df_trade)} сделок)")
                break
    if not depth_frames or not trade_frames:
        raise FileNotFoundError("Нет данных")
    return (pd.concat(depth_frames).sort_values('timestamp').reset_index(drop=True),
            pd.concat(trade_frames).sort_values('timestamp').reset_index(drop=True))

# ==============================================================================
# 3. РАСЧЁТ ФЕЙЧЕЙ (идентично backtest_engine.py v8.3)
# ==============================================================================
def calculate_all_features(df_depth, df_trades):
    print("[INFO] Расчёт фич...")
    df = df_depth.copy()
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    
    # ATR
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['prev_close']),
                                     abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=14).mean().fillna(0.15)
    
    # 🔥 Фильтрация крупных ордеров
    total_trades = len(df_trades)
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    big_mask = df_trades['quantity'] > BIG_ORDER_THRESHOLD
    big_trades = df_trades[big_mask].copy()
    
    print(f"[INFO] Фильтрация ленты: {total_trades} всего -> {len(big_trades)} крупных (>{BIG_ORDER_THRESHOLD} SOL)")
    
    big_trades['delta'] = np.where(big_trades['is_buy'], big_trades['quantity'], -big_trades['quantity'])
    big_trades = big_trades.set_index('timestamp')
    big_trades['delta_10s'] = big_trades['delta'].rolling('10s').sum()
    big_trades['delta_60s'] = big_trades['delta'].rolling('60s').sum()
    big_trades = big_trades.reset_index()
    
    df = pd.merge_asof(df.sort_values('timestamp'),
                       big_trades[['timestamp', 'delta_10s', 'delta_60s']].drop_duplicates('timestamp').sort_values('timestamp'),
                       on='timestamp', direction='backward')
    df['delta_10s'] = df['delta_10s'].fillna(0.0)
    df['delta_60s'] = df['delta_60s'].fillna(0.0)
    
    # Имбаланс
    bid_cols = [f'bid_v_{i}' for i in range(1, 11)]
    ask_cols = [f'ask_v_{i}' for i in range(1, 11)]
    df['bid_total'] = df[bid_cols].sum(axis=1)
    df['ask_total'] = df[ask_cols].sum(axis=1)
    
    for sec in [5, 30]:
        n_bars = max(1, sec // 5)
        bid_smooth = df['bid_total'].rolling(n_bars, min_periods=1).mean()
        ask_smooth = df['ask_total'].rolling(n_bars, min_periods=1).mean()
        df[f'imb_{sec}s'] = (bid_smooth - ask_smooth) / (bid_smooth + ask_smooth + 1e-8)
        df[f'imb_{sec}s'] = df[f'imb_{sec}s'].fillna(0.0)
    
    # HVN
    print("[INFO] Расчёт HVN...")
    for tf_min in [5, 15, 60]:
        df['window_id'] = df['timestamp'].dt.floor(f'{tf_min}min')
        
        idx_below = df.groupby('window_id')['bid_total'].idxmax()
        hvn_below = df.loc[idx_below, ['window_id', 'mid_price']].rename(columns={'mid_price': f'hvn_below_price_{tf_min}m'})
        df = df.merge(hvn_below, on='window_id', how='left')
        df[f'hvn_below_dist_{tf_min}m'] = ((df['mid_price'] - df[f'hvn_below_price_{tf_min}m']) / df['mid_price']).abs() * 100
        
        idx_above = df.groupby('window_id')['ask_total'].idxmax()
        hvn_above = df.loc[idx_above, ['window_id', 'mid_price']].rename(columns={'mid_price': f'hvn_above_price_{tf_min}m'})
        df = df.merge(hvn_above, on='window_id', how='left')
        df[f'hvn_above_dist_{tf_min}m'] = ((df[f'hvn_above_price_{tf_min}m'] - df['mid_price']) / df['mid_price']).abs() * 100
        
        df = df.drop(columns=['window_id'])
        df[f'hvn_below_dist_{tf_min}m'] = df[f'hvn_below_dist_{tf_min}m'].fillna(999.0)
        df[f'hvn_above_dist_{tf_min}m'] = df[f'hvn_above_dist_{tf_min}m'].fillna(999.0)
    
    print(f"[INFO] Фичи рассчитаны. {len(df)} строк")
    return df

# ==============================================================================
# 4. ДВИЖОК (идентично v8.3)
# ==============================================================================
def run_absorption_big_orders(df, params):
    delta_long_thresh = params['delta_long_thresh']
    delta_short_thresh = params['delta_short_thresh']
    imb_tf = params['imb_tf']
    hvn_tf = params['hvn_tf']
    hvn_max_dist = params['hvn_max_dist']
    r_mult = params['r_mult']
    
    long_cond = (df['delta_60s'] < -delta_long_thresh) & \
                (df['delta_10s'] > -delta_short_thresh) & \
                (df[f'imb_{imb_tf}s'] < 0.1) & \
                (df[f'hvn_below_dist_{hvn_tf}m'] <= hvn_max_dist)
    
    short_cond = (df['delta_60s'] > delta_long_thresh) & \
                 (df['delta_10s'] < delta_short_thresh) & \
                 (df[f'imb_{imb_tf}s'] > -0.1) & \
                 (df[f'hvn_above_dist_{hvn_tf}m'] <= hvn_max_dist)
    
    entry_indices = []
    last_entry = -30
    
    all_signals = []
    for idx in np.where(long_cond)[0]:
        all_signals.append((idx, 'long'))
    for idx in np.where(short_cond)[0]:
        all_signals.append((idx, 'short'))
    all_signals.sort(key=lambda x: x[0])
    
    for idx, side in all_signals:
        if idx - last_entry < 30:
            continue
        entry_indices.append((idx, side))
        last_entry = idx
    
    if not entry_indices:
        return pd.DataFrame()
    
    results = []
    for entry_idx, side in entry_indices:
        entry_price = df.iloc[entry_idx]['mid_price']
        atr = df.iloc[entry_idx]['atr']
        
        if side == 'long':
            sl_anchor = df.iloc[entry_idx][f'hvn_below_price_{hvn_tf}m']
            r_val = max(abs(entry_price - sl_anchor) + (atr * 0.2), atr)
            sl_price = sl_anchor - (atr * 0.2)
            tp_price = entry_price + (r_val * r_mult)
            
            future = df.iloc[entry_idx+1:]
            hit_sl = future[future['mid_price'] <= sl_price]
            hit_tp = future[future['mid_price'] >= tp_price]
            
            if hit_sl.empty and hit_tp.empty:
                continue
            
            if hit_tp.empty or (not hit_sl.empty and hit_sl.index[0] < hit_tp.index[0]):
                pnl = ((sl_price - entry_price) / entry_price) - COMMISSION_PER_TRADE
            else:
                pnl = ((tp_price - entry_price) / entry_price) - COMMISSION_PER_TRADE
        else:
            sl_anchor = df.iloc[entry_idx][f'hvn_above_price_{hvn_tf}m']
            r_val = max(abs(sl_anchor - entry_price) + (atr * 0.2), atr)
            sl_price = sl_anchor + (atr * 0.2)
            tp_price = entry_price - (r_val * r_mult)
            
            future = df.iloc[entry_idx+1:]
            hit_sl = future[future['mid_price'] >= sl_price]
            hit_tp = future[future['mid_price'] <= tp_price]
            
            if hit_sl.empty and hit_tp.empty:
                continue
            
            if hit_tp.empty or (not hit_sl.empty and hit_sl.index[0] < hit_tp.index[0]):
                pnl = ((entry_price - sl_price) / entry_price) - COMMISSION_PER_TRADE
            else:
                pnl = ((entry_price - tp_price) / entry_price) - COMMISSION_PER_TRADE
        
        results.append({
            'timestamp': df.iloc[entry_idx]['timestamp'],
            'side': side,
            'entry_price': entry_price,
            'pnl': pnl
        })
    
    return pd.DataFrame(results)

# ==============================================================================
# 5. ВАЛИДАЦИЯ
# ==============================================================================
def run_validation(df):
    print("\n" + "="*80)
    print(" ВАЛИДАЦИЯ ТОП-1 КОНФИГУРАЦИИ (Out-of-Sample)")
    print("="*80)
    print(f"\n Параметры:")
    for k, v in VALIDATION_PARAMS.items():
        print(f"   {k}: {v}")
    
    trades = run_absorption_big_orders(df, VALIDATION_PARAMS)
    
    if trades.empty:
        print("\n❌ Сделок не найдено на этих данных.")
        return
    
    total_trades = len(trades)
    wins = len(trades[trades['pnl'] > 0])
    losses = len(trades[trades['pnl'] <= 0])
    winrate = (wins / total_trades) * 100
    total_pnl = trades['pnl'].sum() * 100
    avg_pnl = total_pnl / total_trades
    best_trade = trades['pnl'].max() * 100
    worst_trade = trades['pnl'].min() * 100
    
    longs = len(trades[trades['side'] == 'long'])
    shorts = len(trades[trades['side'] == 'short'])
    
    print(f"\n РЕЗУЛЬТАТЫ ВАЛИДАЦИИ:")
    print(f"   Всего сделок:    {total_trades}")
    print(f"   LONG / SHORT:    {longs} / {shorts}")
    print(f"   Выигрышных:      {wins}")
    print(f"   Проигрышных:     {losses}")
    print(f"   Winrate:         {winrate:.1f}%")
    print(f"   Total PnL:       {total_pnl:.2f}%")
    print(f"   Avg PnL:         {avg_pnl:.4f}%")
    print(f"   Лучшая сделка:   {best_trade:.3f}%")
    print(f"   Худшая сделка:   {worst_trade:.3f}%")
    
    # Сравнение с обучающей выборкой
    print(f"\n📈 СРАВНЕНИЕ С ОБУЧАЮЩЕЙ ВЫБОРКОЙ (28-29 сентября):")
    print(f"   Обучение:  114 сделок, 51.8% WR, +3.09% PnL")
    print(f"   Валидация: {total_trades} сделок, {winrate:.1f}% WR, {total_pnl:+.2f}% PnL")
    
    if total_pnl > 0 and winrate > 45:
        print(f"\n✅ ЭДЖ ПОДТВЕРЖДЁН! Стратегия работает на новых данных.")
    elif total_pnl > 0:
        print(f"\n⚠️ Частичное подтверждение. PnL положительный, но винрейт низкий.")
    else:
        print(f"\n❌ ЭДЖ НЕ ПОДТВЕРЖДЁН. Вероятно переобучение на обучающей выборке.")
    
    print("="*80)
    
    # Сохраняем результаты
    output_file = DATA_DIR / "validation_results_v83_30sep.csv"
    trades.to_csv(output_file, index=False)
    print(f"\n[INFO] Результаты сохранены: {output_file}")

# ==============================================================================
# 6. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    try:
        df_depth, df_trades = load_data(DATES_TO_TEST)
        df = calculate_all_features(df_depth, df_trades)
        run_validation(df)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()