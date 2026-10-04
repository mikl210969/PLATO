"""
Absorption Strategy V4 (Retest / State Machine)
Интегрирует доказанный эдж: Big Orders (>30 SOL) + HVN 60m + Delta Turnaround.
Архитектура: Касание -> Отскок -> Ретест -> Вход.
"""
import time
import logging
from typing import Optional, Dict, Any

from strategies.wall_fade_v3 import EnrichedSignal
from strategies.adaptive_strategy import AdaptiveStrategy

logger = logging.getLogger(__name__)

# Статусы сетапа
STATE_IDLE = "IDLE"
STATE_WAITING_BOUNCE = "WAITING_BOUNCE"
STATE_WAITING_RETEST = "WAITING_RETEST"

class AbsorptionStrategyV4(AdaptiveStrategy):
    def __init__(self, config: Dict[str, Any], atr_value: float = 0.5, context_manager=None):
        super().__init__(context_manager)
        
        self.config = config
        self.atr_value = atr_value
        self.cooldown_sec = config.get('cooldown_sec', 60.0)
        
        # 🔥 Параметры отскока и ретеста (State Machine)
        self.bounce_threshold_pct = config.get('bounce_threshold_pct', 0.0025) # 0.25%
        self.bounce_max_time_sec = config.get('bounce_max_time_sec', 90)       # 90 сек
        self.max_drift_pct = config.get('max_drift_pct', 0.006)                # 0.6% слом сетапа
        self.retest_tolerance_pct = config.get('retest_tolerance_pct', 0.001)  # 0.1% допуск ретеста
        
        # 🔥 Параметры риска и лотов
        self.risk_per_trade_usd = config.get('risk_per_trade_usd', 50.0)
        self.min_notional_usd = config.get('min_notional_usd', 10.0)
        self.max_position_size = config.get('max_position_size', 100.0)
        
        #  Хранилище активных сетапов (State Machine)
        self.active_setups: Dict[str, Dict[str, Any]] = {}

    def subscribe_to_events(self, event_bus):
        pass

    async def generate_signal(self, context: Dict[str, Any]) -> Optional[EnrichedSignal]:
        now = time.time()
        symbol = context.get('symbol', 'SOLUSDT')
        current_price = context.get('current_price', 0.0)
        
        features = context.get('features')
        if not features or not features.is_fresh(now):
            return None

        snap = features.snapshot(now)
        atr = self.atr_value if self.atr_value > 0 else 0.15
        
        # Очистка протухших сетапов
        self._cleanup_expired_setups(now)

        setup_key = f"{symbol}_setup"
        
        # ========================================================================
        # ФАЗА 2 и 3: Обработка активного сетапа
        # ========================================================================
        if setup_key in self.active_setups:
            setup = self.active_setups[setup_key]
            wall_price = setup['wall_price']
            side = setup['side']
            state = setup['state']
            
            # Проверка на сильный дрейф (слом сетапа)
            drift = abs(current_price - wall_price) / wall_price
            if drift > self.max_drift_pct:
                logger.info(f"❌ [{self.__class__.__name__}] Сетап аннулирован: дрейф {drift:.3%}")
                del self.active_setups[setup_key]
                return None

            # ФАЗА 2: Ожидание отскока
            if state == STATE_WAITING_BOUNCE:
                if side == 'long':
                    bounce_dist = (current_price - wall_price) / wall_price
                else:
                    bounce_dist = (wall_price - current_price) / wall_price
                    
                if bounce_dist >= self.bounce_threshold_pct:
                    setup['state'] = STATE_WAITING_RETEST
                    setup['bounce_time'] = now
                    logger.info(f"✅ [{self.__class__.__name__}] Отскок подтвержден ({bounce_dist:.3%}). Ждем ретест к {wall_price}")
                return None

            # ФАЗА 3: Ожидание ретеста и ВХОД
            if state == STATE_WAITING_RETEST:
                # Цена вернулась к стене?
                if abs(current_price - wall_price) / wall_price <= self.retest_tolerance_pct:
                    logger.info(f"🚀 [{self.__class__.__name__}] РЕТЕСТ! Вход в {side.upper()} по {current_price}")
                    
                    # Расчет уровней
                    if side == 'long':
                        sl_price = round(wall_price - (atr * 0.3), 2)
                        risk_distance = abs(current_price - sl_price)
                        tp1_price = round(current_price + (risk_distance * 1.5), 2)
                        tp2_price = round(current_price + (risk_distance * 3.0), 2)
                    else:
                        sl_price = round(wall_price + (atr * 0.3), 2)
                        risk_distance = abs(sl_price - current_price)
                        tp1_price = round(current_price - (risk_distance * 1.5), 2)
                        tp2_price = round(current_price - (risk_distance * 3.0), 2)

                    # Расчет размера позиции (Risk-based)
                    quantity = self._calculate_position_size(current_price, sl_price)
                    if quantity == 0.0:
                        del self.active_setups[setup_key]
                        return None

                    del self.active_setups[setup_key]
                    
                    return EnrichedSignal(
                        signal_id=f"AbsorptionV4_{symbol}_{int(now)}",
                        symbol=symbol, side=side, entry_price=current_price, strategy="AbsorptionV4",
                        confidence=0.85, edge_price=wall_price, rr_ratio=1.5,
                        atr=atr, volatility_mode="normal", basis=0.0, order_type="limit",
                        execution_params={
                            "quantity": quantity,
                            "sl_price": sl_price, "tp1_price": tp1_price, "tp2_price": tp2_price
                        }
                    )
            return None

        # ========================================================================
        # ФАЗА 1: Поиск нового касания (Инициация сетапа)
        # ========================================================================
        # Получаем данные для фильтрации
        # ВНИМАНИЕ: Настрой индексы окон delta под твои реальные настройки features!
        # Обычно windows[0] - короткое (10с), windows[2] или [3] - длинное (60с)
        delta_short = snap['delta']['windows'][0]['velocity'] 
        delta_long = snap['delta']['windows'][3]['velocity']
        imbalance = snap['imbalance']['imbalance']
        
        hvn_below = snap['volume_profile'].get('nearest_hvn_below')
        hvn_above = snap['volume_profile'].get('nearest_hvn_above')
        walls_bid = snap['walls'].get('walls_bid', [])
        walls_ask = snap['walls'].get('walls_ask', [])

        # --- ПОИСК LONG ---
        if hvn_below:
            hvn_dist = abs(current_price - hvn_below['price']) / current_price
            # 🔥 ДОКАЗАННЫЙ ЭДЖ: HVN_TF=60m, Dist<=0.35%
            if hvn_dist <= 0.0035: 
                # 🔥 ДОКАЗАННЫЙ ЭДЖ: Delta Turnaround (Big Orders > 30 SOL)
                if delta_long < -200 and delta_short > -30 and imbalance < 0.1:
                    self.active_setups[setup_key] = {
                        'state': STATE_WAITING_BOUNCE,
                        'wall_price': hvn_below['price'], # Используем HVN как якорь
                        'side': 'long',
                        'created_at': now,
                        'confidence': 0.7
                    }
                    logger.info(f" [{self.__class__.__name__}] LONG Setup: Касание HVN {hvn_below['price']}. Ждем отскока.")
                    return None

        # --- ПОИСК SHORT ---
        if hvn_above:
            hvn_dist = abs(hvn_above['price'] - current_price) / current_price
            if hvn_dist <= 0.0035:
                if delta_long > 200 and delta_short < 30 and imbalance > -0.1:
                    self.active_setups[setup_key] = {
                        'state': STATE_WAITING_BOUNCE,
                        'wall_price': hvn_above['price'],
                        'side': 'short',
                        'created_at': now,
                        'confidence': 0.7
                    }
                    logger.info(f"👀 [{self.__class__.__name__}] SHORT Setup: Касание HVN {hvn_above['price']}. Ждем отскока.")
                    return None

        return None

    # ========================================================================
    # Вспомогательные методы
    # ========================================================================
    def _cleanup_expired_setups(self, now: float):
        expired_keys = [
            k for k, v in self.active_setups.items() 
            if (now - v['created_at']) > self.bounce_max_time_sec * 2 # Даем чуть больше времени на ретест
        ]
        for k in expired_keys:
            logger.debug(f"⏱ [{self.__class__.__name__}] Сетап {k} удален по таймауту.")
            del self.active_setups[k]

    def _calculate_position_size(self, entry_price: float, stop_price: float) -> float:
        risk_distance = abs(entry_price - stop_price)
        if risk_distance == 0:
            return 0.0
            
        raw_size = self.risk_per_trade_usd / risk_distance
        sized_position = min(raw_size, self.max_position_size)
        
        # Проверка на минимальный нотинал биржи
        min_size_for_exchange = self.min_notional_usd / entry_price
        
        if sized_position < min_size_for_exchange:
            logger.warning(f"⚠️ [{self.__class__.__name__}] Лот {sized_position:.4f} < минимума {min_size_for_exchange:.4f}. Пропуск.")
            return 0.0
            
        return round(sized_position, 4)