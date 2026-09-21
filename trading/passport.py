"""
Паспорт сделки — единый источник правды (SSOT).

🔥 ИСПРАВЛЕНО 2026-09-15:
- calculate_projected_pnls: цена входа берётся по цепочке
  position_entry_price → avg_price → entry_price (сигнальная), поэтому проектный
  PnL больше не считает от нуля.
- real_pnl больше НЕ считается от цены TP1 (это давало -720.79 при entry=0).
  Теперь: для закрытой позиции = gross_pnl; для открытой = unrealized PnL,
  если передана current_price; иначе 0.0.
- close(): если вызывающий код не передал gross_pnl, он вычисляется из цены входа
  и цены выхода (защита от нулевого PnL при закрытии).
- Добавлен helper apply_fill() для корректной обработки частичных исполнений.
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

    # 🔥 НОВОЕ: Поля для корректной обработки частичных исполнений (Partial Fill)
    target_size: float = 0.0
    filled_qty: float = 0.0
    remaining_order_qty: float = 0.0
    avg_price: float = 0.0
    real_pnl: float = 0.0
    realized_pnl: float = 0.0  # 🔥 ПУНКТ 5: накопленный PnL частичных закрытий
    tp1_pnl: float = 0.0  # 🔥 PnL части, закрытой на TP1
    tp2_pnl: float = 0.0  # 🔥 PnL части, закрытой на TP2
    sl_pnl: float = 0.0   # 🔥 PnL части, закрытой на SL    

    # ──────────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ──────────────────────────────────────────────────────────────

    def get_effective_entry_price(self) -> float:
        """
        Фактическая цена входа по цепочке приоритетов:
        позиция (из филлов) → avg_price → сигнальная entry_price.
        """
        if self.position_entry_price > 0.0:
            return self.position_entry_price
        if self.avg_price > 0.0:
            return self.avg_price
        return self.entry_price

    def calculate_projected_pnls(self, current_price: float = 0.0):
        """
        Рассчитать проектный PnL по уровням и текущий/реализованный PnL.

        🔥 ИСПРАВЛЕНО:
- Проектный PnL считается от эффективной цены входа (не от нуля).
        - real_pnl: закрытая позиция → gross_pnl; открытая → unrealized PnL
          по current_price (если передана); иначе 0.0.
        """
        entry = self.get_effective_entry_price()
        size = self.position_size if self.position_size > 0.0 else self.filled_qty
        target = self.target_size if self.target_size > 0.0 else size

        # Позиции нет вообще и цели нет — обнуляем всё
        if entry <= 0.0 or (size <= 0.0 and target <= 0.0):
            self.tp1_projected_pnl = 0.0
            self.tp2_projected_pnl = 0.0
            self.sl_projected_pnl = 0.0
            self.breakeven_projected_pnl = 0.0
            self.real_pnl = 0.0
            return

        # Проектный PnL по уровням — от целевого размера
        if self.side == "short":
            self.tp1_projected_pnl = round((entry - self.tp1_price) * target, 2)
            self.tp2_projected_pnl = round((entry - self.tp2_price) * target, 2)
            self.sl_projected_pnl = round((entry - self.sl_price) * target, 2)
        else:  # long
            self.tp1_projected_pnl = round((self.tp1_price - entry) * target, 2)
            self.tp2_projected_pnl = round((self.tp2_price - entry) * target, 2)
            self.sl_projected_pnl = round((self.sl_price - entry) * target, 2)

        self.breakeven_projected_pnl = 0.0

        # 🔥 Реальный PnL: реализованный (закрытая позиция) или unrealized (открытая)
        if self.status == PassportStatus.CLOSED.value:
            self.real_pnl = self.gross_pnl
        elif current_price > 0.0 and size > 0.0:
            if self.side == "short":
                self.real_pnl = round((entry - current_price) * size, 2)
            else:
                self.real_pnl = round((current_price - entry) * size, 2)
        else:
            self.real_pnl = 0.0

    def apply_fill(self, cumulative_qty: float, avg_price: float = 0.0):
        """
        Обновить состояние позиции по КУМУЛЯТИВНЫМ данным исполнения.
        Единая точка для WS- и REST-событий филлов.
        """
        if cumulative_qty <= 0.0:
            return

        self.filled_qty = cumulative_qty
        self.position_size = cumulative_qty

        if avg_price > 0.0:
            self.avg_price = avg_price
            self.position_entry_price = avg_price

        target = self.target_size if self.target_size > 0.0 else cumulative_qty
        self.remaining_order_qty = max(0.0, target - cumulative_qty)

        self.calculate_projected_pnls()

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
                except Exception:
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
        """
        Закрыть паспорт.
        🔥 ИСПРАВЛЕНО: если вызывающий код не передал gross_pnl, он вычисляется
        из эффективной цены входа и цены выхода (защита от нулевого PnL).
        """
        # 🔥 ИДЕМПОТЕНТНОСТЬ: паспорт можно закрыть только один раз.
        # Повторные close() (поздние события с ценой 0) не затирают факт закрытия.
        if self.status == PassportStatus.CLOSED.value:
            return

        # 🔥 ЗАЩИТА exit_price: никогда не пишем 0 — берём ближайшую осмысленную цену
        if not exit_price or exit_price <= 0:
            exit_price = (
                self.sl_price or self.tp1_price or self.position_entry_price or self.entry_price
            )

        closed_qty = self.position_size if self.position_size > 0.0 else self.filled_qty
        entry = self.get_effective_entry_price()

        self.transition_to(PassportStatus.CLOSED.value, exit_reason)
        self.exit_reason = exit_reason
        self.exit_price = exit_price

        if gross_pnl == 0.0 and exit_price > 0.0 and entry > 0.0 and closed_qty > 0.0:
            if self.side == "short":
                gross_pnl = round((entry - exit_price) * closed_qty, 2)
            else:
                gross_pnl = round((exit_price - entry) * closed_qty, 2)

        self.gross_pnl = gross_pnl
        self.commission = commission
        self.net_pnl = gross_pnl - commission
        self.real_pnl = gross_pnl
        self.position_size = 0.0  # Обнуляем при закрытии
        self.calculate_projected_pnls()  # 🔥 пересчёт после закрытия: real_pnl = gross_pnl
    
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
            "breakeven_projected_pnl": self.breakeven_projected_pnl,
            # 🔥 НОВОЕ: добавляем поля для частичных исполнений
            "target_size": self.target_size,
            "filled_qty": self.filled_qty,
            "remaining_order_qty": self.remaining_order_qty,
            "avg_price": self.avg_price,
            "real_pnl": self.real_pnl,
            "realized_pnl": self.realized_pnl,  # 🔥 ПУНКТ 5: сериализация накопленного PnL
            "tp1_pnl": self.tp1_pnl,
            "tp2_pnl": self.tp2_pnl,
            "sl_pnl": self.sl_pnl,            
        }