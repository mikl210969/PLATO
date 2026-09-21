"""
Менеджер паспортов — хранит все активные паспорта в памяти.
Только CRUD. Без логики изменения статусов.
"""

from typing import Dict, Optional, List
from core.types import PassportStatus
from trading.passport import TradePassport
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from core.logger import get_logger

class PassportManager:
    """Менеджер паспортов (кэш в памяти)."""

    def __init__(self, repository=None):
        self._passports: Dict[str, TradePassport] = {}
        self.repository = repository  # 🔥 Добавляем репозиторий для сохранения
        self.logger = get_logger(__name__)  # 🔥 Добавляем логгер

    def create(self, symbol: str, signal_id: str, strategy: str, side: str, entry_price: float, confidence: float = 0.5) -> TradePassport:
        """Создать новый паспорт."""
        passport = TradePassport(
            symbol=symbol,
            status=PassportStatus.SIGNAL_GENERATED.value,
            signal_id=signal_id,
            strategy=strategy,
            side=side,
            entry_price=entry_price,
            confidence=confidence,
            sl_price=0.0,
            tp1_price=0.0,
            tp2_price=0.0,
            position_size=0.0,
            position_entry_price=0.0
        )
        self._passports[passport.passport_id] = passport
        return passport

    def _save(self, passport):
        """🔥 Безопасное сохранение: repository может быть None."""
        if self.repository is not None:
            self.repository.save(passport)

    def get(self, passport_id: str) -> Optional[TradePassport]:
        """Получить паспорт по ID."""
        return self._passports.get(passport_id)

    def get_all(self) -> List[TradePassport]:
        """Получить все паспорта."""
        return list(self._passports.values())

    def get_active(self) -> List[TradePassport]:
        """Получить все активные паспорта."""
        return [p for p in self._passports.values() if p.status not in (PassportStatus.CLOSED.value, PassportStatus.CANCELED.value, PassportStatus.FAILED.value, "EXTERNAL_CLOSE")]
    def get_by_symbol(self, symbol: str) -> List[TradePassport]:
        """Получить все паспорта по символу."""
        return [p for p in self._passports.values() if p.symbol == symbol]

    def get_active_by_symbol(self, symbol: str) -> Optional[TradePassport]:
        """Получить активный паспорт по символу."""
        for p in self._passports.values():
            if p.symbol == symbol and p.status not in (PassportStatus.CLOSED.value, PassportStatus.CANCELED.value, PassportStatus.FAILED.value, "EXTERNAL_CLOSE"):
                return p
        return None

    def get_all_active_by_symbol(self, symbol: str) -> List[TradePassport]:
        """
        🔥 ШАГ 10.4.3: Получить ВСЕ активные паспорта по символу.
        Используется при реконсиляции для подсчёта суммы локальных позиций.
        """
        return [
            p for p in self._passports.values()
            if p.symbol == symbol and p.status not in (
                PassportStatus.CLOSED.value,
                PassportStatus.CANCELED.value,
                PassportStatus.FAILED.value,
                "EXTERNAL_CLOSE"
            )
        ]

    def is_symbol_busy(self, symbol: str) -> bool:
        """Проверить, занят ли символ."""
        is_busy = self.get_active_by_symbol(symbol) is not None
        
        # 🔥 КАНАРЕЙКА: Если менеджер говорит "свободно", мы заставим его признаться, что он видит внутри
        if not is_busy:
            all_for_symbol = [f"{p.passport_id} (статус: {p.status})" for p in self._passports.values() if p.symbol == symbol]
            # Используем print, чтобы это точно попало в консоль, даже если логгер настроен иначе
            print(f"⚠️ [PASSPORT_MANAGER DIAGNOSTIC] is_symbol_busy('{symbol}') вернул False.")
            print(f"   Паспорта для этого символа, которые менеджер ВИДИТ в памяти: {all_for_symbol}")
            if not all_for_symbol:
                print("   ⛔ ВНИМАНИЕ: Менеджер абсолютно пуст для этого символа! Паспорт создается, но не регистрируется в .create() или .update()")
            
        return is_busy

    def update(self, passport: TradePassport):
        """Обновить паспорт в кэше."""
        self._passports[passport.passport_id] = passport

    def remove(self, passport_id: str):
        """Удалить паспорт из менеджера."""
        if passport_id in self._passports:
            del self._passports[passport_id]

    def get_by_id(self, passport_id: str):
        """Получить паспорт по ID"""
        return self.get(passport_id)  # Используем существующий метод get

    async def apply_change(self, passport_id: str, change_type: str, payload: Dict[str, Any]) -> bool:
        """
        Единая точка изменения паспорта.
        """
        try:
            # 1. Находим паспорт
            passport = self.get(passport_id)
            if not passport:
                self.logger.error(f"❌ Passport {passport_id} not found for change {change_type}")
                return False
            
            # 2. Валидируем переход состояния
            # 🔥 ЗАЩИТА ОТ УСТАРЕВШИХ СОБЫТИЙ: Если паспорт уже закрыт, игнорируем попытки изменить торговые параметры
            if passport.status == "CLOSED" and change_type in [
                "SL_MOVED_TO_BREAKEVEN", "TP1_HIT", "TP2_HIT", "SL_HIT", "PARTIAL_CLOSE", "GUARD_REGISTERED"
            ]:
                self.logger.debug(f"⏭️ Игнорировано устаревшее событие {change_type} для уже закрытого паспорта {passport_id}")
                return True  # Возвращаем True, чтобы не ломать логику вызывающего кода (событие безопасно пропущено)

            if not self._validate_transition(passport.status, change_type):
                self.logger.error(f"❌ Invalid transition: {passport.status} -> {change_type}")
                return False
            
            # 3. Применяем изменение через handler
            handler = self._get_handler(change_type)
            if not handler:
                self.logger.error(f"❌ No handler for {change_type}")
                return False
            
            handler(passport, payload)
            
            # 4. Округляем числа по единым правилам
            self._round_passport_values(passport)
            
            # 5. Добавляем запись в timeline
            self._add_timeline_event(passport, change_type, payload)
            
            # 6. Сохраняем на диск
            self.update(passport)
            if self.repository:
                self.repository.save(passport)
            
            # 7. Логируем изменение (🔥 ИСПРАВЛЕНО: убран аргумент extra)
            self.logger.info(f"✅ Passport {passport_id} changed: {change_type} | Data: {payload}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error applying change {change_type} to {passport_id}: {e}")
            return False
    
    # Валидация переходов состояний
    def _validate_transition(self, current_status: str, change_type: str) -> bool:
        """Проверяет, можно ли применить изменение к текущему статусу"""
        valid_transitions = {
            "ORDER_SENT": ["SIGNAL_GENERATED"],
            "ORDER_FILLED": ["ORDER_SENT"],
            "ORDER_CANCELLED": ["ORDER_SENT", "OPEN"],
            "GUARD_REGISTERED": ["OPEN"],
            "TP1_HIT": ["OPEN"],
            "TP2_HIT": ["OPEN", "PARTIAL_CLOSE"],
            "SL_HIT": ["OPEN", "PARTIAL_CLOSE"],
            "SL_MOVED_TO_BREAKEVEN": ["OPEN", "PARTIAL_CLOSE"],
            "PARTIAL_CLOSE": ["OPEN"],
            "EXTERNAL_CLOSE": ["OPEN", "PARTIAL_CLOSE"],
            "RECOVERY_OPEN": ["ORDER_SENT", "OPEN"]
        }
        
        allowed_from = valid_transitions.get(change_type, [])
        return current_status in allowed_from or current_status == "UNKNOWN"
    
    # Handlers для каждого типа изменения
    def _get_handler(self, change_type: str):
        """Возвращает handler для типа изменения"""
        handlers = {
            "ORDER_SENT": self._handle_order_sent,
            "ORDER_FILLED": self._handle_order_filled,
            "ORDER_CANCELLED": self._handle_order_cancelled,
            "GUARD_REGISTERED": self._handle_guard_registered,
            "TP1_HIT": self._handle_tp1_hit,
            "TP2_HIT": self._handle_tp2_hit,
            "SL_HIT": self._handle_sl_hit,
            "SL_MOVED_TO_BREAKEVEN": self._handle_sl_moved_to_breakeven,
            "PARTIAL_CLOSE": self._handle_partial_close,
            "EXTERNAL_CLOSE": self._handle_external_close,
            "RECOVERY_OPEN": self._handle_recovery_open
        }
        return handlers.get(change_type)
    
    # Конкретные handlers
    def _handle_order_sent(self, passport, payload: Dict):
        passport.status = "ORDER_SENT"
        passport.orders.append({
            "order_id": payload.get("order_id"),
            "client_order_id": payload.get("client_order_id"),
            "status": "NEW",
            "type": payload.get("order_type", "limit"),
            "side": passport.side,
            "price": payload.get("price", passport.entry_price),
            "quantity": payload.get("quantity", passport.position_size)
        })
    
    def _handle_order_filled(self, passport, payload: Dict):
        passport.status = "OPEN"
        passport.position_size = abs(payload.get("quantity", passport.position_size))
        # 🔥 ZERO-GUARD: .get(key, default) возвращает 0, если ключ ЕСТЬ и равен 0
        _px = float(payload.get("price", 0) or 0)
        if _px > 0:
            passport.position_entry_price = _px
        
        # 🔥 ДОБАВИТЬ ЭТИ ДВЕ СТРОКИ ЗДЕСЬ:
        passport.calculate_projected_pnls()
        self._save(passport)  # Сохраняем сразу после расчета
    
    def _handle_order_cancelled(self, passport, payload: Dict):
        passport.status = "CANCELLED"
    
    def _handle_guard_registered(self, passport, payload: Dict):
        # 🔥 ЖЕСТКАЯ ЗАЩИТА ОТ ДУБЛЕЙ: если guard уже зарегистрирован, игнорируем повторные вызовы
        if getattr(passport, '_guard_registered', False):
            self.logger.debug(f"⏭️ Игнорирован дубликат GUARD_REGISTERED для {passport.passport_id}")
            return
            
        passport.sl_price = round(payload.get("sl_price", 0), 8)
        passport.tp1_price = round(payload.get("tp1_price", 0), 8)
        passport.tp2_price = round(payload.get("tp2_price", 0), 8)
        
        # Ставим флаг, что регистрация прошла успешно
        passport._guard_registered = True

    def _handle_sl_hit(self, passport, payload: Dict):
        """
        🔥 SL_HIT: слой данных. Вся работа с биржей (отмена входного ордера)
        выполняется в async-слое risk_manager ДО этого вызова.
        transition_to("CLOSED") обнуляет guard_status и пишет STATUS в timeline.
        """
        # 🔥 ИДЕМПОТЕНТНОСТЬ: если паспорт уже CLOSED, не пересчитываем PnL
        # (защита от двойного вызова через state_manager и apply_change)
        if passport.status == "CLOSED":
            return
        
        passport.sl_activated = True

        # 🔥 1. Факт закрытого количества берём из payload (результат market-ордера)
        # и синхронизируем filled_qty — паспорт не должен отставать от биржи
        closed_qty = (
            abs(float(payload.get("closed_qty", 0) or 0))
            or abs(float(passport.filled_qty or 0))
            or abs(float(passport.position_size or 0))
        )
        if closed_qty > 0:
            passport.filled_qty = closed_qty

        # 🔥 2. ЗАЩИТА ОТ exit_price = 0.0
        exit_price = payload.get("price", 0)
        if not exit_price or exit_price <= 0:
            exit_price = passport.sl_price or passport.position_entry_price or passport.entry_price
            self.logger.warning(f"⚠️ SL_HIT: exit_price=0, используем fallback: {exit_price}")
        passport.exit_price = round(float(exit_price), 8)

        # 🔥 3. PnL из фактических данных
        # 🔥 ZERO-GUARD входа: нулевая position_entry_price давала фантом -758.1
        _entry_px = float(passport.position_entry_price or passport.avg_price or passport.entry_price or 0)
        # 🔥 ZERO-GUARD входа: цепочка position_entry_price → avg_price → entry_price
        _entry_px = float(passport.position_entry_price or passport.avg_price or passport.entry_price or 0)
        if passport.side == "short":
            gross_pnl = (_entry_px - passport.exit_price) * closed_qty
        else:
            gross_pnl = (passport.exit_price - _entry_px) * closed_qty

        # 🔥 ПУНКТ 5: финальный PnL включает накопленное частичными закрытиями
        passport.sl_pnl = round(float(getattr(passport, 'sl_pnl', 0) or 0) + round(gross_pnl, 2), 2)
        gross_pnl = round(gross_pnl + float(getattr(passport, 'realized_pnl', 0) or 0), 2)

        # 🔥 ДИАГНОСТИКА: логируем все значения в момент расчёта PnL
        self.logger.info(
            f"🧮 [SL_HIT PnL] {passport.passport_id} | entry={passport.position_entry_price} | "
            f"exit={passport.exit_price} | qty={closed_qty} | side={passport.side} | "
            f"formula=({passport.position_entry_price} - {passport.exit_price}) * {closed_qty}"
        )
        
        passport.gross_pnl = round(gross_pnl, 2)

        passport.gross_pnl = round(gross_pnl, 2)
        passport.commission = round(payload.get("commission", 0) or getattr(passport, 'commission', 0), 2)
        passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
        passport.real_pnl = passport.gross_pnl  # 🔥 FIX: real_pnl у закрытого паспорта

        # 🔥 4. Обнуляем размер и переходим в CLOSED через transition_to
        passport.position_size = 0.0
        passport.exit_reason = "SL_HIT"
        passport.transition_to("CLOSED", "SL_HIT")
        passport.closed_at = datetime.now(timezone.utc).isoformat()

        self.logger.info(
            f"✅ SL_HIT complete: {passport.passport_id} | qty={closed_qty} | "
            f"exit={passport.exit_price} | PnL={passport.gross_pnl}"
        )

    def _handle_sl_moved_to_breakeven(self, passport, payload: Dict):
        """Перенос SL в безубыток после TP1."""
        new_sl = payload.get("price", 0)
        if new_sl > 0:
            passport.sl_price = round(float(new_sl), 8)
            self.logger.info(
                f"🛡️ [SL BREAKEVEN] {passport.passport_id} | new_sl={new_sl} | "
                f"reason={payload.get('reason', '')}"
            )


    def _handle_tp1_hit(self, passport, payload: Dict):
        # 🔥 ИДЕМПОТЕНТНОСТЬ: повторное событие по уже закрытому паспорту не пересчитывает деньги
        if passport.status == "CLOSED":
            return
        passport.tp1_activated = True
        closed_qty = abs(payload.get("closed_qty", 0))
        
        # 🔥 БЕЗОПАСНОЕ ВЫЧИТАНИЕ С ИСПОЛЬЗОВАНИЕМ abs()
        current_size = abs(passport.position_size or 0)
        passport.position_size = round(max(0.0, current_size - closed_qty), 4)

        # 🔥 Накопление реализованного PnL закрытой части
        _exit = float(payload.get("price", 0) or 0) or float(passport.tp1_price or 0)
        _entry = float(passport.position_entry_price or passport.entry_price or 0)
        if _exit > 0 and _entry > 0 and closed_qty > 0:
            _part = (_entry - _exit) * closed_qty if passport.side == "short" else (_exit - _entry) * closed_qty
            passport.realized_pnl = round(float(getattr(passport, 'realized_pnl', 0) or 0) + _part, 2)
            passport.tp1_pnl = round(float(getattr(passport, 'tp1_pnl', 0) or 0) + _part, 2)

        if passport.position_size > 0:
            passport.status = "PARTIAL_CLOSE"
            be = payload.get("sl_breakeven") or passport.position_entry_price or passport.entry_price
            passport.sl_price = round(be, 8)
        else:
            # Если закрыли всё
            passport.position_size = 0.0
            passport.status = "CLOSED"
            passport.exit_reason = "TP1_HIT"
            
            exit_price = payload.get("price", 0) or passport.sl_price or passport.position_entry_price
            passport.exit_price = round(exit_price, 8)
            
            # 🔥 ZERO-GUARD входа
            _entry_px = float(passport.position_entry_price or passport.avg_price or passport.entry_price or 0)
            if passport.side == "short":
                gross_pnl = (_entry_px - exit_price) * closed_qty
            else:
                gross_pnl = (exit_price - _entry_px) * closed_qty

            # 🔥 ПУНКТ 5: финальный PnL включает накопленное частичными закрытиями
            passport.tp1_pnl = round(float(getattr(passport, 'tp1_pnl', 0) or 0) + round(gross_pnl, 2), 2)
            gross_pnl = round(gross_pnl + float(getattr(passport, 'realized_pnl', 0) or 0), 2)
            passport.gross_pnl = round(gross_pnl, 2)
            passport.net_pnl = round(passport.gross_pnl - (getattr(passport, 'commission', 0) or 0), 2)
            passport.closed_at = datetime.now(timezone.utc).isoformat()

    def _handle_tp2_hit(self, passport, payload: Dict):
        # 🔥 ИДЕМПОТЕНТНОСТЬ: повторное событие по уже закрытому паспорту не пересчитывает деньги
        if passport.status == "CLOSED":
            return
        passport.tp1_activated = True
        passport.tp2_activated = True
        
        closed_qty = abs(payload.get("closed_qty", 0)) or abs(passport.position_size)
        passport.position_size = 0.0
        passport.status = "CLOSED"
        passport.exit_reason = "TP2_HIT"
        
        exit_price = payload.get("price", 0)
        if not exit_price or exit_price <= 0:
            exit_price = passport.sl_price or passport.position_entry_price or passport.entry_price
            self.logger.warning(f"⚠️ TP2_HIT: exit_price=0, используем fallback: {exit_price}")
            
        passport.exit_price = round(exit_price, 8)
        
        # 🔥 ZERO-GUARD входа
        _entry_px = float(passport.position_entry_price or passport.avg_price or passport.entry_price or 0)
        if passport.side == "short":
            gross_pnl = (_entry_px - exit_price) * closed_qty
        else:
            gross_pnl = (exit_price - _entry_px) * closed_qty

        # 🔥 ПУНКТ 5: финальный PnL включает накопленное частичными закрытиями
        passport.tp2_pnl = round(float(getattr(passport, 'tp2_pnl', 0) or 0) + round(gross_pnl, 2), 2)
        gross_pnl = round(gross_pnl + float(getattr(passport, 'realized_pnl', 0) or 0), 2)

        passport.gross_pnl = round(gross_pnl, 2)
        passport.commission = round(payload.get("commission", 0) or getattr(passport, 'commission', 0), 2)
        passport.net_pnl = round(passport.gross_pnl - passport.commission, 2)
        passport.closed_at = datetime.now(timezone.utc).isoformat()
    
    def _handle_partial_close(self, passport, payload: Dict):
        closed_qty = payload.get("closed_qty", 0)
        exit_price = payload.get("exit_price", 0)
        exit_reason = payload.get("exit_reason", "MANUAL_PARTIAL")
        
        passport.position_size = max(0.0, passport.position_size - closed_qty)

        _exit = float(exit_price or 0) or float(passport.tp1_price or 0)
        _entry = float(passport.position_entry_price or passport.entry_price or 0)
        if _exit > 0 and _entry > 0 and closed_qty > 0:
            _part = (_entry - _exit) * closed_qty if passport.side == "short" else (_exit - _entry) * closed_qty
            passport.realized_pnl = round(float(getattr(passport, 'realized_pnl', 0) or 0) + _part, 2)

        # Если закрытие было по TP1, переносим SL в безубыток
        if exit_reason == "TP1_HIT":
            passport.sl_price = passport.position_entry_price
            passport.tp1_activated = True
            
        # 🔥 НОВОЕ: Пересчитываем проектный PnL для оставшегося объема!
        passport.calculate_projected_pnls()
        
        if passport.position_size <= 0.01:
            passport.status = "CLOSED"
            passport.exit_reason = exit_reason
            passport.exit_price = exit_price
            passport.closed_at = datetime.now(timezone.utc).isoformat()
        else:
            passport.status = "PARTIAL_CLOSE"
    
    def _handle_external_close(self, passport, payload: Dict):
        passport.status = "CLOSED"
        passport.exit_reason = "EXTERNAL_CLOSE"
        
        exit_price = payload.get("exit_price", 0)
        gross_pnl = payload.get("gross_pnl", 0)
        commission = payload.get("commission", 0)
        
        # 🔥 ИСПРАВЛЕНИЕ: Если биржа не прислала цену и PnL, считаем сами
        if not exit_price or exit_price <= 0:
            # Используем entry_price как fallback (безубыток), чтобы не писать 0
            exit_price = passport.position_entry_price
            self.logger.warning(f"️ External close: exit_price missing, fallback to entry_price {exit_price}")
            
        if not gross_pnl or gross_pnl == 0:
            # 🔥 Используем наш встроенный калькулятор, который простаивал!
            gross_pnl = self._calculate_pnl(passport, exit_price, passport.position_size)
            
        passport.exit_price = round(exit_price, 8)
        passport.gross_pnl = round(gross_pnl, 2)
        passport.commission = commission
        passport.net_pnl = round(passport.gross_pnl - commission, 2)
        passport.position_size = 0
        passport.closed_at = datetime.now(timezone.utc).isoformat()
    
    def _handle_recovery_open(self, passport, payload: Dict):
        """Восстановление позиции при старте или дрейфе."""
        passport.status = "OPEN"
        passport.position_size = abs(payload.get("position_size", passport.position_size))
        passport.position_entry_price = payload.get("entry_price", passport.entry_price)
        
        # При восстановлении считаем проектный PnL
        passport.calculate_projected_pnls()
        
        # Активируем Guard только если платформа не в состоянии слепоты
        if getattr(passport, 'platform_health', 'HEALTHY') != 'BLIND':
            passport.guard_status = "active"
        
        self._save(passport)
    
    # Утилиты
    def _calculate_pnl(self, passport, exit_price: float, qty: float) -> float:
        if passport.side == "short":
            return (passport.position_entry_price - exit_price) * qty
        else:
            return (exit_price - passport.position_entry_price) * qty
    
    def _round_passport_values(self, passport):
        """Округляет числа по единым правилам"""
        # Цена: 8 знаков
        if passport.exit_price:
            passport.exit_price = round(passport.exit_price, 8)
        if passport.position_entry_price:
            passport.position_entry_price = round(passport.position_entry_price, 8)
        if passport.sl_price:
            passport.sl_price = round(passport.sl_price, 8)
        if passport.tp1_price:
            passport.tp1_price = round(passport.tp1_price, 8)
        if passport.tp2_price:
            passport.tp2_price = round(passport.tp2_price, 8)
        
        # PnL: 2 знака
        if passport.gross_pnl:
            passport.gross_pnl = round(passport.gross_pnl, 2)
        if passport.net_pnl:
            passport.net_pnl = round(passport.net_pnl, 2)
        if passport.commission:
            passport.commission = round(passport.commission, 2)
        
        # Размер позиции: 4 знака
        if passport.position_size:
            passport.position_size = round(passport.position_size, 4)
    
    def _add_timeline_event(self, passport, change_type: str, payload: Dict):
        """Добавляет запись в timeline"""
        event_descriptions = {
            "ORDER_SENT": f"Order sent to exchange",
            "ORDER_FILLED": f"Order filled",
            "ORDER_CANCELLED": f"Order cancelled",
            "GUARD_REGISTERED": f"Guard registered. SL: {payload.get('sl_price', 0)}, TP1: {payload.get('tp1_price', 0)}, TP2: {payload.get('tp2_price', 0)}",
            "TP1_HIT": f"Closed {payload.get('closed_qty', 0)}, SL moved to breakeven: {payload.get('sl_breakeven', 0)}",
            "TP2_HIT": f"Fully closed {payload.get('closed_qty', 0)}",
            "SL_HIT": f"Fully closed {payload.get('closed_qty', 0)} at SL",
            "PARTIAL_CLOSE": f"Partial close: {payload.get('closed_qty', 0)}",
            "EXTERNAL_CLOSE": f"Position closed manually or liquidated on exchange",
            "RECOVERY_OPEN": f"Position recovered from exchange"
        }
        
        passport.timeline.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": change_type,
            "details": event_descriptions.get(change_type, change_type)
        })

        return None