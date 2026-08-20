"""
Паспорт сделки — единый источник правды (SSOT).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import uuid

from core.types import PassportStatus


@dataclass
class TradePassport:
    """Паспорт сделки."""
    
    passport_id: str = field(default_factory=lambda: f"PASS_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}")
    symbol: str = ""
    status: str = PassportStatus.SIGNAL_GENERATED.value
    
    # Данные сигнала
    signal_id: str = ""
    strategy: str = ""
    side: str = ""
    entry_price: float = 0.0
    confidence: float = 0.0
    
    # Уровни
    sl_price: float = 0.0
    tp1_price: float = 0.0
    tp2_price: float = 0.0
    
    # Позиция
    position_size: float = 0.0
    position_entry_price: float = 0.0
    
    # Ордера
    orders: List[Dict[str, Any]] = field(default_factory=list)
    
    # Временные метки
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    closed_at: Optional[str] = None
    
    # История
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    
    # Результат
    exit_reason: str = ""
    exit_price: float = 0.0
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    
    def transition_to(self, new_status: str, reason: str = ""):
        """Безопасный переход статуса."""
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.timeline.append({
            "timestamp": self.updated_at,
            "event": f"STATUS: {new_status}",
            "details": reason
        })

    def add_timeline_event(self, event_type: str, details: str):
        """Добавить событие в таймлайн."""
        from datetime import datetime, timezone
        self.timeline.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "details": details
        })
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_order(self, order: Dict[str, Any]):
        """Добавить ордер в паспорт."""
        self.orders.append(order)
        self.updated_at = datetime.now(timezone.utc).isoformat()
    
    def close(self, exit_reason: str, exit_price: float = 0.0, gross_pnl: float = 0.0, commission: float = 0.0):
        """Закрыть паспорт."""
        self.transition_to(PassportStatus.CLOSED.value, exit_reason)
        self.closed_at = datetime.now(timezone.utc).isoformat()
        self.exit_reason = exit_reason
        self.exit_price = exit_price
        self.gross_pnl = gross_pnl
        self.commission = commission
        self.net_pnl = gross_pnl - commission
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь."""
        return {
            "passport_id": self.passport_id,
            "symbol": self.symbol,
            "status": self.status,
            "signal_id": self.signal_id,
            "strategy": self.strategy,
            "side": self.side,
            "entry_price": self.entry_price,
            "confidence": self.confidence,
            "sl_price": self.sl_price,
            "tp1_price": self.tp1_price,
            "tp2_price": self.tp2_price,
            "position_size": self.position_size,
            "position_entry_price": self.position_entry_price,
            "orders": self.orders,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "timeline": self.timeline,
            "exit_reason": self.exit_reason,
            "exit_price": self.exit_price,
            "gross_pnl": self.gross_pnl,
            "commission": self.commission,
            "net_pnl": self.net_pnl
        }