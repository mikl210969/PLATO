"""Синтетическая проверка каркаса Features + DeltaFeature."""
import time

from core.logger import get_logger
from features.factory import FeatureRegistry

CONFIG = {
    "symbols": ["SOLUSDT"],
    "features": {
        "delta_windows_sec": [3, 30, 300],
        "delta_velocity_ema_alpha": 0.3,
        "recalc_interval_sec": 1.0,
        "session_reset_utc_hour": 0,
        "freshness": {"delta_max_age_sec": 5, "orderbook_max_age_sec": 3},
    },
    "logging": {
        "features": {
            "input": {"enabled": True, "sample_sec": 5},
            "output": {"enabled": True, "interval_sec": 5},
            "events": {"enabled": True},
        }
    },
}

log = get_logger("test_features")
registry = FeatureRegistry(CONFIG, log)
fs = registry.get("SOLUSDT")

now = time.time()

# Фаза 1: 30 сек баланса (покупки = продажи)
for i in range(300):
    t = now + i * 0.1
    fs.on_trade(100.0, 1.0, True, t)
    fs.on_trade(100.0, 1.0, False, t)

# Фаза 2: 10 сек всплеска покупок (агрессия лонг)
for i in range(100):
    t = now + 30 + i * 0.1
    fs.on_trade(100.1, 5.0, True, t)
    fs.on_trade(100.1, 0.5, False, t)

snap = fs.snapshot(now=now + 40)
d3 = snap["delta"]["windows"][3]
assert d3["delta"] > 0, "всплеск покупок должен дать положительную дельту за 3с"
assert snap["is_fresh"], "лента живая — свежесть должна быть True"
log.info(f"✅ Фаза 2: d3={d3['delta']:+.1f} v3={d3['velocity']:+.2f} fresh={snap['is_fresh']}")

# Фаза 3: лента замолкла 7 сек назад (синтетически) → свежесть должна упасть
snap2 = fs.snapshot(now=now + 47)
assert not snap2["is_fresh"], "протухшая лента должна дать is_fresh=False"
log.info(f"✅ Фаза 3: fresh={snap2['is_fresh']} (ожидание False)")

log.info("✅ Каркас Features + DeltaFeature: синтетика пройдена")