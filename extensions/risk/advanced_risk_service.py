"""Advanced Risk Service — теневой режим (Дни 6-7).
Прогоняет сигналы через математику v2.2 (грейды, ступенчатый SL, BE,
аварийный стоп) и логирует решения. НЕ выставляет ордера.
Stable Core не модифицируется."""
import logging
from typing import Callable, Optional

from extensions.analytics.metric_cache import MetricCache
from extensions.risk.grade_calculator import calculate_position_grade
from extensions.risk.stop_levels import build_risk_plan

logger = logging.getLogger(__name__)


class AdvancedRiskService:
    def __init__(self, metric_cache: Optional[MetricCache] = None,
                 fetch_candles: Optional[Callable] = None):
        self.metric_cache = metric_cache
        self.fetch_candles = fetch_candles

    # ------------------------------------------------------------ shadow core
    def evaluate(self, symbol: str, side: str, entry_price: float,
                 edge_price: float, rr_ratio: float, confidence: float,
                 atr: float, volatility_mode: str, basis: float) -> dict:
        grade, size_mult = calculate_position_grade(rr_ratio, confidence)

        if grade == "REJECT":
            logger.warning(f"[SHADOW] {symbol} {side} | REJECT | "
                           f"rr={rr_ratio:.2f} conf={confidence:.0f}")
            return {"action": "REJECT", "grade": grade, "size_multiplier": 0.0}

        plan = build_risk_plan(entry_price, edge_price, atr,
                               volatility_mode, side, basis)
        decision = {"action": "SHADOW_APPROVE", "grade": grade,
                    "size_multiplier": size_mult, **plan}

        logger.info(f"[SHADOW] {symbol} {side} | Grade {grade} ({size_mult*100:.0f}%) | "
                    f"SL1={plan['sl1']:.4f} SL2={plan['sl2']:.4f} | "
                    f"BE={plan['be']:.4f} EMG={plan['emergency_sl']:.4f} | R={plan['r']:.4f}")
        return decision

    # ---------------------------------------- будущая подписка на EventBus
    async def on_signal(self, event):
        """Теневой обработчик SIGNAL_GENERATED. В Фазе 4 подключим к шине."""
        payload = getattr(event, "payload", {}) or {}
        signal = payload.get("signal")
        if signal is None:
            return None

        symbol = getattr(signal, "symbol", None)
        side = getattr(signal, "side", None)
        entry = getattr(signal, "entry_price", None)
        edge = getattr(signal, "edge_price", None)     # появится у стратегий в Фазе 4
        rr = getattr(signal, "rr_ratio", None)
        conf = getattr(signal, "confidence", 0) or 0
        confidence = conf * 100 if conf <= 1.0 else conf

        if None in (symbol, side, entry, edge, rr):
            logger.info(f"[SHADOW] {symbol}: в сигнале нет edge_price/rr_ratio — "
                        f"пропуск (стратегии обогатим полями в Фазе 4)")
            return None

        market = self._get_market(symbol)
        if market["atr"] <= 0:
            logger.warning(f"[SHADOW] {symbol}: нет актуального ATR — пропуск")
            return None

        return self.evaluate(symbol, side, entry, edge, rr, confidence,
                             market["atr"], market["regime"], market["basis"])

    def _get_market(self, symbol: str) -> dict:
        if self.metric_cache is not None and self.fetch_candles is not None:
            data = self.metric_cache.get_or_calculate_atr(symbol, self.fetch_candles)
            return {"atr": data["atr"], "regime": data["regime"], "basis": 0.0}
        # Теневой fallback, пока Basis Monitor не подключён (Фаза 2)
        return {"atr": 0.0, "regime": "normal", "basis": 0.0}