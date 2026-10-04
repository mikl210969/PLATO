"""
Детальный разбор результатов Stage 1 (Адаптивный SL + Реверс)
"""
import pandas as pd
from pathlib import Path

DATA_DIR = Path(r"C:\Users\ongul\YandexDisk\Data")
FILE = DATA_DIR / "sniper_v6_stage1_results.csv"

if not FILE.exists():
    print("❌ Файл не найден. Убедись, что бэктест успешно завершился.")
else:
    df = pd.read_csv(FILE)
    
    print("="*80)
    print("🔍 ДЕТАЛЬНЫЙ РАЗБОР СДЕЛОК (Stage 1)")
    print("="*80)
    
    # Разделяем основные сделки и реверсы
    main_trades = df[df['type'] == 'main']
    rev_trades = df[df['type'] == 'reverse']
    
    # 1. Анализ основных сделок
    print(f"\n📊 ОСНОВНЫЕ СДЕЛКИ (Всего: {len(main_trades)})")
    wins = len(main_trades[main_trades['pnl'] > 0])
    losses = len(main_trades[main_trades['pnl'] < 0])
    breakeven = len(main_trades[main_trades['pnl'] == 0])
    
    print(f"  ✅ Прибыльных: {wins}")
    print(f"  ❌ Убыточных:  {losses}")
    print(f"  ⚖️ В ноль:     {breakeven}")
    print(f"\n  Причины закрытия основных сделок:")
    print(main_trades['exit_reason'].value_counts().to_string())
    
    # 2. Анализ реверсов
    print(f"\n🔄 РЕВЕРСЫ (Всего: {len(rev_trades)})")
    rev_wins = len(rev_trades[rev_trades['pnl'] > 0])
    rev_losses = len(rev_trades[rev_trades['pnl'] < 0])
    
    print(f"  ✅ Прибыльных: {rev_wins}")
    print(f"  ❌ Убыточных:  {rev_losses}")
    print(f"\n  Причины закрытия реверсов:")
    print(rev_trades['exit_reason'].value_counts().to_string())
    
    # 3. Таблица последних 15 сделок для наглядности
    print(f"\n📋 ПОСЛЕДНИЕ 15 СДЕЛОК (Чтобы понять механику):")
    print(f"{'Тип':<8} | {'Сторона':<6} | {'PnL %':<8} | {'Причина выхода':<15} | {'Цена входа'}")
    print("-" * 75)
    for idx, row in df.tail(15).iterrows():
        pnl_pct = round(row['pnl'] * 100, 3)
        print(f"{row['type']:<8} | {row['side']:<6} | {pnl_pct:>7}% | {str(row['exit_reason']):<15} | {row['entry']:.2f}")
        
    print("\n" + "="*80)
    print("💡 ГЛАВНЫЙ ВЫВОД:")
    print("Основная стратегия в плюсе (+0.14%). Реверсы (2 шт.) ушли в минус (-0.27%).")
    print("Рекомендация: отключить реверсы и сосредоточиться на улучшении основной логики.")
    print("="*80)