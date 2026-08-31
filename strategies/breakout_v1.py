"""Breakout Strategy V1 — торгует на пробой уровня с гибридным исполнением и динамическим Delta-фильтром."""
import logging
import time
from typing import Optional, Dict, Any, List

from strategies.wall_fade_v3 import EnrichedSignal

logger = logging.getLogger(__name__)


class BreakoutStrategyV1:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 60.0)
        self.liquidity_void_threshold = config.get('liquidity_void_threshold', 5000.0)
        self.max_attempts = config.get('max_attempts', 2)
        self.timeout_sec = config.get('timeout_sec', 5.0)
        self.price_offset = config.get('price_offset', 0.01)
        
        self._last_breakout_event: Optional[Dict[str, Any]] = None
        self._event_valid_for_sec = 10.0
        self.btc_trend = "FLAT" # Фоллбэк
        self._event_bus = None

    def subscribe_to_events(self, event_bus):
        self._event_bus = event_bus
        self._event_bus.subscribe("BREAKOUT_OPPORTUNITY", self._on_breakout_event)
        self._event_bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        logger.info("✅ BreakoutStrategyV1 subscribed to BREAKOUT_OPPORTUNITY & BTC_CONTEXT_UPDATED")

    async def _on_btc_context_updated(self, event):
        self.btc_trend = event.payload.get("trend", "FLAT")

    async def _on_breakout_event(self, event):
        payload = getattr(event, "payload", {})
        self._last_breakout_event = {
            "wall_price": payload.get("wall_price", 0.0),
            "side": payload.get("side", "BID"),
            "consumption_pct": payload.get("consumption_pct", 0.0),
            "consumption_rate_pct": payload.get("consumption_rate_pct", 0.0),
            "initial_volume": payload.get("initial_volume", 0.0),
            "current_volume": payload.get("current_volume", 0.0),
            "timestamp": time.time()
        }

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        atr = self.atr_value
        
        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        if not self._last_breakout_event:
            return None
            
        event_age = now - self._last_breakout_event["timestamp"]
        if event_age > self._event_valid_for_sec:
            self._last_breakout_event = None
            return None

        event = self._last_breakout_event
        wall_price = event["wall_price"]
        wall_side = event["side"]
        self._last_breakout_event = None

        # 1. ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ
        if wall_side == "ASK":
            signal_side = "long"
        elif wall_side == "BID":
            signal_side = "short"
        else:
            return None

        # 2. ПРОВЕРКА TRENDCONTEXT
        trend_data = context.get('trend', {})
        trend_state = trend_data.get('state', 'RANGING')
        
        if signal_side == "long" and trend_data.get('is_continuation_for_long'):
            trade_type = "CONTINUATION (Long in Uptrend)"
            trend_bonus = 0.15
        elif signal_side == "short" and trend_data.get('is_continuation_for_short'):
            trade_type = "CONTINUATION (Short in Downtrend)"
            trend_bonus = 0.15
        elif signal_side == "long" and trend_data.get('is_reversal_for_long'):
            trade_type = "REVERSAL (Long in Downtrend)"
            trend_bonus = -0.15
        elif signal_side == "short" and trend_data.get('is_reversal_for_short'):
            trade_type = "REVERSAL (Short in Uptrend)"
            trend_bonus = -0.15
        else:
            trade_type = f"NEUTRAL ({trend_state})"
            trend_bonus = 0.0

        if "REVERSAL" in trade_type:
            return None # Пробой против тренда слишком рискован

        # ========================================================================
        # 🔥 УРОВЕНЬ 4: Динамическая корректировка Confidence (Delta Context)
        # ========================================================================
        btc_context = context.get('btc_delta_context', {})
        sol_context = context.get('sol_delta_context', {})
        
        btc_trend = btc_context.get('trend', self.btc_trend)
        sol_delta = sol_context.get('delta_strength', 0.0)
        
        base_confidence = 0.55
        
        # Бонусы за скорость и объем
        if event["consumption_rate_pct"] > 10.0: base_confidence += 0.10
        if event["consumption_pct"] > 80.0: base_confidence += 0.10
        base_confidence += trend_bonus

        # Логика для пробоев: Совпадение с трендом = БОНУС, Против тренда = СУРОВЫЙ ШТРАФ
        if signal_side == 'long' and btc_trend == 'UP':
            base_confidence += 0.15  # Бонус за подтверждение трендом BTC
            logger.info(f"✅ [BreakoutV1] Бонус к confidence: LONG пробой подтвержден UP трендом BTC")
        elif signal_side == 'long' and btc_trend == 'DOWN':
            base_confidence *= 0.4   # Суровый штраф (высокий риск fakeout)
            logger.warning(f"⚠️ [BreakoutV1] Штраф к confidence: LONG пробой против DOWN тренда BTC")
            
        elif signal_side == 'short' and btc_trend == 'DOWN':
            base_confidence += 0.15
            logger.info(f"✅ [BreakoutV1] Бонус к confidence: SHORT пробой подтвержден DOWN трендом BTC")
        elif signal_side == 'short' and btc_trend == 'UP':
            base_confidence *= 0.4
            logger.warning(f"⚠️ [BreakoutV1] Штраф к confidence: SHORT пробой против UP тренда BTC")

        # Подтверждение локальной дельтой SOL
        if signal_side == 'long' and sol_delta > 50.0:
            base_confidence += 0.10
        elif signal_side == 'short' and sol_delta < -50.0:
            base_confidence += 0.10

        # 3. АНАЛИЗ ЛИКВИДНОСТИ
        liquidity_behind_wall = self._analyze_liquidity_behind_wall(orderbook, wall_price, signal_side)
        
        if liquidity_behind_wall < self.liquidity_void_threshold:
            order_type = "market"
            base_confidence += 0.05 # Бонус за пустоту (сильный импульс)
        else:
            order_type = "limit"

        final_confidence = min(max(base_confidence, 0.0), 1.0)

        # Жесткий порог отсечения
        if final_confidence < 0.50:
            logger.info(f"🚫 [BreakoutV1] Сигнал ОТКЛОНЕН: итоговый confidence {final_confidence:.2f} ниже порога 0.50 (BTC: {btc_trend}, SOL_Delta: {sol_delta})")
            return None

        # 4. РАСЧЕТ УРОВНЕЙ
        entry_price = round(current_price, 2)
        if signal_side == "long":
            sl_price = round(wall_price - (atr * 0.5), 2)
        else:
            sl_price = round(wall_price + (atr * 0.5), 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0: r_value = atr
            
        target_rr = 2.5
        if signal_side == "long":
            tp1_price = round(entry_price + (target_rr * r_value), 2)
        else:
            tp1_price = round(entry_price - (target_rr * r_value), 2)

        execution_params = {
            "max_attempts": self.max_attempts,
            "timeout_sec": self.timeout_sec,
            "price_offset": self.price_offset,
            "fallback_to_market": True,
            "wall_price": wall_price,
            "consumption_pct": event["consumption_pct"],
            "liquidity_behind_wall": liquidity_behind_wall
        }
        
        self._last_signal_time = now
        
        logger.info(
            f"🚀 [BreakoutV1] SIGNAL CONFIRMED: {signal_side.upper()} | "
            f"Entry: {entry_price} | SL: {sl_price} | TP1: {tp1_price} | "
            f"R:R: {target_rr:.1f} | Conf: {final_confidence:.2f} | "
            f"Order: {order_type.upper()} | Type: {trade_type} | BTC: {btc_trend} | SOL_Delta: {sol_delta}"
        )

        return EnrichedSignal(
            signal_id=f"BreakoutV1_{symbol}_{int(now)}",
            symbol=symbol,
            side=signal_side,
            entry_price=entry_price,
            strategy="BreakoutV1",
            confidence=final_confidence,
            edge_price=wall_price,
            rr_ratio=target_rr,
            atr=atr,
            volatility_mode="normal",
            basis=0.001,
            order_type=order_type,
            execution_params=execution_params
        )

    def _analyze_liquidity_behind_wall(self, orderbook: Dict, wall_price: float, signal_side: str) -> float:
        try:
            depth_pct = 0.005
            if signal_side == "long":
                asks = orderbook.get('asks', [])
                max_price = wall_price * (1 + depth_pct)
                return sum(float(volume) * float(price) for price, volume in asks if float(price) > wall_price and float(price) <= max_price)
            else:
                bids = orderbook.get('bids', [])
                min_price = wall_price * (1 - depth_pct)
                return sum(float(volume) * float(price) for price, volume in bids if float(price) < wall_price and float(price) >= min_price)
        except Exception as e:
            logger.error(f"Error analyzing liquidity behind wall: {e}")
            return 0.0