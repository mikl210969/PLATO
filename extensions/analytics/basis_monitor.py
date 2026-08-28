"""Basis Monitor — мониторинг спреда Spot/Futures в реальном времени.

Согласно SL_TP.txt v2.2 (раздел 3):
- Basis Stop: если basis изменился на > 1.5% → аварийное закрытие
- Фильтр шума: минимум 3 последовательных тика с превышением порога
- Синхронизация данных Spot/Futures (разные задержки WS)

Архитектура:
- Подписывается на WS-события MarketEvent (спот и фьючерс)
- Рассчитывает basis = (futures_price - spot_price) / spot_price
- Записывает в SQLite (basis_history)
- Публикует BASIS_UPDATED в EventBus
"""
import logging
import time
from typing import Optional, Callable, Dict
from collections import deque

from extensions.data_layer.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class BasisMonitor:
    def __init__(
        self,
        db_manager: DatabaseManager,
        update_interval_ms: float = 100.0,  # Максимальная частота обновлений
        noise_filter_count: int = 3,  # Минимум последовательных тиков для триггера
        basis_stop_threshold: float = 0.015,  # 1.5% изменение basis
        ttl_seconds: int = 60,  # TTL для записи в БД
        on_event: Optional[Callable[[str, dict], None]] = None,
    ):
        self.db = db_manager
        self.update_interval_ms = update_interval_ms
        self.noise_filter_count = noise_filter_count
        self.basis_stop_threshold = basis_stop_threshold
        self.ttl_seconds = ttl_seconds
        self._on_event = on_event

        # Состояние
        self._last_spot_price: Optional[float] = None
        self._last_futures_price: Optional[float] = None
        self._last_spot_ts: float = 0.0
        self._last_futures_ts: float = 0.0
        self._last_update_ts: float = 0.0
        self._basis_at_entry: Optional[float] = None  # Фиксируется при входе в позицию
        
        # Фильтр шума: буфер последних значений basis
        self._basis_history: deque = deque(maxlen=10)
        
        # Статистика
        self._updates_count = 0
        self._skipped_count = 0

    def update_spot_price(self, price: float, timestamp: float) -> None:
        """Обновить спотовую цену (из WS MarketEvent)."""
        self._last_spot_price = price
        self._last_spot_ts = timestamp
        self._try_calculate_basis()

    def update_futures_price(self, price: float, timestamp: float) -> None:
        """Обновить фьючерсную цену (из WS MarketEvent)."""
        self._last_futures_price = price
        self._last_futures_ts = timestamp
        self._try_calculate_basis()

    def _try_calculate_basis(self) -> None:
        """Попытаться рассчитать basis, если есть обе цены."""
        # Проверяем, что есть обе цены
        if self._last_spot_price is None or self._last_futures_price is None:
            return

        # THROTTLING: не чаще чем update_interval_ms
        now = time.time()
        if (now - self._last_update_ts) * 1000 < self.update_interval_ms:
            self._skipped_count += 1
            return

        # Рассчитываем basis
        basis = (self._last_futures_price - self._last_spot_price) / self._last_spot_price
        
        # Проверяем синхронизацию (разница в timestamp не более 2 секунд)
        ts_diff = abs(self._last_spot_ts - self._last_futures_ts)
        if ts_diff > 2.0:
            logger.warning(f"Spot/Futures рассинхронизация: {ts_diff:.2f} сек")

        # Фильтр шума: проверяем стабильность basis
        self._basis_history.append(basis)
        if len(self._basis_history) < self.noise_filter_count:
            return  # Ещё недостаточно данных

        # Проверяем, что последние N значений стабильны
        recent = list(self._basis_history)[-self.noise_filter_count:]
        avg_basis = sum(recent) / len(recent)
        max_deviation = max(abs(b - avg_basis) for b in recent)
        
        if max_deviation > 0.001:  # Разброс > 0.1% — шум
            return

        # Basis стабилен — публикуем событие
        self._last_update_ts = now
        self._updates_count += 1

        event_data = {
            "basis": basis,
            "basis_pct": basis * 100,
            "spot_price": self._last_spot_price,
            "futures_price": self._last_futures_price,
            "timestamp": now,
        }

        # Проверяем Basis Stop (если позиция открыта)
        if self._basis_at_entry is not None:
            basis_change = abs(basis - self._basis_at_entry)
            if basis_change > self.basis_stop_threshold:
                event_data["basis_stop_triggered"] = True
                event_data["basis_change"] = basis_change
                logger.warning(
                    f"Basis Stop: изменение {basis_change*100:.2f}% "
                    f"(порог {self.basis_stop_threshold*100}%)"
                )

        # Сохраняем в БД
        self._save_to_db(basis)

        # Публикуем событие
        if self._on_event:
            self._on_event("BASIS_UPDATED", event_data)

        logger.debug(f"Basis: {basis*100:.3f}% (spot={self._last_spot_price:.2f}, "
                     f"futures={self._last_futures_price:.2f})")

    def set_basis_at_entry(self, basis: float) -> None:
        """Зафиксировать basis при входе в позицию (для Basis Stop)."""
        self._basis_at_entry = basis
        logger.info(f"Basis зафиксирован при входе: {basis*100:.3f}%")

    def clear_basis_at_entry(self) -> None:
        """Очистить basis при выходе из позиции."""
        self._basis_at_entry = None

    def get_current_basis(self) -> Optional[float]:
        """Получить текущий basis (для чтения стратегиями)."""
        if self._last_spot_price and self._last_futures_price:
            return (self._last_futures_price - self._last_spot_price) / self._last_spot_price
        return None

    def _save_to_db(self, basis: float) -> None:
        """Сохранить basis в Hot Storage (SQLite)."""
        import json
        now = int(time.time())
        expires_at = now + self.ttl_seconds

        metadata = json.dumps({
            "spot_price": self._last_spot_price,
            "futures_price": self._last_futures_price,
            "basis_pct": basis * 100,
        })

        # Вставляем новую запись (старые автоматически удалятся по expires_at)
        self.db.execute_query(
            """
            INSERT INTO market_metrics (symbol, metric_type, value, timestamp, expires_at, metadata)
            VALUES (?, 'BASIS', ?, ?, ?, ?)
            """,
            ("SOLUSDT", basis, now, expires_at, metadata)
        )

    def get_stats(self) -> dict:
        return {
            "updates": self._updates_count,
            "skipped": self._skipped_count,
            "current_basis": self.get_current_basis(),
            "basis_at_entry": self._basis_at_entry,
        }