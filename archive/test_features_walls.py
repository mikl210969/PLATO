"""Синтетическая проверка WallsFeature."""
import time
from core.logger import get_logger
from features.factory import FeatureRegistry

CONFIG = {
    "symbols": ["SOLUSDT"],
    "features": {
        "orderbook_depth": 50,
        "walls": {
            "min_size_avg_mult": 3.0, # Понизим для теста, чтобы легче триггерить
            "min_age_sec": 2.0,       # Ускорим для теста
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

log = get_logger("test_walls")
registry = FeatureRegistry(CONFIG, log)
fs = registry.get("SOLUSDT")

now = time.time()

# Фаза 1: Создаём стакан с обычной глубиной и одной огромной стеной на биде 100.0
# Обычные уровни: ~10 SOL
bids_normal = [(99.9, 10.0), (99.8, 10.0), (99.7, 10.0)]
asks_normal = [(100.1, 10.0), (100.2, 10.0), (100.3, 10.0)]

# Стена: 100 SOL (в 10 раз больше медианы)
bids_with_wall = [(100.0, 100.0)] + bids_normal

# 🔥 ИСПРАВЛЕНО: 40 итераций = 4.0 секунды. 
# При min_age_sec=2.0, формула confidence = age / 6.0. 
# При age=4.0, confidence = 4.0 / 6.0 = 0.66 (что > 0.5)
log.info("Фаза 1: Кормим стакан со стеной 4.0 секунды...")
for i in range(40):
    t = now + i * 0.1
    fs.on_orderbook(bids_with_wall, asks_normal, t)

# Ждём чуть-чуть для стабилизации
time.sleep(0.5)

snap = fs.snapshot()
walls_bid = snap["walls"]["walls_bid"]

assert len(walls_bid) > 0, "Стена должна быть обнаружена"
wall = walls_bid[0]
assert abs(wall["price"] - 100.0) < 0.01, f"Цена стены должна быть 100.0, получено {wall['price']}"
assert wall["confidence"] > 0.5, f"Стена должна быть зрелой, confidence={wall['confidence']}"
assert wall["status"] == "alive", f"Статус должен быть alive, получено {wall['status']}"

log.info(f"✅ Фаза 1: Стена найдена | price={wall['price']} size={wall['size']} conf={wall['confidence']:.2f}")

# Фаза 2: Убираем стену из стакана (спойфинг)
log.info("Фаза 2: Убираем стену (тест на спойфинг)...")
for i in range(5):
    t = now + 5.0 + i * 0.1
    fs.on_orderbook(bids_normal, asks_normal, t) # Стены больше нет

snap2 = fs.snapshot()
# Стена должна исчезнуть из активных (мы её удаляем из _active_walls при детекции спойфинга)
assert len(snap2["walls"]["walls_bid"]) == 0, "Спойфинг-стена должна исчезнуть из снимка"
log.info("✅ Фаза 2: Спойфинг корректно обработан (стена исчезла)")

log.info("✅ WallsFeature: синтетика пройдена")