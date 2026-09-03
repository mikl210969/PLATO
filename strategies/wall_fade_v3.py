"""WallFade Strategy v3 — С интеграцией Confidence Score от детекторов, HVN-якорем и динамическим BTC/SOL фильтром."""
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import logging
from core.logger import get_logger
logger = get_logger(__name__)


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
        self._last_divergence = None
        self._divergence_valid_for_sec = 900.0

        # 🔥 DEBUG MODE: Читаем флаги обхода фильтров из конфига
        debug_mode = config.get('debug_mode', {})
        self.bypass_btc_filter = debug_mode.get('bypass_btc_filter', False)
        self.bypass_adaptive_sl = debug_mode.get('bypass_adaptive_sl', False)
        self.bypass_smart_sizing = debug_mode.get('bypass_smart_sizing', False)
        
        # 🔥 НОВЫЕ ФИЛЬТРЫ ДЛЯ ОТЛАДКИ
        self.bypass_macro_hvn_filter = debug_mode.get('bypass_macro_hvn_filter', False)
        self.bypass_wall_distance_filter = debug_mode.get('bypass_wall_distance_filter', False)
        self.bypass_confidence_threshold = debug_mode.get('bypass_confidence_threshold', False)

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
        print(f" [WALLFADE] ВНУТРИ generate_signal | Цена: {context.get('current_price')} | Бидов: {len(context.get('orderbook', {}).get('bids', []))}")
        
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        hvn_micro = context.get('hvn_micro', [])
        hvn_macro = context.get('hvn_macro', [])
        atr = self.atr_value
        
        if current_price <= 0 or not orderbook.get('bids') or not orderbook.get('asks'):
            print("🚫 [WALLFADE] Отказ: нет цены или стакана")
            return None

        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            print(f"🚫 [WALLFADE] Отказ: кулдаун (осталось {self.cooldown_sec - (now - self._last_signal_time):.1f} сек)")
            return None

        btc_regime = context.get('btc_delta_context', {}).get('regime', 'NORMAL')
        if btc_regime == 'IMPULSIVE':
            if not self.bypass_btc_filter:
                print("🚫 [WALLFADE] Отказ: режим BTC IMPULSIVE")
                return None

        if not self.bypass_macro_hvn_filter:
            for macro_hvn in hvn_macro:
                hvn_price = macro_hvn['price']
                distance_pct = abs(current_price - hvn_price) / current_price
                if distance_pct < 0.005 and macro_hvn['strength'] > 15.0:
                    print(f"🚫 [WALLFADE] Отказ: MACRO HVN FILTER (hvn={hvn_price})")
                    return None

        bids = orderbook.get('bids', [])
        if len(bids) < 5:
            print(f"🚫 [WALLFADE] Отказ: в стакане меньше 5 бидов ({len(bids)})")
            return None
            
        avg_vol = sum(float(q) for p, q in bids[:10]) / min(10, len(bids))
        edge_price = current_price
        for p, q in bids:
            if float(q) > avg_vol * 2.0:
                edge_price = float(p)
                break
        
        entry_price = round(current_price, 2)
        edge_price = round(edge_price, 2)
        distance_pct = abs(entry_price - edge_price) / entry_price
        
        if not self.bypass_wall_distance_filter and distance_pct >= 0.005:
            print(f"🚫 [WALLFADE] Отказ: расстояние до стены {distance_pct:.4f} >= 0.005")
            return None
            
        sl_anchor_price = edge_price - (atr * 0.5)
        best_hvn = None
        for micro_hvn in hvn_micro:
            hvn_price = micro_hvn['price']
            if hvn_price < entry_price:
                distance = entry_price - hvn_price
                if distance <= (atr * 1.5):
                    if best_hvn is None or (entry_price - hvn_price) < (entry_price - best_hvn['price']):
                        best_hvn = micro_hvn
        if best_hvn: sl_anchor_price = best_hvn['price']
        
        sl1 = round(sl_anchor_price - (atr * 0.2), 2) 
        r_value = abs(entry_price - sl1)
        tp1 = round(entry_price + (2.0 * r_value), 2)
        rr_ratio = (tp1 - entry_price) / r_value if r_value > 0 else 0.0

        trend_data = context.get('trend', {})
        trend_state = trend_data.get('state', 'RANGING')
        is_short = True 
        signal_side = "short"
        
        if is_short and trend_data.get('is_continuation_for_short'):
            trade_type = "CONTINUATION"
            trend_bonus = 0.15
        elif is_short and trend_data.get('is_reversal_for_short'):
            trade_type = "REVERSAL"
            trend_bonus = -0.10
        else:
            trade_type = f"NEUTRAL ({trend_state})"
            trend_bonus = 0.0

        btc_context = context.get('btc_delta_context', {})
        sol_context = context.get('sol_delta_context', {})
        btc_trend = btc_context.get('trend', self.btc_trend)
        sol_delta = sol_context.get('delta_strength', 0.0)
        
        base_confidence = 0.50 + 0.25 + (0.10 if rr_ratio >= 2.0 else 0.0) + trend_bonus

        if not self.bypass_btc_filter:
            if signal_side == 'short' and btc_trend == 'UP': base_confidence *= 0.5
            elif signal_side == 'long' and btc_trend == 'DOWN': base_confidence *= 0.5

        if signal_side == 'short' and sol_delta > 30.0: base_confidence *= 0.7
        elif signal_side == 'long' and sol_delta < -30.0: base_confidence *= 0.7

        detector_boost = self._calculate_confidence_boost(entry_price)
        
        if "REVERSAL" in trade_type and detector_boost < 0.30:
            print(f" [WALLFADE] Отказ: REVERSAL без достаточного detector_boost ({detector_boost:.2f})")
            return None

        if self._last_divergence:
            div_age = now - self._last_divergence["timestamp"]
            if div_age <= self._divergence_valid_for_sec:
                if self._last_divergence["type"] == "BEARISH" and signal_side == "short":
                    base_confidence += 0.20

        final_confidence = min(base_confidence + detector_boost, 1.0)
        
        if not self.bypass_confidence_threshold and final_confidence < 0.50:
            print(f"🚫 [WALLFADE] Отказ: confidence {final_confidence:.2f} < 0.50")
            return None

        print(f"✅ [WALLFADE] СИГНАЛ СОЗДАН! Side: {signal_side}, Price: {entry_price}, Conf: {final_confidence:.2f}")
        self._last_signal_time = now
        
        return EnrichedSignal(
            signal_id=f"WallFadeV3_{symbol}_{int(now)}",
            symbol=symbol, side=signal_side, entry_price=entry_price, strategy="WallFadeV3",
            confidence=final_confidence, edge_price=edge_price, rr_ratio=rr_ratio,
            atr=atr, volatility_mode="normal", basis=0.001
        )