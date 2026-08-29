"""Breakout Strategy V1 — торгует на пробой уровня с гибридным исполнением."""
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
        
        # Порог ликвидности за стеной для переключения на рыночный ордер
        # Если суммарная ликвидность на 0.5% за стеной меньше этого значения → market
        self.liquidity_void_threshold = config.get('liquidity_void_threshold', 5000.0)
        
        # Параметры гибридного исполнения
        self.max_attempts = config.get('max_attempts', 2)
        self.timeout_sec = config.get('timeout_sec', 5.0)
        self.price_offset = config.get('price_offset', 0.01)
        
        # Храним последнее событие пробоя
        self._last_breakout_event: Optional[Dict[str, Any]] = None
        self._event_valid_for_sec = 10.0  # Сигнал актуален 10 секунд
        
        self._event_bus = None

    def subscribe_to_events(self, event_bus):
        """Подписка на события детектора пробоя."""
        self._event_bus = event_bus
        event_bus.subscribe("BREAKOUT_OPPORTUNITY", self._on_breakout_event)
        logger.info("✅ BreakoutStrategyV1 subscribed to BREAKOUT_OPPORTUNITY")

    async def _on_breakout_event(self, event):
        """Сохраняем событие, когда детектор находит возможность пробоя."""
        payload = getattr(event, "payload", {})
        self._last_breakout_event = {
            "wall_price": payload.get("wall_price", 0.0),
            "side": payload.get("side", "BID"),  # BID или ASK
            "consumption_pct": payload.get("consumption_pct", 0.0),
            "consumption_rate_pct": payload.get("consumption_rate_pct", 0.0),
            "initial_volume": payload.get("initial_volume", 0.0),
            "current_volume": payload.get("current_volume", 0.0),
            "timestamp": time.time()
        }
        logger.info(
            f"🧠 [BreakoutStrat] Получено событие: стена @ {self._last_breakout_event['wall_price']:.2f} | "
            f"Съедено: {self._last_breakout_event['consumption_pct']:.1f}% | "
            f"Скорость: {self._last_breakout_event['consumption_rate_pct']:.1f}%/s"
        )

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        """Генерирует сигнал, если недавно было событие пробоя."""
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        atr = self.atr_value
        
        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # Проверяем, есть ли свежее событие пробоя
        if not self._last_breakout_event:
            return None
            
        event_age = now - self._last_breakout_event["timestamp"]
        if event_age > self._event_valid_for_sec:
            self._last_breakout_event = None
            return None

        event = self._last_breakout_event
        wall_price = event["wall_price"]
        wall_side = event["side"]
        
        # Сбрасываем событие
        self._last_breakout_event = None

        # ========================================================================
        # 1. ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ СДЕЛКИ
        # ========================================================================
        # BID стена съедается → пробой вниз → SHORT
        # ASK стена съедается → пробой вверх → LONG
        if wall_side == "ASK":
            signal_side = "long"
            logger.info(f"🟢 [BreakoutStrat] ASK стена съедается → LONG пробой")
        elif wall_side == "BID":
            signal_side = "short"
            logger.info(f"🔴 [BreakoutStrat] BID стена съедается → SHORT пробой")
        else:
            return None

        # ========================================================================
        # 2. ПРОВЕРКА TRENDCONTEXT
        # ========================================================================
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

        # Для Reversal требуем очень сильное подтверждение
        if "REVERSAL" in trade_type:
            logger.debug(f"🚫 [BreakoutStrat] Отклонено: Reversal пробой слишком рискован")
            return None

        # ========================================================================
        # 3. АНАЛИЗ ЛИКВИДНОСТИ ЗА СТЕНОЙ
        # ========================================================================
        liquidity_behind_wall = self._analyze_liquidity_behind_wall(
            orderbook, wall_price, signal_side
        )
        
        # Определяем тип ордера на основе ликвидности
        if liquidity_behind_wall < self.liquidity_void_threshold:
            order_type = "market"
            logger.info(
                f"⚡ [BreakoutStrat] Пустота ликвидности за стеной! "
                f"Liquidity: {liquidity_behind_wall:.0f} < {self.liquidity_void_threshold:.0f} → MARKET ORDER"
            )
        else:
            order_type = "limit"
            logger.info(
                f"📋 [BreakoutStrat] Ликвидность за стеной достаточна. "
                f"Liquidity: {liquidity_behind_wall:.0f} → LIMIT ORDER (hybrid)"
            )

        # ========================================================================
        # 4. РАСЧЕТ УРОВНЕЙ (Entry, SL, TP)
        # ========================================================================
        entry_price = round(current_price, 2)
        
        # SL: чуть за пробиваемым уровнем
        if signal_side == "long":
            sl_price = round(wall_price - (atr * 0.5), 2)
        else:
            sl_price = round(wall_price + (atr * 0.5), 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0:
            r_value = atr
            
        # TP: R:R 2.5 (пробой — более агрессивная стратегия)
        target_rr = 2.5
        if signal_side == "long":
            tp1_price = round(entry_price + (target_rr * r_value), 2)
        else:
            tp1_price = round(entry_price - (target_rr * r_value), 2)
            
        rr_ratio = target_rr

        # ========================================================================
        # 5. РАСЧЕТ CONFIDENCE
        # ========================================================================
        base_confidence = 0.55  # Пробой сам по себе сильный сигнал
        
        # Бонус за высокую скорость поедания
        if event["consumption_rate_pct"] > 10.0:
            base_confidence += 0.10
            
        # Бонус за высокий процент съеденности
        if event["consumption_pct"] > 80.0:
            base_confidence += 0.10
            
        # Бонус/штраф тренда
        base_confidence += trend_bonus
        
        # Бонус за пустоту ликвидности (сильный импульс гарантирован)
        if order_type == "market":
            base_confidence += 0.05
            
        final_confidence = min(max(base_confidence, 0.0), 1.0)

        # ========================================================================
        # 6. ФОРМИРОВАНИЕ ПАРАМЕТРОВ ГИБРИДНОГО ИСПОЛНЕНИЯ
        # ========================================================================
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
            f"🚀 [BreakoutStrat] SIGNAL: {signal_side.upper()} | "
            f"Entry: {entry_price} | SL: {sl_price} | TP1: {tp1_price} | "
            f"R:R: {rr_ratio:.1f} | Conf: {final_confidence:.2f} | "
            f"Order: {order_type.upper()} | Type: {trade_type}"
        )

        return EnrichedSignal(
            signal_id=f"BreakoutV1_{symbol}_{int(now)}",
            symbol=symbol,
            side=signal_side,
            entry_price=entry_price,
            strategy="BreakoutV1",
            confidence=final_confidence,
            edge_price=wall_price,
            rr_ratio=rr_ratio,
            atr=atr,
            volatility_mode="normal",
            basis=0.001,
            order_type=order_type,
            execution_params=execution_params
        )

    def _analyze_liquidity_behind_wall(
        self, orderbook: Dict, wall_price: float, signal_side: str
    ) -> float:
        """
        Анализирует ликвидность за пробиваемой стеной.
        Для LONG: смотрим asks выше wall_price на 0.5%
        Для SHORT: смотрим bids ниже wall_price на 0.5%
        """
        try:
            depth_pct = 0.005  # 0.5% глубина анализа
            
            if signal_side == "long":
                # Пробой вверх → смотрим asks за стеной
                asks = orderbook.get('asks', [])
                max_price = wall_price * (1 + depth_pct)
                total_liquidity = 0.0
                for price, volume in asks:
                    p = float(price)
                    if p > wall_price and p <= max_price:
                        total_liquidity += float(volume) * p  # В USDT
                return total_liquidity
                
            else:
                # Пробой вниз → смотрим bids за стеной
                bids = orderbook.get('bids', [])
                min_price = wall_price * (1 - depth_pct)
                total_liquidity = 0.0
                for price, volume in bids:
                    p = float(price)
                    if p < wall_price and p >= min_price:
                        total_liquidity += float(volume) * p  # В USDT
                return total_liquidity
                
        except Exception as e:
            logger.error(f"Error analyzing liquidity behind wall: {e}")
            return 0.0