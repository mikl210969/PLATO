"""
Общие типы данных для всей платформы.
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum


class OrderType(Enum):
    """Тип ордера."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderSide(Enum):
    """Сторона ордера."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Статус ордера."""
    NEW = "NEW"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PassportStatus(Enum):
    """Статусы паспорта сделки."""
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    ORDER_SENT = "ORDER_SENT"
    ORDER_ACK = "ORDER_ACK"
    LIMIT_ON_BOOK = "LIMIT_ON_BOOK"
    PARTIAL_FILL = "PARTIAL_FILL"
    OPEN = "OPEN"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    CLOSING = "CLOSING"
    CANCELED = "CANCELED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"        # 🔥 Добавлено
    UNKNOWN = "UNKNOWN"


@dataclass
class Signal:
    """Сигнал от стратегии."""
    signal_id: str  # ← ДОБАВИТЬ
    symbol: str
    side: str
    entry_price: float
    confidence: float = 0.5
    strategy: str = ""
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Order:
    """Ордер."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    quantity: float
    filled_quantity: float = 0.0
    status: str = "NEW"
    timestamp: int = 0


@dataclass
class Position:
    """Позиция."""
    symbol: str
    side: str
    size: float
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0