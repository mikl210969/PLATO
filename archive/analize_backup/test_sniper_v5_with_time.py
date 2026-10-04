"""
PLATO Sniper V5 Backtest с временными метками
Добавляет дату и время к каждой сделке для визуальной проверки на графике
"""
import pandas as pd
import numpy as np
from pathlib import Path

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
BAR_SEC = 5

# Параметры (те же, что в оригинальном тесте)
BIG_ORDER_MIN_QTY = 50.0
AGGREGATION_TARGET = 500.0
WINDOW_BARS = 36
ATR_PERIOD = 14
SL_MULT = 1.5
TP1_MULT = 1.0
TP2_MULT = 2.5
COMMISSION = 0.0007

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_and_prepare_data(dates):
    print(f"[INFO] Загрузка данных за {dates}...")
    depth_frames, trade_frames = [], []
    
    for date_str in dates:
        f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
        if f_depth.exists():
            df_d = pd.read_csv(f_depth)
            df_d['timestamp'] = pd.to_datetime(df_d['timestamp'])
            depth_frames.append(df_d)
            
        f_trade = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}.csv"
        if f_trade.exists():
            df_t = pd.read_csv(f_trade)
            df_t['timestamp'] = pd.to_datetime(df_t['timestamp'], unit='ms')
            trade_frames.append(df_t)

    df_depth = pd.concat(depth_frames).sort_values('timestamp').reset_index(drop=True)
    df_trades = pd.concat(trade_frames).sort_values('timestamp').reset_index(drop=True)
    
    df = df_depth.copy()
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['prev_close']),
                                     abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=ATR_PERIOD).mean().fillna(df['tr'].mean())
    
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    df_trades['delta'] = np.where(df_trades['is_buy'], df_trades['quantity'], -df_trades['quantity'])
    
    big_trades = df_trades[df_trades['quantity'] > BIG_ORDER_MIN_QTY].copy()
    big_trades['bar_time'] = big_trades['timestamp'].dt.floor(f'{BAR_SEC}s')
    bar_delta = big_trades.groupby('bar_time')['delta'].sum().reset_index()
    bar_delta.rename(columns={'delta': 'big_order_delta'}, inplace=True)
    
    df = pd.merge_asof(df.sort_values('timestamp'), 
                       bar_delta.sort_values('bar_time'), 
                       left_on='timestamp', right_on='bar_time', direction='backward')
    df['big_order_delta'] = df['big_order_delta'].fillna(0.0)
    df['rolling_big_vol'] = df['big_order_delta'].abs().rolling(window=WINDOW_BARS, min_periods=1).sum()
    
    print(f"[INFO] Данные подготовлены. {len(df)} баров.")
    return df

# ==============================================================================
# 3. БЭКТЕСТ С ВРЕМЕННЫМИ МЕТКАМИ
# ==============================================================================
def run_sniper_backtest_with_time(df):
    trades = []
    n = len(df)
    
    state = 'IDLE'
    zone_price = 0.0
    trade_side = ''
    entry_price = 0.0
    entry_time = None
    sl_price = 0.0
    tp1_price = 0.0
    tp2_price = 0.0
    entry_idx = 0
    
    i = 0
    while i < n:
        row = df.iloc[i]
        price = row['mid_price']
        atr = row['atr']
        timestamp = row['timestamp']
        
        if state == 'IDLE':
            if row['rolling_big_vol'] >= AGGREGATION_TARGET:
                if row['big_order_delta'] > 0:
                    trade_side = 'long'
                    zone_price = price
                else:
                    trade_side = 'short'
                    zone_price = price
                
                state = 'WAITING_PULLBACK'
        
        elif state == 'WAITING_PULLBACK':
            tolerance = max(0.003 * zone_price, 0.5 * atr)
            
            if trade_side == 'long':
                if abs(price - zone_price) <= tolerance:
                    if row['big_order_delta'] > 0 or (price > df.iloc[i-1]['mid_price']):
                        state = 'IN_TRADE'
                        entry_price = price
                        entry_time = timestamp
                        entry_idx = i
                        risk = atr * SL_MULT
                        sl_price = entry_price - risk
                        tp1_price = entry_price + (risk * TP1_MULT)
                        tp2_price = entry_price + (risk * TP2_MULT)
            else:
                if abs(price - zone_price) <= tolerance:
                    if row['big_order_delta'] < 0 or (price < df.iloc[i-1]['mid_price']):
                        state = 'IN_TRADE'
                        entry_price = price
                        entry_time = timestamp
                        entry_idx = i
                        risk = atr * SL_MULT
                        sl_price = entry_price + risk
                        tp1_price = entry_price - (risk * TP1_MULT)
                        tp2_price = entry_price - (risk * TP2_MULT)

        elif state == 'IN_TRADE':
            future = df.iloc[i+1:]
            if future.empty:
                break
                
            if trade_side == 'long':
                hit_sl = future[future['low'] <= sl_price]
                hit_tp1 = future[future['high'] >= tp1_price]
                hit_tp2 = future[future['high'] >= tp2_price]
            else:
                hit_sl = future[future['high'] >= sl_price]
                hit_tp1 = future[future['low'] <= tp1_price]
                hit_tp2 = future[future['low'] <= tp2_price]
                
            pnl = 0.0
            closed = False
            
            if hit_sl.empty and hit_tp1.empty:
                pass
            elif not hit_sl.empty and (hit_tp1.empty or hit_sl.index[0] < hit_tp1.index[0]):
                pnl = ((sl_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - sl_price) / entry_price)
                pnl -= COMMISSION
                closed = True
            elif not hit_tp1.empty:
                if not hit_tp2.empty and hit_tp2.index[0] < hit_sl.index[0] if not hit_sl.empty else True:
                    pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
                    pnl_2 = ((tp2_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp2_price) / entry_price)
                    pnl = (pnl_1 * 0.5 + pnl_2 * 0.5) - COMMISSION
                else:
                    pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
                    pnl = (pnl_1 * 0.5) - COMMISSION
                closed = True
                
            if closed:
                trades.append({
                    'date': entry_time.strftime('%Y-%m-%d'),
                    'time': entry_time.strftime('%H:%M:%S'),
                    'side': trade_side,
                    'pnl': round(pnl, 6),
                    'entry': round(entry_price, 2),
                    'exit_reason': 'TP/SL'
                })
                state = 'IDLE'
                i = entry_idx + 72
                continue
                
        i += 1
        
    return pd.DataFrame(trades)

# ==============================================================================
# 4. ЗАПУСК
# ==============================================================================
if __name__ == "__main__":
    print("🚀 БЭКТЕСТ SNIPER V5 С ВРЕМЕННЫМИ МЕТКАМИ\n")
    
    dates_list = [
        (["2026-09-28", "2026-09-29"], "TRAIN_28-29_Sep"),
        (["2026-09-30"], "VALIDATION_30_Sep"),
        (["2026-10-01"], "TEST_01_Oct")
    ]
    
    for dates, label in dates_list:
        print(f"\n{'='*60}")
        print(f"Обработка: {label}")
        print(f"{'='*60}")
        
        df = load_and_prepare_data(dates)
        trades = run_sniper_backtest_with_time(df)
        
        if not trades.empty:
            output_file = DATA_DIR / f"sniper_v5_trades_{label}_with_time.csv"
            trades.to_csv(output_file, index=False)
            print(f"\n✅ Сохранено {len(trades)} сделок с временем")
            print(f"   Файл: {output_file}")
            print(f"\n📊 Первые 10 сделок:")
            print(trades.head(10).to_string(index=False))
        else:
            print("⚠️ Сделок не найдено")
    
    print(f"\n{'='*60}")
    print("🏁 Готово! Теперь можешь проверить каждую сделку на графике Binance")
    print(f"{'='*60}")