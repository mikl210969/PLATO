"""Absorption Strategy V2 — торгует на отскок после поглощения агрессии о стену + HVN."""
import time
import logging
from typing import Optional, Dict, Any

from strategies.wall_fade_v3 import EnrichedSignal
from strategies.adaptive_strategy import AdaptiveStrategy  # 🔥 V13 ADAPTIVE: Импорт базового класса

logger = logging.getLogger(__name__)


class AbsorptionStrategyV2(AdaptiveStrategy):  # 🔥 V13 ADAPTIVE: Наследование
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5, context_manager=None):  # 🔥 V13 ADAPTIVE: Добавлен context_manager
        super().__init__(context_manager)  # 🔥 V13 ADAPTIVE: Инициализация базового класса
        
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 60.0)
        
        # 🔥 Тестовый режим
        self.force_test_signal = config.get('force_test_signal', False)
        self.test_signal_interval = config.get('test_signal_interval', 60)
        self.fixed_lot_size = config.get('fixed_lot_size', 7.0)
        self.fixed_sl_distance = config.get('fixed_sl_distance', 0.25)
        self.fixed_tp1_distance = config.get('fixed_tp1_distance', 0.25)
        self.fixed_tp2_distance = config.get('fixed_tp2_distance', 0.50)
        
        # 🔥 V13 ADAPTIVE: Fallback параметры (используются, если context_manager еще не готов)
        self._fallback_params = {
            "min_confidence": config.get("min_confidence", 0.5),
            "bypass_filters": config.get("bypass_filters", False),
            "bypass_btc_filter": config.get("bypass_btc_filter", False),
            "bypass_confidence_threshold": config.get("bypass_confidence_threshold", False),
            "bypass_hvn_filter": config.get("bypass_hvn_filter", False),
        }

        # 🔥 ТУМБЛЕР BTC-КОНТЕКСТА (Жесткое требование)
        btc_cfg = config.get('btc_context', {})
        self.btc_enabled = btc_cfg.get('enabled', False)
        self.btc_penalty = btc_cfg.get('penalty_multiplier', 0.5)

        print(f"🔥 [DEBUG INIT] AbsorptionV2: force_test_signal={self.force_test_signal}, btc_enabled={self.btc_enabled}")
        self._last_test_signal_time = 0.0

    def subscribe_to_events(self, event_bus):
        """Заглушка для совместимости. Новая стратегия читает состояние из context['features']."""
        pass

    async def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:  # 🔥 V13 ADAPTIVE: Добавлен async
        now = time.time()
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        
        # ========================================================================
        # 1. ТЕСТОВЫЙ РЕЖИМ
        # ========================================================================
        if self.force_test_signal and (now - self._last_test_signal_time >= self.test_signal_interval):
            self._last_test_signal_time = now
            side = 'short'
            
            if side == 'short':
                sl_price = round(current_price + self.fixed_sl_distance, 2)
                tp1_price = round(current_price - self.fixed_tp1_distance, 2)
                tp2_price = round(current_price - self.fixed_tp2_distance, 2)
            else:
                sl_price = round(current_price - self.fixed_sl_distance, 2)
                tp1_price = round(current_price + self.fixed_tp1_distance, 2)
                tp2_price = round(current_price + self.fixed_tp2_distance, 2)

            logger.info(f"✅ [{self.__class__.__name__}] ТЕСТОВЫЙ СИГНАЛ | Side: {side}, Price: {current_price}")
            return EnrichedSignal(
                signal_id=f"{self.__class__.__name__}_TEST_{int(now)}",
                symbol=symbol, side=side, entry_price=current_price, strategy=self.__class__.__name__,
                confidence=0.99, edge_price=current_price, rr_ratio=2.0, atr=self.atr_value,
                volatility_mode="normal", basis=0.0, order_type="limit",
                execution_params={
                    "quantity": self.fixed_lot_size,
                    "sl_price": sl_price, "tp1_price": tp1_price, "tp2_price": tp2_price
                }
            )

        # ========================================================================
        # 2. ПРОВЕРКА СВЕЖЕСТИ ДАННЫХ
        # ========================================================================
        features = context.get('features')
        if not features or not features.is_fresh(now):
            return None

        # 🔥 V13 ADAPTIVE: Получаем адаптивные параметры из VolumeContextManager
        params = await self.get_params()
        if not params:
            params = self._fallback_params

        snap = features.snapshot(now)
        atr = self.atr_value if self.atr_value > 0 else 0.15
        
        delta_v3 = snap['delta']['windows'][3]['velocity']
        imbalance = snap['imbalance']['imbalance']
        walls_bid = snap['walls'].get('walls_bid', [])
        walls_ask = snap['walls'].get('walls_ask', [])
        hvn_above = snap['volume_profile'].get('nearest_hvn_above')
        hvn_below = snap['volume_profile'].get('nearest_hvn_below')

        # ========================================================================
        # 3. ПОИСК УСЛОВИЙ ПОГЛОЩЕНИЯ
        # ========================================================================
        best_signal = None
        best_confidence = 0.0

        # --- ПРОВЕРКА НА LONG ---
        for wall in walls_bid:
            if wall.get('confidence', 0) < params.get('min_confidence', 0.5):  # 🔥 V13 ADAPTIVE
                continue
            
            if delta_v3 > -20.0:
                continue
            
            if imbalance < 0.1:
                continue

            conf = 0.6
            sl_anchor = wall['price']

            if not params.get('bypass_hvn_filter', False) and hvn_below:  # 🔥 V13 ADAPTIVE
                dist_to_hvn = abs(wall['price'] - hvn_below['price'])
                if dist_to_hvn <= (atr * 1.5):
                    conf += 0.2
                    sl_anchor = hvn_below['price_low']
                    logger.debug(f"🎯 [Absorption] HVN Confluence LONG: Wall {wall['price']:.2f} near HVN {hvn_below['price']:.2f}")

            if self.btc_enabled and not params.get('bypass_btc_filter', False):  # 🔥 V13 ADAPTIVE
                btc_trend = context.get('btc_delta_context', {}).get('trend', 'FLAT')
                if btc_trend == 'DOWN':
                    conf *= self.btc_penalty
                    logger.warning(f"⚠️ [Absorption] Штраф к confidence: LONG при DOWN тренде BTC")

            if conf > best_confidence:
                best_confidence = conf
                best_signal = {'side': 'long', 'sl_anchor': sl_anchor, 'edge_price': wall['price']}

        # --- ПРОВЕРКА НА SHORT ---
        for wall in walls_ask:
            if wall.get('confidence', 0) < params.get('min_confidence', 0.5):  # 🔥 V13 ADAPTIVE
                continue
            
            if delta_v3 < 20.0:
                continue
            
            if imbalance > -0.1:
                continue

            conf = 0.6
            sl_anchor = wall['price']

            if not params.get('bypass_hvn_filter', False) and hvn_above:  # 🔥 V13 ADAPTIVE
                dist_to_hvn = abs(wall['price'] - hvn_above['price'])
                if dist_to_hvn <= (atr * 1.5):
                    conf += 0.2
                    sl_anchor = hvn_above['price_high']
                    logger.debug(f"🎯 [Absorption] HVN Confluence SHORT: Wall {wall['price']:.2f} near HVN {hvn_above['price']:.2f}")

            if self.btc_enabled and not params.get('bypass_btc_filter', False):  # 🔥 V13 ADAPTIVE
                btc_trend = context.get('btc_delta_context', {}).get('trend', 'FLAT')
                if btc_trend == 'UP':
                    conf *= self.btc_penalty
                    logger.warning(f"⚠️ [Absorption] Штраф к confidence: SHORT при UP тренде BTC")

            if conf > best_confidence:
                best_confidence = conf
                best_signal = {'side': 'short', 'sl_anchor': sl_anchor, 'edge_price': wall['price']}

        # ========================================================================
        # 4. ФИНАЛЬНАЯ ВАЛИДАЦИЯ И РАСЧЕТ УРОВНЕЙ
        # ========================================================================
        if not best_signal:
            return None

        if not params.get('bypass_confidence_threshold', False) and best_confidence < 0.5:  # 🔥 V13 ADAPTIVE
            logger.info(f"🚫 [Absorption] Сигнал отклонен: итоговый confidence {best_confidence:.2f} < 0.5")
            return None

        side = best_signal['side']
        entry_price = round(current_price, 2)
        sl_anchor = best_signal['sl_anchor']
        
        if side == 'long':
            sl_price = round(sl_anchor - (atr * 0.2), 2)
        else:
            sl_price = round(sl_anchor + (atr * 0.2), 2)
            
        r_value = abs(entry_price - sl_price)
        if r_value == 0:
            r_value = atr
            
        tp1_price = round(entry_price + (2.0 * r_value) if side == 'long' else entry_price - (2.0 * r_value), 2)
        tp2_price = round(entry_price + (4.0 * r_value) if side == 'long' else entry_price - (4.0 * r_value), 2)
        
        self._last_signal_time = now
        
        logger.info(f"🚀 [AbsorptionV2] SIGNAL: {side.upper()} | Entry: {entry_price} | SL: {sl_price} (Anchor: {sl_anchor:.2f}) | TP1: {tp1_price} | Conf: {best_confidence:.2f}")

        return EnrichedSignal(
            signal_id=f"AbsorptionV2_{symbol}_{int(now)}",
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            strategy="AbsorptionV2",
            confidence=best_confidence,
            edge_price=best_signal['edge_price'],
            rr_ratio=2.0,
            atr=atr,
            volatility_mode="normal",
            basis=0.0,
            order_type="limit",
            execution_params={
                "quantity": self.fixed_lot_size,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price
            }
        )