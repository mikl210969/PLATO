"""
Evil Mocks — управляемые моки для тестирования устойчивости платформы.
Позволяют инжектировать сбои в конкретные моменты времени.
"""
import asyncio
from typing import Dict, List, Optional, Any
from unittest.mock import AsyncMock


class EvilRestMock:
    """
    Мок REST-клиента Binance.
    Умеет симулировать: успешные ответы, таймауты, баны -1003, пустые ответы.
    """
    
    def __init__(self):
        self._ban_active = False
        self._ban_until = 0.0
        self._timeout_active = False
        self._position_response = {'symbol': 'SOLUSDT', 'side': 'none', 'size': 0.0, 'entry_price': 0.0, 'unrealized_pnl': 0.0}
        self._open_orders_response = []
        self._user_trades_response = []
        self._exchange_info_response = {}
        self._listen_key = "test_listen_key_12345"
        
        # Счётчики вызовов (для отладки)
        self.call_counts = {
            'get_position': 0,
            'get_open_orders': 0,
            'get_user_trades': 0,
            'get_exchange_info': 0,
            'get_listen_key': 0,
        }
    
    def set_position(self, size: float, side: str = 'none', entry_price: float = 0.0):
        """Установить ответ для get_position."""
        self._position_response = {
            'symbol': 'SOLUSDT',
            'side': side,
            'size': size,
            'entry_price': entry_price,
            'unrealized_pnl': 0.0
        }
    
    def set_open_orders(self, orders: List[Dict]):
        self._open_orders_response = orders
    
    def set_user_trades(self, trades: List[Dict]):
        self._user_trades_response = trades
    
    def activate_ban(self, duration_seconds: float = 60.0):
        """Активировать бан -1003 на указанное время."""
        import time
        self._ban_active = True
        self._ban_until = time.time() + duration_seconds
    
    def deactivate_ban(self):
        self._ban_active = False
        self._ban_until = 0.0
    
    def activate_timeout(self):
        self._timeout_active = True
    
    def deactivate_timeout(self):
        self._timeout_active = False
    
    async def get_position(self, symbol: str):
        self.call_counts['get_position'] += 1
        
        if self._timeout_active:
            await asyncio.sleep(10)  # Имитация таймаута
            return None
        
        if self._ban_active:
            import time
            if time.time() < self._ban_until:
                raise Exception("Binance API error: Way too many requests (code: -1003)")
            else:
                self._ban_active = False
        
        return self._position_response
    
    async def get_open_orders(self, symbol: str) -> List[Dict]:
        self.call_counts['get_open_orders'] += 1
        
        if self._ban_active:
            raise Exception("Binance API error: Way too many requests (code: -1003)")
        
        return self._open_orders_response
    
    async def get_user_trades(self, symbol: str, start_time: Optional[int] = None, end_time: Optional[int] = None, limit: int = 500) -> List[Dict]:
        self.call_counts['get_user_trades'] += 1
        
        if self._ban_active:
            raise Exception("Binance API error: Way too many requests (code: -1003)")
        
        return self._user_trades_response
    
    async def get_exchange_info(self, symbol: Optional[str] = None) -> Dict:
        self.call_counts['get_exchange_info'] += 1
        return self._exchange_info_response
    
    async def get_listen_key(self) -> str:
        self.call_counts['get_listen_key'] += 1
        return self._listen_key
    
    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        return {'bids': [['103.0', '10.0']], 'asks': [['103.1', '10.0']]}


class EvilWsMock:
    """
    Мок WebSocket-адаптера.
    Умеет симулировать: обрыв связи, доставку событий с задержкой, дублирование событий.
    """
    
    def __init__(self):
        self._connected = True
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._handlers: Dict[str, Any] = {}
        self._drop_next_events = 0
        self._duplicate_next_events = 0
        self._on_reconnect_callback = None
        
        # Статистика
        self.events_sent = 0
        self.events_dropped = 0
    
    def on(self, event_type: str, handler):
        self._handlers[event_type] = handler
    
    def set_on_reconnect(self, callback):
        self._on_reconnect_callback = callback
    
    def drop_connection(self):
        """Симулировать обрыв соединения."""
        self._connected = False
    
    def restore_connection(self):
        """Восстановить соединение."""
        self._connected = True
    
    def drop_next_events(self, count: int = 1):
        """Сбросить следующие N событий (имитация потери)."""
        self._drop_next_events = count
    
    def duplicate_next_events(self, count: int = 2):
        """Продублировать следующее событие N раз (имитация race condition)."""
        self._duplicate_next_events = count
    
    async def push_event(self, event_type: str, data: Dict):
        """Отправить событие в очередь обработки."""
        self.events_sent += 1
        
        if self._drop_next_events > 0:
            self._drop_next_events -= 1
            self.events_dropped += 1
            return
        
        # Обработка дублирования
        duplicates = self._duplicate_next_events if self._duplicate_next_events > 0 else 1
        self._duplicate_next_events = 0
        
        for _ in range(duplicates):
            await self._event_queue.put((event_type, data))
    
    async def subscribe_user_data(self, listen_key: str, refresh_key_callback=None):
        pass  # В тесте не нужно реальное подключение
    
    async def subscribe_depth(self, symbol: str):
        pass
    
    async def subscribe_btc_streams(self):
        pass
    
    async def connect(self, retries: int = 3):
        self._connected = True
    
    async def run(self):
        """Основной цикл обработки событий."""
        while True:
            try:
                event_type, data = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                handler = self._handlers.get(event_type)
                if handler:
                    await handler(data)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    
    @property
    def is_healthy(self) -> bool:
        return self._connected


class TimeController:
    """
    Контроллер времени для ускорения тестов.
    Позволяет "перематывать" время вперёд без реального ожидания.
    """
    
    def __init__(self, speed_multiplier: float = 100.0):
        self.speed_multiplier = speed_multiplier
        self._time_offset = 0.0
    
    def sleep(self, seconds: float):
        """Ускоренный sleep."""
        real_seconds = seconds / self.speed_multiplier
        return asyncio.sleep(real_seconds)
    
    def advance(self, seconds: float):
        """Переместить время вперёд (для тестов с фиксированным временем)."""
        self._time_offset += seconds