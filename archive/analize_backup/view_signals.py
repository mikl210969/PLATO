"""
Просмотр сигналов Sniper V6 в понятном виде (Торговый Журнал)
Выводит точное время, цену, направление и подтверждение стаканом для одного дня.
"""
import pandas as pd
from pathlib import Path

# ==============================================================================
# 1. НАСТРОЙКИ
# ==============================================================================
DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
FILE = DATA_DIR / "sniper_v6_final_confluences.csv"
TARGET_DATE = "2026-09-28"  # Смотрим только этот день для наглядности

# ==============================================================================
# 2. ЗАГРУЗКА И ФИЛЬТРАЦИЯ
# ==============================================================================
if not FILE.exists():
    print(f"❌ Файл не найден: {FILE}")
    print("Сначала запусти test_sniper_v6_all_days.py")
else:
    df = pd.read_csv(FILE)
    df['date'] = df['date'].astype(str)
    
    # Фильтруем по выбранной дате
    day_signals = df[df['date'] == TARGET_DATE].sort_values('wall_time')
    
    print("=" * 70)
    print(f"📊 ТОРГОВЫЙ ЖУРНАЛ: Sniper V6 (УСТ + Стакан)")
    print(f"📅 Дата анализа: {TARGET_DATE}")
    print(f"🎯 Найдено качественных сетапов: {len(day_signals)}")
    print("=" * 70)
    
    if day_signals.empty:
        print("⚠️ Сигналов за этот день не найдено.")
    else:
        for idx, row in day_signals.iterrows():
            # Определяем направление сделки на основе логики стратегии
            if row['wall_side'] == 'bid' and row['ust_type'] == 'SUPPORT_BREAK':
                direction = "🟢 LONG (Отскок от пробитой поддержки)"
            elif row['wall_side'] == 'ask' and row['ust_type'] == 'RESISTANCE_BREAK':
                direction = "🔴 SHORT (Отскок от пробитого сопротивления)"
            else:
                direction = "⚪ Смешанный сигнал"
            
            print(f"\n⏰ ВРЕМЯ СИГНАЛА : {row['wall_time']}")
            print(f"📈 НАПРАВЛЕНИЕ  : {direction}")
            print(f"📐 УРОВЕНЬ УСТ  : {row['ust_level']:.2f} ({row['ust_type']})")
            print(f"🧱 ЦЕНА СТЕНЫ   : {row['wall_price']:.2f}")
            print(f"💰 ОБЪЕМ СТЕНЫ  : {row['wall_volume']:,.0f} SOL")
            print(f"🎯 СОВПАДЕНИЕ   : Расстояние всего {row['distance_pct']:.3f}%")
            print("-" * 70)
            
    print("\n✅ Анализ завершен. Теперь ты можешь открыть эти точные времена на графике Binance!")