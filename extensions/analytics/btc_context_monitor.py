"""
BtcContextMonitor — анализирует поток данных BTCUSDT и публикует контекст рынка.
Используется стратегиями на альткоинах для фильтрации и адаптации.
"""
import asyncio
import time
from collections import deque
from typing import Dict, Any, Optional
from core.logger import get_logger


class BtcContextMonitor:
    def __init__(self, event_bus, window_seconds: int = 300, publish_interval: float = 5.0):
        self.bus = event_bus
        self.window_seconds = window_seconds
        self.publish_interval = publish_interval
        self.logger = get_logger(__name__)
        
        # Состояние
        self._prices = deque(maxlen=1000)
        self._cumulative_delta = 0.0
        self._last_publish_time = 0.0
        self._is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._is_running = True
        self._last_publish_time = time.time()
        
        self.bus.subscribe("BTC_AGG_TRADE", self._on_btc_trade)
        self.bus.subscribe("BTC_DEPTH_UPDATE", self._on_btc_depth)
        
        self._task = asyncio.create_task(self._publish_loop())
        self.logger.info("✅ BtcContextMonitor started")

    async def stop(self):
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.logger.info("🛑 BtcContextMonitor stopped")

    async def _on_btc_trade(self, event):
        try:
            # Извлекаем словарь из объекта Event
            data = event.payload if hasattr(event, 'payload') else event
            if not isinstance(data, dict):
                return
                
            price = float(data.get('p', 0))
            qty = float(data.get('q', 0))
            is_maker_buyer = data.get('m', False)
            timestamp = data.get('T', time.time() * 1000) / 1000.0
            
            # Агрессивная покупка (taker buy, m=False) = положительная дельта
            # Агрессивная продажа (taker sell, m=True) = отрицательная дельта
            delta = qty if not is_maker_buyer else -qty
            self._cumulative_delta += delta
            self._prices.append((timestamp, price))
            
        except Exception as e:
            self.logger.error(f"Error processing BTC trade: {e}")

    async def _on_btc_depth(self, event):
        # Пока просто заглушка, но с правильной обработкой типа Event
        pass

    async def _publish_loop(self):
        while self._is_running:
            try:
                await asyncio.sleep(self.publish_interval)
                await self._calculate_and_publish()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in BTC publish loop: {e}")

    async def _calculate_and_publish(self):
        current_time = time.time()
        cutoff_time = current_time - self.window_seconds
        
        while self._prices and self._prices[0][0] < cutoff_time:
            self._prices.popleft()
            
        trend = "FLAT"
        if len(self._prices) >= 10:
            first_price = self._prices[0][1]
            last_price = self._prices[-1][1]
            pct_change = ((last_price - first_price) / first_price) * 100
            
            if pct_change > 0.15:
                trend = "UP"
            elif pct_change < -0.15:
                trend = "DOWN"
                
        # Показываем чистую дельту 5-секундного окна с точностью до 3 знаков
        net_delta = self._cumulative_delta
        self._cumulative_delta = 0.0  # Сбрасываем для следующего окна

        context = {
            "trend": trend,
            "delta_strength": round(net_delta, 3),
            "current_price": round(self._prices[-1][1], 2) if self._prices else 0.0,
            "timestamp": current_time
        }
        
        # Аккуратный лог в консоль и файл
        self.logger.info(
            f"BTC_CTX | Trend: {trend:<5} | Delta: {round(net_delta, 3):>7} | Price: {context['current_price']}"
        )
        
        await self.bus.publish(
            event_type="BTC_CONTEXT_UPDATED",
            source="btc_monitor",
            payload=context,
            symbol="BTCUSDT"
        )
        
        self._last_publish_time = current_time