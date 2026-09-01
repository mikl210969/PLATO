"""WallFade Strategy v3 — С интеграцией Confidence Score от детекторов, HVN-якорем и динамическим BTC/SOL фильтром."""
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
    order_type: str = "limit"
    execution_params: dict = field(default_factory=dict)


class WallFadeStrategyV3:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 30.0)
        
        self._recent_detector_events: List[Dict[str, Any]] = []
        self._events_window_sec = 30.0
        self._event_bus = None
        
        # Фоллбэк: Состояние тренда BTC
        self.btc_trend = "FLAT"
        
        # 🔥 НОВОЕ: Хранилище последней дивергенции
        self._last_divergence = None  # {"type": "BULLISH"/"BEARISH", "timestamp": ..., "price": ...}
        self._divergence_valid_for_sec = 900.0  # 15 минут актуальности

    def subscribe_to_events(self, event_bus):
        self._event_bus = event_bus
        self._event_bus.subscribe("WHALE_BUY", self._on_whale_event)
        self._event_bus.subscribe("WHALE_SELL", self._on_whale_event)
        self._event_bus.subscribe("WHALE_CLUSTER", self._on_whale_event)
        self._event_bus.subscribe("WALL_DETECTED", self._on_wall_event)
        self._event_bus.subscribe("WALL_CONFIRMED", self._on_wall_event)
        self._event_bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        
        # 🔥 НОВОЕ: Подписка на дивергенции
        self._event_bus.subscribe("DIVERGENCE_DETECTED", self._on_divergence_detected)
        
        logger.info("✅ WallFadeV3 subscribed to detector events, BTC_CONTEXT_UPDATED & DIVERGENCE_DETECTED")

        # 🔥 АДАПТИВНЫЙ ATR: Подписка на обновления
        self._event_bus.subscribe("ATR_UPDATED", self._on_atr_updated)
        
        logger.info("✅ WallFadeV3 subscribed to detector events, BTC_CONTEXT_UPDATED, DIVERGENCE_DETECTED & ATR_UPDATED")        

    async def _on_btc_context_updated(self, event):
        self.btc_trend = event.payload.get("trend", "FLAT")

    async def _on_divergence_detected(self, event):
        """🔥 НОВОЕ: Сохраняем информацию о дивергенции."""
        payload = getattr(event, "payload", {})
        self._last_divergence = {
            "type": payload.get("type"),  # "BULLISH" или "BEARISH"
            "price": payload.get("price", 0.0),
            "timestamp": time.time()
        }
        logger.info(f"🚨 [WallFadeV3] Запомнена дивергенция: {self._last_divergence['type']} @ {self._last_divergence['price']:.2f}")

    async def _on_whale_event(self, event):
        payload = getattr(event, "payload", {})
        self._recent_detector_events.append({
            "type": event.type,
            "price": payload.get("price", 0.0),
            "volume": payload.get("value_usdt", 0.0),
            "cluster_size": payload.get("cluster_size", 1),
            "timestamp": time.time()
        })

    async def _on_wall_event(self, event):
        payload = getattr(event, "payload", {})
        self._recent_detector_events.append({
            "type": event.type,
            "price": payload.get("price", 0.0),
            "volume": payload.get("volume", 0.0),
            "timestamp": time.time()
        })

    async def _on_atr_updated(self, event):
        """🔥 АДАПТИВНЫЙ ATR: Обновляем значение ATR при получении события."""
        payload = getattr(event, 'payload', {})
        symbol = payload.get('symbol', '')
        new_atr = payload.get('atr', 0.0)
        
        # Обновляем только если символ совпадает
        # (стратегия может работать с несколькими символами)
        if new_atr > 0:
            old_atr = self.atr_value
            self.atr_value = new_atr
            logger.info(f"📊 [WallFadeV3] ATR обновлён: {old_atr:.4f} → {new_atr:.4f}")

    def _cleanup_old_events(self):
        cutoff = time.time() - self._events_window_sec
        self._recent_detector_events = [e for e in self._recent_detector_events if e["timestamp"] >= cutoff]

    def _calculate_confidence_boost(self, entry_price: float) -> float:
        self._cleanup_old_events()
        boost = 0.0
        price_tolerance_pct = 0.005
        
        for event in self._recent_detector_events:
            event_price = event.get("price", 0.0)
            if event_price <= 0: continue
            
            price_diff_pct = abs(entry_price - event_price) / entry_price
            if price_diff_pct > price_tolerance_pct: continue
            
            event_type = event.get("type", "")
            if event_type == "WHALE_CLUSTER":
                boost += min(0.60, 0.20 * event.get("cluster_size", 1))
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
        
        if current_price <= 0 or not orderbook.get('bids') or not orderbook.get('asks'):
            return None

        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # ========================================================================
        # 🔥 УРОВЕНЬ 4: REGIME FILTER (Блокировка контртрендовых стратегий в импульсе)
        # ========================================================================
        btc_regime = context.get('btc_delta_context', {}).get('regime', 'NORMAL')
        
        if btc_regime == 'IMPULSIVE':
            logger.info(f"🚫 [{self.__class__.__name__}] Сигнал отклонен: режим IMPULSIVE (|дельта BTC| слишком сильная, риск 'поймать нож')")
            return None
        # ========================================================================

        # 1. MACRO HVN FILTER
        for macro_hvn in hvn_macro:
            hvn_price = macro_hvn['price']
            distance_pct = abs(current_price - hvn_price) / current_price
            if distance_pct < 0.005 and macro_hvn['strength'] > 15.0:
                return None

        # 2. ПОИСК ТОЧКИ ВХОДА И ЯКОРЯ ДЛЯ SL
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
                sl1 = round(sl_anchor_price - (atr * 0.2), 2) 
                r_value = abs(entry_price - sl1)
                tp1 = round(entry_price + (2.0 * r_value), 2)
                rr_ratio = (tp1 - entry_price) / r_value if r_value > 0 else 0.0

                # 3. ОЦЕНКА ТИПА СДЕЛКИ
                trend_data = context.get('trend', {})
                trend_state = trend_data.get('state', 'RANGING')
                is_short = True 
                signal_side = "short"
                
                if is_short and trend_data.get('is_continuation_for_short'):
                    trade_type = "CONTINUATION (Short in Downtrend)"
                    trend_bonus = 0.15
                elif is_short and trend_data.get('is_reversal_for_short'):
                    trade_type = "REVERSAL (Short in Uptrend)"
                    trend_bonus = -0.10
                else:
                    trade_type = f"NEUTRAL ({trend_state})"
                    trend_bonus = 0.0

                # ========================================================================
                # 🔥 УРОВЕНЬ 4: Динамическая корректировка Confidence (Delta Context)
                # ========================================================================
                btc_context = context.get('btc_delta_context', {})
                sol_context = context.get('sol_delta_context', {})
                
                btc_trend = btc_context.get('trend', self.btc_trend)
                sol_delta = sol_context.get('delta_strength', 0.0)
                
                base_confidence = 0.50
                base_confidence += 0.25  # Бонус за наличие стены
                if rr_ratio >= 2.0: base_confidence += 0.10
                base_confidence += trend_bonus

                # Штрафы за макротренд (WallFade опасен против тренда)
                if signal_side == 'short' and btc_trend == 'UP':
                    base_confidence *= 0.5
                    logger.warning(f"⚠️ [WallFadeV3] Штраф к confidence: попытка SHORT при UP тренде BTC")
                elif signal_side == 'long' and btc_trend == 'DOWN':
                    base_confidence *= 0.5
                    logger.warning(f"⚠️ [WallFadeV3] Штраф к confidence: попытка LONG при DOWN тренде BTC")

                # Штрафы за локальную дельту SOL
                if signal_side == 'short' and sol_delta > 30.0:
                    base_confidence *= 0.7
                    logger.warning(f"⚠️ [WallFadeV3] Штраф к confidence: положительная дельта SOL ({sol_delta}) при SHORT")
                elif signal_side == 'long' and sol_delta < -30.0:
                    base_confidence *= 0.7
                    logger.warning(f"⚠️ [WallFadeV3] Штраф к confidence: отрицательная дельта SOL ({sol_delta}) при LONG")

                detector_boost = self._calculate_confidence_boost(entry_price)
                
                if "REVERSAL" in trade_type and detector_boost < 0.30:
                    return None

                # ========================================================================
                # 🔥 УРОВЕНЬ 3: Бонус за подтверждение дивергенцией
                # ========================================================================
                if self._last_divergence:
                    div_age = now - self._last_divergence["timestamp"]
                    if div_age <= self._divergence_valid_for_sec:
                        div_type = self._last_divergence["type"]
                        
                        if div_type == "BEARISH" and signal_side == "short":
                            base_confidence += 0.20
                            logger.info(f"🚨 [DIVERGENCE CONFIRMED] Сигнал SHORT подтвержден медвежьей дивергенцией! +0.20 к confidence")
                        elif div_type == "BULLISH" and signal_side == "long":
                            base_confidence += 0.20
                            logger.info(f"🚨 [DIVERGENCE CONFIRMED] Сигнал LONG подтвержден бычьей дивергенцией! +0.20 к confidence")
                    else:
                        self._last_divergence = None
                # ========================================================================

                final_confidence = min(base_confidence + detector_boost, 1.0)
                
                # Жесткий порог отсечения
                if final_confidence < 0.50:
                    logger.info(f"🚫 [WallFadeV3] Сигнал ОТКЛОНЕН: итоговый confidence {final_confidence:.2f} ниже порога 0.50 (BTC: {btc_trend}, SOL_Delta: {sol_delta})")
                    return None

                logger.info(f"🎯 [WallFadeV3] Trade Type: {trade_type} | Base: {base_confidence:.2f} | Detector Boost: +{detector_boost:.2f} | Final: {final_confidence:.2f} | BTC: {btc_trend} | SOL_Delta: {sol_delta}")
                
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