"""Advanced Risk Service — теневой режим (Финальная чистая версия)."""
import logging
from typing import Any

from extensions.risk.grade_calculator import calculate_position_grade
from extensions.risk.stop_levels import build_risk_plan

logger = logging.getLogger(__name__)


class AdvancedRiskService:
    def __init__(self):
        pass

    async def on_signal(self, event: Any):
        """Теневой обработчик SIGNAL_GENERATED."""
        try:
            payload = getattr(event, "payload", {}) or {}
            signal = payload.get("signal")
            
            if signal is None:
                return None

            symbol = getattr(signal, "symbol", "UNKNOWN")
            side = getattr(signal, "side", "UNKNOWN")
            entry = getattr(signal, "entry_price", 0.0)
            edge = getattr(signal, "edge_price", 0.0)
            rr = getattr(signal, "rr_ratio", 0.0)
            conf = getattr(signal, "confidence", 0.0)
            confidence = conf * 100 if conf <= 1.0 else conf

            # Берем данные напрямую из обогащенного сигнала
            atr = getattr(signal, "atr", 0.0)
            volatility_mode = getattr(signal, "volatility_mode", "normal")
            basis = getattr(signal, "basis", 0.0)

            if atr <= 0:
                logger.warning(f"[SHADOW] {symbol}: ATR равен 0 — пропуск")
                return None

            if entry == 0.0 or edge == 0.0 or rr == 0.0:
                logger.info(f"[SHADOW] {symbol}: в сигнале нет edge_price/rr_ratio (0.0) — пропуск")
                return None

            return self.evaluate(symbol, side, entry, edge, rr, confidence, atr, volatility_mode, basis)
            
        except Exception as e:
            logger.error(f"[SHADOW ERROR] Критическая ошибка в on_signal: {e}", exc_info=True)
            return None

    def evaluate(self, symbol: str, side: str, entry_price: float, edge_price: float,
                    rr_ratio: float, confidence: float, atr: float, volatility_mode: str, 
                    basis: float) -> dict:
        print(f"👁️ [SHADOW EVAL] Считаем Grade для {symbol} {side} | atr={atr}, rr={rr_ratio}") # <-- ДОБАВИТЬ ЭТО
        grade, size_mult = calculate_position_grade(rr_ratio, confidence)

        """Оценка сигнала и расчет уровней."""
        grade, size_mult = calculate_position_grade(rr_ratio, confidence)

        if grade == "REJECT":
            logger.warning(f"[SHADOW] {symbol} {side} | REJECT | rr={rr_ratio:.2f} conf={confidence:.0f}%")
            return {"action": "REJECT", "grade": grade, "size_multiplier": 0.0}

        plan = build_risk_plan(entry_price, edge_price, atr, volatility_mode, side, basis)
        decision = {
            "action": "SHADOW_APPROVE", 
            "grade": grade,
            "size_multiplier": size_mult, 
            **plan
        }

        logger.info(
            f"[SHADOW] {symbol} {side} | Grade {grade} ({size_mult*100:.0f}%) | "
            f"SL1={plan['sl1']:.2f} SL2={plan['sl2']:.2f} | "
            f"BE={plan['be']:.2f} EMG={plan['emergency_sl']:.2f} | R={plan['r']:.2f}"
        )
        return decision