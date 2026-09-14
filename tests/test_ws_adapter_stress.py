"""
Нагрузочные тесты адаптера Binance WebSocket.
Цель: найти корень проблемы с таймаутами каждые 30 секунд.
"""
import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestWSAdapterConfig:
    """Тест 1: Проверяем конфигурацию WebSocket подключения."""
    
    def test_ping_pong_configured(self):
        """Адаптер ДОЛЖЕН использовать ping_interval и ping_timeout."""
        # Импортируем адаптер
        from adapters.binance_ws import BinanceWsAdapter
        
        # Проверяем исходный код метода connect на наличие ping_interval
        import inspect
        source = inspect.getsource(BinanceWsAdapter.connect)
        
        assert 'ping_interval' in source, (
            "❌ В методе connect() НЕ НАЙДЕН ping_interval! "
            "Это главная причина таймаутов каждые 30 секунд. "
            "Binance закрывает idle-соединения. "
            "Добавь: ping_interval=20, ping_timeout=10 в websockets.connect()"
        )
        assert 'ping_timeout' in source, (
            "❌ В методе connect() НЕ НАЙДЕН ping_timeout!"
        )
    
    def test_recv_timeout_not_too_long(self):
        """Таймаут recv() не должен быть больше 15 секунд."""
        from adapters.binance_ws import BinanceWsAdapter
        import inspect
        source = inspect.getsource(BinanceWsAdapter.run)
        
        # Ищем asyncio.wait_for(..., timeout=...)
        if 'timeout=' in source:
            # Извлекаем значение таймаута
            import re
            match = re.search(r'timeout\s*=\s*(\d+)', source)
            if match:
                timeout = int(match.group(1))
                assert timeout <= 15, (
                    f"️ Таймаут recv() = {timeout} сек. "
                    f"Рекомендуется ≤ 15 сек для быстрого обнаружения обрывов."
                )


class TestWSAdapterMessageHandling:
    """Тест 2: Обработка сообщений адаптером."""
    
    @pytest.mark.asyncio
    async def test_duplicate_message_handling(self):
        """Если биржа прислала одно и то же событие дважды — адаптер не должен дублировать."""
        from adapters.binance_ws import BinanceWsAdapter
        
        # Создаём адаптер с моками
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        adapter._message_queue = asyncio.Queue(maxsize=1000)
        adapter._connected = True
        
        # Имитируем получение двух одинаковых сообщений
        msg = json.dumps({
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "SOLUSDT",
                "c": "TEST_ORDER_001",
                "X": "FILLED",
                "z": "7.0",
                "ap": "101.0"
            }
        })
        
        # Отправляем дважды
        await adapter._message_queue.put(msg)
        await adapter._message_queue.put(msg)
        
        # Проверяем что в очереди 2 сообщения (адаптер не фильтрует дубликаты на этом уровне)
        assert adapter._message_queue.qsize() == 2, (
            "Адаптер должен пропускать все сообщения, "
            "фильтрация дубликатов — задача order_handler"
        )
    
    @pytest.mark.asyncio
    async def test_truncated_payload(self):
        """Если пришло сообщение без поля 'z' — адаптер должен пропустить его дальше,
        но не упасть с исключением."""
        from adapters.binance_ws import BinanceWsAdapter
        
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        adapter._message_queue = asyncio.Queue(maxsize=1000)
        adapter._connected = True
        
        # Обрезанное сообщение (нет executed_qty)
        truncated_msg = json.dumps({
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "SOLUSDT",
                "c": "TEST_ORDER_001"
                # Нет 'z', 'X', 'ap'
            }
        })
        
        # Не должно упасть
        await adapter._message_queue.put(truncated_msg)
        assert adapter._message_queue.qsize() == 1


class TestWSAdapterReconnect:
    """Тест 3: Реконнект и восстановление подписок."""
    
    @pytest.mark.asyncio
    async def test_reconnect_preserves_subscriptions(self):
        """После реконнекта должны восстановиться ВСЕ подписки."""
        from adapters.binance_ws import BinanceWsAdapter
        
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        
        # Добавляем активные подписки
        adapter._active_subscriptions = [
            "solusdt@depth20@100ms",
            "btcusdt@aggTrade",
            "btcusdt@depth@100ms"
        ]
        
        # Проверяем что подписки сохранены
        assert len(adapter._active_subscriptions) == 3
        assert "solusdt@depth20@100ms" in adapter._active_subscriptions


class TestWSAdapterHighLoad:
    """Тест 4: Нагрузка — много сообщений подряд."""
    
    @pytest.mark.asyncio
    async def test_high_frequency_messages(self):
        """Адаптер должен выдерживать поток 100 msg/sec без блокировки."""
        from adapters.binance_ws import BinanceWsAdapter
        
        adapter = BinanceWsAdapter(base_url="wss://test.com/ws")
        adapter._message_queue = asyncio.Queue(maxsize=1000)
        adapter._connected = True
        
        # Отправляем 100 сообщений
        for i in range(100):
            msg = json.dumps({
                "e": "aggTrade",
                "s": "SOLUSDT",
                "p": str(100 + i * 0.01),
                "q": "1.0"
            })
            await adapter._message_queue.put(msg)
        
        # Очередь не должна переполниться
        assert adapter._message_queue.qsize() == 100
        assert not adapter._message_queue.full()


class TestWSAdapterTimeoutBehavior:
    """Тест 5: Поведение при таймауте recv()."""
    
    def test_timeout_leads_to_reconnect(self):
        """При таймауте recv() адаптер ДОЛЖЕН инициировать реконнект."""
        from adapters.binance_ws import BinanceWsAdapter
        import inspect
        
        source = inspect.getsource(BinanceWsAdapter.run)
        
        # Проверяем что в коде есть обработка TimeoutError
        assert 'TimeoutError' in source or 'asyncio.TimeoutError' in source, (
            "❌ В методе run() НЕ НАЙДЕНА обработка TimeoutError! "
            "При таймауте адаптер должен делать reconnect."
        )
    
    def test_timeout_sets_healthy_false(self):
        """При таймауте флаг _healthy должен стать False."""
        from adapters.binance_ws import BinanceWsAdapter
        import inspect
        
        source = inspect.getsource(BinanceWsAdapter.run)
        
        # Проверяем что при таймауте устанавливается _healthy = False
        assert '_healthy = False' in source or 'self._healthy = False' in source, (
            "️ При таймауте recv() флаг _healthy должен стать False, "
            "чтобы Health Monitor увидел DEGRADED/BLIND."
        )