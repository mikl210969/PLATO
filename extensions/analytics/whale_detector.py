"""Whale Detector — детектор крупных игроков (Модуль 1.1, Стратегии.txt v2.0)."""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    price: float
    quantity: float
    value_usdt: float
    aggressor_side: str  # "BUY" | "SELL"
    timestamp: float     # unix seconds


@dataclass
class WhaleEvent:
    event_type: str      # WHALE_BUY | WHALE_SELL | WHALE_CLUSTER
    side: str
    price: float
    value_usdt: float
    threshold_usdt: float
    timestamp: float
    cluster_size: int = 1

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type, "side": self.side, "price": self.price,
            "value_usdt": self.value_usdt, "threshold_usdt": self.threshold_usdt,
            "timestamp": self.timestamp, "cluster_size": self.cluster_size,
        }


class WhaleDetector:
    """Отделяет 'умные деньги' от розничного шума в потоке сделок."""

    def __init__(
        self,
        min_window_trades: int = 100,
        max_window_seconds: float = 120.0,
        ema_alpha: float = 0.3,
        multiplier: float = 3.5,
        absolute_minimum_usdt: float = 10000.0,
        cluster_time_seconds: float = 2.0,
        on_event: Optional[Callable[[WhaleEvent], None]] = None,
    ):
        self.min_window_trades = min_window_trades
        self.max_window_seconds = max_window_seconds
        self.ema_alpha = ema_alpha
        self.multiplier = multiplier
        self.absolute_minimum_usdt = absolute_minimum_usdt
        self.cluster_time_seconds = cluster_time_seconds
        self._on_event = on_event

        self._window: Deque[Trade] = deque()
        self._ema_ats: Optional[float] = None
        self._whale_history: Deque[WhaleEvent] = deque()

    # ------------------------------------------------------------------ API
    def on_trade(self, trade: Trade) -> Optional[WhaleEvent]:
        """Обработать сделку из ленты. Возвращает событие, если это кит."""
        self._window.append(trade)
        self._prune_window(trade.timestamp)

        # Статистическая значимость: минимум 100 сделок в окне
        if len(self._window) < self.min_window_trades:
            return None

        self._update_ats()
        threshold = self.current_threshold

        if trade.value_usdt < threshold:
            return None  # розничный шум

        event = WhaleEvent(
            event_type="WHALE_BUY" if trade.aggressor_side == "BUY" else "WHALE_SELL",
            side=trade.aggressor_side,
            price=trade.price,
            value_usdt=trade.value_usdt,
            threshold_usdt=threshold,
            timestamp=trade.timestamp,
        )
        self._emit(event)

        # Детекция кластера: 2+ кита в одну сторону за 2 секунды
        cluster = self._check_cluster(event)
        if cluster is not None:
            self._emit(cluster)
            return cluster
        return event

    def analyze_last(self, window_seconds: float, now: Optional[float] = None) -> dict:
        """Для структурного анализа TP2 (SL_TP.txt v2.2, раздел 4)."""
        if now is None:
            now = time.time()
        cutoff = now - window_seconds
        threshold = self.current_threshold
        recent = [t for t in self._window if t.timestamp >= cutoff]

        whale_buy = sum(t.value_usdt for t in recent
                        if t.aggressor_side == "BUY" and t.value_usdt >= threshold)
        whale_sell = sum(t.value_usdt for t in recent
                         if t.aggressor_side == "SELL" and t.value_usdt >= threshold)
        return {"buy_volume": whale_buy, "sell_volume": whale_sell,
                "trades_count": len(recent), "threshold": threshold}

    def get_stats(self) -> dict:
        return {"window_size": len(self._window),
                "ema_ats": self._ema_ats or 0.0,
                "threshold": self.current_threshold}

    # ------------------------------------------------------------- internal
    @property
    def current_threshold(self) -> float:
        if self._ema_ats is None:
            return self.absolute_minimum_usdt
        return max(self._ema_ats * self.multiplier, self.absolute_minimum_usdt)

    def _prune_window(self, now: float) -> None:
        while self._window and (now - self._window[0].timestamp) > self.max_window_seconds:
            self._window.popleft()

    def _update_ats(self) -> None:
        raw_ats = sum(t.value_usdt for t in self._window) / len(self._window)
        if self._ema_ats is None:
            self._ema_ats = raw_ats
        else:
            self._ema_ats = self.ema_alpha * raw_ats + (1 - self.ema_alpha) * self._ema_ats

    def _check_cluster(self, event: WhaleEvent) -> Optional[WhaleEvent]:
        self._whale_history.append(event)
        cutoff = event.timestamp - self.cluster_time_seconds
        while self._whale_history and self._whale_history[0].timestamp < cutoff:
            self._whale_history.popleft()

        same_side = [e for e in self._whale_history if e.side == event.side]
        if len(same_side) >= 2:
            return WhaleEvent(
                event_type="WHALE_CLUSTER",
                side=event.side,
                price=event.price,
                value_usdt=sum(e.value_usdt for e in same_side),
                threshold_usdt=event.threshold_usdt,
                timestamp=event.timestamp,
                cluster_size=len(same_side),
            )
        return None

    def _emit(self, event: WhaleEvent) -> None:
        logger.info(f"[WHALE] {event.event_type} | side={event.side} | "
                    f"value={event.value_usdt:.0f} | thr={event.threshold_usdt:.0f}")
        if self._on_event:
            self._on_event(event)