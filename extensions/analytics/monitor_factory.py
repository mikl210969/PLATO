"""
MonitorFactory — Фабрика для создания и управления аналитическими мониторами.
Позволяет легко добавлять новые инструменты без изменения main.py.
"""
from typing import List, Dict
from extensions.analytics.delta_monitor import DeltaMonitor


class MonitorFactory:
    @staticmethod
    def create_delta_monitors(symbols: List[str], event_bus, timeframe_sec: int = 300) -> Dict[str, DeltaMonitor]:
        """
        Создает экземпляры DeltaMonitor для каждого переданного символа.
        Возвращает словарь {symbol: DeltaMonitor}.
        """
        monitors = {}
        for symbol in symbols:
            monitor = DeltaMonitor(
                symbol=symbol.upper(),
                event_bus=event_bus,
                timeframe_sec=timeframe_sec,
                publish_interval=5.0
            )
            monitors[symbol.upper()] = monitor
            print(f"🏭 [Factory] Created DeltaMonitor for {symbol.upper()}")
        return monitors

    @staticmethod
    async def start_all(monitors: Dict[str, DeltaMonitor]):
        print(f"🚀 [Factory] Starting {len(monitors)} monitors...")
        for symbol, monitor in monitors.items():
            await monitor.start()
            print(f"✅ [Factory] Started monitor for {symbol}")

    @staticmethod
    async def stop_all(monitors: Dict[str, DeltaMonitor]):
        print(f"🛑 [Factory] Stopping {len(monitors)} monitors...")
        for symbol, monitor in monitors.items():
            await monitor.stop()
            print(f"✅ [Factory] Stopped monitor for {symbol}")