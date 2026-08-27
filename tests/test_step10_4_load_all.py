"""
Шаг 10.4: PassportRepository.load_all() (T20).
"""
import pytest
from pathlib import Path
from trading.passport_repository import PassportRepository
from trading.passport import TradePassport


@pytest.fixture
def repo(tmp_path):
    return PassportRepository(logs_dir=str(tmp_path))


def test_t20_load_all_returns_all_passports(repo):
    """load_all() должен вернуть все сохранённые паспорта."""
    # Создаём 3 паспорта
    p1 = TradePassport(symbol="SOLUSDT", status="OPEN", position_size=7.0)
    p2 = TradePassport(symbol="BTCUSDT", status="CLOSED", position_size=0.0)
    p3 = TradePassport(symbol="ETHUSDT", status="OPEN", position_size=3.5)
    
    repo.save(p1)
    repo.save(p2)
    repo.save(p3)
    
    # Загружаем все
    loaded = repo.load_all()
    
    assert len(loaded) == 3
    symbols = {p.symbol for p in loaded}
    assert symbols == {"SOLUSDT", "BTCUSDT", "ETHUSDT"}


def test_t20b_load_all_handles_corrupted_files(repo):
    """load_all() должен пропускать битые файлы, не падая."""
    # Создаём валидный паспорт
    p1 = TradePassport(symbol="SOLUSDT", status="OPEN")
    repo.save(p1)
    
    # Создаём битый файл
    corrupted_path = repo.logs_dir / "passport_CORRUPTED.json"
    with open(corrupted_path, 'w') as f:
        f.write("{invalid json")
    
    # load_all должен вернуть только валидный
    loaded = repo.load_all()
    assert len(loaded) == 1
    assert loaded[0].symbol == "SOLUSDT"