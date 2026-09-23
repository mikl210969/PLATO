"""
Базовый класс для стратегий, использующих адаптивные параметры объема.
Устраняет дублирование кода при инициализации и запросе параметров.
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class AdaptiveStrategy:
    """
    Базовый класс для адаптивных стратегий.
    """
    def __init__(self, context_manager):
        """
        :param context_manager: Экземпляр VolumeContextManager
        """
        self._context_mgr = context_manager

    async def get_params(self) -> Dict[str, Any]:
        """
        Возвращает актуальные адаптивные параметры фильтров.
        Если менеджер не установлен, возвращает пустой словарь (fallback).
        """
        if self._context_mgr is None:
            logger.warning("⚠️ Context manager not set, returning empty params")
            return {}
        
        return await self._context_mgr.get_active_params()