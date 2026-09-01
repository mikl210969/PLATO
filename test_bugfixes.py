"""
Таргетные тесты для трёх исправленных багов:
1. Reconnect восстанавливает BTC-потоки
2. avg_price корректно возвращается (не 0.0)
3. JsonLogger стабилен (пишет мгновенно, переживает ошибки)

Запуск: python test_bugfixes.py
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import json
import tempfile
import os
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))


class TestReconnectBTC(unittest.IsolatedAsyncioTestCase):
    """Тест: после reconnect все подписки (включая BTC) восстанавливаются."""

    async def test_subscriptions_saved_on_subscribe(self):
        """Проверка: после подписки потоки сохраняются в _active_subscriptions."""
        from adapters.binance_ws import BinanceWsAdapter
        
        # Создаём adapter с моком WebSocket
        adapter = BinanceWsAdapter(event_bus=MagicMock())
        adapter._connected = True
        adapter._ws = MagicMock()
        adapter._ws.send = AsyncMock()
        
        # Подписываемся на разные потоки
        await adapter.subscribe_depth("SOLUSDT")
        await adapter.subscribe_btc_streams()
        await adapter.subscribe_user_data("test_listen_key_123")
        
        # Проверяем: все подписки сохранены
        self.assertIn("solusdt@depth20@100ms", adapter._active_subscriptions)
        self.assertIn("btcusdt@aggTrade", adapter._active_subscriptions)
        self.assertIn("btcusdt@depth@100ms", adapter._active_subscriptions)
        self.assertIn("test_listen_key_123", adapter._active_subscriptions)
        
        print("✅ Подписки сохраняются корректно")

    async def test_reconnect_logic_sends_all_subscriptions(self):
        """Проверка: при reconnect вызывается _send_subscribe со всеми сохранёнными подписками."""
        from adapters.binance_ws import BinanceWsAdapter
        
        adapter = BinanceWsAdapter(event_bus=MagicMock())
        adapter._connected = True
        adapter._ws = MagicMock()
        adapter._ws.send = AsyncMock()
        
        # Шаг 1: Добавляем подписки вручную (имитируем состояние после первичного подключения)
        adapter._active_subscriptions = [
            "solusdt@depth20@100ms",
            "btcusdt@aggTrade",
            "btcusdt@depth@100ms",
            "test_listen_key_123"
        ]
        adapter._is_initial_connect = False  # Это reconnect
        
        # Шаг 2: Вызываем _send_subscribe напрямую (то, что делает connect() при reconnect)
        result = await adapter._send_subscribe(list(adapter._active_subscriptions), 999)
        
        # Шаг 3: Проверяем, что send был вызван
        self.assertTrue(result, "_send_subscribe должен вернуть True")
        self.assertTrue(adapter._ws.send.called, "send() должен быть вызван")
        
        # Шаг 4: Извлекаем аргумент send()
        call_args = adapter._ws.send.call_args[0][0]
        subscribe_msg = json.loads(call_args)
        
        # Проверяем: метод SUBSCRIBE
        self.assertEqual(subscribe_msg["method"], "SUBSCRIBE")
        
        # Проверяем: все 4 подписки отправлены
        sent_streams = set(subscribe_msg["params"])
        expected_streams = {
            "solusdt@depth20@100ms",
            "btcusdt@aggTrade",
            "btcusdt@depth@100ms",
            "test_listen_key_123"
        }
        
        self.assertEqual(sent_streams, expected_streams,
                        "Все подписки должны быть отправлены при reconnect")
        
        # КЛЮЧЕВАЯ ПРОВЕРКА: BTC-потоки включены
        self.assertIn("btcusdt@aggTrade", sent_streams,
                     "BTC aggTrade должен быть восстановлен при reconnect")
        self.assertIn("btcusdt@depth@100ms", sent_streams,
                     "BTC depth должен быть восстановлен при reconnect")
        
        print(f"✅ Логика reconnect восстанавливает все {len(adapter._active_subscriptions)} подписок (включая BTC)")


class TestAvgPrice(unittest.IsolatedAsyncioTestCase):
    """Тест: OrderVerifier возвращает avg_price, а не price."""

    async def test_avg_price_in_payload(self):
        """Проверка: при FILLED payload содержит 'avg_price', а не 'price'."""
        from trading.order_verifier import OrderVerifier
        
        # Создаём мок REST-клиент
        mock_rest = MagicMock()
        mock_rest.get_order_status = AsyncMock(return_value={
            'status': 'FILLED',
            'executedQty': '7.0',
            'avgPrice': '103.06'  # Реальная цена с биржи
        })
        
        # Создаём мок EventBus
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        
        # Создаём верификатор
        verifier = OrderVerifier(
            rest_client=mock_rest,
            event_bus=mock_bus,
            poll_interval=0.1,  # Быстрый poll для теста
            max_attempts=2
        )
        
        # Запускаем verification loop
        await verifier._verify_loop(
            passport_id="TEST_PASSPORT",
            order_id="12345",
            symbol="SOLUSDT",
            client_order_id="TEST_ORDER_123"
        )
        
        # Проверяем: publish был вызван
        self.assertTrue(mock_bus.publish.called, "publish() должен быть вызван")
        
        # Извлекаем payload
        call_kwargs = mock_bus.publish.call_args[1]
        payload = call_kwargs['payload']
        
        # КЛЮЧЕВАЯ ПРОВЕРКА: 'avg_price' в payload
        self.assertIn('avg_price', payload, 
                     "payload должен содержать 'avg_price' (не 'price')")
        
        # Проверяем значение
        self.assertEqual(payload['avg_price'], 103.06, 
                        "avg_price должен быть 103.06")
        
        # Проверяем, что 'price' НЕ используется (старый баг)
        self.assertNotIn('price', payload, 
                        "payload НЕ должен содержать старый ключ 'price'")
        
        print("✅ OrderVerifier возвращает avg_price корректно (не 0.0)")

    async def test_fallback_to_user_trades(self):
        """Проверка: если avgPrice=0, верификатор берёт цену из user_trades."""
        from trading.order_verifier import OrderVerifier
        
        # Создаём мок REST-клиент
        mock_rest = MagicMock()
        mock_rest.get_order_status = AsyncMock(return_value={
            'status': 'FILLED',
            'executedQty': '7.0',
            'avgPrice': '0'  # Баг Binance: иногда возвращает 0
        })
        
        # Мок для user_trades (fallback)
        mock_rest.get_user_trades = AsyncMock(return_value=[
            {'orderId': '12345', 'qty': '7.0', 'quoteQty': '721.42'}  # 7 * 103.06
        ])
        
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        
        verifier = OrderVerifier(
            rest_client=mock_rest,
            event_bus=mock_bus,
            poll_interval=0.1,
            max_attempts=2
        )
        
        await verifier._verify_loop(
            passport_id="TEST_PASSPORT",
            order_id="12345",
            symbol="SOLUSDT",
            client_order_id="TEST_ORDER_123"
        )
        
        # Проверяем: get_user_trades был вызван (fallback сработал)
        self.assertTrue(mock_rest.get_user_trades.called, 
                       "get_user_trades должен быть вызван когда avgPrice=0")
        
        # Проверяем: avg_price вычислен из trades
        payload = mock_bus.publish.call_args[1]['payload']
        self.assertAlmostEqual(payload['avg_price'], 103.06, places=2,
                              msg="avg_price должен быть вычислен из user_trades")
        
        print("✅ Fallback на user_trades работает при avgPrice=0")


class TestJsonLoggerStability(unittest.TestCase):
    """Тест: JsonLogger пишет мгновенно и переживает ошибки."""

    def setUp(self):
        """Создаём временную директорию для тестов."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Удаляем временную директорию."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_immediate_write(self):
        """Проверка: запись появляется в файле сразу (flush работает)."""
        from core.json_logger import JsonLogger
        
        logger = JsonLogger(log_dir=self.test_dir, enabled=True, max_bytes=1024*1024)
        
        # Записываем событие
        logger.log(module="test", event="test_event", data={"key": "value"})
        
        # Проверяем: файл существует и содержит запись
        log_file = Path(self.test_dir) / "platform_log.jsonl"
        self.assertTrue(log_file.exists(), "Файл лога должен существовать")
        
        # Читаем содержимое
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 1, "Должна быть ровно 1 запись")
        
        # Парсим JSON
        entry = json.loads(lines[0])
        self.assertEqual(entry['module'], 'test')
        self.assertEqual(entry['event'], 'test_event')
        self.assertEqual(entry['data']['key'], 'value')
        
        logger.close()
        print("✅ JsonLogger пишет мгновенно (flush работает)")

    def test_file_handle_persistent(self):
        """Проверка: файл держится открытым (не открывается/закрывается на каждую запись)."""
        from core.json_logger import JsonLogger
        
        logger = JsonLogger(log_dir=self.test_dir, enabled=True, max_bytes=1024*1024)
        
        # Записываем много событий
        for i in range(100):
            logger.log(module="test", event=f"event_{i}", data={"i": i})
        
        # Проверяем: file_handle открыт
        self.assertIsNotNone(logger._file_handle, 
                            "file_handle должен быть открыт")
        self.assertFalse(logger._file_handle.closed, 
                        "file_handle не должен быть закрыт")
        
        # Проверяем: все 100 записей в файле
        log_file = Path(self.test_dir) / "platform_log.jsonl"
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        self.assertEqual(len(lines), 100, "Все 100 записей должны быть в файле")
        
        logger.close()
        print("✅ Файл держится открытым (не переоткрывается на каждую запись)")

    def test_rotation_works(self):
        """Проверка: ротация файлов работает при превышении max_bytes."""
        from core.json_logger import JsonLogger
        import time
        
        # Маленький max_bytes для быстрого триггера ротации
        logger = JsonLogger(log_dir=self.test_dir, enabled=True, max_bytes=500)
        
        # Записываем много данных (больше max_bytes)
        # Добавляем sleep для разных timestamps
        for i in range(50):
            logger.log(module="test", event=f"event_{i}", data={"data": "x" * 100})
            if i % 10 == 0:
                time.sleep(0.001)  # Микросекундная задержка для разных timestamps
        
        # Проверяем: есть ротированные файлы
        log_files = list(Path(self.test_dir).glob("platform_log_*.jsonl"))
        self.assertGreater(len(log_files), 0, 
                          "Должны быть ротированные файлы (platform_log_YYYYMMDD_HHMMSS_ffffff.jsonl)")
        
        # Проверяем: текущий файл тоже существует
        current_file = Path(self.test_dir) / "platform_log.jsonl"
        self.assertTrue(current_file.exists(), "Текущий файл должен существовать")
        
        # Проверяем: имена файлов содержат микросекунды (19 символов: YYYYMMDD_HHMMSS_ffffff)
        for log_file in log_files:
            # Извлекаем timestamp из имени: platform_log_YYYYMMDD_HHMMSS_ffffff.jsonl
            timestamp_part = log_file.stem.replace("platform_log_", "")
            # Должно быть что-то вроде "20260901_092317_123456"
            parts = timestamp_part.split("_")
            if len(parts) >= 3:  # date, time, microseconds
                self.assertEqual(len(parts[2]), 6, 
                               f"Микросекунды должны быть 6 символов, получено: {parts[2]}")
        
        logger.close()
        print(f"✅ Ротация работает (создано {len(log_files)} ротированных файлов с микросекундами)")

class TestSmartSizing(unittest.TestCase):
    """Тест: Smart Sizing корректирует риск под BTC-тренд."""

    def _make_handler(self):
        """Создать минимальный SignalHandlerMixin для теста."""
        from trading.handlers.signal_handler import SignalHandlerMixin
        
        class TestHandler(SignalHandlerMixin):
            def __init__(self):
                super().__init__()
        
        return TestHandler()

    def test_smart_sizing_long_in_uptrend(self):
        """BTC UP + LONG → множитель 1.5."""
        handler = self._make_handler()
        handler._btc_context = {"trend": "UP", "regime": "NORMAL", "delta_strength": 10.0}
        
        risk, mult, reason = handler._calculate_smart_risk(base_risk=30.0, signal_side="long")
        
        self.assertEqual(mult, 1.5)
        self.assertEqual(risk, 45.0)
        print(f"✅ LONG in BTC UP → риск 45$ (×1.5): {reason}")

    def test_smart_sizing_short_in_downtrend(self):
        """BTC DOWN + SHORT → множитель 1.5."""
        handler = self._make_handler()
        handler._btc_context = {"trend": "DOWN", "regime": "NORMAL", "delta_strength": -20.0}
        
        risk, mult, reason = handler._calculate_smart_risk(base_risk=30.0, signal_side="short")
        
        self.assertEqual(mult, 1.5)
        self.assertEqual(risk, 45.0)
        print(f"✅ SHORT in BTC DOWN → риск 45$ (×1.5): {reason}")

    def test_smart_sizing_long_in_downtrend(self):
        """BTC DOWN + LONG → множитель 0.5 (штраф)."""
        handler = self._make_handler()
        handler._btc_context = {"trend": "DOWN", "regime": "NORMAL", "delta_strength": -20.0}
        
        risk, mult, reason = handler._calculate_smart_risk(base_risk=30.0, signal_side="long")
        
        self.assertEqual(mult, 0.5)
        self.assertEqual(risk, 15.0)
        print(f"✅ LONG in BTC DOWN → риск 15$ (×0.5 штраф): {reason}")

    def test_smart_sizing_flat_regime(self):
        """BTC FLAT → множитель 1.0 (нейтрально)."""
        handler = self._make_handler()
        handler._btc_context = {"trend": "FLAT", "regime": "NORMAL", "delta_strength": 0.0}
        
        risk, mult, reason = handler._calculate_smart_risk(base_risk=30.0, signal_side="long")
        
        self.assertEqual(mult, 1.0)
        self.assertEqual(risk, 30.0)
        print(f"✅ BTC FLAT → риск 30$ (×1.0 нейтрально): {reason}")

    def test_smart_sizing_impulsive_regime(self):
        """IMPULSIVE режим → множитель 0.7 (защита от ловли ножей)."""
        handler = self._make_handler()
        handler._btc_context = {"trend": "UP", "regime": "IMPULSIVE", "delta_strength": 500.0}
        
        risk, mult, reason = handler._calculate_smart_risk(base_risk=30.0, signal_side="long")
        
        self.assertEqual(mult, 0.7)
        self.assertAlmostEqual(risk, 21.0, places=2)
        print(f"✅ IMPULSIVE regime → риск 21$ (×0.7 защита): {reason}")

    def test_btc_context_update_via_event(self):
        """Проверка: событие BTC_CONTEXT_UPDATED обновляет локальный контекст."""
        import asyncio
        from trading.handlers.signal_handler import SignalHandlerMixin
        
        class TestHandler(SignalHandlerMixin):
            def __init__(self):
                super().__init__()
            def get_trader(self, symbol):
                return None
        
        handler = TestHandler()
        
        # Имитируем событие
        event = type('Event', (), {
            'payload': {
                "trend": "UP",
                "regime": "NORMAL",
                "delta_strength": 25.5,
                "current_price": 79500.0
            }
        })()
        
        # Вызываем синхронно через asyncio
        loop = asyncio.new_event_loop()
        loop.run_until_complete(handler._on_btc_context_updated(event))
        loop.close()
        
        self.assertEqual(handler._btc_context["trend"], "UP")
        self.assertEqual(handler._btc_context["regime"], "NORMAL")
        self.assertEqual(handler._btc_context["delta_strength"], 25.5)
        print("✅ BTC_CONTEXT_UPDATED обновляет локальный контекст")

if __name__ == "__main__":
    # Запускаем тесты с подробным выводом
    unittest.main(verbosity=2)