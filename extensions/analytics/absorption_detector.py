"""Absorption Detector — детектирует паттерн поглощения (агрессия без движения цены)."""
import logging
import time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AbsorptionDetector:
    def __init__(
        self,
        event_bus: Any,
        symbol: str,
        delta_analyzer: Any,
        imbalance_calculator: Any,
        velocity_threshold: float = 5000.0,      # Мин. дельта за 3 сек для "всплеска"
        stagnation_pct: float = 0.0005,          # Макс. движение цены (0.05%)
        imbalance_threshold: float = 0.3,        # Мин. имбаланс в сторону стены
        cooldown_sec: float = 30.0
    ):
        self.event_bus = event_bus
        self.symbol = symbol
        self.delta_analyzer = delta_analyzer
        self.imbalance_calculator = imbalance_calculator
        
        self.velocity_threshold = velocity_threshold
        self.stagnation_pct = stagnation_pct
        self.imbalance_threshold = imbalance_threshold
        self.cooldown_sec = cooldown_sec
        
        self._price_history = deque(maxlen=100)  # (ts, price)
        self._last_event_time = 0.0
        
        # Подписка на обновления спотовой цены
        self.event_bus.subscribe("SPOT_PRICE_UPDATE", self._on_price_update)
        
        # Подписка на обновления стакана (для проверки имбаланса)
        self.event_bus.subscribe("SPOT_ORDERBOOK_UPDATE", self._on_orderbook_update)
        
        logger.info(f"✅ AbsorptionDetector initialized for {symbol}")
        print("🚨 [ПРЯМОЙ PRINT] AbsorptionDetector класс создан и подписан на события!")
        
    async def _on_price_update(self, event: Any):
        """Записывает цену и проверяет условия поглощения."""
        try:
            payload = getattr(event, "payload", {})
            price = float(payload.get("price", 0.0))
            ts = float(payload.get("ts", time.time()))
            
            if price <= 0:
                return
            
            self._price_history.append((ts, price))
            
            # Проверяем условия поглощения каждые 0.5 секунды
            if len(self._price_history) >= 2:
                await self._check_absorption(ts, price)
                
        except Exception as e:
            logger.error(f"Error in AbsorptionDetector price update: {e}")

    async def _on_orderbook_update(self, event: Any):
        """Заглушка для будущего расширения (анализ стакана)."""
        pass

    async def _check_absorption(self, current_ts: float, current_price: float):
        """Проверяет выполнение условий поглощения."""
        # Cooldown
        if current_ts - self._last_event_time < self.cooldown_sec:
            return
        
        # 1. Получаем velocity дельты
        delta_metrics = self.delta_analyzer.get_metrics()
        delta_velocity = delta_metrics.get("delta_velocity", 0.0)
        
        # 2. Проверяем всплеск агрессии
        if abs(delta_velocity) < self.velocity_threshold:
            return
        
        # 3. Проверяем стагнацию цены (движение за последние 3 сек)
        cutoff_ts = current_ts - 3.0
        old_price = None
        for ts, p in self._price_history:
            if ts >= cutoff_ts:
                old_price = p
                break
        
        if old_price is None or old_price == 0:
            return
        
        price_movement_pct = abs(current_price - old_price) / old_price
        
        if price_movement_pct > self.stagnation_pct:
            # Цена двигается слишком сильно — это не поглощение, а тренд
            return
        
        # 4. Проверяем имбаланс (подтверждение стены)
        imbalance_metrics = self.imbalance_calculator.get_metrics()
        imbalance = imbalance_metrics.get("imbalance", 0.0)
        
        # Определяем направление поглощения по знаку дельты
        # delta_velocity > 0 → агрессивные покупатели → поглощение на ASK (медвежье)
        # delta_velocity < 0 → агрессивные продавцы → поглощение на BID (бычье)
        if delta_velocity > 0:
            # Покупатели бьют в ask-стену → медвежий сигнал (short)
            absorption_side = "BEARISH"  # Шорт
            required_imbalance = self.imbalance_threshold  # Нужен перекос в ask
            if imbalance > -required_imbalance:
                return  # Имбаланс не подтверждает ask-стену
        else:
            # Продавцы бьют в bid-стену → бычий сигнал (long)
            absorption_side = "BULLISH"  # Лонг
            required_imbalance = self.imbalance_threshold  # Нужен перекос в bid
            if imbalance < required_imbalance:
                return  # Имбаланс не подтверждает bid-стену
        
        # ✅ ВСЕ УСЛОВИЯ ВЫПОЛНЕНЫ — публикуем событие
        self._last_event_time = current_ts
        
        logger.info(
            f"🎯 [ABSORPTION] {absorption_side} | "
            f"Velocity: {delta_velocity:.0f} | "
            f"Price Movement: {price_movement_pct*100:.3f}% | "
            f"Imbalance: {imbalance:.2f}"
        )
        
        await self.event_bus.publish(
            event_type="ABSORPTION_DETECTED",
            source="absorption_detector",
            payload={
                "side": absorption_side,
                "delta_velocity": delta_velocity,
                "price_movement_pct": price_movement_pct,
                "imbalance": imbalance,
                "price": current_price,
                "ts": current_ts
            },
            symbol=self.symbol
        )