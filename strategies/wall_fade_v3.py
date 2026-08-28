"""WallFade Strategy v3 — С интеграцией Confidence Score от детекторов."""
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


@dataclass
class EnrichedSignal:
    """Сигнал с полными данными для Advanced Risk."""
    signal_id: str
    symbol: str
    side: str
    entry_price: float
    strategy: str
    confidence: float
    
    edge_price: float
    rr_ratio: float
    atr: float
    volatility_mode: str
    basis: float


class WallFadeStrategyV3:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 30.0)
        
        # 🔥 НОВОЕ: Хранилище недавних событий от детекторов (последние 30 секунд)
        self._recent_detector_events: List[Dict[str, Any]] = []
        self._events_window_sec = 30.0
        
        # Подписка на EventBus для событий детекторов
        # (будет вызвана из main.py после инициализации)
        self._event_bus = None

    def subscribe_to_events(self, event_bus):
        """Подписка на события детекторов."""
        self._event_bus = event_bus
        event_bus.subscribe("WHALE_BUY", self._on_whale_event)
        event_bus.subscribe("WHALE_SELL", self._on_whale_event)
        event_bus.subscribe("WHALE_CLUSTER", self._on_whale_event)
        event_bus.subscribe("WALL_DETECTED", self._on_wall_event)
        event_bus.subscribe("WALL_CONFIRMED", self._on_wall_event)
        logger.info("✅ WallFadeV3 subscribed to detector events")

    async def _on_whale_event(self, event):
        """Обработчик событий от WhaleDetector."""
        payload = getattr(event, "payload", {})
        self._recent_detector_events.append({
            "type": event.type,
            "price": payload.get("price", 0.0),
            "volume": payload.get("value_usdt", 0.0),
            "cluster_size": payload.get("cluster_size", 1),
            "timestamp": time.time()
        })

    async def _on_wall_event(self, event):
        """Обработчик событий от SpoofingDetector."""
        payload = getattr(event, "payload", {})
        self._recent_detector_events.append({
            "type": event.type,
            "price": payload.get("price", 0.0),
            "volume": payload.get("volume", 0.0),
            "timestamp": time.time()
        })

    def _cleanup_old_events(self):
        """Удаляет события старше окна."""
        cutoff = time.time() - self._events_window_sec
        self._recent_detector_events = [
            e for e in self._recent_detector_events if e["timestamp"] >= cutoff
        ]

    def _calculate_confidence_boost(self, entry_price: float) -> float:
        """Рассчитывает бонус к Confidence на основе недавних событий детекторов."""
        self._cleanup_old_events()
        
        boost = 0.0
        price_tolerance_pct = 0.005  # 0.5% от цены входа
        
        for event in self._recent_detector_events:
            event_price = event.get("price", 0.0)
            if event_price <= 0:
                continue
            
            # Проверяем, было ли событие рядом с ценой входа
            price_diff_pct = abs(entry_price - event_price) / entry_price
            if price_diff_pct > price_tolerance_pct:
                continue  # Событие слишком далеко
            
            event_type = event.get("type", "")
            
            # 🔥 Логика из Стратегии.txt (Модуль 1.5)
            if event_type == "WHALE_CLUSTER":
                cluster_size = event.get("cluster_size", 1)
                # +60% за кластер, но не более 100%
                boost += min(0.60, 0.20 * cluster_size)
            elif event_type in ("WHALE_BUY", "WHALE_SELL"):
                boost += 0.30  # +30% за одиночного кита
            elif event_type == "WALL_CONFIRMED":
                boost += 0.25  # +25% за подтвержденную стену
        
        return min(boost, 1.0)  # Максимальный бонус 100%

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        """Генерирует обогащенный сигнал с динамическим Confidence Score."""
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        
        if current_price <= 0 or not orderbook.get('bids') or not orderbook.get('asks'):
            return None

        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # 1. Поиск края стены (Edge Price)
        bids = orderbook.get('bids', [])
        if len(bids) >= 5:
            avg_vol = sum(float(q) for p, q in bids[:10]) / min(10, len(bids))
            edge_price = current_price
            
            for p, q in bids:
                if float(q) > avg_vol * 2.0:
                    edge_price = float(p)
                    break
            
            entry_price = round(current_price, 2)
            edge_price = round(edge_price, 2)
            
            if abs(entry_price - edge_price) / entry_price < 0.005:
                # 2. Расчет R и RR
                atr = self.atr_value
                sl1 = edge_price - (atr * 0.3)
                r_value = abs(entry_price - sl1)
                
                tp1 = entry_price + (2.0 * r_value)
                rr_ratio = (tp1 - entry_price) / r_value if r_value > 0 else 0.0

                # 3.  НОВОЕ: Расчет Confidence Score с бонусом от детекторов
                base_confidence = 0.50  # База
                base_confidence += 0.25  # +25% за реальную стену
                if rr_ratio >= 2.0:
                    base_confidence += 0.10  # +10% за хороший R:R
                
                # Бонус от детекторов
                detector_boost = self._calculate_confidence_boost(entry_price)
                final_confidence = min(base_confidence + detector_boost, 1.0)
                
                if detector_boost > 0:
                    logger.info(
                        f"📈 Confidence boost: +{detector_boost*100:.0f}% from detectors "
                        f"(base={base_confidence:.2f} → final={final_confidence:.2f})"
                    )
                
                self._last_signal_time = now
                
                return EnrichedSignal(
                    signal_id=f"WallFadeV3_{symbol}_{int(now)}",
                    symbol=symbol,
                    side="short",
                    entry_price=entry_price,
                    strategy="WallFadeV3",
                    confidence=final_confidence,
                    edge_price=edge_price,
                    rr_ratio=rr_ratio,
                    atr=atr,
                    volatility_mode="normal",
                    basis=0.001
                )

        return None