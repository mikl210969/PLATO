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
    
    # Флаги активации
    tp1_activated: bool = False
    tp2_activated: bool = False
    sl_activated: bool = False
    
    # Информация о Smart Sizing
    sizing_info: Optional[Dict[str, Any]] = None

    # 🔥 НОВОЕ: Мониторинг состояния платформы и защиты
    platform_health: str = "HEALTHY"  # "HEALTHY", "DEGRADED", "BLIND"
    guard_status: str = "inactive"    # "active", "suspended", "inactive"
    
    # 🔥 НОВОЕ: Расчётный финансовый результат по уровням
    tp1_projected_pnl: float = 0.0
    tp2_projected_pnl: float = 0.0
    sl_projected_pnl: float = 0.0
    breakeven_projected_pnl: float = 0.0

    def calculate_projected_pnls(self):
        """Пересчитывает проектный PnL для всех уровней на основе текущей position_size."""
        if self.position_size == 0:
            return
        
        if self.side == "short":
            self.tp1_projected_pnl = round((self.position_entry_price - self.tp1_price) * self.position_size, 2)
            self.tp2_projected_pnl = round((self.position_entry_price - self.tp2_price) * self.position_size, 2)
            self.sl_projected_pnl = round((self.position_entry_price - self.sl_price) * self.position_size, 2)
            self.breakeven_projected_pnl = 0.0  # Безубыток всегда 0
        else: # long
            self.tp1_projected_pnl = round((self.tp1_price - self.position_entry_price) * self.position_size, 2)
            self.tp2_projected_pnl = round((self.tp2_price - self.position_entry_price) * self.position_size, 2)
            self.sl_projected_pnl = round((self.sl_price - self.position_entry_price) * self.position_size, 2)
            self.breakeven_projected_pnl = 0.0

    def transition_to(self, new_status: str, reason: str = ""):
        """Безопасный переход статуса."""
        self.status = new_status
        self.updated_at = datetime.now(timezone.utc).isoformat()
        
        # 🔥 Автоматическое управление guard_status при закрытии
        if new_status == PassportStatus.CLOSED.value:
            self.guard_status = "inactive"
            self.closed_at = self.updated_at
            
        self.timeline.append({
            "timestamp": self.updated_at,
            "event": f"STATUS: {new_status}",
            "details": reason
        })

    def add_timeline_event(self, event_type: str, details: str):
        """Добавить событие в таймлайн с защитой от дубликатов."""
        if self.timeline:
            last_event = self.timeline[-1]
            if last_event.get('event') == event_type:
                try:
                    last_ts = datetime.fromisoformat(last_event['timestamp'])
                    now = datetime.now(timezone.utc)
                    if (now - last_ts).total_seconds() < 1.0:
                        return
                except:
                    pass
        
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
        self.exit_reason = exit_reason
        self.exit_price = exit_price
        self.gross_pnl = gross_pnl
        self.commission = commission
        self.net_pnl = gross_pnl - commission
        self.position_size = 0.0 # Обнуляем при закрытии
    
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
            "net_pnl": self.net_pnl,
            "tp1_activated": self.tp1_activated,
            "tp2_activated": self.tp2_activated,
            "sl_activated": self.sl_activated,
            "sizing_info": self.sizing_info,
            # 🔥 НОВОЕ: добавляем поля здоровья и проектного PnL
            "platform_health": self.platform_health,
            "guard_status": self.guard_status,
            "tp1_projected_pnl": self.tp1_projected_pnl,
            "tp2_projected_pnl": self.tp2_projected_pnl,
            "sl_projected_pnl": self.sl_projected_pnl,
            "breakeven_projected_pnl": self.breakeven_projected_pnl
        }