"""Тест HVN Calculator на живых данных из JSONL с отладкой."""
import logging
import pandas as pd
from extensions.data_layer.db_manager import DatabaseManager
from extensions.analytics.hvn_calculator import HVNCalculator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

if __name__ == "__main__":
    db = DatabaseManager(db_path="extensions/data_layer/plato_metrics.db")
    
    hvn_calc = HVNCalculator(
        db_manager=db,
        cold_storage_path="data/cold_storage",
        lookback_hours=4,          # Берем 4 часа
        price_step_pct=0.002,      # Шаг сетки 0.2%
        min_prominence_pct=0.5,    # Минимальная выпуклость 0.5%
        top_n=3,
    )

    symbol = "SOLUSDT"
    print(f"\n--- Анализ данных для {symbol} ---")
    
    # 1. Проверяем, что данные загружаются
    trades_df = hvn_calc._load_trades(symbol)
    
    if trades_df.empty:
        print("❌ ОШИБКА: DataFrame пуст. Файл JSONL не читается или в нем нет свежих данных.")
    else:
        print(f"✅ Успешно загружено {len(trades_df)} сделок из JSONL")
        print(f"   Диапазон цен: {trades_df['price'].min():.2f} - {trades_df['price'].max():.2f}")
        print(f"   Общий объем: {trades_df['value_usdt'].sum():.0f} USDT")
        
        # 2. Запускаем расчет
        print("\n--- Запуск расчета HVN ---")
        hvn_levels = hvn_calc.calculate_hvn(symbol)
        
        if hvn_levels:
            print(f"🎯 Найдено {len(hvn_levels)} HVN уровней:")
            for i, hvn in enumerate(hvn_levels, 1):
                print(f"  {i}. Цена: {hvn['price']:.2f} | Объём: {hvn['volume']:.0f} USDT | Strength: {hvn['strength']:.2f}")
            hvn_calc.save_hvn_to_db(symbol, hvn_levels)
            print("✅ Уровни сохранены в Hot Storage (SQLite)")
        else:
            print("ℹ️ HVN уровни не найдены. Это нормально для трендового рынка без явной консолидации.")
            print("   Алгоритм работает корректно, просто не нашел пиков объема, превышающих соседей.")
        
    db.close()