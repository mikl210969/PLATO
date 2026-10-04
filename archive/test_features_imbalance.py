"""Синтетическая проверка ImbalanceFeature."""
import time
from core.logger import get_logger
from features.factory import FeatureRegistry

CONFIG = {
    "symbols": ["SOLUSDT"],
    "features": {
        "orderbook_depth": 20,
        "freshness": {"delta_max_age_sec": 5, "orderbook_max_age_sec": 3},
        "recalc_interval_sec": 1.0,
    },
    "logging": {
        "features": {
            "input": {"enabled": False},
            "output": {"enabled": False},
            "events": {"enabled": False},
        }
    },
}

log = get_logger("test_imbalance")
registry = FeatureRegistry(CONFIG, log)
fs = registry.get("SOLUSDT")

now = time.time()

# Фаза 1: Идеальный баланс (bid=100, ask=100)
bids_1 = [(100.0, 50.0), (99.9, 50.0)]
asks_1 = [(100.1, 50.0), (100.2, 50.0)]
fs.on_orderbook(bids_1, asks_1, now)
snap1 = fs.snapshot()
assert abs(snap1["imbalance"]["imbalance"]) < 0.01, f"Баланс должен быть ~0, получено {snap1['imbalance']['imbalance']}"
assert snap1["imbalance"]["bid_vol"] == 100.0
log.info("✅ Фаза 1: Баланс проверен (imbalance ≈ 0.0)")

# Фаза 2: Сильный перекос в Bid (покупатели давят, bid=200, ask=50)
# imbalance = (200 - 50) / (200 + 50) = 150 / 250 = 0.6
bids_2 = [(100.0, 100.0), (99.9, 100.0)]
asks_2 = [(100.1, 50.0)]
fs.on_orderbook(bids_2, asks_2, now + 1)
snap2 = fs.snapshot()
assert abs(snap2["imbalance"]["imbalance"] - 0.6) < 0.01, f"Ожидалось 0.6, получено {snap2['imbalance']['imbalance']}"
log.info("✅ Фаза 2: Перекос в Bid проверен (imbalance = 0.6)")

# Фаза 3: Сильный перекос в Ask (продавцы давят, bid=10, ask=100)
# imbalance = (10 - 100) / (10 + 100) = -90 / 110 ≈ -0.818
bids_3 = [(100.0, 10.0)]
asks_3 = [(100.1, 50.0), (100.2, 50.0)]
fs.on_orderbook(bids_3, asks_3, now + 2)
snap3 = fs.snapshot()
assert abs(snap3["imbalance"]["imbalance"] - (-0.818)) < 0.01, f"Ожидалось -0.818, получено {snap3['imbalance']['imbalance']}"
log.info("✅ Фаза 3: Перекос в Ask проверен (imbalance ≈ -0.818)")

# Фаза 4: Проверка свежести (ждём 4 секунды, лимит 3 сек)
time.sleep(4)
assert not fs.is_fresh(time.time()), "Стакан протух (>3 сек), is_fresh должен быть False"
log.info("✅ Фаза 4: Свежесть упала после паузы (is_fresh=False)")

log.info("✅ ImbalanceFeature: синтетика пройдена")