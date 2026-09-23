import time
import logging
from collections import defaultdict, deque
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class VolumeRollingWindow:
    """
    Агрегирует тиковые сделки в 1-минутные свечи в памяти.
    Предоставляет историю объемов для VolumeContextManager и сохраняет свечи в БД.
    """
    def __init__(self, db_manager, lookback_candles: int = 30):
        self.db_manager = db_manager
        self.lookback = lookback_candles
        
        # Хранилище текущих формирующихся свечей: symbol -> {open, high, low, close, volume, start_minute}
        self._current_candles: Dict[str, Dict[str, Any]] = {}
        
        # История завершенных минутных объемов: symbol -> deque(макс. длина lookback)
        self._volume_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=self.lookback))

    def on_trade(self, symbol: str, price: float, quantity: float, timestamp: float, is_buyer_maker: bool):
        """Вызывается при получении события TRADE_NORMALIZED_{SYMBOL}"""
        # Определяем начало текущей минуты (unix timestamp)
        minute_start = int(timestamp // 60) * 60
        
        if symbol not in self._current_candles:
            self._current_candles[symbol] = {
                "start_minute": minute_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0
            }

        candle = self._current_candles[symbol]

        # Если началась новая минута, сохраняем старую свечу в историю и БД
        if minute_start > candle["start_minute"]:
            self._save_completed_candle(symbol, candle)
            # Инициализируем новую свечу
            self._current_candles[symbol] = {
                "start_minute": minute_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0
            }
            candle = self._current_candles[symbol]

        # Обновляем текущую свечу
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["volume"] += quantity

    def _save_completed_candle(self, symbol: str, candle: Dict[str, Any]):
        """Сохраняет завершенную свечу в deque и в SQLite"""
        # 1. Добавляем объем в историю в памяти
        self._volume_history[symbol].append(candle["volume"])
        
        # 2. Асинхронно/фоново сохраняем в БД (вызываем синхронный метод, он быстр благодаря WAL)
        try:
            self.db_manager.upsert_candle_1m(
                symbol=symbol,
                timestamp=candle["start_minute"],
                open_p=candle["open"],
                high=candle["high"],
                low=candle["low"],
                close=candle["close"],
                volume=candle["volume"]
            )
        except Exception as e:
            logger.error(f"Ошибка записи свечи в БД для {symbol}: {e}")

    def get_recent_volumes(self, symbol: str, limit: Optional[int] = None) -> List[float]:
        """Возвращает список последних объемов (от старых к новым)"""
        history = list(self._volume_history[symbol])
        if limit:
            return history[-limit:]
        return history

    async def on_trade_event(self, event):
        """
        Адаптер для EventBus. Извлекает данные из event и вызывает on_trade.
        """
        try:
            symbol = event.symbol
            payload = event.payload
            
            price = payload.get('price', 0.0)
            quantity = payload.get('quantity', 0.0)
            timestamp = payload.get('timestamp', 0.0)
            is_buyer_maker = payload.get('is_buyer_maker', False)
            
            self.on_trade(symbol, price, quantity, timestamp, is_buyer_maker)
        except Exception as e:
            logger.error(f"Error in on_trade_event: {e}")

    def calculate_baseline_avg_vol(self, symbol: str, lookback_minutes: int = 1440) -> float:
        """
        Рассчитывает средний объем за минуту. 
        Сначала пытается взять из БД (если там есть история), иначе из памяти.
        """
        # Пытаемся получить из БД (это даст честные 24 часа, если бот работал)
        db_avg = self.db_manager.get_avg_volume_history(symbol, lookback_minutes)
        if db_avg > 0:
            return db_avg
            
        # Fallback: если БД пуста, берем из памяти (будет расти по мере работы бота)
        memory_history = list(self._volume_history[symbol])
        if len(memory_history) > 0:
            return sum(memory_history) / len(memory_history)
            
        return 100.0 # Дефолтное безопасное значение, если данных нет вообще