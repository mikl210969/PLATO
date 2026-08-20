"""
Event Bus — асинхронная шина событий для слабой связности компонентов.
"""

import asyncio
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
    Асинхронная шина событий.
    """

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: str, handler: Callable[[Event], Awaitable[None]]):
        """
        Подписаться на событие.
        """
        async def _wrapper(event: Event):
            try:
                await handler(event)
            except Exception as e:
                print(f"[EVENT_BUS] Handler error: {e}")

        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(_wrapper)

    async def publish(self, event_type: str, source: str, payload: Optional[Dict[str, Any]] = None, symbol: str = "", correlation_id: str = ""):
        """
        Опубликовать событие.
        """
        event = Event(
            type=event_type,
            source=source,
            payload=payload or {},
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