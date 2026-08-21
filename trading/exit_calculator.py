"""
ExitCalculator — расчёт уровней выхода (SL, TP1, TP2).
Базовая версия: по ATR.
"""

from typing import Dict


class ExitCalculator:
    """Калькулятор уровней выхода."""

    def __init__(self, config: Dict):
        self.config = config
        self.atr_multiplier_sl = config.get('atr_multiplier_sl', 1.5)
        self.atr_multiplier_tp1 = config.get('atr_multiplier_tp1', 2.0)
        self.atr_multiplier_tp2 = config.get('atr_multiplier_tp2', 3.0)

        # === ВАЛИДАЦИЯ R:R (Stable Core защита) ===
        # Платформа не должна стартовать с убыточной математикой.
        # TP1 должен быть строго больше SL, TP2 строго больше TP1.
        if self.atr_multiplier_tp1 <= self.atr_multiplier_sl:
            raise ValueError(
                f"КРИТИЧЕСКАЯ ОШИБКА КОНФИГА: "
                f"Множитель TP1 ({self.atr_multiplier_tp1}) должен быть строго больше "
                f"множителя SL ({self.atr_multiplier_sl}). "
                f"Проверьте config/trading.json!"
            )

        if self.atr_multiplier_tp2 <= self.atr_multiplier_tp1:
            raise ValueError(
                f"КРИТИЧЕСКАЯ ОШИБКА КОНФИГА: "
                f"Множитель TP2 ({self.atr_multiplier_tp2}) должен быть строго больше "
                f"множителя TP1 ({self.atr_multiplier_tp1}). "
                f"Проверьте config/trading.json!"
            )
        # === КОНЕЦ ВАЛИДАЦИИ ===

    def calculate(
        self,
        side: str,
        entry_price: float,
        atr_value: float
    ) -> Dict[str, float]:
        """
        Рассчитывает уровни выхода.
        
        Returns:
            {
                'sl_price': float,
                'tp1_price': float,
                'tp2_price': float
            }
        """
        if atr_value <= 0:
            atr_value = 0.5  # fallback
        
        if side == 'long':
            sl_price = entry_price - (atr_value * self.atr_multiplier_sl)
            tp1_price = entry_price + (atr_value * self.atr_multiplier_tp1)
            tp2_price = entry_price + (atr_value * self.atr_multiplier_tp2)
        else:  # short
            sl_price = entry_price + (atr_value * self.atr_multiplier_sl)
            tp1_price = entry_price - (atr_value * self.atr_multiplier_tp1)
            tp2_price = entry_price - (atr_value * self.atr_multiplier_tp2)

        return {
            'sl_price': round(sl_price, 4),
            'tp1_price': round(tp1_price, 4),
            'tp2_price': round(tp2_price, 4)
        }