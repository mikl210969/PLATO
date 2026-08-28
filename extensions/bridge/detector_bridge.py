"""Detector Bridge — мост WS-потока к детекторам (Дни 13-14).
Конвертирует payload любого формата (сырой Binance / нормализованный)
в Trade / OrderbookSnapshot и публикует события детекторов в EventBus.
Ядро не модифицируется: мост только читает события и публикует новые."""
import logging
import time
from typing import Optional, Callable, Awaitable

from extensions.analytics.whale_detector import WhaleDetector, Trade
from extensions.analytics.spoofing_detector import SpoofingDetector, OrderbookSnapshot

logger = logging.getLogger(__name__)


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ts_seconds(value) -> float:
    ts = _f(value)
    if ts > 1e12:  # миллисекунды → секунды
        ts /= 1000.0
    return ts or time.time()


class DetectorBridge:
    def __init__(self, whale: WhaleDetector, spoof: SpoofingDetector,
                 publish: Optional[Callable[[str, dict], Awaitable[None]]] = None):
        self.whale = whale
        self.spoof = spoof
        self._publish = publish
        self._outbox = []
        # События детекторов сначала копятся в outbox,
        # затем сливаются в шину из async-контекста
        whale._on_event = self._outbox.append
        spoof._on_event = self._outbox.append
        self._stats = {"trades": 0, "books": 0, "unrecognized": 0}

    async def on_market_event(self, event) -> None:
        etype = str(getattr(event, "type", "")).lower()
        payload = getattr(event, "payload", None)

        if "trade" in etype or self._looks_like_trade(payload):
            trade = self._parse_trade(payload)
            if trade:
                self._stats["trades"] += 1
                self.whale.on_trade(trade)
            else:
                self._note_unrecognized(payload)
        elif any(k in etype for k in ("book", "depth")) or self._looks_like_book(payload):
            snap = self._parse_orderbook(payload)
            if snap:
                self._stats["books"] += 1
                self.spoof.on_orderbook(snap)
            else:
                self._note_unrecognized(payload)
        else:
            self._note_unrecognized(payload)

        await self._drain()

    def get_stats(self) -> dict:
        return dict(self._stats)

    # ------------------------------------------------------------ parsing
    def _parse_trade(self, payload) -> Optional[Trade]:
        d = payload if isinstance(payload, dict) else None
        if d and isinstance(d.get("data"), dict):
            d = d["data"]
        if not isinstance(d, dict):
            return None

        price = _f(d.get("price", d.get("p")))
        qty = _f(d.get("quantity", d.get("qty", d.get("q"))))
        if price <= 0 or qty <= 0:
            return None
        value = _f(d.get("value_usdt")) or price * qty

        side = d.get("aggressor_side") or d.get("side")
        if side is None and d.get("m") is not None:
            side = "SELL" if bool(d["m"]) else "BUY"  # m = buyer is maker
        side = "BUY" if str(side).upper() == "BUY" else "SELL"

        ts = _ts_seconds(d.get("timestamp", d.get("T", d.get("E"))))
        return Trade(price=price, quantity=qty, value_usdt=value,
                     aggressor_side=side, timestamp=ts)

    def _parse_orderbook(self, payload) -> Optional[OrderbookSnapshot]:
        d = payload if isinstance(payload, dict) else None
        if d and isinstance(d.get("data"), dict):
            d = d["data"]
        if not isinstance(d, dict):
            return None

        bids_raw = d.get("bids", d.get("b"))
        asks_raw = d.get("asks", d.get("a"))
        if not bids_raw or not asks_raw:
            return None
        # ВАЖНО: если адаптер шлёт diff-depth (только изменения в b/a),
        # здесь понадобится локальная сборка стакана — уточним по binance_ws.py
        bids = [(_f(p), _f(q)) for p, q in bids_raw[:20]]
        asks = [(_f(p), _f(q)) for p, q in asks_raw[:20]]
        return OrderbookSnapshot(bids=bids, asks=asks,
                                 timestamp=_ts_seconds(d.get("timestamp", d.get("E"))))

    # ----------------------------------------------------------- internal
    @staticmethod
    def _looks_like_trade(payload) -> bool:
        return isinstance(payload, dict) and (
            ("p" in payload and "q" in payload)
            or ("price" in payload and "quantity" in payload))

    @staticmethod
    def _looks_like_book(payload) -> bool:
        return isinstance(payload, dict) and ("bids" in payload or "b" in payload)

    def _note_unrecognized(self, payload) -> None:
        self._stats["unrecognized"] += 1
        if self._stats["unrecognized"] <= 3:
            keys = list(payload)[:8] if isinstance(payload, dict) else type(payload)
            logger.warning(f"Нераспознанный формат MarketEvent: keys={keys}")

    async def _drain(self) -> None:
        if not self._publish:
            self._outbox.clear()
            return
        
        while self._outbox:
            e = self._outbox.pop(0)
            try:
                # 🔥 ЯРКИЙ ЛОГ ДЛЯ КЛЮЧЕВЫХ СОБЫТИЙ
                if e.event_type in ("WHALE_BUY", "WHALE_SELL", "WHALE_CLUSTER"):
                    print(f"🐋 [DETECTOR] {e.event_type} | Цена: {e.price} | Объём: {e.value_usdt:.0f} USDT | Cluster: {e.cluster_size}")
                elif e.event_type in ("WALL_DETECTED", "WALL_CONFIRMED", "SPOOFING_CONFIRMED", "REPOSITIONING"):
                    print(f"🧱 [DETECTOR] {e.event_type} | {e.side} @ {e.price} | Vol: {e.volume:.0f} | {e.detail}")
                
                # Публикуем в шину для стратегий
                await self._publish(e.event_type, e.to_dict())
            except Exception as ex:
                logger.error(f"publish failed: {ex}")