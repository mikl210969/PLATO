"""WallFade Strategy v3 — Обогащенная генерация сигналов."""
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class EnrichedSignal:
    """Сигнал с полными данными для Advanced Risk (SL_TP.txt v2.2)."""
    signal_id: str
    symbol: str
    side: str
    entry_price: float
    strategy: str
    confidence: float
    
    # Новые поля для Advanced Risk
    edge_price: float
    rr_ratio: float
    atr: float
    volatility_mode: str
    basis: float


class WallFadeStrategyV3:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value  # Пока используем фиксированное значение из конфига
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 30.0)

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        orderbook = context.get('orderbook', {'bids': [], 'asks': []})
        
        if current_price <= 0 or not orderbook.get('bids') or not orderbook.get('asks'):
            return None

        now = time.time()
        if now - self._last_signal_time < self.cooldown_sec:
            return None

        # 1. Поиск края стены (Edge Price)
        bids = orderbook.get('bids', [])
        if len(bids) >= 5:
            avg_vol = sum(float(q) for p, q in bids[:10]) / min(10, len(bids))
            edge_price = current_price
            
            # Снизил порог до 2.0x для тестнета, где объемы в стакане меньше
            for p, q in bids:
                if float(q) > avg_vol * 2.0:
                    edge_price = float(p)
                    break
            
            # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Округляем цены до 2 знаков (precision SOLUSDT)
            entry_price = round(current_price, 2)
            edge_price = round(edge_price, 2)
            
            # Если стена найдена близко к цене (в пределах 0.5%)
            if abs(entry_price - edge_price) / entry_price < 0.005:
                # 2. Расчет R и RR
                atr = self.atr_value
                sl1 = edge_price - (atr * 0.3)
                r_value = abs(entry_price - sl1)
                
                tp1 = entry_price + (2.0 * r_value)
                rr_ratio = (tp1 - entry_price) / r_value if r_value > 0 else 0.0

                # 3. Расчет Confidence Score
                confidence = 0.50  # База
                confidence += 0.25  # +25% за реальную стену
                if rr_ratio >= 2.0:
                    confidence += 0.10  # +10% за хороший R:R
                
                confidence = min(confidence, 1.0)
                self._last_signal_time = now
                
                return EnrichedSignal(
                    signal_id=f"WallFadeV3_{symbol}_{int(now)}",
                    symbol=symbol,
                    side="short",
                    entry_price=entry_price,
                    strategy="WallFadeV3",
                    confidence=confidence,
                    edge_price=edge_price,
                    rr_ratio=rr_ratio,
                    atr=atr,
                    volatility_mode="normal",
                    basis=0.001
                )

        return None