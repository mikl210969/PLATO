# 📌 Текущий статус (обновляется после каждого сеанса)

## 📌 Текущий статус (Обновлено: 2026-08-25)
**Версия:** v1.1-Stable-Testnet

### 🏗 Архитектурные изменения и Hedge Mode
- **Полный переход на Hedge Mode**: Во всех модулях (`trader.py`, `risk_manager.py`, `orchestrator.py`, `position_monitor.py`) при отправке ордеров явно передаётся параметр `position_side` ("LONG" или "SHORT").
- **Удалён `reduce_only`**: Параметр `reduce_only=True` полностью убран из вызовов закрытия позиций, так как в Hedge Mode Binance он запрещён и вызывает ошибку `-1106`.
- **Отключение дублирования**: В `orchestrator.py` закомментированы вызовы `start_position_monitor()` и `stop_position_monitor()`. Логикой TP/SL и закрытия позиций теперь управляет исключительно `RiskManager`, что предотвращает гонку состояний и двойные закрытия.

### 🐛 Критические исправления багов
1. **Binance API Error -4015 (Client order id length)**: В `trader.py` добавлена принудительная обрезка `client_order_id` до 35 символов (`[:35]`) перед отправкой на биржу. Это защищает от падения при генерации длинных ID (например, `CLOSE_SL_HIT_PASS_...`).
2. **Потеря потока цен при реконнекте (WS DEAD)**: В `main.py` в обработчик `on_ws_reconnect` добавлен вызов `await self.ws.subscribe_depth(self.symbol)`. Теперь при обрыве связи бот заново подписывается и на `user_data`, и на стакан, предотвращая "ослепление" RiskManager.
3. **AttributeError в StateManager**: В `event_handlers.py` исправлена передача данных при ошибке ордера: `result.get('error')` теперь оборачивается в словарь `{'error': ...}`, так как `state_manager.handle_event` ожидает `Dict`, а не `str`.
4. **Windows BOM в JSON**: В `core/config_loader.py` чтение файлов изменено на `encoding="utf-8-sig"`, что предотвращает `JSONDecodeError` при наличии скрытых BOM-символов в `secrets.json`.
5. **Импорт datetime**: В `event_handlers.py` исправлен устаревший вызов `datetime.datetime.now` на корректный `datetime.now(timezone.utc)`.

### 🧹 Рефакторинг и чистота кода
- **LifecycleManager**: Шумные отладочные `print` заменены на структурированные вызовы `self._log()` (JSON Logger). Оставлен только один финальный `print` для подтверждения запуска таймера.
- **RiskManager**: Метод `_close_market` теперь корректно обновляет статус паспорта (`CLOSED`), рассчитывает PnL и сохраняет изменения в репозиторий после успешного рыночного закрытия.
- **EventHandlers (Sync)**: Реализована полноценная логика обработки события `SYNC_REQUEST`. Теперь при потере WS-событий бот сверяет размер позиции через REST и корректно закрывает паспорт, если позиция была закрыта вручную на бирже.

### 💻 Инфраструктура и развёртывание
- Проект успешно клонирован и настроен на удалённой машине (Windows 10) через GitHub.
- Создан и настроен `.gitignore` (исключает `venv/`, `__pycache__/`, `config/secrets.json`).
- Настроено виртуальное окружение (`venv`) с актуальными зависимостями: `aiohttp`, `websockets`, `pytest`, `pytest-asyncio`.
- Все изменения закоммичены и отправлены в ветку `main` репозитория `mikl210969/PLATO`.

### ✅ Статус тестирования
- **Unit/Integration Tests (`pytest tests/ -v`)**: 27 / 27 passed ✅
- **Test Stand (`python run_test_stand.py`)**: 19 / 19 passed ✅ (Сценарии: внутренний стоп TP1→SL и SYNC после ручного закрытия).
- **Live Testnet Run**: Платформа работает стабильно. Реконнекты WS проходят гладко, TTL-таймеры запускаются, ошибки длины ID и режима Hedge Mode устранены.

### ⚠️ Известные проблемы / Технический долг
- `PositionMonitor` формально присутствует в кодовой базе, но отключён в `Orchestrator`. В будущем его стоит либо полностью удалить, либо перепрофилировать под другие задачи (например, только для трейлинга), чтобы не вводить в заблуждение.
- В логах `main.py` иногда встречаются временные ошибки `[REST] Failed to get position` или `TimeoutError` при обновлении listen key. Это нормальное поведение тестнета Binance; система успешно обрабатывает их через механизмы retry и не падает.

### 🚀 Следующие шаги (приоритет)
1. Наблюдение за полным жизненным циклом ордера на тестнете (исполнение → TP1/SL или отмена по TTL).
2. Доработка логики торговых стратегий (`strategies/wall_fade.py`, `absorption.py`).
3. Создание файла `requirements.txt` для стандартизации развёртывания.
# 🏗 Архитектура и модули

## Кратко
Торговый бот для Binance (spot/futures). Python 3.11, asyncio.
Архитектура: адаптеры → ядро → стратегии → торговый движок.
**Режим работы: Hedge Mode** (position_side передаётся явно, reduceOnly НЕ используется).

## Стек
- Python 3.11, asyncio
- Binance REST + WebSocket (собственные адаптеры)
- pytest для тестов
- JSON-конфиги (без БД)

## Структура модулей

### adapters/ — связь с биржей
- `binance_rest.py` — REST-клиент Binance
- `binance_ws.py` — WebSocket-клиент (стримы)
- `channel_router.py` — маршрутизация событий WS к обработчикам

### core/ — ядро системы
- `config_loader.py` — загрузка и валидация JSON-конфигов
- `event_bus.py` — pub/sub шина событий (см. "Ключевые сущности")
- `json_logger.py` — структурированное логирование
- `logger.py` — обёртка над логгером
- `types.py` — enum и dataclass (см. "Ключевые сущности")

### strategies/ — торговые стратегии
- `base.py` — абстрактный базовый класс Strategy
- Конкретные стратегии (`absorption`, `breakout`, `wall_fade`) — не описываем

### trading/ — торговый движок
- `orchestrator.py` — главный координатор (миксины: EventHandlers, Monitor, Recovery, PositionMonitor)
- `passport.py` — dataclass `TradePassport` (SSOT)
- `passport_manager.py` — CRUD-кэш паспортов в памяти
- `passport_repository.py` — хранилище паспортов
- `state_manager.py` — машина состояний паспорта
- `trader.py` — исполнитель команд (ордера, закрытие позиций)
- `event_handlers.py` — `EventHandlersMixin` для Orchestrator
- `monitor.py` — `MonitorMixin` (stuck orders)
- `recovery.py` — `RecoveryMixin`
- `position_monitor.py` — `PositionMonitor` (наследуется Orchestrator)
- `risk_manager.py`, `exit_calculator.py`, `lifecycle_manager.py`, `base_mixin.py`

### config/ — JSON-конфиги
- `exchange.json`, `risk.json`, `strategies.json`, `trading.json`
- `secrets.json` — API-ключи (в .gitignore!)
- `secrets.example.json` — шаблон

### tests/ — pytest-тесты
- `test_external_close.py`, `test_order_placement.py`, `test_passport_statuses.py`
- `test_position_monitor.py`, `test_recovery.py`, `test_rest_fallback.py`, `test_ttl_expired.py`

### Корневые файлы
- `main.py` — точка входа
- `test_stand.py` / `run_test_stand.py` — тестовый стенд
- `diag_state.py` — диагностика

## Ключевые сущности

### Типы (`core/types.py`)

**Enum:**
- `OrderType`: MARKET, LIMIT
- `OrderSide`: BUY, SELL
- `OrderStatus`: NEW, FILLED, PARTIALLY_FILLED, CANCELED, REJECTED, EXPIRED
- `PassportStatus`: SIGNAL_GENERATED, ORDER_SENT, ORDER_ACK, LIMIT_ON_BOOK, PARTIAL_FILL, OPEN, PARTIAL_CLOSE, CLOSING, CANCELED, CLOSED, FAILED, UNKNOWN

**Dataclass:**
- `Signal` — сигнал от стратегии (signal_id, symbol, side, entry_price, confidence, strategy, metadata)
- `Order` — ордер (order_id, client_order_id, symbol, side, order_type, price, quantity, filled_quantity, status, timestamp)
- `Position` — позиция (symbol, side, size, entry_price, current_price, unrealized_pnl)

### TradePassport (`trading/passport.py`)
Dataclass, единый источник правды (SSOT) по сделке.

**Ключевые поля:**
- `passport_id` — формат: `PASS_YYYYMMDD_HHMMSS_<uuid6>`
- `symbol`, `status` (из PassportStatus), `signal_id`, `strategy`, `side`
- `entry_price`, `confidence`, `sl_price`, `tp1_price`, `tp2_price`
- `position_size`, `position_entry_price`
- `orders: List[Dict]`, `timeline: List[Dict]`
- `created_at`, `updated_at`, `closed_at`
- `exit_reason`, `exit_price`, `gross_pnl`, `commission`, `net_pnl`

**Ключевые методы:**
- `transition_to(new_status, reason)` — смена статуса + запись в timeline
- `add_timeline_event(event_type, details)` — добавить событие
- `add_order(order: Dict)` — добавить ордер
- `close(exit_reason, exit_price, gross_pnl, commission)` — закрыть паспорт
- `to_dict()` — сериализация

### PassportManager (`trading/passport_manager.py`)
CRUD-кэш паспортов в памяти. Только хранение, без логики статусов.

**Методы:**
- `create(symbol, signal_id, strategy, side, entry_price, confidence)` — создать паспорт со статусом SIGNAL_GENERATED
- `get(passport_id)`, `get_all()`, `get_active()`, `get_by_symbol(symbol)`
- `get_active_by_symbol(symbol)` — найти активный паспорт по символу (используется в Orchestrator.close_position)
- `is_symbol_busy(symbol)` — проверить, занят ли символ
- `update(passport)`, `remove(passport_id)`

**Активный паспорт** = статус не CLOSED и не CANCELED.

### StateManager (`trading/state_manager.py`)
Машина состояний паспорта. Управляет переходами статусов.

**Карта переходов:**
- SIGNAL_GENERATED → ORDER_SENT, CANCELED, CLOSED
- ORDER_SENT → ORDER_ACK, OPEN (market), CANCELED, FAILED
- ORDER_ACK → LIMIT_ON_BOOK, OPEN, CANCELED, FAILED
- LIMIT_ON_BOOK → OPEN, CANCELED, FAILED
- OPEN → PARTIAL_CLOSE, CLOSING, CLOSED, CANCELED
- PARTIAL_CLOSE → OPEN, CLOSING, CLOSED
- CLOSING → CLOSED, FAILED
- CANCELED, CLOSED, FAILED — терминальные

**Ключевые методы:**
- `can_transition(current, new)` — проверить разрешение
- `transition(passport, new_status, reason)` — выполнить переход + вывод с эмодзи в консоль
- `handle_event(passport, event_type, event_data)` — обработать событие и выполнить переход
  - События: ORDER_SENT, ORDER_ACK, ORDER_FILLED, ORDER_PARTIAL, ORDER_CANCELED, ORDER_FAILED, POSITION_CLOSED, POSITION_CLOSING, PARTIAL_CLOSE
- `sync_with_exchange(passport, exchange_status, position_size)` — синхронизация с биржей (например, если позиция закрылась извне)

### EventBus (`core/event_bus.py`)
Асинхронная pub/sub шина событий.

**Event dataclass:**
- `type`, `source`, `payload: Dict`, `symbol`, `correlation_id`, `timestamp`

**Методы:**
- `subscribe(event_type, handler)` — подписаться (handler — async функция)
- `publish(event_type, source, payload, symbol, correlation_id)` — опубликовать
- `clear()` — очистить подписки

**Особенности:**
- Параллельный вызов обработчиков через `asyncio.gather`
- Ловит исключения в обработчиках и печатает traceback
- Если нет подписчиков — событие игнорируется

### Trader (`trading/trader.py`)
Исполнитель команд. Только отправляет ордера, без логики принятия решений.

**Ключевые методы:**
- `execute_order(symbol, side, quantity, order_type, client_order_id, passport_id, reduce_only, limit_price, stop_price, position_side)`
  - Поддерживает: market, limit, stop_market, stop_limit
  - **Hedge Mode**: `position_side` передаётся явно ('LONG'/'SHORT'), `reduce_only` НЕ используется
- `close_position(symbol, quantity, exit_reason, exit_price, position_side)`
  - Закрывает позицию рыночным ордером
  - **Hedge Mode**: `reduce_only=False`, `position_side` — сторона закрываемой позиции
- `cancel_order(symbol, order_id)` — устойчив к ошибке -2011 (Unknown order = успех)
- `calculate_exit_levels(side, entry_price, atr_value)` — через ExitCalculator
- `get_position_from_exchange(symbol)`, `get_order_status(symbol, order_id, client_order_id)`

**Важно:**
- `client_order_id` по умолчанию: `ORD_{passport_id}` или `CLOSE_{exit_reason}_{timestamp}`
- `limit_price` обязателен для limit и stop_limit
- `stop_price` обязателен для stop_market и stop_limit

### Orchestrator (`trading/orchestrator.py`)
Главный координатор. Наследует миксины: EventHandlersMixin, MonitorMixin, RecoveryMixin, PositionMonitor.

**Зависимости (в `__init__`):**
- `config`, `event_bus`, `passport_manager`, `passport_repository`, `state_manager`, `json_logger`

**Менеджеры (устанавливаются из `main.py`):**
- `set_risk_manager(rm)`, `set_lifecycle_manager(lm)`
- `register_trader(symbol, trader_instance)`, `get_trader(symbol)`

**Жизненный цикл:**
- `start()` → `perform_startup_recovery()` → `start_stuck_orders_monitor()` → `start_position_monitor()`
- `stop()` → `stop_monitors()` → `stop_position_monitor()`

**Ключевой метод:**
- `close_position(symbol, exit_reason, exit_price)` — находит активный паспорт через `passport_manager.get_active_by_symbol()`, вызывает `trader.close_position()`, публикует событие POSITION_CLOSING

**Логирование:**
- `_log(event, data)` — унифицированный метод с защитой от разных сигнатур `json_logger.log()`

## Правила кода
- Типизация через `dataclasses` / `TypedDict`
- Логирование через `json_logger` (структурированные JSON-логи)
- Все асинхронные операции — через `asyncio`
- Тесты — изолированные, с моками адаптеров
- Конфиги — только JSON
- Секреты — только в `secrets.json` (в `.gitignore`)
- **Миксины** — основной способ расширения Orchestrator
- **SSOT** — `TradePassport` единственный источник правды по сделке
- **Hedge Mode** — `position_side` передаётся явно, `reduceOnly` НЕ используется

## Документация
- `core/doc/` — внутренние заметки
- `DOC/` — статус работ (Open_work.txt, Closed_work.txt)