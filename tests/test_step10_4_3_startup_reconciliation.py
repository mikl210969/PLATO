"""
Шаг 10.4.3: Стартовая реконсиляция — загрузка паспортов и сверка (T21, T22).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import datetime
from pathlib import Path

from trading.passport import TradePassport
from trading.passport_manager import PassportManager
from trading.passport_repository import PassportRepository
from trading.state_manager import StateManager
from trading.orchestrator import Orchestrator


@pytest.fixture
def setup(tmp_path):
    bus = MagicMock()
    bus.publish = AsyncMock()

    pm = PassportManager()
    repo = PassportRepository(logs_dir=str(tmp_path))
    sm = StateManager(pm)

    config = {"trading": {"atr_value": 0.5}}
    json_logger = MagicMock()

    orch = Orchestrator(
        config=config,
        event_bus=bus,
        passport_manager=pm,
        passport_repository=repo,
        state_manager=sm,
        json_logger=json_logger,
    )

    # Мокаем трейдера
    trader = MagicMock()
    trader.rest = MagicMock()
    trader.calculate_exit_levels = MagicMock(return_value={
        'sl_price': 102.0, 'tp1_price': 100.5, 'tp2_price': 100.0
    })
    orch.register_trader("SOLUSDT", trader)

    return orch, pm, repo, bus, trader


def test_t21_get_all_active_by_symbol(setup):
    """get_all_active_by_symbol должен вернуть ВСЕ активные паспорта."""
    orch, pm, repo, bus, trader = setup

    # Создаём 2 активных паспорта
    p1 = TradePassport(symbol="SOLUSDT", status="OPEN", position_size=7.0)
    p2 = TradePassport(symbol="SOLUSDT", status="PARTIAL_CLOSE", position_size=3.5)
    # И 1 закрытый (не должен попасть)
    p3 = TradePassport(symbol="SOLUSDT", status="CLOSED", position_size=0.0)

    pm.update(p1)
    pm.update(p2)
    pm.update(p3)

    result = pm.get_all_active_by_symbol("SOLUSDT")
    assert len(result) == 2
    sizes = sorted([p.position_size for p in result])
    assert sizes == [3.5, 7.0]


@pytest.mark.asyncio
async def test_t21b_load_passports_from_repository(setup):
    """Паспорта из репозитория должны быть загружены в PassportManager при старте."""
    orch, pm, repo, bus, trader = setup

    # Сохраняем 2 паспорта в репозиторий
    p1 = TradePassport(symbol="SOLUSDT", status="OPEN", position_size=7.0)
    p2 = TradePassport(symbol="SOLUSDT", status="OPEN", position_size=3.5)
    repo.save(p1)
    repo.save(p2)

    # Память пустая
    assert len(pm.get_all()) == 0

    # Загружаем
    loaded = await orch._load_passports_from_repository("SOLUSDT")

    assert loaded == 2
    assert len(pm.get_all()) == 2


@pytest.mark.asyncio
async def test_t22_create_recovery_passport_when_exchange_has_position(setup):
    """
    Сценарий: биржа 9.57 SOL, локально 0 (рестарт платформы).
    Должен быть создан RECOVERY-паспорт.
    """
    orch, pm, repo, bus, trader = setup

    # Настраиваем моки
    trader.rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 9.57,
        'entry_price': 101.50,
    })
    trader.rest.get_user_trades = AsyncMock(return_value=[])

    # Выполняем реконсиляцию
    await orch.perform_startup_recovery("SOLUSDT")

    # Проверяем: должен появиться RECOVERY паспорт
    passports = pm.get_all_active_by_symbol("SOLUSDT")
    assert len(passports) == 1
    
    p = passports[0]
    assert p.strategy == "Recovery"
    assert p.position_size == 9.57
    assert p.status == "OPEN"
    assert p.position_entry_price == 101.50


@pytest.mark.asyncio
async def test_t22b_replay_closes_from_user_trades(setup):
    """
    Сценарий: закрытие было во время простоя платформы.
    Replay трейдов должен обновить паспорт.
    """
    orch, pm, repo, bus, trader = setup

    # Создаём паспорт с полной позицией
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        position_size=7.0,
        position_entry_price=101.50,
        side="short",
    )
    passport_id = passport.passport_id
    pm.update(passport)

    # Настраиваем моки:
    # 1. Позиция на бирже = 3.5 (TP1 закрыл половину)
    trader.rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 3.5,
        'entry_price': 101.50,
    })

    # 2. Трейды за 24 часа: вход + закрытие TP1
    end_time = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    trader.rest.get_user_trades = AsyncMock(return_value=[
        # Вход
        {
            "orderId": 12345,
            "qty": "7.0",
            "quoteQty": "710.5",
            "price": "101.50",
            "side": "SELL",
        },
        # Закрытие TP1 (3.5)
        {
            "orderId": 12346,
            "qty": "3.5",
            "quoteQty": "355.0",
            "price": "101.40",
            "side": "BUY",
        },
    ])
    
    # get_order_status для определения client_order_id
    async def mock_get_order_status(symbol, order_id=None, client_order_id=None):
        if str(order_id) == "12345":
            return {"orderId": 12345, "clientOrderId": f"ENTRY_{passport_id}"}
        elif str(order_id) == "12346":
            return {"orderId": 12346, "clientOrderId": f"C1_{passport_id}"}
        return None
    
    trader.rest.get_order_status = AsyncMock(side_effect=mock_get_order_status)

    # Выполняем реконсиляцию
    await orch.perform_startup_recovery("SOLUSDT")

    # Проверяем: паспорт должен быть обновлён
    updated = pm.get(passport_id)
    assert updated.position_size == 3.5, f"Expected 3.5, got {updated.position_size}"
    
    # Должен быть event PARTIAL_CLOSE в timeline
    events = [e['event'] for e in updated.timeline]
    assert any('RECOVERY_PARTIAL_CLOSE' in e for e in events)


@pytest.mark.asyncio
async def test_t22c_positions_match_no_recovery(setup):
    """
    Сценарий: биржа и локально одинаково — ничего не делаем.
    """
    orch, pm, repo, bus, trader = setup

    # Создаём паспорт с полной позицией
    passport = TradePassport(
        symbol="SOLUSDT",
        status="OPEN",
        position_size=7.0,
        position_entry_price=101.50,
        side="short",
    )
    pm.update(passport)

    # На бирже тоже 7.0
    trader.rest.get_position = AsyncMock(return_value={
        'symbol': 'SOLUSDT',
        'side': 'short',
        'size': 7.0,
        'entry_price': 101.50,
    })
    trader.rest.get_user_trades = AsyncMock(return_value=[])

    # Выполняем реконсиляцию
    await orch.perform_startup_recovery("SOLUSDT")

    # Проверяем: не должен быть создан новый паспорт
    passports = pm.get_all_active_by_symbol("SOLUSDT")
    assert len(passports) == 1
    assert passports[0].passport_id == passport.passport_id
    assert passports[0].position_size == 7.0