"""Absorption Strategy V2 — торгует на отскок после поглощения агрессии."""
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# Импортируем EnrichedSignal из wall_fade_v3, чтобы не дублировать код
from strategies.wall_fade_v3 import EnrichedSignal 

logger = logging.getLogger(__name__)


class AbsorptionStrategyV2:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 60.0) # 60 сек кулдаун
        
        # Храним последнее событие поглощения
        self._last_absorption_event: Optional[Dict[str, Any]] = None
        self._event_valid_for_sec = 5.0  # Сигнал актуален только 5 секунд после события
        
        # 🔥 НОВОЕ: Состояние тренда BTC (по умолчанию FLAT)
        self.btc_trend = "FLAT"        
        self._event_bus = None

    def subscribe_to_events(self, event_bus):
        """Подписка на события детектора поглощения."""
        self._event_bus = event_bus
        self._event_bus.subscribe("ABSORPTION_DETECTED", self._on_absorption_event)
        
        # 🔥 ИСПРАВЛЕНО: используем self._event_bus вместо self.bus (была опечатка)
        self._event_bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        
        logger.info("✅ AbsorptionStrategyV2 subscribed to ABSORPTION_DETECTED & BTC_CONTEXT_UPDATED")

    async def _on_btc_context_updated(self, event):
        """Обновляет локальное состояние тренда BTC при поступлении события."""
        self.btc_trend = event.payload.get("trend", "FLAT")

    async def _on_absorption_event(self, event):
        """Сохраняем событие, когда детектор его находит."""
        payload = getattr(event, "payload", {})
        self._last_absorption_event = {
            "side": payload.get("side"),          # "BULLISH" или "BEARISH"
            "price": payload.get("price", 0.0),
            "delta_velocity": payload.get("delta_velocity", 0.0),
            "imbalance": payload.get("imbalance", 0.0),
            "timestamp": time.time()
        }
        logger.info(f"🧠 [AbsorptionStrat] Получено событие: {self._last_absorption_event['side']} @ {self._last_absorption_event['price']:.2f}")

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        """Генерирует сигнал, если недавно было событие поглощения."""
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        atr = self.atr_value
        
        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # Проверяем, есть ли свежее событие поглощения
        if not self._last_absorption_event:
            return None
            
        event_age = now - self._last_absorption_event["timestamp"]
        if event_age > self._event_valid_for_sec:
            # Событие устарело, сбрасываем его
            self._last_absorption_event = None
            return None

        # Извлекаем данные события
        event = self._last_absorption_event
        absorption_side = event["side"]
        event_price = event["price"]
        
        # Сбрасываем событие, чтобы не генерировать сигналы многократно на одном и том же
        self._last_absorption_event = None

        # ========================================================================
        # ОПРЕДЕЛЕНИЕ НАПРАВЛЕНИЯ СДЕЛКИ
        # ========================================================================
        # BULLISH поглощение (продавцы бьют в bid-стену) -> СИГНАЛ НА LONG
        # BEARISH поглощение (покупатели бьют в ask-стену) -> СИГНАЛ НА SHORT
        if absorption_side == "BULLISH":
            signal_side = "long"
            logger.info(f"🟢 [AbsorptionStrat] BULLISH Absorption detected! Preparing LONG signal.")
        elif absorption_side == "BEARISH":
            signal_side = "short"
            logger.info(f"🔴 [AbsorptionStrat] BEARISH Absorption detected! Preparing SHORT signal.")
        else:
            return None

        # ========================================================================
        # 🔥 ЭТАП 3: СВЕТОФОР (BTC Correlation Filter)
        # Блокируем сигналы, идущие против сильного тренда BTC
        # ========================================================================
        if signal_side == 'long' and self.btc_trend == 'DOWN':
            logger.info(f"🚦 [BTC FILTER] Absorption LONG сигнал отклонен. BTC тренд: {self.btc_trend}")
            return None
            
        if signal_side == 'short' and self.btc_trend == 'UP':
            logger.info(f"🚦 [BTC FILTER] Absorption SHORT сигнал отклонен. BTC тренд: {self.btc_trend}")
            return None

        # ========================================================================
        # РАСЧЕТ УРОВНЕЙ (Entry, SL, TP) на основе ATR
        # ========================================================================
        entry_price = round(current_price, 2)
        
        # Для поглощения стоп ставим чуть за уровень, где происходило поглощение
        # с небольшим буфером ATR
        if signal_side == "long":
            sl_price = round(event_price - (atr * 0.3), 2)
        else: # short
            sl_price = round(event_price + (atr * 0.3), 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0:
            r_value = atr # Защита от деления на ноль
            
        tp1_price = round(entry_price + (2.0 * r_value) if signal_side == "long" else entry_price - (2.0 * r_value), 2)
        rr_ratio = 2.0 # Мы жестко целимся в R:R 2.0

        # ========================================================================
        # РАСЧЕТ CONFIDENCE
        # ========================================================================
        # Поглощение само по себе сильный сигнал. Даем высокий базовый confidence.
        base_confidence = 0.65 
        
        # Если дельта была огромной, повышаем уверенность
        if abs(event["delta_velocity"]) > 10000.0:
            base_confidence += 0.10
            
        # Если имбаланс стакана подтверждает стену, повышаем уверенность
        if abs(event["imbalance"]) > 0.4:
            base_confidence += 0.10
            
        final_confidence = min(base_confidence, 1.0)
        
        self._last_signal_time = now
        
        logger.info(f"🚀 [AbsorptionStrat] SIGNAL GENERATED: {signal_side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP1: {tp1_price} | Conf: {final_confidence:.2f} | BTC_Trend: {self.btc_trend}")

        return EnrichedSignal(
            signal_id=f"AbsorptionV2_{symbol}_{int(now)}",
            symbol=symbol,
            side=signal_side,
            entry_price=entry_price,
            strategy="AbsorptionV2",
            confidence=final_confidence,
            edge_price=event_price, # Используем цену поглощения как край
            rr_ratio=rr_ratio,
            atr=atr,
            volatility_mode="normal",
            basis=0.001
        )