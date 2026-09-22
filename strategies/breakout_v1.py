"""Breakout Strategy V1 — торгует пробой стены после 70% съедения с подтверждением агрессии."""
import time
import logging
from collections import deque
from typing import Optional, Dict, Any

from strategies.wall_fade_v3 import EnrichedSignal

logger = logging.getLogger(__name__)


class BreakoutStrategyV1:
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5):
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        self.cooldown_sec = config.get('cooldown_sec', 60.0)
        
        # 🔥 Тестовый режим
        self.force_test_signal = config.get('force_test_signal', False)
        self.test_signal_interval = config.get('test_signal_interval', 60)
        self.fixed_lot_size = config.get('fixed_lot_size', 7.0)
        
        # 🔥 Параметры пробоя
        self.min_eaten_pct = config.get('min_eaten_pct', 0.70)  # Ждем 70% съедения
        self.min_wall_age_sec = config.get('min_wall_age_sec', 30)  # Минимальный возраст стены
        self.max_wall_exposure_pct = config.get('max_wall_exposure_pct', 0.15)  # Макс 15% от остатка стены
        self.min_wall_updates = config.get('min_wall_updates', 30)  # Минимум обновлений для зрелости
        self.max_wall_cv = config.get('max_wall_cv', 0.15)  # Максимальный CV для стабильности
        
        # 🔥 Фильтры
        self.bypass_filters = config.get('bypass_filters', False)
        self.min_confidence = config.get('min_confidence', 0.6)
        self.delta_spike_ratio = config.get('delta_spike_ratio', 3.0)  # Порог всплеска дельты
        
        # 🔥 Ring buffers для анализа
        self._delta_history = deque(maxlen=10)  # История дельты за последние 10 секунд
        self._price_history = deque(maxlen=10)  # История цен для расчета скорости
        
        print(f"🔥 [DEBUG INIT] BreakoutV1: force_test_signal={self.force_test_signal}, min_eaten={self.min_eaten_pct}")
        self._last_test_signal_time = 0.0

    def subscribe_to_events(self, event_bus):
        pass

    def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        now = time.time()
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        
        # ========================================================================
        # 1. ТЕСТОВЫЙ РЕЖИМ
        # ========================================================================
        if self.force_test_signal and (now - self._last_test_signal_time >= self.test_signal_interval):
            self._last_test_signal_time = now
            side = 'short'
            sl_price = round(current_price + self.atr_value * 0.5, 2)
            tp_price = round(current_price - (sl_price - current_price) * 2.5, 2)

            logger.info(f"✅ [{self.__class__.__name__}] ТЕСТОВЫЙ СИГНАЛ | Side: {side}, Price: {current_price}")
            return EnrichedSignal(
                signal_id=f"{self.__class__.__name__}_TEST_{int(now)}",
                symbol=symbol, side=side, entry_price=current_price, strategy=self.__class__.__name__,
                confidence=0.99, edge_price=current_price, rr_ratio=2.5, atr=self.atr_value,
                volatility_mode="normal", basis=0.0, order_type="limit",
                execution_params={"quantity": self.fixed_lot_size, "sl_price": sl_price, "tp1_price": tp_price, "tp2_price": tp_price}
            )

        # ========================================================================
        # 2. ПРОВЕРКА СВЕЖЕСТИ ДАННЫХ
        # ========================================================================
        features = context.get('features')
        if not features or not features.is_fresh(now):
            return None

        snap = features.snapshot(now)
        atr = self.atr_value if self.atr_value > 0 else 0.15
        market_regime = context.get('market_regime', 'NORMAL')
        
        # Во флэте не торгуем пробои
        if not self.bypass_filters and market_regime == 'FLAT':
            return None

        # Обновляем ring buffers
        delta_v3 = snap['delta']['windows'][3]['velocity']
        self._delta_history.append(delta_v3)
        self._price_history.append(current_price)

        walls_bid = snap['walls'].get('walls_bid', [])
        walls_ask = snap['walls'].get('walls_ask', [])
        hvn_below = snap['volume_profile'].get('nearest_hvn_below')
        hvn_above = snap['volume_profile'].get('nearest_hvn_above')

        best_signal = None
        best_confidence = 0.0

        # ========================================================================
        # 3. ПРОВЕРКА КАЧЕСТВА АГРЕССИИ (delta_spike_ratio)
        # ========================================================================
        if len(self._delta_history) >= 3:
            delta_median = sorted(self._delta_history)[len(self._delta_history) // 2]
            if abs(delta_median) < 1.0:
                delta_median = 1.0  # Защита от деления на ноль
            delta_spike = abs(delta_v3) / abs(delta_median)
        else:
            delta_spike = 0.0

        # ========================================================================
        # 4. ПОИСК ПРОБОЯ
        # ========================================================================
        
        # --- LONG: Пробой ASK-стены вверх ---
        for wall in walls_ask:
            if wall.get('confidence', 0) < self.min_confidence:
                continue
            
            # Фильтр зрелости и стабильности
            if wall.get('update_count', 0) < self.min_wall_updates:
                continue
            if wall.get('cv', 1.0) > self.max_wall_cv:
                continue
            
            # Проверка съедения
            if wall.get('eaten_pct', 0.0) < self.min_eaten_pct:
                continue
            
            # Проверка агрессии (для LONG дельта должна быть положительной и с всплеском)
            if delta_v3 < 0 or delta_spike < self.delta_spike_ratio:
                continue

            # Ограничение размера позиции
            max_allowed_qty = wall['size'] * self.max_wall_exposure_pct
            qty = min(self.fixed_lot_size, max_allowed_qty)
            if qty < 0.1:
                continue

            # Расчет смещения лимитки (адаптивно)
            tick_size = 0.01  # Для SOLUSDT
            volatility = atr / current_price if current_price > 0 else 0
            offset_ticks = 1 if volatility < 0.001 else 2
            offset = tick_size * offset_ticks
            
            # Вход: лимитка на тик выше стены (для LONG покупаем чуть выше сопротивления)
            entry_price = round(wall['price'] + offset, 2)
            sl_price = round(wall['price'] - (atr * 0.5), 2)  # Стоп ниже стены
            tp_price = 0.0  # 🔥 Инициализация для Pylance
            
            # Проверка второй стены в пределах 3 ATR
            second_wall_found = False
            for other_wall in walls_ask:
                if other_wall['price'] == wall['price']:
                    continue
                distance = other_wall['price'] - wall['price']
                if 0 < distance <= 3 * atr:
                    # Вторая стена найдена, проверяем HVN между ними
                    if hvn_above and wall['price'] < hvn_above['price'] < other_wall['price']:
                        # HVN между стенами — торгуем
                        tp_price = round(hvn_above['price'] - (atr * 0.1), 2)
                    else:
                        # Нет HVN между стенами — пропускаем
                        second_wall_found = True
                        break
                elif distance > 3 * atr:
                    break  # Дальше не сканируем
            
            if second_wall_found:
                continue
            
            # Если второй стены нет или HVN между ними — рассчитываем TP
            if not second_wall_found:
                reward_distance = (entry_price - sl_price) * 2.5
                tp_price_candidate = round(entry_price + reward_distance, 2)
                
                if hvn_above and hvn_above['price'] > entry_price:
                    # HVN выше входа — ставим TP перед ним
                    tp_price = round(hvn_above['price'] - (atr * 0.1), 2)
                    tp_price = min(tp_price, tp_price_candidate)
                else:
                    tp_price = tp_price_candidate

            # Проверка минимального R:R
            risk = entry_price - sl_price
            reward = tp_price - entry_price
            if risk > 0 and (reward / risk) < 2.0:
                continue

            conf = wall['confidence'] + 0.2  # Бонус за пробой
            
            if conf > best_confidence:
                best_confidence = conf
                best_signal = {
                    'side': 'long',
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'qty': qty,
                    'edge_price': wall['price']
                }

        # --- SHORT: Пробой BID-стены вниз ---
        for wall in walls_bid:
            if wall.get('confidence', 0) < self.min_confidence:
                continue
            
            if wall.get('update_count', 0) < self.min_wall_updates:
                continue
            if wall.get('cv', 1.0) > self.max_wall_cv:
                continue
            
            if wall.get('eaten_pct', 0.0) < self.min_eaten_pct:
                continue
            
            # Для SHORT дельта должна быть отрицательной
            if delta_v3 > 0 or delta_spike < self.delta_spike_ratio:
                continue

            max_allowed_qty = wall['size'] * self.max_wall_exposure_pct
            qty = min(self.fixed_lot_size, max_allowed_qty)
            if qty < 0.1:
                continue

            tick_size = 0.01
            volatility = atr / current_price if current_price > 0 else 0
            offset_ticks = 1 if volatility < 0.001 else 2
            offset = tick_size * offset_ticks
            
            # Вход: лимитка на тик ниже стены (для SHORT продаем чуть ниже поддержки)
            entry_price = round(wall['price'] - offset, 2)
            sl_price = round(wall['price'] + (atr * 0.5), 2)

            tp_price = 0.0  # 🔥 Инициализация для Pylance
            
            second_wall_found = False
            for other_wall in walls_bid:
                if other_wall['price'] == wall['price']:
                    continue
                distance = wall['price'] - other_wall['price']
                if 0 < distance <= 3 * atr:
                    if hvn_below and other_wall['price'] < hvn_below['price'] < wall['price']:
                        tp_price = round(hvn_below['price'] + (atr * 0.1), 2)
                    else:
                        second_wall_found = True
                        break
                elif distance > 3 * atr:
                    break
            
            if second_wall_found:
                continue
            
            if not second_wall_found:
                reward_distance = (sl_price - entry_price) * 2.5
                tp_price_candidate = round(entry_price - reward_distance, 2)
                
                if hvn_below and hvn_below['price'] < entry_price:
                    tp_price = round(hvn_below['price'] + (atr * 0.1), 2)
                    tp_price = max(tp_price, tp_price_candidate)
                else:
                    tp_price = tp_price_candidate

            risk = sl_price - entry_price
            reward = entry_price - tp_price
            if risk > 0 and (reward / risk) < 2.0:
                continue

            conf = wall['confidence'] + 0.2
            
            if conf > best_confidence:
                best_confidence = conf
                best_signal = {
                    'side': 'short',
                    'entry_price': entry_price,
                    'sl_price': sl_price,
                    'tp_price': tp_price,
                    'qty': qty,
                    'edge_price': wall['price']
                }

        # ========================================================================
        # 5. ФИНАЛЬНАЯ ВАЛИДАЦИЯ
        # ========================================================================
        if not best_signal:
            return None

        self._last_signal_time = now
        
        side = best_signal['side'].upper()
        logger.info(f"🚀 [BreakoutV1] BREAKOUT SIGNAL: {side} | Entry: {best_signal['entry_price']} | "
                    f"SL: {best_signal['sl_price']} | TP: {best_signal['tp_price']} | Qty: {best_signal['qty']:.2f} | Conf: {best_confidence:.2f}")

        return EnrichedSignal(
            signal_id=f"BreakoutV1_{symbol}_{int(now)}",
            symbol=symbol,
            side=best_signal['side'],
            entry_price=best_signal['entry_price'],
            strategy="BreakoutV1",
            confidence=best_confidence,
            edge_price=best_signal['edge_price'],
            rr_ratio=2.5,
            atr=atr,
            volatility_mode="normal",
            basis=0.0,
            order_type="limit",
            execution_params={
                "quantity": best_signal['qty'],
                "sl_price": best_signal['sl_price'],
                "tp1_price": best_signal['tp_price'],
                "tp2_price": best_signal['tp_price']
            }
        )