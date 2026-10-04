"""Синтетическая проверка стабильности стен в WallsFeature."""
import time
from core.logger import get_logger
from features.factory import FeatureRegistry

CONFIG = {
    "symbols": ["SOLUSDT"],
    "features": {
        "orderbook_depth": 50,
        "walls": {
            "min_size_avg_mult": 3.0,
            "min_age_sec": 2.0,
            "relocate_radius_ticks": 3
        },
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

log = get_logger("test_walls_stability")
registry = FeatureRegistry(CONFIG, log)
fs = registry.get("SOLUSDT")

now = time.time()

bids_normal = [(99.9, 10.0), (99.8, 10.0)]
asks_normal = [(100.1, 10.0), (100.2, 10.0)]

# ============================================================================
# Фаза 1: Стабильная стена на 100.0 (CV ≈ 0)
# ============================================================================
log.info("Фаза 1: Создаем стабильную стену на 100.0...")
bids_with_stable_wall = [(100.0, 100.0)] + bids_normal

for i in range(50):
    t = now + i * 0.1
    fs.on_orderbook(bids_with_stable_wall, asks_normal, t)

time.sleep(0.5)
snap = fs.snapshot()
walls_bid = snap["walls"]["walls_bid"]

wall1 = next((w for w in walls_bid if abs(w["price"] - 100.0) < 0.01), None)
assert wall1 is not None, "Стабильная стена должна быть найдена"
assert wall1["update_count"] >= 30, f"update_count должен быть >= 30, получено {wall1['update_count']}"
assert wall1["cv"] <= 0.15, f"CV должен быть <= 0.15, получено {wall1['cv']}"
assert wall1["is_stable"] == True, "Стена должна быть стабильной"
assert wall1["is_mature"] == True, "Стена должна быть зрелой"

log.info(f"✅ Фаза 1: Стабильная стена | update_count={wall1['update_count']} cv={wall1['cv']:.3f} stable={wall1['is_stable']} mature={wall1['is_mature']}")

# ============================================================================
# Фаза 2: Нестабильная стена на 99.5 (CV > 0.15)
# ============================================================================
log.info("Фаза 2: Создаем нестабильную стену на 99.5...")

for i in range(50):
    t = now + 10.0 + i * 0.1
    if i % 2 == 0:
        size = 50.0
    else:
        size = 150.0
    bids_with_unstable_wall = [(99.5, size)] + bids_normal
    fs.on_orderbook(bids_with_unstable_wall, asks_normal, t)

time.sleep(0.5)
snap2 = fs.snapshot()
walls_bid2 = snap2["walls"]["walls_bid"]

wall2 = next((w for w in walls_bid2 if abs(w["price"] - 99.5) < 0.01), None)
assert wall2 is not None, "Нестабильная стена должна быть найдена"
assert wall2["cv"] > 0.15, f"CV должен быть > 0.15, получено {wall2['cv']}"
assert wall2["is_stable"] == False, "Стена должна быть нестабильной"

log.info(f"✅ Фаза 2: Нестабильная стена | cv={wall2['cv']:.3f} stable={wall2['is_stable']}")

# ============================================================================
# Фаза 3: Незрелая стена на 99.0 (update_count < 30)
# ============================================================================
log.info("Фаза 3: Создаем незрелую стену на 99.0...")

bids_with_young_wall = [(99.0, 100.0)] + bids_normal

# Увеличиваем до 25 итераций (2.5 секунды), чтобы пройти порог min_age_sec=2.0
for i in range(25):
    t = now + 20.0 + i * 0.1
    fs.on_orderbook(bids_with_young_wall, asks_normal, t)

time.sleep(0.5)
snap3 = fs.snapshot()
walls_bid3 = snap3["walls"]["walls_bid"]

wall3 = next((w for w in walls_bid3 if abs(w["price"] - 99.0) < 0.01), None)
assert wall3 is not None, "Незрелая стена должна быть найдена"
assert wall3["update_count"] < 30, f"update_count должен быть < 30, получено {wall3['update_count']}"
assert wall3["is_mature"] == False, "Стена должна быть незрелой"

log.info(f"✅ Фаза 3: Незрелая стена | update_count={wall3['update_count']} mature={wall3['is_mature']}")