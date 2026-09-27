import pandas as pd
import sys
import os
from datetime import datetime
from collections import deque

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from features.volume_context_manager import VolumeContextManager

# === НАСТРОЙКИ (синхронизированы с твоим strategies.json) ===
DATA_FILE = "data/history/SOLUSDT/SOLUSDT_1m_klines.csv"
VCM_CONFIG = {
    "enabled": True,
    "lookback_candles": 20,
    "baseline_avg_vol": 100.0,
    "dry_run": False,
    "regimes": {
        "calm": {"vol_ratio_max": 0.7, "overrides": {"min_confidence": 0.75, "price_distance_pct": 0.3, "bypass_filters": False}},
        "normal": {"vol_ratio_max": 2.0, "overrides": {"min_confidence": 0.6, "price_distance_pct": 0.5, "bypass_filters": False}},
        "volatile": {"vol_ratio_max": 5.0, "overrides": {"min_confidence": 0.8, "price_distance_pct": 0.8, "bypass_filters": False}},
        "impulsive": {"vol_ratio_max": 999.0, "overrides": {"min_confidence": 0.95, "price_distance_pct": 1.5, "bypass_filters": False}}
    }
}

V12_PARAMS = {"min_confidence": 0.6, "price_distance_pct": 0.5}


async def run_backtest():
    print("🚀 Запуск MVP Backtester V2 (с rolling window и гистерезисом)")
    print("=" * 70)
    
    df = pd.read_csv(DATA_FILE)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    print(f"✅ Загружено {len(df)} свечей.\n")

    vcm = VolumeContextManager(volume_config=VCM_CONFIG, base_strategy_params=V12_PARAMS)
    
    # Rolling window для объемов (последние 20 свечей)
    volume_window = deque(maxlen=20)
    
    stats = {
        "total_candles": len(df),
        "regime_changes": 0,
        "v12_accepted": 0,
        "v13_accepted": 0,
        "v13_filtered_out": 0,
        "last_regime": None
    }
    
    print("🔄 Начинаем симуляцию...\n")
    
    for i, row in df.iterrows():
        volume = float(row['volume'])
        timestamp = row['timestamp']
        
        # Добавляем объем в rolling window
        volume_window.append(volume)
        
        # Ждём, пока накопится 20 свечей (lookback)
        if len(volume_window) < 20:
            continue
        
        # Передаём РЕАЛЬНЫЕ последние 20 объемов
        await vcm.update_context(list(volume_window))
        
        active_params = await vcm.get_active_params()
        current_regime = vcm.current_regime
        
        # Логируем смену режима
        if current_regime != stats["last_regime"]:
            stats["regime_changes"] += 1
            ema_ratio = vcm.last_metrics.get('ema_vol_ratio', 0)
            print(f"📅 {timestamp.strftime('%Y-%m-%d %H:%M')} | 🔄 {stats['last_regime']} ➔ {current_regime.upper()}")
            print(f"   ├─ EMA_VolRatio: {ema_ratio:.2f} | min_conf: {active_params.get('min_confidence')} | dist_pct: {active_params.get('price_distance_pct')}")
            stats["last_regime"] = current_regime

        # Симуляция сигналов с разными параметрами
        if int(i) % 3 == 0:
            signal_confidence = 0.65
            signal_distance = 0.6
            
            v12_pass = (signal_confidence >= V12_PARAMS["min_confidence"]) and (signal_distance <= V12_PARAMS["price_distance_pct"])
            if v12_pass:
                stats["v12_accepted"] += 1
            
            v13_min_conf = active_params.get("min_confidence", 0.6)
            v13_max_dist = active_params.get("price_distance_pct", 0.5)
            v13_pass = (signal_confidence >= v13_min_conf) and (signal_distance <= v13_max_dist)
            if v13_pass:
                stats["v13_accepted"] += 1
            else:
                stats["v13_filtered_out"] += 1

    print("\n" + "=" * 70)
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print("=" * 70)
    print(f"📈 Свечей обработано: {stats['total_candles']}")
    print(f"🔄 Смен режима: {stats['regime_changes']}")
    print(f"🤖 V12 принял: {stats['v12_accepted']}")
    print(f"🧠 V13 принял: {stats['v13_accepted']}")
    print(f"🛡️ V13 отфильтровал: {stats['v13_filtered_out']}")
    print("=" * 70)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_backtest())