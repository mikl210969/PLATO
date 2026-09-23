"""WallFade Strategy V3 — торгует на отскок (фейд) от зрелых лимитных стен."""
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

from strategies.adaptive_strategy import AdaptiveStrategy

logger = logging.getLogger(__name__)


@dataclass
class EnrichedSignal:
    """Унифицированная структура сигнала для всех стратегий v2."""
    signal_id: str
    symbol: str
    side: str
    entry_price: float
    strategy: str
    confidence: float
    edge_price: float
    rr_ratio: float
    atr: float
    volatility_mode: str
    basis: float
    order_type: str
    execution_params: Dict[str, Any]


class WallFadeStrategyV3(AdaptiveStrategy):
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5, context_manager=None):
        super().__init__(context_manager)
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        
        # 🔥 Fallback параметры
        self._fallback_params = {
            "cooldown_sec": config.get('cooldown_sec', 30.0),
            "force_test_signal": config.get('force_test_signal', False),
            "test_signal_interval": config.get('test_signal_interval', 60),
            "fixed_lot_size": config.get('fixed_lot_size', 7.0),
            "fixed_sl_distance": config.get('fixed_sl_distance', 0.25),
            "fixed_tp1_distance": config.get('fixed_tp1_distance', 0.25),
            "fixed_tp2_distance": config.get('fixed_tp2_distance', 0.50),
            "bypass_filters": config.get('bypass_filters', False),
            "min_confidence": config.get('min_confidence', 0.6),
            "price_distance_pct": config.get('price_distance_pct', 0.5) / 100.0,
        }
        
        print(f"🔥 [DEBUG INIT] WallFadeV3: force_test_signal={self._fallback_params['force_test_signal']}, min_conf={self._fallback_params['min_confidence']}")
        self._last_test_signal_time = 0.0

    def subscribe_to_events(self, event_bus):
        pass

    async def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        now = time.time()
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        
        # 🔥 Получаем адаптивные параметры
        params = await self.get_params()
        if not params:
            params = self._fallback_params

        # ========================================================================
        # 1. ТЕСТОВЫЙ РЕЖИМ
        # ========================================================================
        if params.get('force_test_signal') and (now - self._last_test_signal_time >= params.get('test_signal_interval', 60)):
            self._last_test_signal_time = now
            side = 'short'
            
            if side == 'short':
                sl_price = round(current_price + params['fixed_sl_distance'], 2)
                tp1_price = round(current_price - params['fixed_tp1_distance'], 2)
                tp2_price = round(current_price - params['fixed_tp2_distance'], 2)
            else:
                sl_price = round(current_price - params['fixed_sl_distance'], 2)
                tp1_price = round(current_price + params['fixed_tp1_distance'], 2)
                tp2_price = round(current_price + params['fixed_tp2_distance'], 2)

            logger.info(f"✅ [{self.__class__.__name__}] ТЕСТОВЫЙ СИГНАЛ | Side: {side}, Price: {current_price}")
            return EnrichedSignal(
                signal_id=f"{self.__class__.__name__}_TEST_{int(now)}",
                symbol=symbol, side=side, entry_price=current_price, strategy=self.__class__.__name__,
                confidence=0.99, edge_price=current_price, rr_ratio=2.0, atr=self.atr_value,
                volatility_mode="normal", basis=0.0, order_type="limit",
                execution_params={
                    "quantity": params['fixed_lot_size'],
                    "sl_price": sl_price, "tp1_price": tp1_price, "tp2_price": tp2_price
                }
            )

        # ========================================================================
        # 2. ПРОВЕРКА СВЕЖЕСТИ ДАННЫХ
        # ========================================================================
        features = context.get('features')
        if not features or not features.is_fresh(now):
            return None

        snap = features.snapshot(now)
        atr = self.atr_value if self.atr_value > 0 else 0.15
        
        walls_bid = snap['walls'].get('walls_bid', [])
        walls_ask = snap['walls'].get('walls_ask', [])
        imbalance = snap['imbalance']['imbalance']
        
        market_regime = context.get('market_regime', 'NORMAL')
        if not params.get('bypass_filters', False) and market_regime == 'IMPULSIVE':
            return None

        # ========================================================================
        # 3. ПОИСК УСЛОВИЙ ДЛЯ ФЕЙДА
        # ========================================================================
        best_signal = None
        best_confidence = 0.0
        min_conf = params.get('min_confidence', 0.6)
        price_dist = params.get('price_distance_pct', 0.005)

        for wall in walls_bid:
            if wall.get('confidence', 0) < min_conf:
                continue
            dist_pct = (current_price - wall['price']) / current_price
            if dist_pct < 0 or dist_pct > price_dist:
                continue
            if imbalance < 0.1:
                continue
            conf = wall['confidence']
            if conf > best_confidence:
                best_confidence = conf
                best_signal = {'side': 'long', 'sl_anchor': wall['price'], 'edge_price': wall['price']}

        for wall in walls_ask:
            if wall.get('confidence', 0) < min_conf:
                continue
            dist_pct = (wall['price'] - current_price) / current_price
            if dist_pct < 0 or dist_pct > price_dist:
                continue
            if imbalance > -0.1:
                continue
            conf = wall['confidence']
            if conf > best_confidence:
                best_confidence = conf
                best_signal = {'side': 'short', 'sl_anchor': wall['price'], 'edge_price': wall['price']}

        # ========================================================================
        # 4. ФИНАЛЬНАЯ ВАЛИДАЦИЯ
        # ========================================================================
        if not best_signal:
            return None

        side = best_signal['side']
        entry_price = round(current_price, 2)
        sl_anchor = best_signal['sl_anchor']
        
        atr_buffer = atr * 0.3
        if side == 'long':
            sl_price = round(sl_anchor - atr_buffer, 2)
        else:
            sl_price = round(sl_anchor + atr_buffer, 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0:
            r_value = atr 
            
        rr = 1.5
        tp1_price = round(entry_price + (rr * r_value) if side == 'long' else entry_price - (rr * r_value), 2)
        tp2_price = round(entry_price + (rr * 2.0 * r_value) if side == 'long' else entry_price - (rr * 2.0 * r_value), 2)
        
        cooldown = params.get('cooldown_sec', 30)
        if now - self._last_signal_time < cooldown:
            return None
        self._last_signal_time = now
        
        logger.info(f"🚀 [WallFadeV3] SIGNAL: {side.upper()} | Entry: {entry_price} | SL: {sl_price} (Wall: {sl_anchor:.2f}) | TP1: {tp1_price} | Conf: {best_confidence:.2f}")

        return EnrichedSignal(
            signal_id=f"WallFadeV3_{symbol}_{int(now)}",
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            strategy="WallFadeV3",
            confidence=best_confidence,
            edge_price=best_signal['edge_price'],
            rr_ratio=rr,
            atr=atr,
            volatility_mode="normal",
            basis=0.0,
            order_type="limit",
            execution_params={
                "quantity": params['fixed_lot_size'],
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price
            }
        )