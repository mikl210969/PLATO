"""
Мониторинг зависших паспортов.
Проверяет паспорта в статусе OPEN дольше N минут и закрывает их если на бирже позиции нет.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional  #  Добавили Optional

if TYPE_CHECKING:
    from trading.passport_manager import PassportManager
    from trading.passport_repository import PassportRepository

logger = logging.getLogger(__name__)


class StuckPassportMonitor:
    """
    Фоновая задача для обнаружения и закрытия зависших паспортов.
    
    Паспорт считается "зависшим" если:
    - Статус OPEN
    - Последнее обновление было больше N минут назад
    - На бирже позиция = 0
    """
    
    def __init__(
        self,
        passport_manager: "PassportManager",
        passport_repository: "PassportRepository",
        rest_client,  # REST клиент для проверки биржи
        max_age_minutes: int = 30,  # Максимальный возраст без активности
        check_interval_seconds: int = 60  # Интервал проверки
    ):
        self.passport_manager = passport_manager
        self.passport_repository = passport_repository
        self.rest_client = rest_client
        self.max_age_minutes = max_age_minutes
        self.check_interval_seconds = check_interval_seconds
        self._running = False
    
    async def start(self):
        """Запустить фоновую задачу."""
        self._running = True
        logger.info(f"🔍 StuckPassportMonitor запущен (max_age={self.max_age_minutes}min, interval={self.check_interval_seconds}s)")
        
        while self._running:
            try:
                await self._check_stuck_passports()
            except Exception as e:
                logger.error(f"❌ Ошибка в StuckPassportMonitor: {e}")
            
            await asyncio.sleep(self.check_interval_seconds)
    
    def stop(self):
        """Остановить фоновую задачу."""
        self._running = False
        logger.info("🛑 StuckPassportMonitor остановлен")
    
    async def _check_stuck_passports(self):
        """Проверить все паспорта на зависание."""
        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(minutes=self.max_age_minutes)
        
        # Получаем все активные паспорта
        active_passports = self.passport_manager.get_active()
        
        for passport in active_passports:
            if passport.status != "OPEN":
                continue
            
            # Проверяем время последнего обновления
            last_update = self._get_last_update_time(passport)
            if last_update and last_update > cutoff_time:
                # Паспорт недавно обновлялся — не завис
                continue
            
            logger.warning(f"⚠️ Обнаружен зависший паспорт: {passport.passport_id} (последнее обновление: {last_update})")
            
            # Проверяем биржу
            try:
                exchange_position = await self.rest_client.get_position(passport.symbol)
                exchange_size = float(exchange_position.get('size', 0) or 0)
                
                if exchange_size == 0:
                    logger.warning(f"🔒 Закрываем зависший паспорт {passport.passport_id} (на бирже позиция = 0)")
                    
                    # Закрываем паспорт
                    await self.passport_manager.apply_change(
                        passport.passport_id,
                        "EXTERNAL_CLOSE",
                        {
                            "exit_price": passport.position_entry_price,
                            "gross_pnl": 0.0,
                            "commission": 0.0
                        }
                    )
                else:
                    logger.info(f"✅ Паспорт {passport.passport_id} не завис — на бирже позиция {exchange_size}")
                    
            except Exception as e:
                logger.error(f"❌ Ошибка проверки биржи для {passport.passport_id}: {e}")
    
    def _get_last_update_time(self, passport) -> Optional[datetime]:  #  Optional[datetime]
        """Получить время последнего обновления паспорта."""
        if hasattr(passport, 'updated_at') and passport.updated_at:
            try:
                return datetime.fromisoformat(passport.updated_at)
            except:
                pass
        
        # Fallback: проверяем timeline
        if hasattr(passport, 'timeline') and passport.timeline:
            last_event = passport.timeline[-1]
            if 'timestamp' in last_event:
                try:
                    return datetime.fromisoformat(last_event['timestamp'])
                except:
                    pass
        
        return None  #  Теперь можно вернуть None