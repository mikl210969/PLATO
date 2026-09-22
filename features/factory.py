"""FeatureSet: реестр признаков символа, разводка ленты, снимок для стратегий."""
import time
from typing import Any, Dict, Optional

from features.delta import DeltaFeature
from features.imbalance import ImbalanceFeature
from features.volume_profile import VolumeProfileFeature
from features.walls import WallsFeature


class FeatureSet:
    def __init__(self, symbol: str, config: Dict[str, Any], log):
        self.symbol = symbol
        self._log = log
        self._fcfg = config.get("features", {})

        # Инициализация всех Features
        self.delta = DeltaFeature(symbol, self._fcfg, log)
        self.imbalance = ImbalanceFeature(symbol, self._fcfg, log)
        self.volume_profile = VolumeProfileFeature(symbol, self._fcfg, log)
        self.walls = WallsFeature(symbol, self._fcfg, log)
        
        self._features = {
            "delta": self.delta,
            "imbalance": self.imbalance,
            "volume_profile": self.volume_profile,
            "walls": self.walls
        }

        # Настройки логирования
        lcfg = config.get("logging", {}).get("features", {})
        self._log_input_on = bool(lcfg.get("input", {}).get("enabled", False))
        self._log_input_sample = float(lcfg.get("input", {}).get("sample_sec", 5))
        self._log_output_on = bool(lcfg.get("output", {}).get("enabled", True))
        self._log_output_interval = float(lcfg.get("output", {}).get("interval_sec", 10))
        self._log_events_on = bool(lcfg.get("events", {}).get("enabled", True))

        self._last_input_log = 0.0
        self._last_output_log = 0.0
        self._trade_count = 0
        self._book_count = 0

    def on_trade(self, price: float, qty: float, is_buy: bool, ts: float) -> None:
        self._trade_count += 1
        self.delta.on_trade(price, qty, is_buy, ts)
        self.volume_profile.on_trade(price, qty, is_buy, ts)
        self._maybe_log_input(ts)

    def on_orderbook(self, bids, asks, ts: float) -> None:
        self._book_count += 1
        self.imbalance.on_orderbook(bids, asks, ts)
        self.walls.on_orderbook(bids, asks, ts) # 🔥 НОВОЕ: кормим стены
        self._maybe_log_input(ts)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        snap = {
            "symbol": self.symbol,
            "ts": now,
            "delta": self.delta.snapshot(),
            "imbalance": self.imbalance.snapshot(),
            "volume_profile": self.volume_profile.snapshot(),
            "walls": self.walls.snapshot(), # 🔥 НОВОЕ
            "is_fresh": self.is_fresh(now),
        }
        self._maybe_log_output(now, snap)
        return snap

    def is_fresh(self, now: float) -> bool:
        fc = self._fcfg.get("freshness", {})
        ok_delta = self.delta.is_fresh(now, float(fc.get("delta_max_age_sec", 5)))
        ok_imb = self.imbalance.is_fresh(now, float(fc.get("orderbook_max_age_sec", 3)))
        return bool(ok_delta and ok_imb)

    def log_event(self, text: str) -> None:
        if self._log_events_on:
            self._log.info(f" [FEATURES EVENT] {self.symbol} | {text}")

    def _maybe_log_input(self, ts: float) -> None:
        if not self._log_input_on:
            return
        if ts - self._last_input_log < self._log_input_sample:
            return
        self._last_input_log = ts
        self._log.info(
            f" [FEATURES INPUT] {self.symbol} | trades={self._trade_count} "
            f"books={self._book_count} за {self._log_input_sample:.0f}с"
        )
        self._trade_count = 0
        self._book_count = 0

    def _maybe_log_output(self, now: float, snap: Dict[str, Any]) -> None:
        if not self._log_output_on:
            return
        if now - self._last_output_log < self._log_output_interval:
            return
        self._last_output_log = now
        
        d = snap["delta"]["windows"]
        imb = snap["imbalance"]
        vp = snap.get("volume_profile", {})
        walls = snap.get("walls", {})
        
        hvn_up = f"{vp['nearest_hvn_above']['price']:.2f}" if vp.get('nearest_hvn_above') else "None"
        hvn_dn = f"{vp['nearest_hvn_below']['price']:.2f}" if vp.get('nearest_hvn_below') else "None"
        
        # Считаем количество стен для лога
        n_walls_bid = len(walls.get("walls_bid", []))
        n_walls_ask = len(walls.get("walls_ask", []))
        
        self._log.info(
            f"📤 [FEATURES OUTPUT] {self.symbol} | fresh={snap['is_fresh']} | "
            f"d3={d[3]['delta']:+.1f} v3={d[3]['velocity']:+.2f} | "
            f"imb={imb['imbalance']:+.2f} | "
            f"hvn_up={hvn_up} hvn_dn={hvn_dn} | "
            f"walls_bid={n_walls_bid} walls_ask={n_walls_ask}"
        )


class FeatureRegistry:
    def __init__(self, config: Dict[str, Any], log):
        self._config = config
        self._log = log
        self._sets: Dict[str, FeatureSet] = {}
        
        symbols = config.get("symbols", []) or []
        for symbol in symbols:
            self._sets[symbol] = FeatureSet(symbol, config, log)
            log.info(f"✅ [FEATURES] FeatureSet создан для {symbol}")

    def get(self, symbol: str) -> FeatureSet:
        if symbol not in self._sets:
            self._sets[symbol] = FeatureSet(symbol, self._config, self._log)
            self._log.info(f"✅ [FEATURES] FeatureSet создан для {symbol}")
        return self._sets[symbol]

    async def start(self):
        import asyncio
        lcfg = self._config.get("logging", {}).get("features", {})
        output_on = bool(lcfg.get("output", {}).get("enabled", True))
        interval = float(lcfg.get("output", {}).get("interval_sec", 10))
        
        if not output_on:
            self._log.info(" [FEATURES] OUTPUT логирование выключено в конфиге")
            return
        
        self._log.info(f"📤 [FEATURES] OUTPUT логирование запущено (интервал {interval}с)")
        while True:
            await asyncio.sleep(interval)
            for symbol, fs in self._sets.items():
                fs.snapshot()