"""DeltaFeature: агрессия рынка по ленте сделок (SPOT aggTrade).

Окна 3/30/300 сек, velocity с EMA-сглаживанием, cum_delta с сбросом сессии.
"""
import time
from collections import deque
from typing import Any, Dict

from features.base import Feature


class DeltaFeature(Feature):
    name = "delta"

    def __init__(self, symbol: str, config: Dict[str, Any], log):
        super().__init__(symbol, config, log)
        self._windows = list(config.get("delta_windows_sec", [3, 30, 300]))
        self._ema_alpha = float(config.get("delta_velocity_ema_alpha", 0.3))
        self._session_reset_hour = int(config.get("session_reset_utc_hour", 0))

        self._tape = deque()                      # (ts, signed_qty)
        self._tape_max_age = max(self._windows) + 5.0

        self._state = {
            "windows": {w: {"delta": 0.0, "velocity": 0.0} for w in self._windows},
            "cum_delta": 0.0,
            "ts": 0.0,
        }
        self._cum_delta = 0.0
        self._session_key = self._session_key_of(time.time())

    def _session_key_of(self, ts: float) -> int:
        """Ключ сессии: индекс UTC-дня со смещением на час сброса."""
        return int((ts - self._session_reset_hour * 3600) // 86400)

    def _check_session(self, ts: float) -> None:
        key = self._session_key_of(ts)
        if key != self._session_key:
            self._session_key = key
            self._cum_delta = 0.0
            self._log.info(f"🔄 [DELTA] {self.symbol}: сброс cum_delta (новая сессия UTC)")

    def on_trade(self, price: float, qty: float, is_buy: bool, ts: float) -> None:
        signed = qty if is_buy else -qty
        self._tape.append((ts, signed))
        self._last_update_ts = ts

        self._check_session(ts)
        self._cum_delta += signed

        cutoff = ts - self._tape_max_age
        while self._tape and self._tape[0][0] < cutoff:
            self._tape.popleft()

        self._maybe_recalc(ts)

    def _recalc(self, ts: float) -> None:
        for w in self._windows:
            cutoff = ts - w
            delta = sum(q for t, q in self._tape if t >= cutoff)
            raw_velocity = delta / w
            prev = self._state["windows"][w]["velocity"]
            ema = prev + self._ema_alpha * (raw_velocity - prev)
            self._state["windows"][w] = {"delta": float(delta), "velocity": ema}
        self._state["cum_delta"] = self._cum_delta
        self._state["ts"] = ts

    def snapshot(self) -> Dict[str, Any]:
        # копия, чтобы стратегия не могла изменить состояние
        return {
            "windows": {w: dict(v) for w, v in self._state["windows"].items()},
            "cum_delta": self._state["cum_delta"],
            "ts": self._state["ts"],
        }