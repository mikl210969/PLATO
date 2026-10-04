"""
Sniper V6 Strategy: УСТ + Аномальная Стена + Поглощение Дельты + Умный TP2 + Breakeven.
Наследуется от AdaptiveStrategy для интеграции с VolumeContextManager.
"""
import time
import logging
from typing import Optional, Dict, Any

# 🔥 Импорт базовых классов и типов платформы
from strategies.adaptive_strategy import AdaptiveStrategy
from strategies.wall_fade_v3 import EnrichedSignal
from extensions.data_layer.db_manager import DatabaseManager
from core.logger import get_logger

logger = get_logger(__name__)

class SniperV6(AdaptiveStrategy):
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5, context_manager=None):
        super().__init__(context_manager)
        self.config = config
        self.atr_value = atr_value
        self._last_signal_time = 0.0
        
        # Параметры из конфига (с fallback на дефолтные значения из бэктестов)
        snp_cfg = config.get('sniper_v6', {})
        self.force_test_signal = snp_cfg.get('force_test_signal', False)
        self.test_signal_interval = snp_cfg.get('test_signal_interval', 60)
        self.fixed_lot_size = snp_cfg.get('fixed_lot_size', 7.0)
        self.cooldown_sec = snp_cfg.get('cooldown_sec', 120)
        
        self.atr_mult_sl = snp_cfg.get('atr_mult_sl', 1.0)
        self.tp1_mult = snp_cfg.get('tp1_mult', 1.5)
        self.base_tp2_mult = snp_cfg.get('base_tp2_mult', 2.5)
        self.max_tp2_mult = snp_cfg.get('max_tp2_mult', 4.0)
        
        self.min_wall_size_mult = snp_cfg.get('min_wall_size_mult', 3.0)
        self.max_wall_cv = snp_cfg.get('max_wall_cv', 0.15)
        
        self.delta_window_sec = snp_cfg.get('delta_window_sec', 3)
        self.delta_shift_min = snp_cfg.get('delta_shift_min', 500.0)
        
        self.hist_ust_rr_imp = snp_cfg.get('historical_ust_rr_improvement', 1.1)
        self.hist_ust_max_dist = snp_cfg.get('historical_ust_max_distance_mult', 2.0)

        # Инициализация БД для исторических УСТ
        try:
            self.db = DatabaseManager()
            self.db.init_ust_table()
        except Exception as e:
            logger.error(f"❌ [SniperV6] Ошибка инициализации БД: {e}")
            self.db = None
        print(f"🎯 [DEBUG INIT] SniperV6: force_test_signal={self.force_test_signal}, cooldown={self.cooldown_sec}") # <-- ДОБАВИТЬ

        
    def subscribe_to_events(self, event_bus):
        pass

    async def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        now = time.time()
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        
        # 1. Тестовый режим
        if self.force_test_signal and (now - self._last_signal_time >= self.test_signal_interval):
            self._last_signal_time = now
            return self._create_test_signal(symbol, current_price, now)

        # 2. Проверка свежести данных
        features = context.get('features')
        if not features or not features.is_fresh(now):
            return None

        snap = features.snapshot()
        atr = self.atr_value if self.atr_value > 0 else (context.get('atr', 0.15) or 0.15)
        
        # 3. Извлечение фич
        walls_bid = snap.get('walls', {}).get('walls_bid', [])
        walls_ask = snap.get('walls', {}).get('walls_ask', [])
        delta_windows = snap.get('delta', {}).get('windows', {})
        current_delta_vel = delta_windows.get(self.delta_window_sec, {}).get('velocity', 0.0)

        best_signal = None
        best_confidence = 0.0

        # 4. ПОИСК СДЕЛОК (LONG и SHORT)
        for side, walls in [('long', walls_bid), ('short', walls_ask)]:
            for wall in walls:
                # Фильтр 1: Качество стены (CV и возраст уже отфильтрованы в WallsFeature, проверяем размер)
                if wall.get('cv', 1.0) > self.max_wall_cv:
                    continue
                
                # Фильтр 2: Аномальный размер (например, > 3x от среднего, что уже заложено в WallsFeature, 
                # но мы можем добавить доп. проверку на абсолютный размер, если нужно)
                
                # Фильтр 3: Поглощение дельты (Delta Shift)
                # Для LONG (bid стена): мы хотим, чтобы продажи (отриц. дельта) ослабевали или развернулись
                # Для упрощения в реальном времени: проверяем, что дельта не идет агрессивно ПРОТИВ нас
                if side == 'long' and current_delta_vel < -self.delta_shift_min:
                    continue # Продавцы слишком сильны, стена может не выдержать
                if side == 'short' and current_delta_vel > self.delta_shift_min:
                    continue # Покупатели слишком сильны

                # 5. РАСЧЕТ УРОВНЕЙ
                entry_price = wall['price']
                risk = atr * self.atr_mult_sl
                
                sl_price = entry_price - risk if side == 'long' else entry_price + risk
                tp1_price = entry_price + (risk * self.tp1_mult) if side == 'long' else entry_price - (risk * self.tp1_mult)
                
                # 6. УМНЫЙ TP2 (Smart TP2 vs Исторический УСТ)
                tp2_price, tp2_reason = self._calculate_smart_tp2(
                    entry_price, side, risk, atr, symbol
                )

                # Проверка минимального R:R
                reward = abs(tp2_price - entry_price)
                if (reward / risk) < 2.0:
                    continue

                qty = self.fixed_lot_size
                conf = wall.get('confidence', 0.5) + 0.3 # Бонус за стратегию
                
                if conf > best_confidence:
                    best_confidence = conf
                    best_signal = {
                        'side': side,
                        'entry_price': round(entry_price, 2),
                        'sl_price': round(sl_price, 2),
                        'tp1_price': round(tp1_price, 2),
                        'tp2_price': round(tp2_price, 2),
                        'qty': qty,
                        'edge_price': wall['price'],
                        'tp2_reason': tp2_reason
                    }

        # 7. ФИНАЛЬНАЯ ВАЛИДАЦИЯ И COOLDOWN
        if not best_signal:
            return None
            
        if (now - self._last_signal_time) < self.cooldown_sec:
            return None

        self._last_signal_time = now
        side_str = best_signal['side'].upper()
        
        logger.info(f"🎯 [SniperV6] СИГНАЛ: {side_str} | Entry: {best_signal['entry_price']} | "
                    f"SL: {best_signal['sl_price']} | TP1: {best_signal['tp1_price']} | "
                    f"TP2: {best_signal['tp2_price']} ({best_signal['tp2_reason']})")

        return EnrichedSignal(
            signal_id=f"SniperV6_{symbol}_{int(now)}_{best_signal['tp2_reason'].replace(' ', '_')}",  # 🔥 Добавил причину в ID
            symbol=symbol,
            side=best_signal['side'],
            entry_price=best_signal['entry_price'],
            strategy="SniperV6",
            confidence=best_confidence,
            edge_price=best_signal['edge_price'],
            rr_ratio=round(abs(best_signal['tp2_price'] - best_signal['entry_price']) / (atr * self.atr_mult_sl), 2),
            atr=atr,
            volatility_mode="normal",
            basis=0.0,
            order_type="limit",
            execution_params={
                "quantity": best_signal['qty'],
                "sl_price": best_signal['sl_price'],
                "tp1_price": best_signal['tp1_price'],
                "tp2_price": best_signal['tp2_price']
            }
            #  metadata убран, так как EnrichedSignal его не поддерживает
       
        )

    def _calculate_smart_tp2(self, entry_price: float, side: str, risk: float, atr: float, symbol: str):
        """Рассчитывает TP2: Smart (2.5-4.0) или Исторический УСТ, если он лучше."""
        base_tp2 = entry_price + (risk * self.base_tp2_mult) if side == 'long' else entry_price - (risk * self.base_tp2_mult)
        smart_dist = abs(base_tp2 - entry_price)
        smart_rr = self.base_tp2_mult

        # Попытка найти исторический УСТ
        hist_tp2 = None
        hist_rr = 0.0
        hist_reason = "Smart TP2"

        if self.db:
            try:
                # Ищем активные УСТ за последние 5 дней (432000 сек)
                cutoff_time = time.time() - 432000
                query = """
                    SELECT level_price, side FROM ust_levels 
                    WHERE symbol = ? AND is_active = 1 AND created_at > ?
                    ORDER BY created_at DESC
                """
                rows = self.db.execute(query, (symbol, cutoff_time))
                
                for row in rows:
                    ust_price = row['level_price']
                    ust_side = row['side']
                    
                    # Проверка направления
                    if side == 'long' and ust_price <= entry_price:
                        continue
                    if side == 'short' and ust_price >= entry_price:
                        continue
                    
                    ust_dist = abs(ust_price - entry_price)
                    ust_rr = ust_dist / risk
                    
                    # Условия замены: R:R лучше на 10%+ И расстояние не дальше 2x от Smart TP2
                    if ust_rr > (smart_rr * self.hist_ust_rr_imp) and ust_dist <= (smart_dist * self.hist_ust_max_dist):
                        if hist_tp2 is None or ust_rr > hist_rr:
                            hist_tp2 = ust_price
                            hist_rr = ust_rr
                            hist_reason = f"Истор.УСТ {ust_price:.2f}"
            except Exception as e:
                logger.warning(f"⚠️ Ошибка запроса УСТ из БД: {e}")

        if hist_tp2 is not None:
            return hist_tp2, hist_reason
        else:
            # Если исторического нет, применяем простую логику Smart TP2 (можно расширить дельтой/стаканом как в бэктесте)
            # Для краткости здесь оставляем base, но можно добавить логику из test_smart_tp2.py
            return base_tp2, "Smart TP2 (Base)"

    def _create_test_signal(self, symbol: str, current_price: float, now: float) -> EnrichedSignal:
        side = 'short' # Для теста
        sl_price = round(current_price + self.atr_value * self.atr_mult_sl, 2)
        tp1_price = round(current_price - (sl_price - current_price) * self.tp1_mult, 2)
        tp2_price = round(current_price - (sl_price - current_price) * self.base_tp2_mult, 2)
        
        return EnrichedSignal(
            signal_id=f"SniperV6_TEST_{int(now)}",
            symbol=symbol, side=side, entry_price=current_price, strategy="SniperV6",
            confidence=0.99, edge_price=current_price, rr_ratio=self.base_tp2_mult, atr=self.atr_value,
            volatility_mode="normal", basis=0.0, order_type="limit",
            execution_params={"quantity": self.fixed_lot_size, "sl_price": sl_price, "tp1_price": tp1_price, "tp2_price": tp2_price}
        )