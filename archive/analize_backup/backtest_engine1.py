"""
PLATO Backtest Engine v2.0
Честная симуляция с портированной логикой features/walls.py
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

# Параметры, идентичные live-платформе + ужесточенные фильтры из твоего плана
STRATEGY_PARAMS = {
    "wall_fade_v3": {
        "min_confidence": 0.8,
        "min_update_count": 30,
        "max_eaten_pct": 0.3,
        "max_dist_pct": 0.15,
        "min_delta_sol": 150.0,
        "max_delta_sol": 600.0,
        # 🔥 НОВЫЕ ПРАВИЛА BTC КОНТЕКСТА
        "allow_btc_flat": False,
        "btc_flat_threshold_pct": 0.08,      # Если изменение BTC < 0.08%, считаем флэтом
        "btc_max_adverse_dump_pct": -0.15    # Если BTC падает сильнее 0.15%, шортить опасно (стену снесут)
    }
}

POSITION_SIZE_USDT = 100.0
SL_PCT = 0.005  # 0.5%
TP_PCT = 0.010  # 1.0%

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_data(dates: list[str]) -> pd.DataFrame:
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
            # Рассчитываем 5-минутное изменение цены в %
            df_btc['btc_5m_change_pct'] = df_btc['close'].pct_change() * 100
            df_btc['btc_5m_volume'] = df_btc['volume']
            btc_frames.append(df_btc[['timestamp', 'btc_5m_change_pct', 'btc_5m_volume']])

    if not depth_frames or not trade_frames:
        raise FileNotFoundError("Не найдены файлы данных SOL")
        
    df_depth_all = pd.concat(depth_frames, ignore_index=True).sort_values('timestamp')
    df_trade_all = pd.concat(trade_frames, ignore_index=True).sort_values('timestamp')
    
    # Объединяем и сортируем BTC, заполняем пропуски (forward fill для первой строки)
    if btc_frames:
        df_btc_all = pd.concat(btc_frames, ignore_index=True).sort_values('timestamp')
        df_btc_all['btc_5m_change_pct'] = df_btc_all['btc_5m_change_pct'].fillna(0.0)
    else:
        raise FileNotFoundError("Не найдены файлы данных BTC")

    print(f"[INFO] ВСЕГО: {len(df_depth_all)} снапшотов SOL, {len(df_trade_all)} сделок, {len(df_btc_all)} свечей BTC")
    return df_depth_all, df_trade_all, df_btc_all

# ==============================================================================
# 3. РАСЧЕТ ФЕЙЧЕЙ (С ЧЕСТНЫМ ДЕТЕКТОРОМ СТЕН)
# ==============================================================================
def calculate_features(df_depth: pd.DataFrame, df_trades: pd.DataFrame, df_btc: pd.DataFrame) -> pd.DataFrame:
    print("[INFO] Расчет фич, симуляция WallsFeature и слияние с BTC...")
    
    # 1. Базовые фичи SOL
    df_depth['mid_price'] = (df_depth['bid_p_1'] + df_depth['ask_p_1']) / 2
    
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    df_trades['delta'] = np.where(df_trades['is_buy'], df_trades['quantity'], -df_trades['quantity'])
    df_trades = df_trades.set_index('timestamp')
    df_trades['delta_5s'] = df_trades['delta'].rolling('5s').sum()
    df_trades = df_trades.reset_index()
    
    df_trades_agg = df_trades[['timestamp', 'delta_5s']].drop_duplicates(subset=['timestamp'], keep='last')
    
    # 2. Слияние стакана и ленты SOL -> создаем df_merged
    df_merged = pd.merge_asof(
        df_depth.sort_values('timestamp'),
        df_trades_agg.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    df_merged['delta_5s'] = df_merged['delta_5s'].fillna(0.0)
    
    # 3. 🔥 Слияние с данными BTC (теперь df_merged уже существует)
    df_merged = pd.merge_asof(
        df_merged.sort_values('timestamp'),
        df_btc.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    df_merged['btc_5m_change_pct'] = df_merged['btc_5m_change_pct'].fillna(0.0)
    
    # 4. Инициализация колонок для стен
    for col in ['ask_wall_conf', 'ask_wall_updates', 'ask_wall_eaten', 'ask_wall_dist_pct',
                'bid_wall_conf', 'bid_wall_updates', 'bid_wall_eaten', 'bid_wall_dist_pct']:
        df_merged[col] = 0.0
        
    # 5. Stateful симуляция WallsFeature (аналог live-логики)
    median_history = deque(maxlen=10)
    active_walls = {}  # price -> wall_data
    
    print("[INFO] Обработка снапшотов (это может занять 10-20 сек)...")
    
    for idx, row in df_merged.iterrows():
        ts = row['timestamp'].timestamp()
        
        # Топ-50 уровней (как в live config)
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
        
        # Обработка асков (для SHORT)
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
                    
        # Обработка бидов (для LONG)
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
                    
        # Удаление исчезнувших стен (упрощенная проверка релокации)
        disappeared = set(active_walls.keys()) - current_wall_prices
        for price in list(disappeared):
            w = active_walls[price]
            relocated = False
            # Проверка релокации в пределах 3 тиков (0.03)
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
                
        # Сборка снапшота для текущей строки
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
                'confidence': confidence, 
                'update_count': w['update_count'],
                'eaten_pct': w['eaten_pct'], 
                'price': w['price'],
                'size': w['size']
            }
            
            if w['side'] == 'ask' and (best_ask is None or w['size'] > best_ask['size']):
                best_ask = wall_data
            elif w['side'] == 'bid' and (best_bid is None or w['size'] > best_bid['size']):
                best_bid = wall_data
                
        # Запись в DataFrame
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
# 4. СТРАТЕГИЯ
# ==============================================================================
def evaluate_wall_fade_v3(row: pd.Series, params: dict, side: str) -> dict:
    abs_delta = abs(row.get('delta_5s', 0.0))
    btc_change = row.get('btc_5m_change_pct', 0.0)
    
    # 1. Проверка локальной дельты SOL
    if abs_delta < params['min_delta_sol']:
        return {'signal': None, 'reason': f'FLAT_SOL (d={abs_delta:.0f})'}
    if abs_delta > params['max_delta_sol']:
        return {'signal': None, 'reason': f'IMPULSIVE_SOL (d={abs_delta:.0f})'}
        
    # 2. 🔥 ПРОВЕРКА BTC КОНТЕКСТА (для SHORT)
    if not params['allow_btc_flat'] and abs(btc_change) < params['btc_flat_threshold_pct']:
        return {'signal': None, 'reason': f'BTC_FLAT (chg={btc_change:.2f}%)'}
        
    if side == 'SHORT' and btc_change < params['btc_max_adverse_dump_pct']:
        return {'signal': None, 'reason': f'BTC_DUMPING (chg={btc_change:.2f}%)'}
        
    # 3. Проверка параметров стены (как было раньше)
    if side == 'SHORT':
        conf = row.get('ask_wall_conf', 0.0)
        updates = row.get('ask_wall_updates', 0)
        eaten = row.get('ask_wall_eaten', 1.0)
        dist = row.get('ask_wall_dist_pct', 100.0)
    else:
        conf = row.get('bid_wall_conf', 0.0)
        updates = row.get('bid_wall_updates', 0)
        eaten = row.get('bid_wall_eaten', 1.0)
        dist = row.get('bid_wall_dist_pct', 100.0)
        
    if conf < params['min_confidence']:
        return {'signal': None, 'reason': f'LOW_CONF ({conf:.2f})'}
    if updates < params['min_update_count']:
        return {'signal': None, 'reason': f'LOW_UPDATES ({updates})'}
    if eaten > params['max_eaten_pct']:
        return {'signal': None, 'reason': f'HIGH_EATEN ({eaten:.2f})'}
    if dist > params['max_dist_pct']:
        return {'signal': None, 'reason': f'WALL_TOO_FAR ({dist:.2f}%)'}
        
    return {'signal': side, 'reason': 'VALID_WALL_FADE'}

# ==============================================================================
# 5. ДВИЖОК И ОТЧЕТ (без изменений, работают корректно)
# ==============================================================================
# ==============================================================================
# 5. ДВИЖОК БЭКТЕСТА (С ЧАСТИЧНЫМ ФИКСОМ И БЕЗУБЫТКОМ)
# ==============================================================================
def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    print("[INFO] Запуск симуляции (с частичным фиксом 50% и переводом в BE)...")
    results = []
    
    in_position = False
    entry_price = 0.0
    tp1_hit = False
    sl_price = 0.0
    
    # Параметры для SHORT
    TP1_PCT = 0.005  # 0.5% для частичного фикса
    TP2_PCT = 0.010  # 1.0% для окончательного выхода
    INITIAL_SL_PCT = 0.005 # 0.5% начальный стоп
    
    for idx, row in df.iterrows():
        current_price = row['mid_price']
        
        if not in_position:
            # Проверка входа
            eval_result = evaluate_wall_fade_v3(row, STRATEGY_PARAMS['wall_fade_v3'], side='SHORT')
            if eval_result['signal'] == 'SHORT':
                in_position = True
                entry_price = current_price
                tp1_hit = False
                # Начальный SL для SHORT: цена входа + 0.5%
                sl_price = entry_price * (1 + INITIAL_SL_PCT)
                
                results.append({
                    'timestamp': row['timestamp'],
                    'type': 'ENTRY',
                    'side': 'SHORT',
                    'entry_price': entry_price,
                    'reason': eval_result['reason']
                })
        else:
            # Мы в позиции. Проверяем условия выхода.
            
            # 1. Проверка TP1 (частичный фикс 50%)
            if not tp1_hit:
                tp1_price = entry_price * (1 - TP1_PCT)
                if current_price <= tp1_price:
                    pnl_pct = (entry_price - current_price) / entry_price * 0.5  # 50% объема
                    results.append({
                        'timestamp': row['timestamp'],
                        'type': 'EXIT_TP1',
                        'side': 'SHORT',
                        'entry_price': entry_price,
                        'exit_price': current_price,
                        'pnl_pct': pnl_pct,
                        'reason': 'PARTIAL_TP_50%'
                    })
                    tp1_hit = True
                    sl_price = entry_price  # 🔥 ПЕРЕВОД В БЕЗУБЫТОК
            
            # 2. Проверка SL или TP2 для оставшейся части
            # Для SHORT: SL срабатывает, если цена >= sl_price
            if current_price >= sl_price:
                remaining_size = 0.5 if tp1_hit else 1.0
                pnl_pct = (entry_price - current_price) / entry_price * remaining_size
                
                # Определяем причину: если мы выше цены входа, это стоп. Если ниже, но сработал триггер - это TP2 (или трейлинг)
                reason = 'STOP_LOSS' if current_price > entry_price else 'TAKE_PROFIT_2'
                
                results.append({
                    'timestamp': row['timestamp'],
                    'type': 'EXIT_FINAL',
                    'side': 'SHORT',
                    'entry_price': entry_price,
                    'exit_price': current_price,
                    'pnl_pct': pnl_pct,
                    'reason': reason
                })
                in_position = False
                
    return pd.DataFrame(results)

def generate_report(trades_df: pd.DataFrame):
    print("\n" + "="*70)
    print(" ОТЧЕТ БЭКТЕСТА PLATO (v2.1 - ЧАСТИЧНЫЙ ФИКС + BE)")
    print("="*70)
    if trades_df.empty:
        print("Сделки не найдены.")
        return
        
    entries = trades_df[trades_df['type'] == 'ENTRY']
    print(f"Всего попыток входа (сигналов): {len(entries)}")
    
    # Считаем PnL по всем закрытиям (TP1 и FINAL)
    exits = trades_df[trades_df['type'].isin(['EXIT_TP1', 'EXIT_FINAL'])]
    
    if not exits.empty:
        # Группируем по времени входа, чтобы посчитать результат полной сделки
        # Но для простоты оценки стратегии суммируем весь PnL
        total_pnl_pct = exits['pnl_pct'].sum()
        
        # Подсчет "выигрышных" итераций (где суммарный PnL по паре TP1+FINAL > 0)
        # Для упрощенного отчета покажем просто сумму
        print(f"Завершенных этапов выхода: {len(exits)}")
        print(f"Суммарный PnL (%): {total_pnl_pct:.4f}%")
        print(f"Суммарный PnL (USDT): {total_pnl_pct * POSITION_SIZE_USDT:.2f}")
        
        print("\n--- Последние 10 событий выхода ---")
        print(exits[['timestamp', 'type', 'side', 'entry_price', 'exit_price', 'pnl_pct', 'reason']].tail(10).to_string(index=False))
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
        
        output_file = DATA_DIR / "backtest_results_v2.2_btc_filtered.csv"
        trades.to_csv(output_file, index=False)
        print(f"[INFO] Результаты сохранены в: {output_file}")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()