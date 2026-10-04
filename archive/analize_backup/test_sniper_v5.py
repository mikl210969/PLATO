"""
PLATO Sniper V5 Backtest
Проверка гипотезы: Агрегация крупных ордеров -> Откат -> Вход с подтверждением
"""
import pandas as pd
import numpy as np
from pathlib import Path
import time

# ==============================================================================
# 1. КОНФИГУРАЦИЯ "СНАЙПЕРА"
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
BAR_SEC = 5

# Параметры агрегации крупных ордеров (Твои числа, адаптированные)
BIG_ORDER_MIN_QTY = 50.0      # Считаем только сделки от 50 SOL
AGGREGATION_TARGET = 500.0    # Сумма таких сделок за окно должна быть > 500 SOL
WINDOW_BARS = 36              # 36 баров по 5 сек = 180 сек (3 минуты)

# Параметры управления позицией
ATR_PERIOD = 14
SL_MULT = 1.5                 # Стоп-лосс = 1.5 * ATR
TP1_MULT = 1.0                # Тейк-профт 1 (Сейф) = 1.0 * Риск (закрываем 50%)
TP2_MULT = 2.5                # Тейк-профт 2 = 2.5 * Риск
COMMISSION = 0.0007           # 0.07% за сделку (вход + выход)

# ==============================================================================
# 2. ПОДГОТОВКА ДАННЫХ
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
    
    # Расчет базовых метрик на 5-секундных барах
    df = df_depth.copy()
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    
    # ATR
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['prev_close']),
                                     abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=ATR_PERIOD).mean().fillna(df['tr'].mean())
    
    # Дельта на баре
    df_trades['is_buy'] = df_trades['is_buyer_maker'] == False
    df_trades['delta'] = np.where(df_trades['is_buy'], df_trades['quantity'], -df_trades['quantity'])
    
    # Агрегация ТОЛЬКО крупных ордеров (>50 SOL) на 5-секундные бары
    big_trades = df_trades[df_trades['quantity'] > BIG_ORDER_MIN_QTY].copy()
    big_trades['bar_time'] = big_trades['timestamp'].dt.floor(f'{BAR_SEC}s')
    bar_delta = big_trades.groupby('bar_time')['delta'].sum().reset_index()
    bar_delta.rename(columns={'delta': 'big_order_delta'}, inplace=True)
    
    # Сливаем с основным датафреймом
    df = pd.merge_asof(df.sort_values('timestamp'), 
                       bar_delta.sort_values('bar_time'), 
                       left_on='timestamp', right_on='bar_time', direction='backward')
    df['big_order_delta'] = df['big_order_delta'].fillna(0.0)
    
    # 🔥 КЛЮЧЕВОЙ ИНДИКАТОР: Скользящая сумма крупных ордеров за 3 минуты (36 баров)
    df['rolling_big_vol'] = df['big_order_delta'].abs().rolling(window=WINDOW_BARS, min_periods=1).sum()
    
    print(f"[INFO] Данные подготовлены. {len(df)} баров.")
    return df

# ==============================================================================
# 3. ДВИЖОК БЭКТЕСТА (State Machine)
# ==============================================================================
def run_sniper_backtest(df):
    trades = []
    n = len(df)
    
    # Состояния: 'IDLE', 'WAITING_PULLBACK', 'IN_TRADE'
    state = 'IDLE'
    zone_price = 0.0
    trade_side = ''
    entry_price = 0.0
    sl_price = 0.0
    tp1_price = 0.0
    tp2_price = 0.0
    entry_idx = 0
    
    i = 0
    while i < n:
        row = df.iloc[i]
        price = row['mid_price']
        atr = row['atr']
        
        # --- СОСТОЯНИЕ 1: ОЖИДАНИЕ ИНИЦИАЦИИ (Кит зашел в рынок) ---
        if state == 'IDLE':
            if row['rolling_big_vol'] >= AGGREGATION_TARGET:
                # Определяем направление импульса по знаку дельты крупных ордеров
                if row['big_order_delta'] > 0:
                    trade_side = 'long'
                    zone_price = price
                else:
                    trade_side = 'short'
                    zone_price = price
                
                state = 'WAITING_PULLBACK'
                # print(f"[{row['timestamp']}] Инициация {trade_side.upper()} найдена. Зона: {zone_price:.2f}")
        
        # --- СОСТОЯНИЕ 2: ОЖИДАНИЕ ОТКАТА К ЗОНЕ ---
        elif state == 'WAITING_PULLBACK':
            # Проверяем, вернулась ли цена к зоне (допуск 0.3% или 0.5 * ATR, что больше)
            tolerance = max(0.003 * zone_price, 0.5 * atr)
            
            if trade_side == 'long':
                # Для лонга цена должна упасть к зоне, но не пробить её сильно вниз
                if abs(price - zone_price) <= tolerance:
                    # Подтверждение: дельта текущего бара должна быть положительной (поглощение/разворот)
                    if row['big_order_delta'] > 0 or (price > df.iloc[i-1]['mid_price']):
                        state = 'IN_TRADE'
                        entry_price = price
                        entry_idx = i
                        risk = atr * SL_MULT
                        sl_price = entry_price - risk
                        tp1_price = entry_price + (risk * TP1_MULT)
                        tp2_price = entry_price + (risk * TP2_MULT)
            else: # short
                if abs(price - zone_price) <= tolerance:
                    if row['big_order_delta'] < 0 or (price < df.iloc[i-1]['mid_price']):
                        state = 'IN_TRADE'
                        entry_price = price
                        entry_idx = i
                        risk = atr * SL_MULT
                        sl_price = entry_price + risk
                        tp1_price = entry_price - (risk * TP1_MULT)
                        tp2_price = entry_price - (risk * TP2_MULT)

        # --- СОСТОЯНИЕ 3: В СДЕЛКЕ (Управление рисками) ---
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
                
            # Логика закрытия с учетом "Сейфа"
            pnl = 0.0
            closed = False
            
            # Сценарий А: Удар по стопу до любого тейка
            if not hit_sl.empty and (hit_tp1.empty or hit_sl.index[0] < hit_tp1.index[0]):
                pnl = ((sl_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - sl_price) / entry_price)
                pnl -= COMMISSION
                closed = True
                
            # Сценарий Б: Достигли TP1, но не TP2 (Сейф сработал, потом цена развернулась и ударила SL или мы закрыли остаток вручную. Для простоты бэктеста: закрываем всё по TP1, если TP2 не достигнут быстро, или считаем средний PnL)
            # Упрощенная, но честная логика для бэктеста: 50% по TP1, 50% по TP2. Если ударил SL после TP1, вторая половина уходит в 0.
            elif not hit_tp1.empty:
                if not hit_tp2.empty and hit_tp2.index[0] < hit_sl.index[0] if not hit_sl.empty else True:
                    # Достигли обоих тейков
                    pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
                    pnl_2 = ((tp2_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp2_price) / entry_price)
                    pnl = (pnl_1 * 0.5 + pnl_2 * 0.5) - COMMISSION
                else:
                    # Достигли TP1, но потом ударил SL (вторая половина в 0)
                    pnl_1 = ((tp1_price - entry_price) / entry_price) if trade_side == 'long' else ((entry_price - tp1_price) / entry_price)
                    pnl = (pnl_1 * 0.5) - COMMISSION # Вторая половина закрыта по SL в 0 PnL (или небольшой минус, упростим до 0 для второй половины)
                closed = True
                
            if closed:
                trades.append({'side': trade_side, 'pnl': pnl, 'entry': entry_price, 'exit_reason': 'TP/SL'})
                state = 'IDLE'
                i = entry_idx + 72 # Кулдаун 6 минут после сделки, чтобы не заходить сразу на том же месте
                continue
                
        i += 1
        
    return pd.DataFrame(trades)

# ==============================================================================
# 4. ЗАПУСК И АНАЛИЗ
# ==============================================================================
def analyze_results(trades_df, label):
    if trades_df.empty:
        print(f"\n[{label}] Сделок не найдено.")
        return
        
    total_trades = len(trades_df)
    winning_trades = len(trades_df[trades_df['pnl'] > 0])
    winrate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
    total_pnl = trades_df['pnl'].sum() * 100
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"📊 РЕЗУЛЬТАТЫ: {label}")
    print(f"{'='*60}")
    print(f"Всего сделок:      {total_trades}")
    print(f"Winrate:           {winrate:.1f}%")
    print(f"Total PnL:         {total_pnl:.2f}%")
    print(f"Avg PnL per trade: {avg_pnl:.3f}%")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    print("🚀 ЗАПУСК ТЕСТА SNIPER V5 (Агрегация + Откат + Сейф)")
    
    # Тест на разных периодах
    dates_train = ["2026-09-28", "2026-09-29"]
    dates_val = ["2026-09-30"]
    dates_test = ["2026-10-01"]
    
    for dates, label in [(dates_train, "TRAIN (28-29 Сен)"), 
                         (dates_val, "VALIDATION (30 Сен)"), 
                         (dates_test, "TEST (01 Окт)")]:
        df = load_and_prepare_data(dates)
        trades = run_sniper_backtest(df)
        analyze_results(trades, label)
        
        # Сохраняем детали для ручного разбора
        if not trades.empty:
            trades.to_csv(DATA_DIR / f"sniper_v5_trades_{label.replace(' ', '_')}.csv", index=False)
            
    print("✅ Тест завершен. Проверь CSV файлы в папке Data для детального разбора.")