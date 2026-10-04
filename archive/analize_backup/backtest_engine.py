"""
PLATO Backtest Engine v8.3 (Absorption + Big Orders Filter)
Фильтрация ленты: только крупные ордера > 30 SOL
Логика: Разворот дельты + ограничение HVN
"""

import pandas as pd
import numpy as np
from pathlib import Path
import itertools
import time
import sys

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\m.ongudushev\YandexDisk\Data")
SYMBOL = "SOLUSDT"
DATES_TO_TEST = ["2026-09-28", "2026-09-29"]
POSITION_SIZE_USDT = 100.0
COMMISSION_PER_TRADE = 0.0007

# 🔥 ПОРОГ КРУПНОГО ОРДЕРА (как в live BreakoutV1)
BIG_ORDER_THRESHOLD = 30.0  # SOL

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data(dates):
    print(f"[INFO] Загрузка спотовых данных за {dates}...")
    depth_frames, trade_frames = [], []
    for date_str in dates:
        for ext in ['.csv']:
            f = DATA_DIR / f"{SYMBOL}_depth_{date_str}{ext}"
            if f.exists():
                df_depth = pd.read_csv(f)
                df_depth['timestamp'] = pd.to_datetime(df_depth['timestamp'])
                depth_frames.append(df_depth)
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
                break
    if not depth_frames or not trade_frames:
        raise FileNotFoundError("Нет данных")
    return (pd.concat(depth_frames).sort_values('timestamp').reset_index(drop=True),
            pd.concat(trade_frames).sort_values('timestamp').reset_index(drop=True))

# ==============================================================================
# 3. ПРЕДВЫЧИСЛЕНИЕ ФЕЙЧЕЙ (С ФИЛЬТРОМ КРУПНЫХ ОРДЕРОВ)
# ==============================================================================
def calculate_all_features(df_depth, df_trades):
    print("[INFO] Предвычисление мульти-таймфреймов...")
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
    
    # 🔥 ФИЛЬТРАЦИЯ КРУПНЫХ ОРДЕРОВ
    total_trades = len(df_trades)
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    
    # Считаем дельту ТОЛЬКО от крупных ордеров (> 30 SOL)
    big_mask = df_trades['quantity'] > BIG_ORDER_THRESHOLD
    big_trades = df_trades[big_mask].copy()
    
    print(f"[INFO] Фильтрация ленты: {total_trades} всего сделок -> {len(big_trades)} крупных (>{BIG_ORDER_THRESHOLD} SOL)")
    
    big_trades['delta'] = np.where(big_trades['is_buy'], big_trades['quantity'], -big_trades['quantity'])
    big_trades = big_trades.set_index('timestamp')
    
    # Дельта от крупных ордеров за разные окна
    big_trades['delta_10s'] = big_trades['delta'].rolling('10s').sum()
    big_trades['delta_60s'] = big_trades['delta'].rolling('60s').sum()
    
    big_trades = big_trades.reset_index()
    
    df = pd.merge_asof(df.sort_values('timestamp'),
                       big_trades[['timestamp', 'delta_10s', 'delta_60s']].drop_duplicates('timestamp').sort_values('timestamp'),
                       on='timestamp', direction='backward')
    df['delta_10s'] = df['delta_10s'].fillna(0.0)
    df['delta_60s'] = df['delta_60s'].fillna(0.0)
    
    # ИМБАЛАНС (весь стакан, без фильтрации)
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
# 4. ДВИЖОК (Разворот дельты от крупных ордеров)
# ==============================================================================
def run_absorption_big_orders(df, params):
    delta_long_thresh = params['delta_long_thresh']
    delta_short_thresh = params['delta_short_thresh']
    imb_tf = params['imb_tf']
    hvn_tf = params['hvn_tf']
    hvn_max_dist = params['hvn_max_dist']
    r_mult = params['r_mult']
    
    # LONG: Крупные продажи были (60с) + Разворот (10с) + Имбаланс + HVN рядом
    long_cond = (df['delta_60s'] < -delta_long_thresh) & \
                (df['delta_10s'] > -delta_short_thresh) & \
                (df[f'imb_{imb_tf}s'] < 0.1) & \
                (df[f'hvn_below_dist_{hvn_tf}m'] <= hvn_max_dist)
    
    # SHORT: Крупные покупки были (60с) + Разворот (10с) + Имбаланс + HVN рядом
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
        
        results.append({'side': side, 'entry_price': entry_price, 'pnl': pnl})
    
    return pd.DataFrame(results)

# ==============================================================================
# 5. ОПТИМИЗАТОР
# ==============================================================================
def run_optimizer(df):
    print("\n" + "="*80)
    print("[OPTIMIZER] Absorption + Фильтр крупных ордеров (>30 SOL)")
    print("="*80)
    
    param_grid = {
        'delta_long_thresh': [100, 200, 300],
        'delta_short_thresh': [30, 50, 80],
        'imb_tf': [5, 30],
        'hvn_tf': [5, 15, 60],
        'hvn_max_dist': [0.15, 0.25, 0.35],
        'r_mult': [1.5, 2.0, 2.5]
    }
    
    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    total = len(combinations)
    print(f"[INFO] Комбинаций: {total}")
    print("[INFO] Запуск...\n")
    
    results = []
    start_time = time.time()
    
    for i, params in enumerate(combinations):
        trades = run_absorption_big_orders(df, params)
        
        if not trades.empty:
            total_trades = len(trades)
            winrate = (len(trades[trades['pnl'] > 0]) / total_trades) * 100
            total_pnl = trades['pnl'].sum() * 100
            avg_pnl = total_pnl / total_trades
            
            results.append({
                'Trades': total_trades,
                'Winrate%': round(winrate, 1),
                'TotalPnL%': round(total_pnl, 2),
                'AvgPnL%': round(avg_pnl, 3),
                'Delta_Long': params['delta_long_thresh'],
                'Delta_Short': params['delta_short_thresh'],
                'Imb_TF': params['imb_tf'],
                'HVN_TF': params['hvn_tf'],
                'HVN_Dist%': params['hvn_max_dist'],
                'R_Mult': params['r_mult']
            })
        
        elapsed = time.time() - start_time
        progress = (i + 1) / total
        bar_length = 40
        filled = int(bar_length * progress)
        bar = '█' * filled + '-' * (bar_length - filled)
        eta = (elapsed / (i + 1)) * (total - i - 1) if i > 0 else 0
        
        sys.stdout.write(f'\r[{bar}] {progress*100:.1f}% | {i+1}/{total} | {elapsed:.0f}с | ETA: {eta:.0f}с')
        sys.stdout.flush()
    
    print(f"\n\n[INFO] Завершено за {time.time() - start_time:.1f} сек.")
    
    if not results:
        print("\n[WARNING] Не найдено ни одной сделки.")
        return
    
    df_results = pd.DataFrame(results)
    
    print("\n" + "="*80)
    print("🏆 ТОП-10 КОНФИГУРАЦИЙ (минимум 3 сделки)")
    print("="*80)
    top = df_results[df_results['Trades'] >= 3].nlargest(10, 'TotalPnL%')
    if top.empty:
        top = df_results.nlargest(10, 'TotalPnL%')
    print(top.to_string(index=False))
    
    output_file = DATA_DIR / "optimizer_results_v8.3_big_orders.csv"
    df_results.to_csv(output_file, index=False)
    print(f"\n[INFO] Результаты сохранены в: {output_file}")
    
    return df_results

# ==============================================================================
# 6. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    try:
        df_depth, df_trades = load_data(DATES_TO_TEST)
        df = calculate_all_features(df_depth, df_trades)
        results = run_optimizer(df)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()