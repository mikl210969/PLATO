#!/usr/bin/env python3
"""
Запуск платформы на тестовом стенде.
Сценарий 1: открытие → TP1 → безубыток → SL.
Сценарий 2: ручное закрытие на бирже при мёртвом стриме → SYNC_REQUEST → паспорт CLOSED.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.event_bus import EventBus
from core.types import Signal, PassportStatus
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.state_manager import StateManager
from trading.trader import Trader
from trading.orchestrator import Orchestrator
from trading.risk_manager import RiskManager
from core.json_logger import JsonLogger
from test_stand import MockBinanceRestClient, MockBinanceWsAdapter

RESULTS = []


def check(name: str, cond: bool, extra: str = ""):
    status = "✅" if cond else "❌"
    print(f"   {status} {name}" + (f" ({extra})" if extra else ""))
    RESULTS.append(bool(cond))


class TestPlatform:
    """Тестовая платформа на моках."""

    def __init__(self):
        self.bus = EventBus()
        self.passport_manager = PassportManager()
        self.passport_repository = PassportRepository()
        self.json_logger = JsonLogger(enabled=True)
        self._running = True

        self.rest = MockBinanceRestClient()
        self.ws = MockBinanceWsAdapter()

        self.state_manager = StateManager(self.passport_manager)

        self.orchestrator = Orchestrator(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            passport_repository=self.passport_repository,
            state_manager=self.state_manager,
            config={'trading': {'lot_size': 7.0, 'entry_order_type': 'market', 'atr_value': 0.5}},
            json_logger=self.json_logger
        )

        self.trader = Trader(
            symbol='SOLUSDT',
            rest_client=self.rest,
            ws_adapter=self.ws,
            event_bus=self.bus,
            config={'trading': {'lot_size': 7.0, 'entry_order_type': 'market', 'atr_value': 0.5}}
        )
        self.orchestrator.register_trader('SOLUSDT', self.trader)

        self.risk_manager = RiskManager(
            event_bus=self.bus,
            passport_manager=self.passport_manager,
            trader=self.trader,
            config={},
            json_logger=self.json_logger
        )
        self.orchestrator.set_risk_manager(self.risk_manager)

        print("✅ [TEST] Platform initialized")

    async def _process_events(self):
        """Обработка событий от мока биржи."""
        while self._running:
            events = self.rest.get_pending_events()
            for evt in events:
                if evt['type'] == 'ORDER_TRADE_UPDATE':
                    await self.bus.publish(
                        event_type="ORDER_TRADE_UPDATE",
                        source="mock_rest",
                        payload=evt['data'],
                        symbol="SOLUSDT"
                    )
                elif evt['type'] == 'ACCOUNT_UPDATE':
                    await self.bus.publish(
                        event_type="ACCOUNT_UPDATE",
                        source="mock_rest",
                        payload=evt['data'],
                        symbol="SOLUSDT"
                    )
            await asyncio.sleep(0.1)

    async def send_signal(self, signal):
        await self.bus.publish(
            event_type="SIGNAL_GENERATED",
            source="test",
            payload={"signal": signal},
            symbol="SOLUSDT"
        )

    async def send_price(self, price: float):
        """Эмитировать тик цены (как depthUpdate в main.py)."""
        await self.bus.publish(
            event_type="PRICE_UPDATE",
            source="test",
            payload={'symbol': 'SOLUSDT', 'price': price, 'ts': time.time()},
            symbol="SOLUSDT"
        )

    async def run(self):
        print("🚀 [TEST] Starting test platform...")
        await self.orchestrator.start()
        asyncio.create_task(self._process_events())
        await asyncio.sleep(1)

    async def stop(self):
        self._running = False
        await self.orchestrator.stop()


async def test_scenario():
    """Сценарий 1: открытие → TP1 → безубыток → SL."""
    platform = TestPlatform()
    await platform.run()

    print("\n" + "=" * 60)
    print("🧪 СЦЕНАРИЙ 1: Внутренний стоп (TP1 → безубыток → SL)")
    print("=" * 60)

    # ===== 1. Открытие позиции =====
    print("\n📊 Отправка сигнала SHORT @ 76.00...")
    await platform.send_signal(Signal(
        signal_id="TEST_001",
        symbol="SOLUSDT",
        strategy="WallFade",
        side="short",
        entry_price=76.00,
        confidence=0.7
    ))
    await asyncio.sleep(1)

    pos = await platform.rest.get_position("SOLUSDT")
    print(f"📊 Позиция после входа: size={pos['size']}")

    guards = platform.risk_manager._guards
    check("Позиция открыта (-7.0)", abs(pos['size'] + 7.0) < 1e-6)
    check("Guard зарегистрирован", len(guards) == 1)

    if not guards:
        print("❌ Guard не создан — дальнейшие проверки бессмысленны")
        await platform.stop()
        return

    guard = list(guards.values())[0]
    passport_id = guard['passport_id']
    print(f"🛡️ Guard: tp1={guard['tp1_price']}, tp2={guard['tp2_price']}, sl={guard['sl_price']}, remaining={guard['remaining']}")

    check("Уровень TP1 = 75.0", abs(guard['tp1_price'] - 75.0) < 1e-6)
    check("Уровень SL = 76.75", abs(guard['sl_price'] - 76.75) < 1e-6)
    check("На бирже НЕТ защитных лимиток",
          not any(o.order_type == 'LIMIT' for o in platform.rest._orders.values()))

    # ===== 2. Цена идёт к TP1 =====
    print("\n📉 Тик цены: 74.90 (ниже TP1=75.0) → ожидаем tp1_triggered...")
    await platform.send_price(74.90)
    await asyncio.sleep(1)

    pos = await platform.rest.get_position("SOLUSDT")
    passport = platform.passport_manager.get(passport_id)
    print(f"📊 Позиция после TP1: size={pos['size']}")

    check("TP1: позиция уменьшена до -3.5", abs(pos['size'] + 3.5) < 1e-6)
    check("TP1: флаг tp1_done=True", guard['tp1_done'] is True)
    check("TP1: remaining=3.5 в guard", abs(guard['remaining'] - 3.5) < 1e-6)
    check("SL перенесён в безубыток (76.0) в guard", abs(guard['sl_price'] - 76.0) < 1e-6)
    check("SL перенесён в безубыток в паспорте", passport is not None and abs(passport.sl_price - 76.0) < 1e-6)
    check("Market-закрытие TP1 отправлено (CLOSE_TP1_HIT_...)",
          any(c.startswith('CLOSE_TP1_HIT') for c in platform.rest._orders.keys()))

    # ===== 3. Цена идёт к SL (безубыток) =====
    print("\n📈 Тик цены: 76.10 (выше SL=76.0) → ожидаем sl_triggered...")
    await platform.send_price(76.10)
    await asyncio.sleep(1)

    pos = await platform.rest.get_position("SOLUSDT")
    passport = platform.passport_manager.get(passport_id)
    print(f"📊 Позиция после SL: size={pos['size']}")

    check("SL: позиция закрыта (0.0)", abs(pos['size']) < 1e-6)
    check("SL: market-закрытие отправлено (CLOSE_SL_HIT_...)",
          any(c.startswith('CLOSE_SL_HIT') for c in platform.rest._orders.keys()))
    check("Guard удалён", len(platform.risk_manager._guards) == 0)
    check("Паспорт CLOSED", passport is not None and passport.status == PassportStatus.CLOSED.value)

    await platform.stop()


async def test_sync_scenario():
    """Сценарий 2: ручное закрытие на бирже при мёртвом стриме → SYNC_REQUEST → паспорт CLOSED."""
    platform = TestPlatform()
    await platform.run()

    print("\n" + "=" * 60)
    print("🧪 СЦЕНАРИЙ 2: SYNC после ручного закрытия (WS-события потеряны)")
    print("=" * 60)

    await platform.send_signal(Signal(
        signal_id="TEST_002", symbol="SOLUSDT", strategy="WallFade",
        side="short", entry_price=76.00, confidence=0.7
    ))
    await asyncio.sleep(1)

    check("Позиция открыта и guard активен", len(platform.risk_manager._guards) == 1)

    # Биржа: позиция закрыта вручную; WS-события НЕ доходят (мёртвый стрим)
    platform.rest.close_position_manually("SOLUSDT")
    platform.rest.get_pending_events()  # выбрасываем события = имитация потери

    await asyncio.sleep(0.5)
    check("До SYNC паспорт всё ещё OPEN", platform.passport_manager.get_active_by_symbol("SOLUSDT") is not None)

    # Реконнект опубликовал SYNC_REQUEST → Orchestrator сверяется с биржей через REST
    await platform.bus.publish(
        event_type="SYNC_REQUEST", source="test",
        payload={"symbol": "SOLUSDT"}, symbol="SOLUSDT"
    )
    await asyncio.sleep(0.5)

    passport = platform.passport_manager.get_all()[-1]
    check("После SYNC паспорт CLOSED", passport.status == PassportStatus.CLOSED.value)
    check("Guard удалён", len(platform.risk_manager._guards) == 0)

    await platform.stop()


if __name__ == "__main__":
    asyncio.run(test_scenario())
    asyncio.run(test_sync_scenario())
    passed, total = sum(RESULTS), len(RESULTS)
    print("\n" + "=" * 60)
    print(f"🏁 ИТОГО ПО ВСЕМ СЦЕНАРИЯМ: {passed}/{total}")
    print("=" * 60)