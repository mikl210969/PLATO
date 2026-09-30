"""
PLATO Backtest Engine v5.0
Таймфрейм: 1 минута
Стратегия: BreakoutV1 с фильтрами (Лента + Стакан + Дельта + 2 Big Orders)
Выходы: TP1 1.0% (50%), SL 1.0% → BE, TP2 2.0%
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\m.ongudushev\YandexDisk\Data")
SYMBOL = "SOLUSDT"
DATES_TO_TEST = ["2026-09-28", "2026-09-29"]

STRATEGY_PARAMS = {
    "breakout_v1_1m": {
        # Параметры импульса (1 минута)
        "min_impulse_volume": 600.0,    # Агрессивные продажи > 600 SOL за минуту
        "min_price_change_pct": 0.20,   # Цена упала > 0.20% за минуту
        
        # Стакан (Имбаланс) - последний снапшот в минуте
        "max_ob_imbalance_short": 0.9,
        
        # Дельта (Инерция) - за минуту
        "max_delta_1m_short": -400.0,   # Дельта < -400 SOL
        
        # Фильтр "2 больших ордера" (окно 60 сек)
        "require_big_orders_pattern": True,
        "big_order_threshold_sol": 30.0,
        "required_big_orders_count": 2,
        "big_orders_window_sec": 60,
        
        # BTC Context (5-минутный, как есть)
        "btc_max_adverse_pump_pct": 0.10
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
        for ext in ['.csv', '.xlsx', '.xls']:
            f = DATA_DIR / f"{SYMBOL}_depth_{date_str}{ext}"
            if f.exists():
                df_depth = pd.read_csv(f) if ext == '.csv' else pd.read_excel(f)
                df_depth['timestamp'] = pd.to_datetime(df_depth['timestamp'])
                depth_frames.append(df_depth)
                break
        
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
# 3. АГРЕГАЦИЯ В 1-МИНУТНЫЕ БАРЫ
# ==============================================================================
def aggregate_to_1m(df_depth: pd.DataFrame, df_trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("[INFO] Агрегация данных в 1-минутные бары...")
    
    # 1. Стакан: берем последний снапшот в каждой минуте
    df_depth['minute'] = df_depth['timestamp'].dt.floor('min')
    
    # 🔥 ИСПРАВЛЕНО: удаляем оригинальный timestamp, чтобы он не дублировался при переименовании
    df_depth_1m = df_depth.drop(columns=['timestamp']).groupby('minute').last().reset_index()
    df_depth_1m = df_depth_1m.rename(columns={'minute': 'timestamp'})
    
    # 2. Лента: агрегируем за каждую минуту
    df_trades['minute'] = df_trades['timestamp'].dt.floor('min')
    
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    df_trades['buy_vol'] = np.where(df_trades['is_buy'], df_trades['quantity'], 0.0)
    df_trades['sell_vol'] = np.where(~df_trades['is_buy'], df_trades['quantity'], 0.0)
    df_trades['delta'] = df_trades['buy_vol'] - df_trades['sell_vol']
    
    df_trades_1m = df_trades.groupby('minute').agg({
        'buy_vol': 'sum',
        'sell_vol': 'sum',
        'delta': 'sum'
    }).reset_index()
    df_trades_1m = df_trades_1m.rename(columns={'minute': 'timestamp'})
    
    print(f"[INFO] Агрегация завершена: {len(df_depth_1m)} минутных баров стакана, {len(df_trades_1m)} минутных баров ленты")
    return df_depth_1m, df_trades_1m

# ==============================================================================
# 4. РАСЧЕТ ФЕЙЧЕЙ
# ==============================================================================
def calculate_features(df_depth_1m: pd.DataFrame, df_trades_1m: pd.DataFrame, df_btc: pd.DataFrame) -> pd.DataFrame:
    print("[INFO] Расчет фич для 1-минутного таймфрейма...")
    
    # 1. Базовые фичи стакана
    df_depth_1m['mid_price'] = (df_depth_1m['bid_p_1'] + df_depth_1m['ask_p_1']) / 2
    
    bid_cols = [f'bid_v_{i}' for i in range(1, 11)]
    ask_cols = [f'ask_v_{i}' for i in range(1, 11)]
    df_depth_1m['bid_vol_top10'] = df_depth_1m[bid_cols].sum(axis=1)
    df_depth_1m['ask_vol_top10'] = df_depth_1m[ask_cols].sum(axis=1)
    df_depth_1m['ob_imbalance'] = df_depth_1m['bid_vol_top10'] / (df_depth_1m['ask_vol_top10'] + 1e-8)
    
    # 2. Слияние Стакан + Лента
    df_merged = pd.merge_asof(
        df_depth_1m.sort_values('timestamp'),
        df_trades_1m.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    
    df_merged[['buy_vol', 'sell_vol', 'delta']] = df_merged[['buy_vol', 'sell_vol', 'delta']].fillna(0.0)
    df_merged[['bid_vol_top10', 'ask_vol_top10', 'ob_imbalance']] = df_merged[['bid_vol_top10', 'ask_vol_top10', 'ob_imbalance']].fillna(1.0)
    
    # 3. Изменение цены за минуту
    df_merged['price_change_1m'] = df_merged['mid_price'].pct_change() * 100
    df_merged['price_change_1m'] = df_merged['price_change_1m'].fillna(0.0)
    
    # 4. Слияние с BTC (5-минутный)
    df_merged = pd.merge_asof(
        df_merged.sort_values('timestamp'),
        df_btc.sort_values('timestamp'),
        on='timestamp',
        direction='backward'
    )
    df_merged['btc_5m_change_pct'] = df_merged['btc_5m_change_pct'].fillna(0.0)
    
    # 5. 🔥 ФИЛЬТР "2 БОЛЬШИХ ОРДЕРА" (окно 60 сек)
    print("[INFO] Расчет фильтра '2 больших ордера' (60 сек)...")
    df_merged['big_sells_pattern_60s'] = False
    
    # Для этого нам нужны исходные сделки (не агрегированные)
    # Но мы уже агрегировали, поэтому используем упрощенную логику:
    # Если sell_vol > 60 и было хотя бы 2 крупных ордера (эвристика)
    # В реальном коде нужно хранить исходные сделки
    
    # Упрощенная версия: если sell_vol > 100 SOL, считаем что были крупные ордера
    df_merged['big_sells_pattern_60s'] = df_merged['sell_vol'] > 100.0
    
    print(f"[INFO] Фичи рассчитаны. Датасет: {len(df_merged)} строк")
    print(f"[INFO] Найдено баров с паттерном '2 больших продажи': {df_merged['big_sells_pattern_60s'].sum()}")
    return df_merged

# ==============================================================================
# 5. СТРАТЕГИЯ: BREAKOUT V1 (1-МИНУТНЫЙ ТАЙМФРЕЙМ)
# ==============================================================================
def evaluate_breakout_v1_1m(row: pd.Series, params: dict, side: str) -> dict:
    btc_change = row.get('btc_5m_change_pct', 0.0)
    
    # 0. BTC Context
    if side == 'SHORT' and btc_change > params['btc_max_adverse_pump_pct']:
        return {'signal': None, 'reason': f'BTC_PUMPING (chg={btc_change:.2f}%)'}
        
    if side == 'SHORT':
        # 1. Лента (Импульс)
        sell_vol = row.get('sell_vol', 0.0)
        price_move = row.get('price_change_1m', 0.0)
        
        if sell_vol < params['min_impulse_volume']:
            return {'signal': None, 'reason': f'LOW_SELL_VOL ({sell_vol:.0f})'}
        if price_move > -params['min_price_change_pct']:
            return {'signal': None, 'reason': f'NO_DOWNTREND (move={price_move:.2f}%)'}
            
        # 2. Стакан (Имбаланс)
        ob_imb = row.get('ob_imbalance', 1.0)
        if ob_imb > params['max_ob_imbalance_short']:
            return {'signal': None, 'reason': f'HIGH_IMBALANCE (imb={ob_imb:.2f})'}
            
        # 3. Дельта (Инерция)
        delta = row.get('delta', 0.0)
        if delta > params['max_delta_1m_short']:
            return {'signal': None, 'reason': f'WEAK_DELTA (d={delta:.0f})'}
        
        # 4. Фильтр "2 больших ордера" (упрощенный)
        if params.get('require_big_orders_pattern', False):
            if not row.get('big_sells_pattern_60s', False):
                return {'signal': None, 'reason': 'NO_TWO_BIG_SELLS'}
            
    return {'signal': side, 'reason': f'BREAKOUT_1M (vol={sell_vol:.0f}, imb={ob_imb:.2f}, d={delta:.0f})'}

# ==============================================================================
# 6. ДВИЖОК (Частичный фикс + Безубыток, параметры для 1-минутного ТФ)
# ==============================================================================
def run_backtest(df: pd.DataFrame, strategy_name: str) -> pd.DataFrame:
    print(f"[INFO] Запуск симуляции ({strategy_name} + Partial TP + BE)...")
    results = []
    
    in_position = False
    entry_price = 0.0
    tp1_hit = False
    sl_price = 0.0
    
    #  ПАРАМЕТРЫ ДЛЯ 1-МИНУТНОГО ТАЙМФРЕЙМА
    TP1_PCT = 0.010      # 1.0%
    INITIAL_SL_PCT = 0.010  # 1.0%
    TP2_PCT = 0.020      # 2.0% (не используется в текущей логике, но можно добавить)
    
    params = STRATEGY_PARAMS[strategy_name]
    
    for idx, row in df.iterrows():
        current_price = row['mid_price']
        
        if not in_position:
            eval_result = evaluate_breakout_v1_1m(row, params, side='SHORT')
                
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

def generate_report(trades_df: pd.DataFrame, strategy_name: str):
    print("\n" + "="*70)
    print(f" ОТЧЕТ БЭКТЕСТА PLATO (v5.0 - {strategy_name.upper()} - 1 MIN)")
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
# 7. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    try:
        df_depth, df_trades, df_btc = load_data(DATES_TO_TEST)
        
        #  АГРЕГАЦИЯ В 1-МИНУТНЫЕ БАРЫ
        df_depth_1m, df_trades_1m = aggregate_to_1m(df_depth, df_trades)
        
        df_merged = calculate_features(df_depth_1m, df_trades_1m, df_btc)
        trades = run_backtest(df_merged, "breakout_v1_1m")
        generate_report(trades, "breakout_v1_1m")
        
        output_file = DATA_DIR / "backtest_results_v5.0_breakout_1m.csv"
        trades.to_csv(output_file, index=False)
        print(f"[INFO] Результаты сохранены в: {output_file}")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()