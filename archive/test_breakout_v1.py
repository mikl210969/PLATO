"""Синтетическая проверка BreakoutStrategyV1."""
import time
from core.logger import get_logger
from strategies.breakout_v1 import BreakoutStrategyV1
from typing import Dict, Any
log = get_logger("test_breakout")

CONFIG = {
    "force_test_signal": False,
    "test_signal_interval": 60,
    "fixed_lot_size": 7.0,
    "min_eaten_pct": 0.70,
    "min_wall_age_sec": 30,
    "max_wall_exposure_pct": 0.15,
    "min_wall_updates": 30,
    "max_wall_cv": 0.15,
    "bypass_filters": False,
    "min_confidence": 0.6,
    "delta_spike_ratio": 3.0,
    "cooldown_sec": 60
}

strategy = BreakoutStrategyV1(CONFIG, atr_value=0.15)

# Имитируем контекст с features
class MockFeatures:
    def __init__(self):
        # 🔥 Исправление: явно указываем, что это словарь, а не None
        self._snap: Dict[str, Any] = {}
    
    def is_fresh(self, now):
        return True
    
    def snapshot(self, now):
        return self._snap

mock_features = MockFeatures()

now = time.time()

# ============================================================================
# Фаза 1: Нормальный пробой LONG (ASK-стена съедена на 70%, дельта положительная)
# ============================================================================
log.info("Фаза 1: Тестируем пробой LONG...")

mock_features._snap = {
    'delta': {'windows': {3: {'velocity': 50.0}}},  # Положительная дельта
    'imbalance': {'imbalance': 0.3},
    'walls': {
        'walls_bid': [],
        'walls_ask': [{
            'price': 100.0,
            'size': 30.0,  # Остаток 30 SOL (было 100, съели 70%)
            'confidence': 0.8,
            'eaten_pct': 0.70,
            'update_count': 50,
            'cv': 0.05,
            'age_sec': 60
        }]
    },
    'volume_profile': {
        'nearest_hvn_above': {'price': 101.0},
        'nearest_hvn_below': None
    }
}

# Кормим дельту для расчета spike
for i in range(10):
    mock_features._snap['delta']['windows'][3]['velocity'] = 10.0
    context = {
        'symbol': 'SOLUSDT',
        'current_price': 99.9,
        'features': mock_features,
        'market_regime': 'NORMAL'
    }
    strategy.generate_signal(context)

# Теперь резкий всплеск
mock_features._snap['delta']['windows'][3]['velocity'] = 50.0
context = {
    'symbol': 'SOLUSDT',
    'current_price': 99.9,
    'features': mock_features,
    'market_regime': 'NORMAL'
}

signal = strategy.generate_signal(context)
assert signal is not None, "Должен быть сигнал на пробой LONG"
assert signal.side == 'long', f"Ожидался long, получен {signal.side}"
assert signal.entry_price > 100.0, f"Вход должен быть выше стены 100.0, получено {signal.entry_price}"

log.info(f"✅ Фаза 1: Пробой LONG | Entry: {signal.entry_price} | SL: {signal.execution_params['sl_price']} | TP: {signal.execution_params['tp1_price']}")

# ============================================================================
# Фаза 2: Ложный пробой (стена нестабильная, CV > 0.15)
# ============================================================================
log.info("Фаза 2: Тестируем фильтр нестабильной стены...")

mock_features._snap['walls']['walls_ask'][0]['cv'] = 0.20  # Нестабильная
mock_features._snap['delta']['windows'][3]['velocity'] = 50.0

context = {
    'symbol': 'SOLUSDT',
    'current_price': 99.9,
    'features': mock_features,
    'market_regime': 'NORMAL'
}

signal2 = strategy.generate_signal(context)
assert signal2 is None, "Нестабильная стена должна быть отфильтрована"

log.info("✅ Фаза 2: Нестабильная стена отфильтрована")

# ============================================================================
# Фаза 3: Вторая стена без HVN (пропуск)
# ============================================================================
log.info("Фаза 3: Тестируем вторую стену без HVN...")

mock_features._snap['walls']['walls_ask'][0]['cv'] = 0.05  # Возвращаем стабильность
mock_features._snap['walls']['walls_ask'].append({
    'price': 100.2,  # Вторая стена в 0.2 от первой (меньше 3 ATR)
    'size': 50.0,
    'confidence': 0.7,
    'eaten_pct': 0.0,
    'update_count': 40,
    'cv': 0.05,
    'age_sec': 50
})
mock_features._snap['volume_profile']['nearest_hvn_above'] = None  # Нет HVN между стенами

context = {
    'symbol': 'SOLUSDT',
    'current_price': 99.9,
    'features': mock_features,
    'market_regime': 'NORMAL'
}

signal3 = strategy.generate_signal(context)
assert signal3 is None, "Вторая стена без HVN должна привести к пропуску"

log.info("✅ Фаза 3: Вторая стена без HVN — пропуск")

log.info("✅ BreakoutStrategyV1: синтетика пройдена")