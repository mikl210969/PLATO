"""Тест HVN Calculator на синтетических данных."""
import time
base_ts = time.time() - 3600  # сделки в течение последнего часа

import logging
from pathlib import Path
from extensions.analytics.hvn_calculator import HVNCalculator
from extensions.data_layer.db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("test_day8_10")


def create_synthetic_trades():
    """Создать синтетические тиковые данные для теста."""
    import pandas as pd
    import numpy as np

    # Генерируем 1000 сделок с тремя "горячими" зонами
    np.random.seed(42)
    
    trades = []
    # Зона 1: цена ~140.0 (высокий объём)
    for _ in range(300):
        trades.append({
            "price": 140.0 + np.random.uniform(-0.1, 0.1),
            "quantity": np.random.uniform(10, 50),
            "value_usdt": np.random.uniform(1400, 7000),
            "timestamp": base_ts + np.random.uniform(0, 3600),
        })
    
    # Зона 2: цена ~138.5 (средний объём)
    for _ in range(200):
        trades.append({
            "price": 138.5 + np.random.uniform(-0.1, 0.1),
            "quantity": np.random.uniform(5, 20),
            "value_usdt": np.random.uniform(700, 2800),
            "timestamp": base_ts + np.random.uniform(0, 3600),
        })
    
    # Зона 3: цена ~142.0 (низкий объём — шум)
    for _ in range(500):
        trades.append({
            "price": 142.0 + np.random.uniform(-0.5, 0.5),
            "quantity": np.random.uniform(1, 5),
            "value_usdt": np.random.uniform(140, 700),
            "timestamp": base_ts + np.random.uniform(0, 3600),
        })

    return pd.DataFrame(trades)


def test_hvn_calculation():
    """Тест расчета HVN на синтетических данных."""
    log.info("--- Создание тестовых данных ---")
    trades_df = create_synthetic_trades()
    log.info(f"Создано {len(trades_df)} синтетических сделок")

    # Сохраняем во временный Parquet
    cold_storage_path = Path("test_cold_storage")
    cold_storage_path.mkdir(exist_ok=True)
    parquet_file = cold_storage_path / "SOLUSDT_trades_20260828_120000.parquet"
    trades_df.to_parquet(parquet_file, index=False)
    log.info(f"Сохранено в {parquet_file}")

    # Инициализация
    db = DatabaseManager(db_path="test_plato_metrics.db")
    hvn_calc = HVNCalculator(
        db_manager=db,
        cold_storage_path=str(cold_storage_path),
        lookback_hours=24,
        price_step_pct=0.001,  # 0.1%
        min_prominence_pct=10.0,
        top_n=5,
    )

    # Расчет HVN
    log.info("\n--- Расчет HVN ---")
    hvn_levels = hvn_calc.calculate_hvn("SOLUSDT", current_price=140.0)
    
    log.info(f"\nНайдено {len(hvn_levels)} HVN уровней:")
    for i, hvn in enumerate(hvn_levels, 1):
        log.info(
            f"  {i}. Цена: {hvn['price']:.2f} | "
            f"Объём: {hvn['volume']:.0f} USDT | "
            f"Strength: {hvn['strength']:.2f}"
        )

    # Сохранение в БД
    log.info("\n--- Сохранение в БД ---")
    hvn_calc.save_hvn_to_db("SOLUSDT", hvn_levels)

    # Чтение из кэша
    log.info("\n--- Чтение из кэша БД ---")
    cached = hvn_calc.get_cached_hvn("SOLUSDT")
    log.info(f"Получено {len(cached)} HVN уровней из кэша:")
    for i, hvn in enumerate(cached, 1):
        log.info(f"  {i}. Цена: {hvn['price']:.2f} | Объём: {hvn['volume']:.0f}")

    # Проверка
    assert len(hvn_levels) > 0, "HVN не найдены"
    assert len(cached) == len(hvn_levels), "Кэш не совпадает с расчетом"
    assert hvn_levels[0]['price'] > 139.0, "Первый HVN должен быть около 140.0"

    log.info("\n✅ Все проверки пройдены!")
    db.close()


if __name__ == "__main__":
    test_hvn_calculation()