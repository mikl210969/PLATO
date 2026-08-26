"""
Event Bus — асинхронная шина событий для слабой связности компонентов.
Включает дедупликацию: события с одинаковым payload['dedup_key'] в пределах
TTL публикуются только один раз (защита от дублей WS + REST-верификатора).
"""

import asyncio
import time
import traceback
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Event:
    """Событие."""
    type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    symbol: str = ""
    correlation_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class EventBus:
    """
    Асинхронная шина событий с дедупликацией.
    """

    def __init__(self, dedup_ttl: float = 120.0, dedup_max_size: int = 2000):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = asyncio.Lock()
        # 🔥 Дедупликация: ключ -> время первой публикации
        self._seen: Dict[str, float] = {}
        self._dedup_ttl = dedup_ttl
        self._dedup_max_size = dedup_max_size
        self.dedup_hits = 0  # метрика: сколько дубликатов отфильтровано

    def subscribe(self, event_type: str, handler: Callable[[Event], Awaitable[None]]):
        """
        Подписаться на событие.
        """
        async def _wrapper(event: Event):
            try:
                await handler(event)
            except Exception as e:
                print(f"❌ [EVENT_BUS] Handler error: {e}")
                print("🔥 ПОЛНАЯ ТРАССИРОВКА ОШИБКИ (TRACEBACK):")
                traceback.print_exc()

        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(_wrapper)

    async def publish(self, event_type: str, source: str, payload: Optional[Dict[str, Any]] = None, symbol: str = "", correlation_id: str = ""):
        """
        Опубликовать событие.
        Если payload содержит 'dedup_key' и такой ключ уже публиковался
        в пределах TTL — событие отбрасывается (логируется dedup_hit).
        """
        payload = payload or {}

        # 🔥 ДЕДУПЛИКАЦИЯ (до создания Event и рассылки)
        dedup_key = payload.get("dedup_key")
        if dedup_key:
            now = time.time()
            if dedup_key in self._seen:
                self.dedup_hits += 1
                print(f"🔁 [EVENT_BUS] dedup_hit: {event_type} | key={dedup_key}")
                return
            self._seen[dedup_key] = now
            # Периодическая очистка устаревших ключей
            if len(self._seen) > self._dedup_max_size:
                cutoff = now - self._dedup_ttl
                self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

        event = Event(
            type=event_type,
            source=source,
            payload=payload,
            symbol=symbol,
            correlation_id=correlation_id
        )

        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            tasks.append(asyncio.create_task(handler(event)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def clear(self):
        """Очистить все подписки."""
        self._handlers.clear()