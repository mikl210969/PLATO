"""Синтетическая проверка VolumeProfileFeature."""
import time
from core.logger import get_logger
from features.factory import FeatureRegistry

CONFIG = {
    "symbols": ["SOLUSDT"],
    "features": {
        "profile": {
            "micro_min": 5,
            "macro_hours": 1,
            "bin_price_pct_min": 0.001,
            "bin_atr_mult": 0.0,
            "hvn_median_mult": 1.5,
            "lvn_median_mult": 0.5,
            "max_nodes": 5
        },
        "recalc_interval_sec": 1.0,
        "freshness": {"delta_max_age_sec": 5, "orderbook_max_age_sec": 3},
    },
    "logging": {
        "features": {
            "input": {"enabled": False},
            "output": {"enabled": False},
            "events": {"enabled": False},
        }
    },
}

log = get_logger("test_volume_profile")
registry = FeatureRegistry(CONFIG, log)
fs = registry.get("SOLUSDT")

now = time.time()

# Фаза 1: Создаём явный HVN-кластер на цене 100.0 (много объёма)
log.info("Фаза 1: Формируем HVN-кластер...")

# HVN на 100.0 (1000 объёма)
for i in range(100):
    t = now + i * 0.1
    fs.on_trade(100.0, 10.0, True, t)

# Шумовые бины (по 10 объёма каждый) — чтобы медиана была низкой
for i in range(10):
    t = now + 10.0 + i * 0.1
    fs.on_trade(99.8, 1.0, True, t)
    fs.on_trade(99.6, 1.0, True, t)
    fs.on_trade(100.2, 1.0, True, t)
    fs.on_trade(100.4, 1.0, True, t)
    fs.on_trade(100.6, 1.0, True, t)

# Финальный тик на 100.2 (текущая цена выше HVN)
fs.on_trade(100.2, 1.0, True, now + 20.0)

# Ждём пересчёта
time.sleep(1.5)

snap = fs.snapshot()
vp = snap["volume_profile"]

hvn_above = vp.get("nearest_hvn_above")
hvn_below = vp.get("nearest_hvn_below")

# Текущая цена 100.2, HVN на 100.0 должен быть ниже
assert hvn_below is not None, "HVN ниже должен быть найден"
assert abs(hvn_below["price"] - 100.0) < 0.5, f"Ожидался HVN на ~100.0, получен {hvn_below['price']}"
assert hvn_below["strength"] >= 1.5, f"Сила HVN должна быть >= 1.5, получена {hvn_below['strength']}"

log.info(f"✅ Фаза 1: HVN найден | price={hvn_below['price']:.2f} strength={hvn_below['strength']:.1f}")

# Фаза 2: HVN выше нет (мы выше всех уровней)
assert hvn_above is None, "HVN выше не должно быть"
log.info("✅ Фаза 2: HVN выше отсутствует (корректно)")

log.info("✅ VolumeProfileFeature: синтетика пройдена")