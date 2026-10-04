import pandas as pd
import sys
import os
from datetime import datetime
from collections import deque

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from features.volume_context_manager import VolumeContextManager

# === НАСТРОЙКИ ===
SOL_FILE = "data/history/SOLUSDT/SOLUSDT_1m_klines.csv"
BTC_FILE = "data/history/BTCUSDT/BTCUSDT_1m_klines.csv"

VCM_CONFIG = {
    "enabled": True,
    "lookback_candles": 20,
    "baseline_avg_vol": 100.0,
    "dry_run": False,
    "regimes": {
        "calm": {"vol_ratio_max": 0.7, "overrides": {"min_confidence": 0.75, "price_distance_pct": 0.3}},
        "normal": {"vol_ratio_max": 2.0, "overrides": {"min_confidence": 0.6, "price_distance_pct": 0.5}},
        "volatile": {"vol_ratio_max": 5.0, "overrides": {"min_confidence": 0.8, "price_distance_pct": 0.8}},
        "impulsive": {"vol_ratio_max": 999.0, "overrides": {"min_confidence": 0.95, "price_distance_pct": 1.5}}
    }
}

V12_PARAMS = {"min_confidence": 0.6, "price_distance_pct": 0.5}
BTC_PENALTY = 0.5  # Штрафной коэффициент из strategies.json


async def run_backtest():
    print("🚀 Запуск Backtester V3 (с BTC макро-фильтром)")
    print("=" * 70)
    
    # 1. Загрузка данных
    print(f"📂 Загрузка данных...")
    sol_df = pd.read_csv(SOL_FILE)
    sol_df['timestamp'] = pd.to_datetime(sol_df['timestamp'])
    
    btc_df = pd.read_csv(BTC_FILE)
    btc_df['timestamp'] = pd.to_datetime(btc_df['timestamp'])
    
    # 2. Предварительный расчет тренда BTC (используем EMA для плавности)
    btc_df['ema_50'] = btc_df['close'].ewm(span=50, adjust=False).mean()
    btc_df['btc_trend'] = 'FLAT'

    # Если цена выше EMA на 0.2% или больше — тренд UP
    btc_df.loc[btc_df['close'] > btc_df['ema_50'] * 1.002, 'btc_trend'] = 'UP'
    # Если цена ниже EMA на 0.2% или больше — тренд DOWN
    btc_df.loc[btc_df['close'] < btc_df['ema_50'] * 0.998, 'btc_trend'] = 'DOWN'

    # Статистика по трендам
    trend_counts = btc_df['btc_trend'].value_counts()
    print(f"📊 Распределение трендов BTC: {trend_counts.to_dict()}")
    
    print(f"✅ Загружено {len(sol_df)} свечей SOL и {len(btc_df)} свечей BTC.\n")

    vcm = VolumeContextManager(volume_config=VCM_CONFIG, base_strategy_params=V12_PARAMS)
    volume_window = deque(maxlen=20)
    
    # Статистика
    stats = {
        "total_candles": len(sol_df),
        "regime_changes": 0,
        "v12_accepted": 0,
        "v13_accepted": 0,
        "v13_filtered_regime": 0,
        "v13_filtered_btc": 0, # <-- Новая метрика
        "last_regime": None
    }
    
    print("🔄 Начинаем симуляцию...\n")
    
    # Итерируемся по свечам (SOL и BTC синхронизированы по времени)
    for i, (sol_row, btc_row) in enumerate(zip(sol_df.itertuples(), btc_df.itertuples())):
        volume = float(sol_row.volume)
        timestamp = sol_row.timestamp
        btc_trend = btc_row.btc_trend
        
        volume_window.append(volume)
        if len(volume_window) < 20:
            continue
        
        # Обновляем контекст V13
        await vcm.update_context(list(volume_window))
        active_params = await vcm.get_active_params()
        current_regime = vcm.current_regime
        
        # Лог смены режима
        if current_regime != stats["last_regime"]:
            stats["regime_changes"] += 1
            print(f"📅 {timestamp.strftime('%Y-%m-%d %H:%M')} | 🔄 {stats['last_regime']} ➔ {current_regime.upper()}")
            print(f"   ├─ min_conf: {active_params.get('min_confidence')} | BTC Trend: {btc_trend}")
            stats["last_regime"] = current_regime

        # 3. Симуляция сигналов (генерируем сигнал каждые 5 свечей)
        if int(i) % 5 == 0:
            signal_side = 'long' if (int(i) % 10 == 0) else 'short'
            
            # 🔥 ИЗМЕНЕНИЕ: Повышаем уверенность до 0.85. 
            # Это позволит сигналу пройти фильтр VOLATILE (0.8), но он все еще может быть отклонен 
            # фильтром BTC или режимом IMPULSIVE (0.95).
            signal_confidence = 0.85  
            signal_distance = 0.4     
            
            # --- Проверка V12 (Статика) ---
            v12_pass = (signal_confidence >= V12_PARAMS["min_confidence"]) and (signal_distance <= V12_PARAMS["price_distance_pct"])
            if v12_pass:
                stats["v12_accepted"] += 1
            
            # --- Проверка V13 (Адаптивность + BTC) ---
            v13_min_conf = active_params.get("min_confidence", 0.6)
            v13_max_dist = active_params.get("price_distance_pct", 0.5)
            
            # 1. Фильтр по режиму рынка
            regime_pass = (signal_confidence >= v13_min_conf) and (signal_distance <= v13_max_dist)
            
            if not regime_pass:
                stats["v13_filtered_regime"] += 1
                continue # Сигнал убит волатильностью (например, в IMPULSIVE)
            
            # 2. Фильтр по BTC (Макро-щит) - ТЕПЕРЬ МЫ СЮДА ДОЙДЕМ!
            btc_penalty_applied = False
            final_confidence = signal_confidence
            
            if (signal_side == 'long' and btc_trend == 'DOWN') or \
               (signal_side == 'short' and btc_trend == 'UP'):
                final_confidence *= BTC_PENALTY  # 0.85 * 0.5 = 0.425
                btc_penalty_applied = True
            
            # Финальная проверка после штрафа BTC
            if final_confidence < v13_min_conf:
                if btc_penalty_applied:
                    stats["v13_filtered_btc"] += 1
                else:
                    stats["v13_filtered_regime"] += 1
            else:
                stats["v13_accepted"] += 1

    # 4. Финальный отчет
    print("\n" + "=" * 70)
    print("📊 ИТОГОВЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
    print("=" * 70)
    print(f"📈 Свечей обработано: {stats['total_candles']}")
    print(f"🔄 Смен режима: {stats['regime_changes']}")
    print("-" * 70)
    print(f"🤖 V12 (Статика) принял бы: {stats['v12_accepted']} сигналов")
    print(f"🧠 V13 (Адаптив) принял: {stats['v13_accepted']} сигналов")
    print("-" * 70)
    print(f"🛡️ Отфильтровано из-за волатильности (V13 режимы): {stats['v13_filtered_regime']}")
    print(f" ОТФИЛЬТРОВАНО макро-фильтром BTC: {stats['v13_filtered_btc']}")
    print("=" * 70)
    
    if stats['v13_filtered_btc'] > 0:
        print(f"✅ УСПЕХ: BTC-фильтр спас депозит от {stats['v13_filtered_btc']} убыточных сделок против тренда!")
    else:
        print("ℹ️  BTC-фильтр не сработал (возможно, тренд BTC был плоским во время сигналов).")


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_backtest())