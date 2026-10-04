"""Тест Basis Monitor на синтетических данных."""
import logging
import time
from extensions.analytics.basis_monitor import BasisMonitor
from extensions.data_layer.db_manager import DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("test_day11_12")


def on_basis_event(event_type: str, data: dict):
    """Обработчик событий BASIS_UPDATED."""
    basis_pct = data.get("basis_pct", 0)
    spot = data.get("spot_price", 0)
    futures = data.get("futures_price", 0)
    stop_triggered = data.get("basis_stop_triggered", False)
    
    log.info(f"[EVENT] {event_type} | Basis: {basis_pct:.3f}% | "
             f"Spot: {spot:.2f} | Futures: {futures:.2f}")
    
    if stop_triggered:
        change = data.get("basis_change", 0) * 100
        log.warning(f"🚨 BASIS STOP TRIGGERED! Изменение: {change:.2f}%")


def test_basis_monitor():
    """Тест Basis Monitor с синтетическими данными."""
    log.info("--- Инициализация ---")
    db = DatabaseManager(db_path="test_basis_metrics.db")
    monitor = BasisMonitor(
        db_manager=db,
        update_interval_ms=100.0,
        noise_filter_count=3,
        basis_stop_threshold=0.015,  # 1.5%
        on_event=on_basis_event,
    )

    log.info("\n--- Сценарий 1: Нормальный basis (~0.1%) ---")
    base_ts = time.time()
    for i in range(10):
        # Spot: 140.00, Futures: 140.14 → basis = 0.1%
        monitor.update_spot_price(140.00 + i * 0.01, base_ts + i * 0.1)
        monitor.update_futures_price(140.14 + i * 0.01, base_ts + i * 0.1)
        time.sleep(0.05)  # Имитируем реальное время

    log.info(f"\nСтатистика: {monitor.get_stats()}")
    
    log.info("\n--- Сценарий 2: Резкое изменение basis (Basis Stop) ---")
    # Фиксируем basis при "входе в позицию"
    entry_basis = monitor.get_current_basis()
    monitor.set_basis_at_entry(entry_basis)
    log.info(f"Basis при входе: {entry_basis*100:.3f}%")

    # Имитируем резкое изменение basis
    for i in range(5):
        # Spot остается ~140.10, Futures резко растет до 142.50 → basis = 1.7%
        monitor.update_spot_price(140.10, base_ts + 2.0 + i * 0.1)
        monitor.update_futures_price(142.50, base_ts + 2.0 + i * 0.1)
        time.sleep(0.05)

    log.info(f"\nФинальная статистика: {monitor.get_stats()}")

    # Проверки
    current_basis = monitor.get_current_basis()
    assert current_basis is not None, "Basis не рассчитан"
    assert abs(current_basis - 0.017) < 0.001, f"Basis должен быть ~1.7%, получено {current_basis*100:.2f}%"

    log.info("\n✅ Все проверки пройдены!")
    db.close()


if __name__ == "__main__":
    test_basis_monitor()