"""WallFade Strategy v3 — С интеграцией Confidence Score от детекторов, HVN-якорем и BTC-фильтром."""
import logging
import time
from dataclasses import dataclass, field
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
    
    # 🔥 Параметры гибридного исполнения (обратно совместимые)
    order_type: str = "limit"  # "limit" или "market"
    execution_params: dict = field(default_factory=dict)


class WallFadeStrategyV3:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        # В продакшене используем нормальный кулдаун (например, 30 сек)
        self.cooldown_sec = config.get('cooldown_sec', 30.0)
        
        # Хранилище недавних событий от детекторов (последние 30 секунд)
        self._recent_detector_events: List[Dict[str, Any]] = []
        self._events_window_sec = 30.0
        self._event_bus = None
        
        # 🔥 НОВОЕ: Состояние тренда BTC (по умолчанию FLAT)
        self.btc_trend = "FLAT"

    def subscribe_to_events(self, event_bus):
        """Подписка на события детекторов."""
        self._event_bus = event_bus
        self._event_bus.subscribe("WHALE_BUY", self._on_whale_event)
        self._event_bus.subscribe("WHALE_SELL", self._on_whale_event)
        self._event_bus.subscribe("WHALE_CLUSTER", self._on_whale_event)
        self._event_bus.subscribe("WALL_DETECTED", self._on_wall_event)
        self._event_bus.subscribe("WALL_CONFIRMED", self._on_wall_event)
        
        # 🔥 НОВОЕ: Подписка на контекст BTC
        self._event_bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        
        logger.info("✅ WallFadeV3 subscribed to detector events & BTC_CONTEXT_UPDATED")

    async def _on_btc_context_updated(self, event):
        """Обновляет локальное состояние тренда BTC при поступлении события."""
        self.btc_trend = event.payload.get("trend", "FLAT")

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
            
            price_diff_pct = abs(entry_price - event_price) / entry_price
            if price_diff_pct > price_tolerance_pct:
                continue  # Событие слишком далеко
            
            event_type = event.get("type", "")
            
            if event_type == "WHALE_CLUSTER":
                cluster_size = event.get("cluster_size", 1)
                boost += min(0.60, 0.20 * cluster_size)
            elif event_type in ("WHALE_BUY", "WHALE_SELL"):
                boost += 0.30
            elif event_type == "WALL_CONFIRMED":
                boost += 0.25
        
        return min(boost, 1.0)

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        hvn_micro = context.get('hvn_micro', [])
        hvn_macro = context.get('hvn_macro', [])
        atr = self.atr_value
        
        # Базовые проверки
        if current_price <= 0 or not orderbook.get('bids') or not orderbook.get('asks'):
            return None

        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # ========================================================================
        # 1. MACRO HVN FILTER (Глобальная защита)
        # ========================================================================
        for macro_hvn in hvn_macro:
            hvn_price = macro_hvn['price']
            distance_pct = abs(current_price - hvn_price) / current_price
            
            if distance_pct < 0.005 and macro_hvn['strength'] > 15.0:
                logger.debug(f"🛡️ [WallFadeV3] Отклонено: близко к Macro HVN @ {hvn_price:.2f} (dist: {distance_pct*100:.2f}%)")
                return None

        # ========================================================================
        # 2. ПОИСК ТОЧКИ ВХОДА И ЯКОРЯ ДЛЯ SL (Micro HVN)
        # ========================================================================
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
            
            distance_pct = abs(entry_price - edge_price) / entry_price
            
            if distance_pct < 0.005:
                sl_anchor_price = edge_price - (atr * 0.5)
                best_hvn = None
                
                for micro_hvn in hvn_micro:
                    hvn_price = micro_hvn['price']
                    if hvn_price < entry_price:
                        distance = entry_price - hvn_price
                        if distance <= (atr * 1.5):
                            if best_hvn is None or (entry_price - hvn_price) < (entry_price - best_hvn['price']):
                                best_hvn = micro_hvn
                
                if best_hvn:
                    sl_anchor_price = best_hvn['price']
                    logger.info(f"🎯 [WallFadeV3] SL привязан к Micro HVN @ {sl_anchor_price:.2f} (Strength: {best_hvn['strength']:.1f}%)")
                else:
                    logger.info(f"⚠️ [WallFadeV3] Micro HVN не найден рядом, fallback SL @ {sl_anchor_price:.2f}")

                sl1 = round(sl_anchor_price - (atr * 0.2), 2) 
                r_value = abs(entry_price - sl1)
                
                tp1 = round(entry_price + (2.0 * r_value), 2)
                rr_ratio = (tp1 - entry_price) / r_value if r_value > 0 else 0.0

                # ========================================================================
                # 3. ОЦЕНКА ТИПА СДЕЛКИ: Continuation vs Reversal (TrendContext)
                # ========================================================================
                trend_data = context.get('trend', {})
                trend_state = trend_data.get('state', 'RANGING')
                
                # Пока стратегия настроена на short, логика универсальна
                is_short = True 
                signal_side = "short"
                
                if is_short and trend_data.get('is_continuation_for_short'):
                    trade_type = "CONTINUATION (Short in Downtrend)"
                    trend_bonus = 0.15  # +15% к уверенности за торговлю по тренду
                    logger.info(f"📈 [WallFadeV3] CONTINUATION: Short в нисходящем тренде ({trend_state})")
                    
                elif is_short and trend_data.get('is_reversal_for_short'):
                    trade_type = "REVERSAL (Short in Uptrend)"
                    trend_bonus = -0.10 # -10% штраф за торговлю против тренда
                    logger.info(f"🛡️ [WallFadeV3] REVERSAL: Short против восходящего тренда ({trend_state}). Требуется сильный детектор!")
                    
                else:
                    trade_type = f"NEUTRAL ({trend_state})"
                    trend_bonus = 0.0
                    logger.info(f"⚖️ [WallFadeV3] NEUTRAL: Рынок во флэте ({trend_state})")

                # ========================================================================
                # 4. РАСЧЕТ CONFIDENCE SCORE
                # ========================================================================
                base_confidence = 0.50
                base_confidence += 0.25  # Бонус за наличие стены
                
                # ✅ Бонус за хорошее соотношение R:R
                if rr_ratio >= 2.0:
                    base_confidence += 0.10
                
                # Применяем бонус/штраф тренда
                base_confidence += trend_bonus
                
                # Бонус от детекторов (Киты, подтвержденные стены)
                detector_boost = self._calculate_confidence_boost(entry_price)
                
                # 🔥 КРИТИЧЕСКОЕ ПРАВИЛО: Для Reversal требуем минимум 30% буста от детекторов
                if "REVERSAL" in trade_type and detector_boost < 0.30:
                    logger.debug(f"🚫 [WallFadeV3] Отклонено: Reversal без достаточного подтверждения детекторов (boost={detector_boost:.2f})")
                    return None

                final_confidence = min(base_confidence + detector_boost, 1.0)
                
                logger.info(f"🎯 [WallFadeV3] Trade Type: {trade_type} | Base: {base_confidence:.2f} | Detector Boost: +{detector_boost:.2f} | Final: {final_confidence:.2f}")
                
                # ========================================================================
                # 🔥 ЭТАП 3: СВЕТОФОР (BTC Correlation Filter)
                # Блокируем сигналы, идущие против сильного тренда BTC
                # ========================================================================
                if signal_side == 'short' and self.btc_trend == 'UP':
                    logger.info(f"🚦 [BTC FILTER] WallFade SHORT сигнал отклонен. BTC тренд: {self.btc_trend}")
                    return None

                self._last_signal_time = now
                
                return EnrichedSignal(
                    signal_id=f"WallFadeV3_{symbol}_{int(now)}",
                    symbol=symbol,
                    side=signal_side,
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