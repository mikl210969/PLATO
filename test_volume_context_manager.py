"""
Синтетический тест для VolumeContextManager.
Проверяет:
1. Холодный старт (игнорирование при недостатке данных).
2. Смену режимов (CALM, NORMAL, VOLATILE) и применение overrides.
3. Режим DRY RUN (параметры не меняются).
"""
import asyncio
import logging
from features.volume_context_manager import VolumeContextManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("test_context_manager")

async def run_test():
    volume_config = {
        "enabled": True,
        "lookback_candles": 3,
        "baseline_avg_vol": 10.0,
        "dry_run": False,
        "force_regime": None,
        "regimes": {
            "calm": {"vol_ratio_max": 0.5, "overrides": {"min_wall_volume": 10, "cooldown_sec": 60}},
            "normal": {"vol_ratio_max": 2.0, "overrides": {"min_wall_volume": 20, "cooldown_sec": 30}},
            "volatile": {"vol_ratio_max": 999, "overrides": {"min_wall_volume": 50, "cooldown_sec": 15}}
        }
    }
    
    base_params = {
        "min_wall_volume": 20,
        "cooldown_sec": 30
    }
    
    manager = VolumeContextManager(volume_config, base_params)
    
    # 1. Проверка холодного старта
    logger.info("🔍 Тест 1: Холодный старт (мало данных)...")
    await manager.update_context([5.0, 8.0]) # Всего 2 элемента, нужно 3
    params = await manager.get_active_params()
    assert params["min_wall_volume"] == 20, "Параметры не должны были измениться"
    logger.info("✅ Холодный старт обработан корректно (параметры не изменены)")

    # 2. Проверка смены режима на CALM (avg_vol = 4.0, ratio = 0.4)
    logger.info("🔍 Тест 2: Смена режима на CALM...")
    await manager.update_context([3.0, 4.0, 5.0]) 
    params = await manager.get_active_params()
    assert params["min_wall_volume"] == 10, f"Ожидалось 10, получено {params['min_wall_volume']}"
    assert params["cooldown_sec"] == 60, f"Ожидалось 60, получено {params['cooldown_sec']}"
    logger.info("✅ Режим CALM применен корректно")

    # 3. Проверка смены режима на VOLATILE (avg_vol = 30.0, ratio = 3.0)
    logger.info("🔍 Тест 3: Смена режима на VOLATILE...")
    await manager.update_context([25.0, 30.0, 35.0]) 
    params = await manager.get_active_params()
    assert params["min_wall_volume"] == 50, f"Ожидалось 50, получено {params['min_wall_volume']}"
    assert params["cooldown_sec"] == 15, f"Ожидалось 15, получено {params['cooldown_sec']}"
    logger.info("✅ Режим VOLATILE применен корректно")

    # 4. Проверка dry_run
    logger.info("🔍 Тест 4: Режим DRY RUN...")
    manager.dry_run = True
    await manager.update_context([3.0, 4.0, 5.0]) # Должен попробовать применить CALM, но не менять параметры
    params = await manager.get_active_params()
    assert params["min_wall_volume"] == 50, "В dry_run параметры не должны меняться (остаются от VOLATILE)"
    logger.info("✅ DRY RUN работает корректно (параметры не изменены)")

    logger.info("🎉 ТЕСТ ПРОЙДЕН УСПЕШНО! VolumeContextManager работает корректно.")

if __name__ == "__main__":
    asyncio.run(run_test())