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
        
        # 🔥 НОВОЕ: Хранилище последней дивергенции
        self._last_divergence = None  # {"type": "BULLISH"/"BEARISH", "timestamp": ..., "price": ...}
        self._divergence_valid_for_sec = 900.0  # 15 минут актуальности
        
        # 🔥 Фоллбэк: Состояние тренда BTC (если контекст из main.py еще не пришел)
        self.btc_trend = "FLAT"        
        self._event_bus = None

    def subscribe_to_events(self, event_bus):
        """Подписка на события детектора поглощения."""
        self._event_bus = event_bus
        self._event_bus.subscribe("ABSORPTION_DETECTED", self._on_absorption_event)
        
        # Подписка на BTC контекст (как фоллбэк, основной источник теперь - словарь context)
        self._event_bus.subscribe("BTC_CONTEXT_UPDATED", self._on_btc_context_updated)
        
        # 🔥 НОВОЕ: Подписка на дивергенции
        self._event_bus.subscribe("DIVERGENCE_DETECTED", self._on_divergence_detected)
        
        logger.info("✅ AbsorptionStrategyV2 subscribed to ABSORPTION_DETECTED, BTC_CONTEXT_UPDATED & DIVERGENCE_DETECTED")

    async def _on_btc_context_updated(self, event):
        """Обновляет локальное состояние тренда BTC при поступлении события (фоллбэк)."""
        self.btc_trend = event.payload.get("trend", "FLAT")

    async def _on_divergence_detected(self, event):
        """🔥 НОВОЕ: Сохраняем информацию о дивергенции."""
        payload = getattr(event, "payload", {})
        self._last_divergence = {
            "type": payload.get("type"),  # "BULLISH" или "BEARISH"
            "price": payload.get("price", 0.0),
            "timestamp": time.time()
        }
        logger.info(f"🚨 [AbsorptionV2] Запомнена дивергенция: {self._last_divergence['type']} @ {self._last_divergence['price']:.2f}")

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
        if absorption_side == "BULLISH":
            signal_side = "long"
            logger.info(f"🟢 [AbsorptionStrat] BULLISH Absorption detected! Preparing LONG signal.")
        elif absorption_side == "BEARISH":
            signal_side = "short"
            logger.info(f"🔴 [AbsorptionStrat] BEARISH Absorption detected! Preparing SHORT signal.")
        else:
            return None

        # ========================================================================
        # 🔥 УРОВЕНЬ 4: Динамическая корректировка Confidence (Delta Context)
        # Используем данные, переданные из main.py, с фоллбэком на self.btc_trend
        # ========================================================================
        btc_context = context.get('btc_delta_context', {})
        sol_context = context.get('sol_delta_context', {})
        
        # Берем тренд из контекста, если его там нет (первый запуск), берем из фоллбэка
        btc_trend = btc_context.get('trend', self.btc_trend)
        sol_delta = sol_context.get('delta_strength', 0.0)
        
        # Базовая уверенность за сам факт поглощения
        base_confidence = 0.65 
        
        # 1. Оценка влияния макротренда BTC
        if signal_side == 'long' and btc_trend == 'DOWN':
            base_confidence *= 0.5  # Режем уверенность вдвое
            logger.warning(f"⚠️ [AbsorptionV2] Штраф к confidence: попытка LONG при DOWN тренде BTC")
        elif signal_side == 'short' and btc_trend == 'UP':
            base_confidence *= 0.5
            logger.warning(f"⚠️ [AbsorptionV2] Штраф к confidence: попытка SHORT при UP тренде BTC")
            
        # 2. Оценка влияния дельты самого SOL (дополнительный фильтр)
        # Если мы хотим лонг, а дельта SOL резко отрицательная (продавцы агрессивно давят)
        if signal_side == 'long' and sol_delta < -30.0:
            base_confidence *= 0.7
            logger.warning(f"⚠️ [AbsorptionV2] Штраф к confidence: отрицательная дельта SOL ({sol_delta}) при LONG")
            
        # Если мы хотим шорт, а дельта SOL резко положительная (покупатели агрессивно давят)
        elif signal_side == 'short' and sol_delta > 30.0:
            base_confidence *= 0.7
            logger.warning(f"⚠️ [AbsorptionV2] Штраф к confidence: положительная дельта SOL ({sol_delta}) при SHORT")

        # 3. Бонусы за силу самого события поглощения
        if abs(event["delta_velocity"]) > 10000.0:
            base_confidence += 0.10
            
        if abs(event["imbalance"]) > 0.4:
            base_confidence += 0.10

        # ========================================================================
        # 🔥 УРОВЕНЬ 3: Бонус за подтверждение дивергенцией
        # ========================================================================
        if self._last_divergence:
            div_age = now - self._last_divergence["timestamp"]
            if div_age <= self._divergence_valid_for_sec:
                div_type = self._last_divergence["type"]
                
                # Бычья дивергенция + LONG сигнал = бонус
                if div_type == "BULLISH" and signal_side == "long":
                    base_confidence += 0.20
                    logger.info(f"🚨 [DIVERGENCE CONFIRMED] Сигнал LONG подтвержден бычьей дивергенцией! +0.20 к confidence")
                
                # Медвежья дивергенция + SHORT сигнал = бонус
                elif div_type == "BEARISH" and signal_side == "short":
                    base_confidence += 0.20
                    logger.info(f"🚨 [DIVERGENCE CONFIRMED] Сигнал SHORT подтвержден медвежьей дивергенцией! +0.20 к confidence")
            else:
                self._last_divergence = None  # Сбрасываем устаревшую
        # ========================================================================
            
        # Ограничиваем максимум 1.0
        final_confidence = min(base_confidence, 1.0)
        
        # 🔥 ЖЕСТКИЙ ПОРОГ: Если после всех штрафов уверенность слишком низкая, отменяем сделку
        if final_confidence < 0.50:
            logger.info(f"🚫 [AbsorptionV2] Сигнал ОТКЛОНЕН: итоговый confidence {final_confidence:.2f} ниже порога 0.50 (BTC: {btc_trend}, SOL Delta: {sol_delta})")
            return None
        # ========================================================================

        # ========================================================================
        # РАСЧЕТ УРОВНЕЙ (Entry, SL, TP) на основе ATR
        # ========================================================================
        entry_price = round(current_price, 2)
        
        # Для поглощения стоп ставим чуть за уровень, где происходило поглощение, с буфером ATR
        if signal_side == "long":
            sl_price = round(event_price - (atr * 0.3), 2)
        else: # short
            sl_price = round(event_price + (atr * 0.3), 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0:
            r_value = atr # Защита от деления на ноль
            
        tp1_price = round(entry_price + (2.0 * r_value) if signal_side == "long" else entry_price - (2.0 * r_value), 2)
        rr_ratio = 2.0 # Мы жестко целимся в R:R 2.0

        self._last_signal_time = now
        
        logger.info(f"🚀 [AbsorptionStrat] SIGNAL CONFIRMED: {signal_side.upper()} | Entry: {entry_price} | SL: {sl_price} | TP1: {tp1_price} | Conf: {final_confidence:.2f} | BTC: {btc_trend} | SOL_Delta: {sol_delta}")

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