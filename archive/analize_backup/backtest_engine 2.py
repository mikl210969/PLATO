"""
PLATO Backtest Engine v3.0
Стратегия: AbsorptionV2 (Поглощение)
Фичи: Честный WallsFeature + Агрессивный поток (10s) + BTC Context
Выходы: Частичный фикс 50% + Безубыток
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import deque

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\m.ongudushev\YandexDisk\Data")
SYMBOL = "SOLUSDT"
DATES_TO_TEST = ["2026-09-28", "2026-09-29"]


# Параметры AbsorptionV2 (ЗОЛОТАЯ СЕРЕДИНА)
STRATEGY_PARAMS = {
    "absorption_v2": {
        # Параметры стены
        "min_wall_volume_sol": 8000.0,
        "min_confidence": 0.8,
        "min_update_count": 20,          # 🔥 КОМПРОМИСС: 100 секунд жизни стены
        "max_eaten_pct": 0.5,            
        "max_dist_pct": 0.15,            
        
        # Параметры поглощения
        "min_aggressive_volume": 250.0,  # 🔥 КОМПРОМИСС: между 200 и 300
        "max_price_move_pct": 0.08,      
        
        # BTC Context (без изменений)
        "allow_btc_flat": False,
        "btc_flat_threshold_pct": 0.08,
        "btc_max_adverse_dump_pct": -0.15
    }
}

POSITION_SIZE_USDT = 100.0

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data(dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    print(f"[INFO] Загрузка данных SOL и BTC за {dates}...")
    depth_frames, trade_frames, btc_frames = [], [], []
    
    for date_str in dates:
        # 1. Стакан SOL
        for ext in ['.csv', '.xlsx', '.xls']:
            f = DATA_DIR / f"{SYMBOL}_depth_{date_str}{ext}"
            if f.exists():
                df_depth = pd.read_csv(f) if ext == '.csv' else pd.read_excel(f)
                df_depth['timestamp'] = pd.to_datetime(df_depth['timestamp'])
                depth_frames.append(df_depth)
                break
        
        # 2. Лента SOL
        for ext in ['.csv', '.xlsx', '.xls']:
            f = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}{ext}"
            if f.exists():
                df_trade = pd.read_csv(f) if ext == '.csv' else pd.read_excel(f)
                if 'datetime' in df_trade.columns:
                    df_trade['timestamp'] = pd.to_datetime(df_trade['datetime'])
                else:
                    df_trade['timestamp'] = pd.to_datetime(df_trade['timestamp'], unit='ms')
                trade_frames.append(df_trade)
                break
                
        # 3. Данные BTC (5m)
        btc_file = DATA_DIR / f"BTCUSDT_5m_{date_str}.csv"
        if btc_file.exists():
            df_btc = pd.read_csv(btc_file)
            df_btc['timestamp'] = pd.to_datetime(df_btc['timestamp'])
            df_btc['btc_5m_change_pct'] = df_btc['close'].pct_change() * 100
            btc_frames.append(df_btc[['timestamp', 'btc_5m_change_pct']])

    if not depth_frames or not trade_frames:
        raise FileNotFoundError("Не найдены файлы данных SOL")
        
    df_depth_all = pd.concat(depth_frames, ignore_index=True).sort_values('timestamp')
    df_trade_all = pd.concat(trade_frames, ignore_index=True).sort_values('timestamp')
    
    if btc_frames:
        df_btc_all = pd.concat(btc_frames, ignore_index=True).sort_values('timestamp')
        df_btc_all['btc_5m_change_pct'] = df_btc_all['btc_5m_change_pct'].fillna(0.0)
    else:
        raise FileNotFoundError("Не найдены файлы данных BTC")

    print(f"[INFO] ВСЕГО: {len(df_depth_all)} снапшотов SOL, {len(df_trade_all)} сделок, {len(df_btc_all)} свечей BTC")
    return df_depth_all, df_trade_all, df_btc_all

# ==============================================================================
# 3. РАСЧЕТ ФЕЙЧЕЙ
# ==============================================================================
def calculate_features(df_depth: pd.DataFrame, df_trades: pd.DataFrame, df_btc: pd.DataFrame) -> pd.DataFrame:
    print("[INFO] Расчет фич, симуляция WallsFeature и слияние...")
    
    # 1. Базовые фичи SOL
    df_depth['mid_price'] = (df_depth['bid_p_1'] + df_depth['ask_p_1']) / 2
    
    # Агрессивные объемы
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    df_trades['buy_vol'] = np.where(df_trades['is_buy'], df_trades['quantity'], 0.0)
    df_trades['sell_vol'] = np.where(~df_trades['is_buy'], df_trades['quantity'], 0.0)
    df_trades['delta'] = df_trades['buy_vol'] - df_trades['sell_vol']
    
    df_trades = df_trades.set_index('timestamp')
    df_trades['delta_5s'] = df_trades['delta'].rolling('5s').sum()
    df_trades['buy_vol_10s'] = df_trades['buy_vol'].rolling('10s').sum()
    df_trades['sell_vol_10s'] = df_trades['sell_vol'].rolling('10s').sum()
    df_trades = df_trades.reset_index()
    
    df_trades_agg = df_trades[['timestamp', 'delta_5s', 'buy_vol_10s', 'sell_vol_10s']].drop_duplicates(subset=['timestamp'], keep='last')
    
    # 2. Слияние стакана и ленты
    df_merged = pd.merge_asof(
        df_depth.sort_values('timestamp'),
        df_trades_agg.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    df_merged[['delta_5s', 'buy_vol_10s', 'sell_vol_10s']] = df_merged[['delta_5s', 'buy_vol_10s', 'sell_vol_10s']].fillna(0.0)
    
    # 3. Изменение цены за ~10 секунд (2 периода по 5 сек)
    df_merged['price_change_10s'] = df_merged['mid_price'].pct_change(2) * 100
    df_merged['price_change_10s'] = df_merged['price_change_10s'].fillna(0.0)
    
    # 4. Слияние с BTC
    df_merged = pd.merge_asof(
        df_merged.sort_values('timestamp'),
        df_btc.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    df_merged['btc_5m_change_pct'] = df_merged['btc_5m_change_pct'].fillna(0.0)
    
    # 5. Инициализация колонок для стен
    for col in ['ask_wall_conf', 'ask_wall_updates', 'ask_wall_eaten', 'ask_wall_dist_pct',
                'bid_wall_conf', 'bid_wall_updates', 'bid_wall_eaten', 'bid_wall_dist_pct']:
        df_merged[col] = 0.0
        
    # 6. Stateful симуляция WallsFeature (копия логики из features/walls.py)
    median_history = deque(maxlen=10)
    active_walls = {}
    
    print("[INFO] Обработка снапшотов (WallsFeature emulation)...")
    
    for idx, row in df_merged.iterrows():
        ts = row['timestamp'].timestamp()
        
        bid_sizes = [row[f'bid_v_{i}'] for i in range(1, 51) if row[f'bid_v_{i}'] > 0]
        ask_sizes = [row[f'ask_v_{i}'] for i in range(1, 51) if row[f'ask_v_{i}'] > 0]
        
        if not bid_sizes or not ask_sizes:
            continue
            
        median_bid = sorted(bid_sizes)[len(bid_sizes) // 2]
        median_ask = sorted(ask_sizes)[len(ask_sizes) // 2]
        median_history.append((median_bid + median_ask) / 2.0)
        
        smooth_median = sum(median_history) / len(median_history)
        threshold = smooth_median * 1.5
        
        current_wall_prices = set()
        
        # Аски
        for i in range(1, 51):
            price = row[f'ask_p_{i}']
            qty = row[f'ask_v_{i}']
            if qty >= threshold:
                current_wall_prices.add(price)
                if price in active_walls:
                    w = active_walls[price]
                    w['last_seen'] = ts
                    w['update_count'] += 1
                    w['size_history'].append(qty)
                    w['size'] = qty
                    if qty < w['initial_size']:
                        w['eaten_pct'] = (w['initial_size'] - qty) / w['initial_size']
                    else:
                        w['initial_size'] = qty
                else:
                    active_walls[price] = {
                        'side': 'ask', 'price': price, 'initial_size': qty, 'size': qty,
                        'first_seen': ts, 'last_seen': ts, 'update_count': 1,
                        'size_history': deque([qty], maxlen=50), 'eaten_pct': 0.0, 'status': 'alive'
                    }
                    
        # Биды
        for i in range(1, 51):
            price = row[f'bid_p_{i}']
            qty = row[f'bid_v_{i}']
            if qty >= threshold:
                current_wall_prices.add(price)
                if price in active_walls:
                    w = active_walls[price]
                    w['last_seen'] = ts
                    w['update_count'] += 1
                    w['size_history'].append(qty)
                    w['size'] = qty
                    if qty < w['initial_size']:
                        w['eaten_pct'] = (w['initial_size'] - qty) / w['initial_size']
                    else:
                        w['initial_size'] = qty
                else:
                    active_walls[price] = {
                        'side': 'bid', 'price': price, 'initial_size': qty, 'size': qty,
                        'first_seen': ts, 'last_seen': ts, 'update_count': 1,
                        'size_history': deque([qty], maxlen=50), 'eaten_pct': 0.0, 'status': 'alive'
                    }
                    
        # Удаление исчезнувших
        disappeared = set(active_walls.keys()) - current_wall_prices
        for price in list(disappeared):
            w = active_walls[price]
            relocated = False
            search_prices = [row[f'ask_p_{i}'] for i in range(1, 51)] if w['side'] == 'ask' else [row[f'bid_p_{i}'] for i in range(1, 51)]
            search_qtys = [row[f'ask_v_{i}'] for i in range(1, 51)] if w['side'] == 'ask' else [row[f'bid_v_{i}'] for i in range(1, 51)]
            
            for p, q in zip(search_prices, search_qtys):
                if abs(p - price) <= 0.03 and q >= w['initial_size'] * 0.7:
                    active_walls[p] = w
                    active_walls[p]['price'] = p
                    active_walls[p]['last_seen'] = ts
                    del active_walls[price]
                    relocated = True
                    break
            if not relocated:
                del active_walls[price]
                
        # Сборка снапшота
        best_ask = None
        best_bid = None
        
        for w in active_walls.values():
            age = ts - w['first_seen']
            if age < 2.0:
                continue
                
            sizes = list(w['size_history'])
            if len(sizes) >= 2:
                mean_s = sum(sizes) / len(sizes)
                if mean_s > 0:
                    var = sum((s - mean_s)**2 for s in sizes) / len(sizes)
                    cv = (var ** 0.5) / mean_s
                else:
                    cv = 0.0
            else:
                cv = 0.0
                
            if cv > 0.15:
                continue
                
            confidence = min(1.0, age / 6.0)
            wall_data = {
                'confidence': confidence, 'update_count': w['update_count'],
                'eaten_pct': w['eaten_pct'], 'price': w['price'], 'size': w['size']
            }
            
            if w['side'] == 'ask' and (best_ask is None or w['size'] > best_ask['size']):
                best_ask = wall_data
            elif w['side'] == 'bid' and (best_bid is None or w['size'] > best_bid['size']):
                best_bid = wall_data
                
        if best_ask:
            df_merged.at[idx, 'ask_wall_conf'] = best_ask['confidence']
            df_merged.at[idx, 'ask_wall_updates'] = best_ask['update_count']
            df_merged.at[idx, 'ask_wall_eaten'] = best_ask['eaten_pct']
            df_merged.at[idx, 'ask_wall_dist_pct'] = ((best_ask['price'] - row['mid_price']) / row['mid_price']) * 100
            
        if best_bid:
            df_merged.at[idx, 'bid_wall_conf'] = best_bid['confidence']
            df_merged.at[idx, 'bid_wall_updates'] = best_bid['update_count']
            df_merged.at[idx, 'bid_wall_eaten'] = best_bid['eaten_pct']
            df_merged.at[idx, 'bid_wall_dist_pct'] = ((row['mid_price'] - best_bid['price']) / row['mid_price']) * 100

    print(f"[INFO] Фичи рассчитаны. Датасет: {len(df_merged)} строк")
    return df_merged

# ==============================================================================
# 4. СТРАТЕГИЯ: ABSORPTION V2
# ==============================================================================
def evaluate_absorption_v2(row: pd.Series, params: dict, side: str) -> dict:
    abs_delta = abs(row.get('delta_5s', 0.0))
    btc_change = row.get('btc_5m_change_pct', 0.0)
    
    # 1. Базовая проверка дельты SOL
    if abs_delta < 100.0: # Минимальная активность
        return {'signal': None, 'reason': f'FLAT_SOL (d={abs_delta:.0f})'}
        
    # 2. BTC Context
    if not params['allow_btc_flat'] and abs(btc_change) < params['btc_flat_threshold_pct']:
        return {'signal': None, 'reason': f'BTC_FLAT (chg={btc_change:.2f}%)'}
    if side == 'SHORT' and btc_change < params['btc_max_adverse_dump_pct']:
        return {'signal': None, 'reason': f'BTC_DUMPING (chg={btc_change:.2f}%)'}
        
    # 3. Проверка стены (нужна "доска", об которую поглощают)
    if side == 'SHORT':
        conf = row.get('ask_wall_conf', 0.0)
        updates = row.get('ask_wall_updates', 0)
        eaten = row.get('ask_wall_eaten', 1.0)
        dist = row.get('ask_wall_dist_pct', 100.0)
        agg_vol = row.get('buy_vol_10s', 0.0) # Агрессивные ПОКУПКИ бьют в АСК стену
    else:
        conf = row.get('bid_wall_conf', 0.0)
        updates = row.get('bid_wall_updates', 0)
        eaten = row.get('bid_wall_eaten', 1.0)
        dist = row.get('bid_wall_dist_pct', 100.0)
        agg_vol = row.get('sell_vol_10s', 0.0) # Агрессивные ПРОДАЖИ бьют в БИД стену
        
    if conf < params['min_confidence']:
        return {'signal': None, 'reason': f'LOW_CONF ({conf:.2f})'}
    if updates < params['min_update_count']:
        return {'signal': None, 'reason': f'LOW_UPDATES ({updates})'}
    if eaten > params['max_eaten_pct']:
        return {'signal': None, 'reason': f'HIGH_EATEN ({eaten:.2f})'}
    if dist > params['max_dist_pct']:
        return {'signal': None, 'reason': f'WALL_TOO_FAR ({dist:.2f}%)'}
        
    # 4. 🔥 ПРОВЕРКА ПОГЛОЩЕНИЯ
    if agg_vol < params['min_aggressive_volume']:
        return {'signal': None, 'reason': f'LOW_AGG_VOL ({agg_vol:.0f} < {params["min_aggressive_volume"]})'}
        
    price_move = abs(row.get('price_change_10s', 0.0))
    if price_move > params['max_price_move_pct']:
        return {'signal': None, 'reason': f'PRICE_MOVING_TOO_FAST ({price_move:.2f}%)'}
        
    return {'signal': side, 'reason': f'ABSORPTION (vol={agg_vol:.0f}, move={price_move:.2f}%)'}

# ==============================================================================
# 5. ДВИЖОК (Частичный фикс + Безубыток)
# ==============================================================================
def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    print("[INFO] Запуск симуляции (AbsorptionV2 + Partial TP + BE)...")
    results = []
    
    in_position = False
    entry_price = 0.0
    tp1_hit = False
    sl_price = 0.0
    
    TP1_PCT = 0.005
    INITIAL_SL_PCT = 0.005 
    
    for idx, row in df.iterrows():
        current_price = row['mid_price']
        
        if not in_position:
            eval_result = evaluate_absorption_v2(row, STRATEGY_PARAMS['absorption_v2'], side='SHORT')
            if eval_result['signal'] == 'SHORT':
                in_position = True
                entry_price = current_price
                tp1_hit = False
                sl_price = entry_price * (1 + INITIAL_SL_PCT)
                
                results.append({
                    'timestamp': row['timestamp'], 'type': 'ENTRY', 'side': 'SHORT',
                    'entry_price': entry_price, 'reason': eval_result['reason']
                })
        else:
            if not tp1_hit:
                tp1_price = entry_price * (1 - TP1_PCT)
                if current_price <= tp1_price:
                    pnl_pct = (entry_price - current_price) / entry_price * 0.5
                    results.append({
                        'timestamp': row['timestamp'], 'type': 'EXIT_TP1', 'side': 'SHORT',
                        'entry_price': entry_price, 'exit_price': current_price,
                        'pnl_pct': pnl_pct, 'reason': 'PARTIAL_TP_50%'
                    })
                    tp1_hit = True
                    sl_price = entry_price  # Перевод в безубыток
            
            if current_price >= sl_price:
                remaining_size = 0.5 if tp1_hit else 1.0
                pnl_pct = (entry_price - current_price) / entry_price * remaining_size
                reason = 'STOP_LOSS' if current_price > entry_price else 'TAKE_PROFIT_2'
                
                results.append({
                    'timestamp': row['timestamp'], 'type': 'EXIT_FINAL', 'side': 'SHORT',
                    'entry_price': entry_price, 'exit_price': current_price,
                    'pnl_pct': pnl_pct, 'reason': reason
                })
                in_position = False
                
    return pd.DataFrame(results)

def generate_report(trades_df: pd.DataFrame):
    print("\n" + "="*70)
    print(" ОТЧЕТ БЭКТЕСТА PLATO (v3.0 - ABSORPTION V2)")
    print("="*70)
    if trades_df.empty:
        print("Сделки не найдены.")
        return
        
    entries = trades_df[trades_df['type'] == 'ENTRY']
    print(f"Всего попыток входа (сигналов): {len(entries)}")
    
    exits = trades_df[trades_df['type'].isin(['EXIT_TP1', 'EXIT_FINAL'])]
    
    if not exits.empty:
        total_pnl_pct = exits['pnl_pct'].sum()
        print(f"Завершенных этапов выхода: {len(exits)}")
        print(f"Суммарный PnL (%): {total_pnl_pct:.4f}%")
        print(f"Суммарный PnL (USDT): {total_pnl_pct * POSITION_SIZE_USDT:.2f}")
        
        print("\n--- Все события выхода ---")
        print(exits[['timestamp', 'type', 'side', 'entry_price', 'exit_price', 'pnl_pct', 'reason']].to_string(index=False))
    else:
        print("Нет завершенных сделок.")
    print("="*70 + "\n")

# ==============================================================================
# 6. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    try:
        df_depth, df_trades, df_btc = load_data(DATES_TO_TEST)
        df_merged = calculate_features(df_depth, df_trades, df_btc)
        trades = run_backtest(df_merged)
        generate_report(trades)
        
        output_file = DATA_DIR / "backtest_results_v3.0_absorption.csv"
        trades.to_csv(output_file, index=False)
        print(f"[INFO] Результаты сохранены в: {output_file}")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()