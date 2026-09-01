#!/usr/bin/env python3
"""
PLATO Scenario Simulator - тестирование конкретных сценариев
Запуск: python test_scenarios.py
"""

import asyncio
from pathlib import Path
from core.event_bus import EventBus
from core.config_loader import ConfigLoader
from trading.passport_manager import PassportManager

class ScenarioSimulator:
    def __init__(self):
        self.bus = EventBus()
        self.config = ConfigLoader().load_all()
        self.passport_manager = PassportManager()
    
    async def test_ttl_with_live_position(self):
        """Сценарий: TTL истёк, но позиция жива на бирже"""
        print("\n🧪 ТЕСТ: TTL с живой позицией")
        print("=" * 60)
        
        # Создаём паспорт с позицией 7.0
        passport = self.passport_manager.create(
            symbol="SOLUSDT",
            signal_id="TEST_001",
            strategy="Test",
            side="long",
            entry_price=100.0,
            confidence=0.8
        )
        
        if not passport:
            print("❌ FAIL: Не удалось создать паспорт")
            return False
        
        passport.position_size = 7.0
        passport.status = "OPEN"
        
        # Публикуем TTL_EXPIRED
        await self.bus.publish(
            event_type="TTL_EXPIRED",
            source="test",
            payload={"passport_id": passport.passport_id, "symbol": "SOLUSDT"},
            symbol="SOLUSDT"
        )
        
        # Проверяем: паспорт должен остаться OPEN (защита сработала)
        updated = self.passport_manager.get(passport.passport_id)
        if not updated:
            print("❌ FAIL: Паспорт не найден после TTL")
            return False
        
        if updated.status == "OPEN":
            print("✅ PASS: Паспорт остался OPEN (защита работает)")
            return True
        else:
            print(f"❌ FAIL: Паспорт закрыт! Статус: {updated.status}")
            return False
    
    async def test_partial_fill_reconciliation(self):
        """Сценарий: Частичное исполнение + сверка с биржей"""
        print("\n🧪 ТЕСТ: Частичное исполнение")
        print("=" * 60)
        
        # Создаём паспорт
        passport = self.passport_manager.create(
            symbol="SOLUSDT",
            signal_id="TEST_002",
            strategy="Test",
            side="long",
            entry_price=100.0,
            confidence=0.8
        )
        
        if not passport:
            print("❌ FAIL: Не удалось создать паспорт")
            return False
        
        # Симулируем FILLED с executed_qty=7.0
        await self.bus.publish(
            event_type="ORDER_FILLED",
            source="test",
            payload={
                "passport_id": passport.passport_id,
                "executed_qty": 7.0,
                "avg_price": 100.5
            },
            symbol="SOLUSDT"
        )
        
        # Проверяем: position_size должен быть 7.0
        updated = self.passport_manager.get(passport.passport_id)
        if not updated:
            print("❌ FAIL: Паспорт не найден после FILLED")
            return False
        
        if updated.position_size == 7.0:
            print("✅ PASS: Размер позиции корректен")
            return True
        else:
            print(f"❌ FAIL: Размер позиции {updated.position_size}, ожидалось 7.0")
            return False
    
    async def run_all(self):
        results = []
        results.append(await self.test_ttl_with_live_position())
        results.append(await self.test_partial_fill_reconciliation())
        
        passed = sum(results)
        total = len(results)
        
        print(f"\n{'='*60}")
        print(f"📊 РЕЗУЛЬТАТ: {passed}/{total} тестов пройдено")
        
        if passed == total:
            print("🎉 ВСЕ СЦЕНАРИИ РАБОТАЮТ!\n")
        else:
            print("⚠️  ЕСТЬ ПРОБЛЕМЫ!\n")

if __name__ == "__main__":
    asyncio.run(ScenarioSimulator().run_all())