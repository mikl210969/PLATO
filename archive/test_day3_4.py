import logging
from extensions.analytics.whale_detector import WhaleDetector, Trade
from extensions.analytics.spoofing_detector import SpoofingDetector, OrderbookSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("test_day3_4")

def on_whale(e): log.info(f">>> WHALE: {e.event_type} | side={e.side} | value={e.value_usdt:.0f} | cluster={e.cluster_size}")
def on_wall(e):  log.info(f">>> WALL:  {e.event_type} | {e.side} @ {e.price} | {e.detail}")

print("--- Сценарий 1: Whale Detector ---")
wd = WhaleDetector(on_event=on_whale)
ts = 1000.0
for i in range(110):  # розничный шум
    ts += 0.25
    wd.on_trade(Trade(price=140.0, quantity=3.0, value_usdt=420.0,
                      aggressor_side="BUY" if i % 2 == 0 else "SELL", timestamp=ts))
log.info(f"ATS={wd.get_stats()['ema_ats']:.2f} | threshold={wd.get_stats()['threshold']:.0f}")

ts += 0.5
wd.on_trade(Trade(price=140.1, quantity=180.0, value_usdt=25200.0, aggressor_side="BUY", timestamp=ts))  # ждём WHALE_BUY
ts += 1.0
wd.on_trade(Trade(price=140.2, quantity=150.0, value_usdt=21000.0, aggressor_side="BUY", timestamp=ts))  # ждём WHALE_CLUSTER

report = wd.analyze_last(60, now=ts)
log.info(f"analyze_last(60s): whale_buy={report['buy_volume']:.0f} | whale_sell={report['sell_volume']:.0f}")

print("\n--- Сценарий 2: Spoofing Detector (throttling + сценарии A/B) ---")
sd = SpoofingDetector(on_event=on_wall)

def make_snap(ts, wall_price=None, wall_volume=1000.0):
    bids = [(139.9 - i * 0.1, 100.0) for i in range(10)]
    asks = [(140.1 + i * 0.1, 100.0) for i in range(10)]
    if wall_price is not None:
        bids[3] = (wall_price, wall_volume)
    return OrderbookSnapshot(bids=bids, asks=asks, timestamp=ts)

t = 0.0
while t <= 5.0:  # стена живёт 5 сек (снапшоты каждые 0.1 c -> throttling срежет ~половину)
    sd.on_orderbook(make_snap(t, wall_price=139.6))
    t += 0.1
for t in (5.1, 5.2, 5.3):  # исчезла на 0.3 c
    sd.on_orderbook(make_snap(t, wall_price=None))
sd.on_orderbook(make_snap(5.4, wall_price=139.5))  # вернулась рядом -> REPOSITIONING
t = 5.5
while t <= 8.0:  # снова стабильна
    sd.on_orderbook(make_snap(t, wall_price=139.5))
    t += 0.1
t = 8.1
while t <= 9.0:  # исчезла навсегда -> SPOOFING_CONFIRMED
    sd.on_orderbook(make_snap(t, wall_price=None))
    t += 0.1

log.info(f"Spoofing stats: {sd.get_stats()}")