"""
BaseMixin — базовый класс для всех миксинов Orchestrator.
Убирает дублирование аннотаций типов и общих методов.
"""
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .orchestrator import Orchestrator


class BaseMixin:
    """
    Базовый миксин с общими аннотациями типов.
    Все миксины наследуются от него, чтобы Pylance не ругался на атрибуты Orchestrator.
    """
    
    # Общие атрибуты, которые предоставляет Orchestrator
    _log: Any
    bus: Any
    passport_manager: Any
    repository: Any
    state_manager: Any
    config: Any
    get_trader: Any
    json_logger: Any
    
    def _get_atr_value(self) -> float:
        """Получить ATR из конфига с дефолтным значением."""
        return self.config.get('trading', {}).get('atr_value', 0.5)
    
    def _get_lot_size(self) -> float:
        """Получить размер лота из конфига."""
        return self.config.get('trading', {}).get('lot_size', 7.0)
    
    def _get_entry_order_type(self) -> str:
        """Получить тип ордера входа из конфига."""
        return self.config.get('trading', {}).get('entry_order_type', 'limit')
    
    def _get_ttl_seconds(self) -> int:
        """Получить TTL из конфига."""
        return self.config.get('trading', {}).get('ttl_seconds', 300)
    
    def _calculate_exit_levels(self, trader: Any, side: str, entry_price: float) -> Dict[str, float]:
        """Рассчитать уровни SL/TP через трейдер."""
        atr_value = self._get_atr_value()
        return trader.calculate_exit_levels(
            side=side,
            entry_price=entry_price,
            atr_value=atr_value
        )