"""
PLATO Sniper V6: Финальный бэктест на 86 конфлюэнсах.
Проверяет механику входа/выхода (Сейф 1:1 + TP2) на отфильтрованных сетапах.
"""
import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm

# ==============================================================================
# 1. КОНФИГУРАЦИЯ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
SYMBOL = "SOLUSDT"
CONFLUENCE_FILE = DATA_DIR / "sniper_v6_final_confluences.csv"

# Параметры управления позицией
ATR_PERIOD = 14
SL_MULT = 1.5       # Стоп-лосс = 1.5 * ATR за уровнем
TP1_MULT = 1.0      # Тейк-профт 1 (Сейф) = 1.0 * Риск (закрываем 50%)
TP2_MULT = 2.5      # Тейк-профт 2 = 2.5 * Риск
COMMISSION = 0.0007 # 0.07% за сделку (вход + выход)

# Параметры фильтра дельты (поглощение)
DELTA_WINDOW_SEC = 60   # Смотрим дельту за 60 секунд до входа
DELTA_REVERSAL_MIN = 0  # Минимальный разворот дельты для подтверждения

# ==============================================================================
# 2. ЗАГРУЗКА ДАННЫХ
# ==============================================================================
def load_confluences():
    """Загружает список конфлюэнсов."""
    if not CONFLUENCE_FILE.exists():
        raise FileNotFoundError(f"Файл не найден: {CONFLUENCE_FILE}")
    df = pd.read_csv(CONFLUENCE_FILE)
    df['ust_time'] = pd.to_datetime(df['ust_time'])
    df['wall_time'] = pd.to_datetime(df['wall_time'])
    print(f"[INFO] Загружено {len(df)} конфлюэнсов")
    return df

def load_depth_data(date_str):
    """Загружает стакан для конкретного дня."""
    f_depth = DATA_DIR / f"{SYMBOL}_depth_{date_str}.csv"
    if not f_depth.exists():
        return None
    df = pd.read_csv(f_depth)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid_price'] = (df['bid_p_1'] + df['ask_p_1']) / 2
    df['high'] = df[[f'ask_p_{i}' for i in range(1, 6)]].max(axis=1)
    df['low'] = df[[f'bid_p_{i}' for i in range(1, 6)]].min(axis=1)
    
    # ATR
    df['prev_close'] = df['mid_price'].shift(1)
    df['tr'] = np.maximum(df['high'] - df['low'],
                          np.maximum(abs(df['high'] - df['prev_close']),
                                     abs(df['low'] - df['prev_close'])))
    df['atr'] = df['tr'].rolling(window=ATR_PERIOD).mean().fillna(df['tr'].mean())
    
    return df

def load_trades_data(date_str):
    """Загружает ленту сделок для расчета дельты."""
    f_trades = DATA_DIR / f"{SYMBOL}_aggTrades_{date_str}.csv"
    if not f_trades.exists():
        return None
    df = pd.read_csv(f_trades)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['is_buy'] = df['is_buyer_maker'] == False
    df['delta'] = np.where(df['is_buy'], df['quantity'], -df['quantity'])
    return df

# ==============================================================================
# 3. ПРОВЕРКА ФИЛЬТРА ДЕЛЬТЫ (ПОГЛОЩЕНИЕ)
# ==============================================================================
def check_delta_reversal(df_trades, wall_time, wall_side):
    """
    Проверяет, был ли разворот дельты в момент касания стены.
    Возвращает True, если дельта подтверждает поглощение.
    """
    if df_trades is None:
        return True  # Если нет данных ленты, пропускаем фильтр
    
    # Смотрим дельту за 60 секунд ДО wall_time
    start_time = wall_time - pd.Timedelta(seconds=DELTA_WINDOW_SEC)
    end_time = wall_time
    
    mask = (df_trades['timestamp'] >= start_time) & (df_trades['timestamp'] <= end_time)
    window_trades = df_trades[mask]
    
    if window_trades.empty:
        return True  # Нет данных, пропускаем
    
    total_delta = window_trades['delta'].sum()
    
    # Для LONG (bid стена): дельта должна быть отрицательной (продажи поглощены)
    # Для SHORT (ask стена): дельта должна быть положительной (покупки поглощены)
    if wall_side == 'bid':
        return total_delta < 0  # Продавцы давили, но стена выстояла
    else:  # ask
        return total_delta > 0  # Покупатели давили, но стена выстояла

# ==============================================================================
# 4. СИМУЛЯЦИЯ ТОРГОВЛИ
# ==============================================================================
def simulate_trade(df_depth, df_trades, confluence_row):
    """
    Симулирует одну сделку на основе конфлюэнса.
    Возвращает dict с результатами или None, если сделка не состоялась.
    """
    wall_time = confluence_row['wall_time']
    wall_price = confluence_row['wall_price']
    wall_side = confluence_row['wall_side']
    ust_type = confluence_row['ust_type']
    
    # Определяем направление сделки
    # Если стена bid (поддержка) и УСТ был пробит вниз → LONG (отскок от поддержки)
    # Если стена ask (сопротивление) и УСТ был пробит вверх → SHORT (отскок от сопротивления)
    if wall_side == 'bid' and ust_type == 'SUPPORT_BREAK':
        trade_side = 'long'
    elif wall_side == 'ask' and ust_type == 'RESISTANCE_BREAK':
        trade_side = 'short'
    else:
        return None  # Неясное направление, пропускаем
    
    # Находим ATR в момент wall_time
    depth_at_time = df_depth[df_depth['timestamp'] <= wall_time].tail(1)
    if depth_at_time.empty:
        return None
    
    atr = depth_at_time.iloc[0]['atr']
    
    # Рассчитываем уровни
    if trade_side == 'long':
        entry_price = wall_price
        sl_price = entry_price - (atr * SL_MULT)
        tp1_price = entry_price + (atr * SL_MULT * TP1_MULT)  # 1:1
        tp2_price = entry_price + (atr * SL_MULT * TP2_MULT)  # 2.5:1
    else:  # short
        entry_price = wall_price
        sl_price = entry_price + (atr * SL_MULT)
        tp1_price = entry_price - (atr * SL_MULT * TP1_MULT)
        tp2_price = entry_price - (atr * SL_MULT * TP2_MULT)
    
    # Проверяем фильтр дельты
    if not check_delta_reversal(df_trades, wall_time, wall_side):
        return {'status': 'filtered', 'side': trade_side, 'entry': entry_price, 'reason': 'delta_filter'}
    
    # Ищем результат сделки в будущих данных
    future_depth = df_depth[df_depth['timestamp'] > wall_time].copy()
    
    if future_depth.empty:
        return None  # Нет данных после входа
    
    # Проверяем, что было достигнуто первым: TP1, TP2 или SL
    hit_tp1 = None
    hit_tp2 = None
    hit_sl = None
    
    for idx, row in future_depth.iterrows():
        if trade_side == 'long':
            if row['high'] >= tp1_price and hit_tp1 is None:
                hit_tp1 = row['timestamp']
            if row['high'] >= tp2_price and hit_tp2 is None:
                hit_tp2 = row['timestamp']
            if row['low'] <= sl_price and hit_sl is None:
                hit_sl = row['timestamp']
        else:  # short
            if row['low'] <= tp1_price and hit_tp1 is None:
                hit_tp1 = row['timestamp']
            if row['low'] <= tp2_price and hit_tp2 is None:
                hit_tp2 = row['timestamp']
            if row['high'] >= sl_price and hit_sl is None:
                hit_sl = row['timestamp']
        
        # Останавливаемся, если нашли все три или прошло слишком много времени
        if hit_tp1 and hit_tp2 and hit_sl:
            break
        if (row['timestamp'] - wall_time).total_seconds() > 3600:  # 1 час максимум
            break
    
    # Рассчитываем PnL
    pnl = 0.0
    exit_reason = ''
    
    if hit_sl and (not hit_tp1 or hit_sl < hit_tp1):
        # Удар по стопу до TP1
        if trade_side == 'long':
            pnl = ((sl_price - entry_price) / entry_price) - COMMISSION
        else:
            pnl = ((entry_price - sl_price) / entry_price) - COMMISSION
        exit_reason = 'SL'
        
    elif hit_tp1 and (not hit_sl or hit_tp1 < hit_sl):
        # Достигли TP1
        if trade_side == 'long':
            pnl_tp1 = ((tp1_price - entry_price) / entry_price)
        else:
            pnl_tp1 = ((entry_price - tp1_price) / entry_price)
        
        # Проверяем, достигли ли TP2 после TP1
        if hit_tp2 and hit_tp2 > hit_tp1:
            if trade_side == 'long':
                pnl_tp2 = ((tp2_price - entry_price) / entry_price)
            else:
                pnl_tp2 = ((entry_price - tp2_price) / entry_price)
            
            # 50% по TP1, 50% по TP2
            pnl = (pnl_tp1 * 0.5 + pnl_tp2 * 0.5) - COMMISSION
            exit_reason = 'TP1+TP2'
        else:
            # Только TP1, вторая половина закрыта по SL (упрощенно считаем 0)
            pnl = (pnl_tp1 * 0.5) - COMMISSION
            exit_reason = 'TP1_only'
    else:
        # Сделка не закрылась в течение часа
        return {'status': 'timeout', 'side': trade_side, 'entry': entry_price, 'reason': 'no_exit'}
    
    return {
        'status': 'completed',
        'side': trade_side,
        'entry': entry_price,
        'sl': sl_price,
        'tp1': tp1_price,
        'tp2': tp2_price,
        'pnl': pnl,
        'exit_reason': exit_reason,
        'wall_time': wall_time,
        'wall_price': wall_price,
        'wall_side': wall_side
    }

# ==============================================================================
# 5. ГЛАВНЫЙ ЦИКЛ
# ==============================================================================
if __name__ == "__main__":
    print(f"🚀 ФИНАЛЬНЫЙ БЭКТЕСТ SNIPER V6")
    print(f"   Проверяем механику входа/выхода на {len(pd.read_csv(CONFLUENCE_FILE))} конфлюэнсах\n")
    
    # Загружаем конфлюэнсы
    df_confluences = load_confluences()
    
    results = []
    filtered_count = 0
    timeout_count = 0
    
    # Группируем по датам для эффективной загрузки данных
    dates = df_confluences['date'].unique()
    
    for date_str in tqdm(dates, desc="Обработка дней", unit="день"):
        print(f"\n[INFO] Загрузка данных для {date_str}...")
        df_depth = load_depth_data(date_str)
        df_trades = load_trades_data(date_str)
        
        if df_depth is None:
            print(f"  ⚠️ Данные стакана не найдены для {date_str}")
            continue
        
        # Фильтруем конфлюэнсы для этого дня
        day_confluences = df_confluences[df_confluences['date'] == date_str]
        
        for idx, confluence_row in tqdm(day_confluences.iterrows(), 
                                        desc=f"  Сетапы {date_str}", 
                                        leave=False,
                                        unit="сетап"):
            result = simulate_trade(df_depth, df_trades, confluence_row)
            
            if result is None:
                continue
            
            if result['status'] == 'filtered':
                filtered_count += 1
            elif result['status'] == 'timeout':
                timeout_count += 1
            else:
                results.append(result)
    
    # Анализ результатов
    df_results = pd.DataFrame(results)
    
    print(f"\n{'='*80}")
    print(f" ИТОГОВЫЕ РЕЗУЛЬТАТЫ")
    print(f"{'='*80}")
    print(f"Всего конфлюэнсов:          {len(df_confluences)}")
    print(f"Отфильтровано (дельта):     {filtered_count}")
    print(f"Таймаут (не закрылась):     {timeout_count}")
    print(f"Завершенных сделок:         {len(df_results)}")
    print(f"{'='*80}")
    
    if not df_results.empty:
        total_trades = len(df_results)
        winning_trades = len(df_results[df_results['pnl'] > 0])
        winrate = (winning_trades / total_trades) * 100
        total_pnl = df_results['pnl'].sum() * 100
        avg_pnl = total_pnl / total_trades
        max_pnl = df_results['pnl'].max() * 100
        min_pnl = df_results['pnl'].min() * 100
        
        print(f"\n МЕТРИКИ СТРАТЕГИИ:")
        print(f"  Win Rate:           {winrate:.1f}%")
        print(f"  Total PnL:          {total_pnl:.2f}%")
        print(f"  Avg PnL per trade:  {avg_pnl:.3f}%")
        print(f"  Max PnL (single):   {max_pnl:.3f}%")
        print(f"  Min PnL (single):   {min_pnl:.3f}%")
        print(f"  Profit Factor:      {abs(df_results[df_results['pnl']>0]['pnl'].sum() / df_results[df_results['pnl']<0]['pnl'].sum()):.2f}")
        
        print(f"\n📈 РАСПРЕДЕЛЕНИЕ ПО СТОРОНАМ:")
        for side in ['long', 'short']:
            side_df = df_results[df_results['side'] == side]
            if not side_df.empty:
                side_wr = (len(side_df[side_df['pnl'] > 0]) / len(side_df)) * 100
                side_pnl = side_df['pnl'].sum() * 100
                print(f"  {side.upper():5}: {len(side_df):3} сделок, WR={side_wr:.1f}%, PnL={side_pnl:.2f}%")
        
        print(f"\n РАСПРЕДЕЛЕНИЕ ПО ПРИЧИНАМ ВЫХОДА:")
        for reason in df_results['exit_reason'].unique():
            count = len(df_results[df_results['exit_reason'] == reason])
            pct = (count / total_trades) * 100
            print(f"  {reason:10}: {count:3} ({pct:.1f}%)")
        
        # Сохраняем детали
        output_file = DATA_DIR / "sniper_v6_final_results.csv"
        df_results.to_csv(output_file, index=False)
        print(f"\n✅ Детали сделок сохранены: {output_file}")
    else:
        print("\n⚠️ Нет завершенных сделок для анализа")
        
    print(f"\n🏁 Бэктест завершен!")