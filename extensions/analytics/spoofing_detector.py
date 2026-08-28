"""Spoofing Detector — детектор фейковых стен (Модуль 1.2, Стратегии.txt v2.0).

Защита от перегрузок (согласно скорректированному плану):
- THROTTLING: обрабатывается не более 1 снапшота за snapshot_interval_ms (200 мс).
- БУФЕРИЗАЦИЯ: наружу публикуются ТОЛЬКО финальные события
  (WALL_DETECTED / WALL_CONFIRMED / REPOSITIONING / SUSPICIOUS / WEAKENING / SPOOFING_CONFIRMED),
  сырые изменения стакана в EventBus НЕ попадают.
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OrderbookSnapshot:
    bids: List[Tuple[float, float]]  # [(price, volume), ...]
    asks: List[Tuple[float, float]]
    timestamp: float


@dataclass
class WallEvent:
    event_type: str  # WALL_DETECTED | WALL_CONFIRMED | REPOSITIONING | SUSPICIOUS | WEAKENING | SPOOFING_CONFIRMED
    side: str        # "BID" | "ASK"
    price: float
    volume: float
    timestamp: float
    detail: str = ""

    def to_dict(self) -> dict:
        return {"event_type": self.event_type, "side": self.side, "price": self.price,
                "volume": self.volume, "timestamp": self.timestamp, "detail": self.detail}


@dataclass
class _TrackedWall:
    side: str
    price: float
    volume: float
    first_seen: float
    last_seen: float
    missing_since: Optional[float] = None
    confirmed: bool = False
    move_times: Deque[float] = field(default_factory=deque)
    volume_history: Deque[Tuple[float, float]] = field(default_factory=deque)


class SpoofingDetector:
    def __init__(
        self,
        snapshot_interval_ms: float = 200.0,
        wall_volume_multiplier: float = 3.0,
        repositioning_threshold_ms: float = 500.0,
        suspicious_moves_count: int = 5,
        suspicious_moves_window_sec: float = 10.0,
        confirmed_age_sec: float = 5.0,
        weakening_threshold_pct: float = 30.0,
        weakening_window_sec: float = 5.0,
        price_tolerance_pct: float = 0.1,
        event_cooldown_sec: float = 5.0,
        on_event: Optional[Callable[[WallEvent], None]] = None,
    ):
        self.snapshot_interval_ms = snapshot_interval_ms
        self.wall_volume_multiplier = wall_volume_multiplier
        self.repositioning_threshold_ms = repositioning_threshold_ms
        self.suspicious_moves_count = suspicious_moves_count
        self.suspicious_moves_window_sec = suspicious_moves_window_sec
        self.confirmed_age_sec = confirmed_age_sec
        self.weakening_threshold_pct = weakening_threshold_pct
        self.weakening_window_sec = weakening_window_sec
        self.price_tolerance_pct = price_tolerance_pct
        self.event_cooldown_sec = event_cooldown_sec
        self._on_event = on_event

        self._tracked: List[_TrackedWall] = []
        self._last_processed: Optional[float] = None
        self._processed = 0
        self._skipped = 0
        self._cooldown: dict = {}

    # ------------------------------------------------------------------ API
    def on_orderbook(self, snap: OrderbookSnapshot) -> List[WallEvent]:
        """THROTTLING: лишние снапшоты отбрасываются, обрабатываем не чаще 1 раза в 200 мс."""
        if (self._last_processed is not None
                and (snap.timestamp - self._last_processed) * 1000 < self.snapshot_interval_ms):
            self._skipped += 1
            return []

        self._last_processed = snap.timestamp
        self._processed += 1
        events = self._process_snapshot(snap)

        for e in events:  # БУФЕРИЗАЦИЯ: наружу только финальные события
            logger.info(f"[SPOOF] {e.event_type} | {e.side} @ {e.price} | {e.detail}")
            if self._on_event:
                self._on_event(e)
        return events

    def get_stats(self) -> dict:
        return {"processed": self._processed, "skipped": self._skipped,
                "tracked_walls": len(self._tracked)}

    # ------------------------------------------------------------- internal
    def _process_snapshot(self, snap: OrderbookSnapshot) -> List[WallEvent]:
        events: List[WallEvent] = []
        now = snap.timestamp
        to_remove: List[_TrackedWall] = []

        for side, levels in (("BID", snap.bids), ("ASK", snap.asks)):
            current_walls = self._detect_walls(levels)
            tracked = [w for w in self._tracked if w.side == side and w not in to_remove]
            matched: List[_TrackedWall] = []

            for price, volume in current_walls:
                best = self._match(tracked, matched, price)
                if best is not None:
                    matched.append(best)
                    events.extend(self._update_wall(best, price, volume, now))
                else:
                    wall = _TrackedWall(side=side, price=price, volume=volume,
                                        first_seen=now, last_seen=now)
                    wall.volume_history.append((now, volume))
                    self._tracked.append(wall)
                    events.append(WallEvent("WALL_DETECTED", side, price, volume, now, "NEW_WALL"))

            # Стена не найдена в снапшоте -> отслеживаем исчезновение
            for w in tracked:
                if w in matched:
                    continue
                if w.missing_since is None:
                    w.missing_since = now
                elif (now - w.missing_since) * 1000 > self.repositioning_threshold_ms:
                    # Сценарий B: исчезла и не вернулась за 500 мс -> Спуфинг
                    events.append(WallEvent("SPOOFING_CONFIRMED", w.side, w.price, w.volume, now,
                                            f"absent {(now - w.missing_since) * 1000:.0f}ms"))
                    to_remove.append(w)

        for w in to_remove:
            if w in self._tracked:
                self._tracked.remove(w)
        return events

    def _detect_walls(self, levels: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        if len(levels) < 5:
            return []
        avg = sum(v for _, v in levels) / len(levels)
        return [(p, v) for p, v in levels if v > self.wall_volume_multiplier * avg]

    def _match(self, tracked: List[_TrackedWall], matched: List[_TrackedWall],
               price: float) -> Optional[_TrackedWall]:
        tol = self.price_tolerance_pct / 100.0
        best, best_dist = None, None
        for w in tracked:
            if w in matched:
                continue
            dist = abs(w.price - price) / w.price if w.price else 1.0
            if dist <= tol and (best is None or dist < best_dist):
                best, best_dist = w, dist
        return best

    def _update_wall(self, w: _TrackedWall, price: float, volume: float,
                     now: float) -> List[WallEvent]:
        events: List[WallEvent] = []

        # Сценарий A: стена вернулась после короткого исчезновения -> REPOSITIONING
        if w.missing_since is not None:
            gap_ms = (now - w.missing_since) * 1000
            w.missing_since = None
            if gap_ms <= self.repositioning_threshold_ms:
                events.append(WallEvent("REPOSITIONING", w.side, price, volume, now,
                                        f"gap {gap_ms:.0f}ms"))
                w.move_times.append(now)
        elif abs(w.price - price) / w.price > 1e-9:
            w.move_times.append(now)  # стена переместилась, не исчезая

        # Сценарий C: > 5 перемещений за 10 сек -> SUSPICIOUS
        cutoff = now - self.suspicious_moves_window_sec
        while w.move_times and w.move_times[0] < cutoff:
            w.move_times.popleft()
        if len(w.move_times) > self.suspicious_moves_count:
            e = self._emit_guarded(w, "SUSPICIOUS", now,
                                   f"{len(w.move_times)} moves / {self.suspicious_moves_window_sec}s")
            if e:
                events.append(e)

        # Анализ убытия объема -> WEAKENING (стена слабеет)
        w.volume_history.append((now, volume))
        cutoff_v = now - self.weakening_window_sec - 1.0
        while w.volume_history and w.volume_history[0][0] < cutoff_v:
            w.volume_history.popleft()
        ref = self._volume_at(w, now - self.weakening_window_sec)
        if ref is not None and volume <= ref * (1 - self.weakening_threshold_pct / 100.0):
            e = self._emit_guarded(w, "WEAKENING", now, f"volume {ref:.0f} -> {volume:.0f}")
            if e:
                events.append(e)

        # Стена реальна: возраст >= 5 сек -> WALL_CONFIRMED (+25% в Confidence Score)
        if not w.confirmed and (now - w.first_seen) >= self.confirmed_age_sec:
            w.confirmed = True
            events.append(WallEvent("WALL_CONFIRMED", w.side, price, volume, now,
                                    f"age {now - w.first_seen:.1f}s"))

        w.price, w.volume, w.last_seen = price, volume, now
        return events

    @staticmethod
    def _volume_at(wall: _TrackedWall, target_ts: float) -> Optional[float]:
        for ts, vol in wall.volume_history:
            if ts >= target_ts:
                return vol
        return None

    def _emit_guarded(self, wall: _TrackedWall, etype: str, now: float,
                      detail: str = "") -> Optional[WallEvent]:
        """Cooldown: защита EventBus от спама повторяющимися событиями."""
        key = (id(wall), etype)
        last = self._cooldown.get(key)
        if last is not None and (now - last) < self.event_cooldown_sec:
            return None
        self._cooldown[key] = now
        return WallEvent(etype, wall.side, wall.price, wall.volume, now, detail)