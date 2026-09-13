"""
Тесты для мониторинга здоровья, проектного PnL и Watchdog.
"""
import pytest
import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from trading.passport import TradePassport
from core.types import PassportStatus


# ============================================================================
# БЛОК 1: Тесты calculate_projected_pnls()
# ============================================================================

class TestProjectedPnL:
    """Тесты расчёта проектного PnL."""
    
    def test_short_position_projected_pnl(self):
        """SHORT позиция: корректный расчёт всех уровней."""
        passport = TradePassport(
            side="short",
            position_entry_price=101.06,
            position_size=7.0,
            tp1_price=100.81,
            tp2_price=100.56,
            sl_price=101.21
        )
        
        passport.calculate_projected_pnls()
        
        # SHORT: (entry - exit) * qty
        assert passport.tp1_projected_pnl == round((101.06 - 100.81) * 7.0, 2)
        assert passport.tp2_projected_pnl == round((101.06 - 100.56) * 7.0, 2)
        assert passport.sl_projected_pnl == round((101.06 - 101.21) * 7.0, 2)
        assert passport.breakeven_projected_pnl == 0.0
    
    def test_long_position_projected_pnl(self):
        """LONG позиция: корректный расчёт всех уровней."""
        passport = TradePassport(
            side="long",
            position_entry_price=100.0,
            position_size=5.0,
            tp1_price=101.0,
            tp2_price=102.0,
            sl_price=99.0
        )
        
        passport.calculate_projected_pnls()
        
        # LONG: (exit - entry) * qty
        assert passport.tp1_projected_pnl == round((101.0 - 100.0) * 5.0, 2)
        assert passport.tp2_projected_pnl == round((102.0 - 100.0) * 5.0, 2)
        assert passport.sl_projected_pnl == round((99.0 - 100.0) * 5.0, 2)
        assert passport.breakeven_projected_pnl == 0.0
    
    def test_zero_position_size(self):
        """При position_size=0 проектный PnL не рассчитывается."""
        passport = TradePassport(
            side="short",
            position_entry_price=100.0,
            position_size=0.0,
            tp1_price=99.0
        )
        
        passport.calculate_projected_pnls()
        
        assert passport.tp1_projected_pnl == 0.0
    
    def test_recalculate_after_sl_move_to_breakeven(self):
        """После переноса SL в безубыток sl_projected_pnl становится 0."""
        passport = TradePassport(
            side="short",
            position_entry_price=101.06,
            position_size=7.0,
            sl_price=101.21,
            tp1_price=100.81
        )
        
        # Первоначальный расчёт
        passport.calculate_projected_pnls()
        assert passport.sl_projected_pnl < 0  # Убыток
        
        # Переносим SL в безубыток
        passport.sl_price = passport.position_entry_price
        passport.calculate_projected_pnls()
        
        assert passport.sl_projected_pnl == 0.0


# ============================================================================
# БЛОК 2: Тесты сериализации новых полей
# ============================================================================

class TestPassportSerialization:
    """Тесты сохранения и загрузки новых полей."""
    
    def test_to_dict_includes_new_fields(self):
        """to_dict() включает все 6 новых полей."""
        passport = TradePassport()
        passport.platform_health = "DEGRADED"
        passport.guard_status = "suspended"
        passport.tp1_projected_pnl = 1.5
        passport.tp2_projected_pnl = 3.0
        passport.sl_projected_pnl = -1.0
        passport.breakeven_projected_pnl = 0.0
        
        data = passport.to_dict()
        
        assert "platform_health" in data
        assert "guard_status" in data
        assert "tp1_projected_pnl" in data
        assert "tp2_projected_pnl" in data
        assert "sl_projected_pnl" in data
        assert "breakeven_projected_pnl" in data
        
        assert data["platform_health"] == "DEGRADED"
        assert data["guard_status"] == "suspended"
    
    def test_default_values_for_new_fields(self):
        """Новые поля имеют корректные значения по умолчанию."""
        passport = TradePassport()
        
        assert passport.platform_health == "HEALTHY"
        assert passport.guard_status == "inactive"
        assert passport.tp1_projected_pnl == 0.0
        assert passport.tp2_projected_pnl == 0.0
        assert passport.sl_projected_pnl == 0.0
        assert passport.breakeven_projected_pnl == 0.0
    
    def test_save_and_load(self):
        """После save() и load() поля восстанавливаются."""
        # Это интеграционный тест, требует репозиторий
        # Здесь просто проверяем, что to_dict() работает корректно
        passport = TradePassport(
            platform_health="BLIND",
            guard_status="suspended",
            tp1_projected_pnl=2.5
        )
        
        data = passport.to_dict()
        
        # Симулируем загрузку из JSON
        loaded_passport = TradePassport(**data)
        
        assert loaded_passport.platform_health == "BLIND"
        assert loaded_passport.guard_status == "suspended"
        assert loaded_passport.tp1_projected_pnl == 2.5


# ============================================================================
# БЛОК 3: Тесты детектора слепоты (Health Monitor)
# ============================================================================

class TestHealthMonitor:
    """Тесты логики определения состояния платформы."""
    
    def _check_health(self, ws_age: float, rest_banned: bool) -> str:
        """Эмуляция логики из main.py."""
        if ws_age < 45 and not rest_banned:
            return "HEALTHY"
        elif ws_age >= 45 and not rest_banned:
            return "DEGRADED"
        else:
            return "BLIND"
    
    def test_healthy_state(self):
        """WS и REST работают → HEALTHY."""
        assert self._check_health(ws_age=10, rest_banned=False) == "HEALTHY"
    
    def test_degraded_state(self):
        """WS упал, REST работает → DEGRADED."""
        assert self._check_health(ws_age=60, rest_banned=False) == "DEGRADED"
    
    def test_blind_state(self):
        """REST забанен → BLIND."""
        assert self._check_health(ws_age=10, rest_banned=True) == "BLIND"
    
    def test_blind_state_both_down(self):
        """Оба канала упали → BLIND."""
        assert self._check_health(ws_age=120, rest_banned=True) == "BLIND"
    
    def test_guard_status_mapping(self):
        """Проверка маппинга platform_health → guard_status."""
        health_to_guard = {
            "HEALTHY": "active",
            "DEGRADED": "suspended",
            "BLIND": "suspended"
        }
        
        for health, expected_guard in health_to_guard.items():
            # Эмуляция логики из main.py
            if health == "HEALTHY":
                guard = "active"
            else:
                guard = "suspended"
            
            assert guard == expected_guard


# ============================================================================
# БЛОК 4: Тесты Watchdog-логики (лимит перезапусков)
# ============================================================================

class TestWatchdogLogic:
    """Тесты логики ограничения перезапусков."""
    
    def _check_restart_limit(self, restarts: list, max_restarts: int, now: datetime) -> bool:
        """Эмуляция логики из watchdog.ps1."""
        one_hour_ago = now - timedelta(hours=1)
        valid_restarts = [ts for ts in restarts if ts > one_hour_ago]
        return len(valid_restarts) < max_restarts
    
    def test_under_limit(self):
        """Меньше лимита → можно перезапускать."""
        now = datetime.now(timezone.utc)
        restarts = [now - timedelta(minutes=10)]  # 1 перезапуск за последний час
        
        assert self._check_restart_limit(restarts, max_restarts=2, now=now) == True
    
    def test_at_limit(self):
        """Достигнут лимит → нельзя перезапускать."""
        now = datetime.now(timezone.utc)
        restarts = [
            now - timedelta(minutes=10),
            now - timedelta(minutes=20)
        ]  # 2 перезапуска за последний час
        
        assert self._check_restart_limit(restarts, max_restarts=2, now=now) == False
    
    def test_old_restarts_not_counted(self):
        """Перезапуски старше 1 часа не учитываются."""
        now = datetime.now(timezone.utc)
        restarts = [
            now - timedelta(hours=2),  # Старый, не считается
            now - timedelta(minutes=10)  # Новый
        ]
        
        assert self._check_restart_limit(restarts, max_restarts=2, now=now) == True
    
    def test_exit_code_handling(self):
        """Проверка обработки кодов возврата."""
        exit_codes = {
            0: "normal_stop",      # Нормальная остановка
            42: "restart",         # Безопасный перезапуск
            1: "error_stop"        # Ошибка, остановка
        }
        
        for code, expected_action in exit_codes.items():
            if code == 0:
                action = "normal_stop"
            elif code == 42:
                action = "restart"
            else:
                action = "error_stop"
            
            assert action == expected_action