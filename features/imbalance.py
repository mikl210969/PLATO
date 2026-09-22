"""ImbalanceFeature: перекос стакана (SPOT depth)."""
import time
from typing import Any, Dict, List, Tuple

from features.base import Feature


class ImbalanceFeature(Feature):
    name = "imbalance"

    def __init__(self, symbol: str, config: Dict[str, Any], log):
        super().__init__(symbol, config, log)
        self._depth_levels = int(config.get("orderbook_depth", 20))
        
        self._state = {
            "bid_vol": 0.0,
            "ask_vol": 0.0,
            "imbalance": 0.0,
            "ts": 0.0,
        }

    def on_orderbook(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], ts: float) -> None:
        """
        bids/asks: список кортежей [(price, qty), ...]
        """
        self._last_update_ts = ts
        
        # Берём только верхние N уровней
        top_bids = bids[:self._depth_levels]
        top_asks = asks[:self._depth_levels]
        
        bid_vol = sum(qty for price, qty in top_bids if price > 0 and qty > 0)
        ask_vol = sum(qty for price, qty in top_asks if price > 0 and qty > 0)
        
        total_vol = bid_vol + ask_vol
        if total_vol > 0:
            imbalance = (bid_vol - ask_vol) / total_vol
        else:
            imbalance = 0.0  # Защита от деления на ноль при пустом стакане
            
        self._state["bid_vol"] = float(bid_vol)
        self._state["ask_vol"] = float(ask_vol)
        self._state["imbalance"] = float(imbalance)
        self._state["ts"] = ts

    def snapshot(self) -> Dict[str, Any]:
        return {
            "bid_vol": self._state["bid_vol"],
            "ask_vol": self._state["ask_vol"],
            "imbalance": self._state["imbalance"],
            "ts": self._state["ts"],
        }