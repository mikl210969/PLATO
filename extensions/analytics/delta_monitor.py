"""
DeltaMonitor — Универсальный монитор рыночной структуры и дивергенций.
Работает на нормализованных событиях (готов к Bybit).
Агрегирует сделки в 5-минутные свечи и ищет дивергенции.
"""
import asyncio
import time
from collections import deque
from typing import Dict, Any, Optional


class DeltaMonitor:
    def __init__(self, symbol: str, event_bus, timeframe_sec: int = 300, publish_interval: float = 5.0):
        self.symbol = symbol
        self.bus = event_bus
        self.timeframe_sec = timeframe_sec
        self.publish_interval = publish_interval
        
        # Состояние текущей формирующейся свечи
        self._current_bar = {
            "open": 0.0, "high": 0.0, "low": 999999.0, "close": 0.0,
            "volume": 0.0, "delta": 0.0, "start_time": 0.0
        }
        
        # История завершенных свечей (храним последние 24 свечи = 2 часа для 5м ТФ)
        self._history: deque = deque(maxlen=24)
        
        self._is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        print(f"▶️  [DeltaMonitor {self.symbol}] Starting...")
        self._is_running = True
        self._current_bar["start_time"] = time.time()
        self.bus.subscribe(f"TRADE_NORMALIZED_{self.symbol}", self._on_trade)
        print(f"📡 [DeltaMonitor {self.symbol}] Subscribed to TRADE_NORMALIZED_{self.symbol}")
        
        self._task = asyncio.create_task(self._publish_loop())
        print(f"✅ [DeltaMonitor {self.symbol}] Started (TF: {self.timeframe_sec}s)")

    async def stop(self):
        self._is_running = False
        if self._task:
            self._task.cancel()
        print(f"🛑 [DeltaMonitor {self.symbol}] Stopped")

    async def _on_trade(self, event):
        """Обработка нормализованной сделки."""
        print(f"👂 [DeltaMonitor {self.symbol}] Received TRADE_NORMALIZED event")
        
        try:
            data = event.payload if hasattr(event, 'payload') else event
            price = float(data.get('price', 0))
            qty = float(data.get('qty', 0))
            is_buyer_maker = bool(data.get('is_buyer_maker', False))
            
            if price <= 0 or qty <= 0:
                return

            # Инициализация первой свечи
            if self._current_bar["open"] == 0.0:
                self._current_bar["open"] = price
                self._current_bar["high"] = price
                self._current_bar["low"] = price
                self._current_bar["start_time"] = time.time()

            # Агрегация в свечу
            self._current_bar["close"] = price
            if price > self._current_bar["high"]: self._current_bar["high"] = price
            if price < self._current_bar["low"]: self._current_bar["low"] = price
            self._current_bar["volume"] += qty
            
            # Расчет дельты: Агрессивная покупка (taker buy) = +, Продажа = -
            delta = qty if not is_buyer_maker else -qty
            self._current_bar["delta"] += delta

        except Exception as e:
            print(f"❌ [DeltaMonitor {self.symbol}] Error in _on_trade: {e}")

    def _check_divergence(self) -> str:
        """
        Поиск дивергенций на истории свечей.
        Возвращает: 'BULLISH', 'BEARISH' или 'NONE'
        """
        if len(self._history) < 6:
            return "NONE"

        recent_bars = list(self._history)[-6:]
        
        min_price_bar = min(recent_bars, key=lambda x: x['low'])
        max_price_bar = max(recent_bars, key=lambda x: x['high'])
        
        min_delta_bar = min(recent_bars, key=lambda x: x['delta'])
        max_delta_bar = max(recent_bars, key=lambda x: x['delta'])

        current_low = self._current_bar["low"]
        current_delta = self._current_bar["delta"]
        
        price_near_low = (current_low - min_price_bar['low']) / min_price_bar['low'] < 0.002
        delta_higher_than_low = current_delta > min_delta_bar['delta'] * 1.5

        if price_near_low and delta_higher_than_low and min_delta_bar['delta'] < 0:
            return "BULLISH"

        current_high = self._current_bar["high"]
        price_near_high = (max_price_bar['high'] - current_high) / max_price_bar['high'] < 0.002
        delta_lower_than_high = current_delta < max_delta_bar['delta'] * 0.5

        if price_near_high and delta_lower_than_high and max_delta_bar['delta'] > 0:
            return "BEARISH"

        return "NONE"

    async def _publish_loop(self):
        last_publish = time.time()
        last_bar_close = time.time()

        while self._is_running:
            try:
                await asyncio.sleep(1.0)
                now = time.time()

                # 1. Закрытие 5-минутной свечи
                if now - last_bar_close >= self.timeframe_sec:
                    if self._current_bar["open"] > 0:
                        self._history.append(dict(self._current_bar))
                        
                        divergence = self._check_divergence()
                        if divergence != "NONE":
                            print(f"🚨 [{self.symbol}] ДИВЕРГЕНЦИЯ: {divergence}")
                            await self.bus.publish(
                                event_type="DIVERGENCE_DETECTED",
                                source="delta_monitor",
                                payload={"symbol": self.symbol, "type": divergence, "price": self._current_bar["close"]},
                                symbol=self.symbol
                            )
                        
                        self._current_bar = {
                            "open": 0.0, "high": 0.0, "low": 999999.0, "close": 0.0,
                            "volume": 0.0, "delta": 0.0, "start_time": now
                        }
                    last_bar_close = now

                # 2. Быстрый контекст (каждые 5 секунд)
                if now - last_publish >= self.publish_interval:
                    trend = "FLAT"
                    if len(self._history) >= 3:
                        closes = [b['close'] for b in list(self._history)[-3:]]
                        if closes[-1] > closes[0] * 1.001: trend = "UP"
                        elif closes[-1] < closes[0] * 0.999: trend = "DOWN"

                    context = {
                        "trend": trend,
                        "delta_strength": round(self._current_bar["delta"], 3),
                        "current_price": round(self._current_bar["close"], 2) if self._current_bar["close"] > 0 else 0.0,
                        "timeframe": f"{self.timeframe_sec}s"
                    }
                    
                    event_type = "BTC_CONTEXT_UPDATED" if self.symbol == "BTCUSDT" else f"CONTEXT_UPDATED_{self.symbol}"
                    
                    await self.bus.publish(
                        event_type=event_type,
                        source="delta_monitor",
                        payload=context,
                        symbol=self.symbol
                    )
                    last_publish = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ [DeltaMonitor {self.symbol}] Error in _publish_loop: {e}")