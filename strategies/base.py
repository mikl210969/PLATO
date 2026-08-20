"""
Базовый класс для всех стратегий.
"""

from typing import Dict, Any, Optional
from core.types import Signal


class BaseStrategy:
    """Базовый класс стратегии."""

    def __init__(self, name: str, config: Dict):
        self.name = name
        self.config = config
        self.enabled = config.get('enabled', False)

    def generate_signal(self, context: Dict[str, Any]) -> Optional[Signal]:
        """
        Генерирует сигнал на основе контекста.
        Должен быть переопределён в дочерних классах.
        """
        raise NotImplementedError(f"{self.name}.generate_signal() not implemented")