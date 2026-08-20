# 🏛️ Архитектурный Манифест: PLAT_WALLS_NEW

**Версия документа:** 2.0 (Обновлено после рефакторинга Orchestrator и критических исправлений)
**Назначение:** Единый источник правды об архитектуре торговой платформы для автоматической торговли фьючерсами на Binance.

---

## 🎯 1. Назначение и Философия

`PLAT_WALLS_NEW` — это событийно-ориентированная (Event-Driven) торговая платформа, построенная на принципах слабой связности (loose coupling) и строгого разделения ответственности.

# 🏛️ Архитектурный Манифест: PLATO

**Версия документа:** 3.0 (Stable Core + Analytics Layer)  
**Дата последнего обновления:** 20 августа 2026  
**Назначение:** Единый источник правды об архитектуре торговой платформы для автоматической торговли фьючерсами на Binance/Bybit.

---

## 🎯 1. Назначение и Философия

`PLATO` — это событийно-ориентированная (Event-Driven) торговая платформа, построенная на принципах слабой связности (loose coupling), строгого разделения ответственности и **стабильного ядра (Stable Core)**.

### Три Столпа Архитектуры:

1. **Биржа — Абсолютный Источник Истины.** Внутреннее состояние платформы может устареть или повредиться. Единственная правда — это состояние позиции и ордеров на самой бирже. Все модули (особенно `RecoveryMixin` и `StateManager`) постоянно сверяются с биржей.

2. **Паспорт (Passport) — Внутренний SSOT.** Для самой платформы единым источником правды является `TradePassport`. Ни один модуль не хранит состояние сделки у себя — они читают и пишут только в Паспорт.

3. **EventBus — Нервная Система.** Модули не вызывают методы друг друга напрямую (за редкими исключениями). Они публикуют и слушают события, что позволяет легко добавлять новые компоненты без переписывания старых.

### Дополнительные Принципы (v3.0):

4. **Stable Core (Стабильное Ядро).** Платформа разделена на **ядро** (неизменяемый фундамент) и **расширения** (модули, которые можно добавлять/менять без риска сломать базу). См. `docs/10_STABLE_CORE.md`.

5. **Типобезопасность через ID Registry.** Все идентификаторы (статусы, события, роли) централизованы в `core/identifiers.py`. Никаких строковых литералов в бизнес-логике. См. `docs/09_IDENTIFIERS.md`.

6. **Бирже-независимость.** Архитектура готова к работе с несколькими биржами (Binance, Bybit) через нормализацию событий (`MarketEvent`). См. `docs/08_ANALYTICS_LAYER.md`.

---

## 🗺️ 2. Анатомия Платформы (Роли Компонентов)

Платформа построена по метафорическому принципу "организма":

| Метафора | Компонент | Файл | Ответственность |
| :--- | :--- | :--- | :--- |
| 🧠 **Мозг** | `Orchestrator` | `trading/orchestrator.py` | Координация через миксины. Делегирует обработку событий, восстановление и мониторинг. |
| 📡 **Обработчики событий** | `EventHandlers` | `trading/event_handlers.py` | Обработка SIGNAL_GENERATED, ORDER_TRADE_UPDATE, ACCOUNT_UPDATE. Создание паспортов, расчет уровней, отправка ордеров. |
| ♻️ **Восстановление** | `RecoveryMixin` | `trading/recovery.py` | Startup Recovery, блокировка символа при недоступности REST, закрытие позиций. |
| 📡 **Мониторинг** | `MonitorMixin` | `trading/monitor.py` | Фоновая проверка зависших ордеров, форсированный реконнект WS. |
| 👐 **Руки** | `Trader` | `trading/trader.py` | Только исполняет команды. Формирует запросы к API биржи, рассчитывает размер лота и уровни SL/TP. |
| 🛡️ **Щит** | `RiskManager` | `trading/risk_manager.py` | Выставляет защитные ордера (TP1, TP2, SL) на биржу сразу после открытия позиции. |
| 👂 **Уши** | `BinanceWsAdapter` | `adapters/binance_ws.py` | Слушает рынок (стакан) и аккаунт (исполнения, позиции) через WebSocket. |
| 🌉 **Мост** | `BinanceRestClient` | `adapters/binance_rest.py` | Отправляет команды на биржу через REST API (с HMAC-подписью). |
| 📖 **Паспорт** | `TradePassport` | `trading/passport.py` | Dataclass, хранящий всю историю и состояние конкретной сделки. |
|  **Регулировщик** | `StateManager` | `trading/state_manager.py` | Конечный автомат. Контролирует допустимые переходы статусов Паспорта. |
| ️ **Часовщик** | `LifecycleManager` | `trading/lifecycle_manager.py` | Следит за TTL лимитных входных ордеров (отменяет, не конвертирует в market). |
| 📚 **Словарь** | `ID Registry` | `core/identifiers.py` | Единый реестр всех enum'ов и констант платформы. |
| 📊 **Аналитики** | Стратегии | `strategies/*.py` | Генерируют сигналы на основе рыночных данных. Не знают про ордера и паспорта. |
| 🔬 **Аналитический слой** | `Analytics Layer` | `trading/data_collector/` | (Будущее) Сбор и обработка рыночных данных: ATR, HVN, имбаланс, киты. |

---

## 🔄 3. Жизненный Цикл Сделки (Data Flow)

Типовой сценарий от сигнала до закрытия:

```mermaid
sequenceDiagram
    participant Strategy as  Стратегия
    participant Main as  main.py
    participant Bus as 📡 EventBus
    participant Handlers as 📡 EventHandlers
    participant Trader as 👐 Trader
    participant Exchange as  Биржа
    participant Risk as ️ RiskManager
    
    Strategy->>Main: Возвращает Signal
    Main->>Bus: Публикует SIGNAL_GENERATED
    Bus->>Handlers: Доставляет событие
    Handlers->>Handlers: Проверяет is_symbol_busy()
    Handlers->>Trader: calculate_exit_levels() (SL/TP)
    Handlers->>Handlers: Создаёт TradePassport с уровнями
    Handlers->>Bus: Публикует POSITION_OPENED
    Bus->>Risk: Доставляет событие
    Risk->>Risk: Регистрирует стража (guard_registered)
    Handlers->>Trader: execute_order(client_order_id=signal_id)
    Trader->>Exchange: REST POST /order
    Exchange-->>Trader: Подтверждение
    Trader-->>Handlers: Результат
    Handlers->>Handlers: Добавляет ордер в паспорт
    Note over Exchange,Risk: Позиция открыта, защита на бирже
    Exchange-->>Main: WS: ORDER_TRADE_UPDATE (FILLED)
    Main->>Bus: Публикует событие
    Bus->>Handlers: Доставляет
    Handlers->>Handlers: Обновляет паспорт (OPEN, position_size, entry_price)




    

    Ключевые изменения в архитектуре (v2.0):
Разбиение Orchestrator на миксины — устранен "God Object", каждый миксин отвечает за свою зону.
Явный расчет уровней в EventHandlers — уровни SL/TP рассчитываются до создания паспорта, RiskManager только регистрирует стража.
Передача client_order_id — signal_id передается как client_order_id на биржу, что позволяет WS корректно обновлять паспорт.
Определение quantity из Trader — размер лота берется из trader._get_lot_size(), а не хардкодится.
📡 4. Шина Событий (EventBus)
EventBus реализован паттерном Publisher/Subscriber с параллельным исполнением обработчиков через asyncio.gather.
Ключевые события платформы:

Событие	Отправитель	Получатель	Назначение
`SIGNAL_GENERATED`	main.py	EventHandlers	Сигнал от стратегии
`POSITION_OPENED`	EventHandlers	RiskManager	Уведомление об открытии позиции
`ORDER_TRADE_UPDATE`	BinanceWsAdapter	EventHandlers	Исполнение ордера на бирже
`ACCOUNT_UPDATE`	BinanceWsAdapter	EventHandlers	Изменение позиции/баланса
`PASSPORT_CREATED`	EventHandlers	LifecycleManager	Запуск TTL для лимитных ордеров
`TTL_EXPIRED`	LifecycleManager	EventHandlers	Истечение времени лимитки
`WS_RECONNECT_FORCED`	MonitorMixin	main.py	Принудительный реконнект WS
`SYNC_REQUEST`	main.py	EventHandlers	Запрос синхронизации с биржей

🛡️ 5. Startup Recovery (Защита при старте)
Алгоритм восстановления при запуске:
Проверка локального состояния — если есть активный паспорт, синхронизируем с биржей.
Запрос позиции через REST (до 3 попыток по 5 сек).
Если REST недоступен — создаем BLOCKED паспорт и регистрируем в памяти (блокировка символа).
Если позиция найдена — создаем RECOVERY паспорт с явным расчетом SL/TP.
Публикация POSITION_OPENED — RiskManager регистрирует стража.
Критическая защита:
BLOCKED паспорт регистрируется в passport_manager._passports до сохранения на диск. Это гарантирует, что is_symbol_busy() вернет True и предотвратит открытие дублирующих ордеров.
6. Система Логирования (v2.0)
Формат JSONL (JSON Lines):
Каждая строка файла logs/platform_log.jsonl — самостоятельный JSON-объект:
{"timestamp": "2026-08-19T10:38:44.851533", "module": "orchestrator", "event": "signal_received", "level": "INFO", "data": {...}, "correlation_id": "PASS_..."}

Преимущества:
Читаемость при краше — файл всегда валиден, даже при аварийной остановке.
Ротация — автоматическая ротация при достижении 5 МБ.
Трассировка — поле correlation_id (passport_id) связывает все события сделки.
Фильтрация шума — игнорируются технические WS события (ping/pong, подписки).
Усечение payload — ORDER_TRADE_UPDATE логируется с 8 ключевыми полями вместо 40+.
🚨 7. КРИТИЧЕСКИЙ TECH DEBT (Бэклог Задач)
🔴 Блокеры (Blockers)
Инвертированный Risk/Reward в config/trading.json
Суть: atr_multiplier_sl = 20.0, а atr_multiplier_tp1 = 2.0. Стоп-Лосс в 10 раз больше Тейк-Профита.
Риск: Математически убыточная стратегия.
Решение: Исправить множители (SL=2, TP1=4, TP2=6) или сделать ATR динамическим.
Мёртвый Глобальный Risk-Менеджмент (config/risk.json)
Суть: Параметры max_daily_loss, max_drawdown, max_trades_per_hour загружаются, но нигде не проверяются.
Риск: При серии убытков платформа сольёт депозит.
Решение: Внедрить проверки лимитов в EventHandlers._on_signal() или создать GlobalRiskManager.
Стратегия Breakout не работает (Нет свечей)
Суть: BreakoutStrategy требует context['candles'], но main.py передаёт только цену и стакан.
Риск: Стратегия всегда возвращает None.
Решение: Добавить загрузку истории свечей в main.py.
Отключен RecoveryManager в main.py
Суть: В main.py вызов RecoveryManager.recover() закомментирован.
Риск: При перезапуске платформа "забывает" про открытые позиции.
Решение: Раскомментировать и доработать синхронизацию TP/SL ордеров.
Утечка Секретов
Суть: API-ключи лежат в config/exchange.json в открытом виде.
Риск: Компрометация ключей при коммите в Git.
Решение: Перенести ключи строго в secrets.json, добавить exchange.json в .gitignore.
🟡 Важные Улучшения (High Priority)
Отсутствие Переноса SL в Безубыток
Суть: В описании RiskManager заявлено, что при срабатывании TP1 SL переносится в безубыток. В коде этот метод отсутствует.
Решение: Реализовать логику в RiskManager или EventHandlers.
Polling вместо Event-Driven для Стратегий
Суть: main.py опрашивает стратегии каждые 2 секунды, а не по событию depthUpdate.
Риск: Задержка реакции на рынок.
Решение: Перевести вызов стратегий на колбэк depthUpdate.
Избыточные REST-запросы
Суть: В цикле main.py каждые 2 секунды делается rest.get_position().
Риск: Быстрое исчерпание лимитов Binance API.
Решение: Убрать запрос из цикла. Позиция должна обновляться только из WS ACCOUNT_UPDATE.
Статический ATR
Суть: atr_value = 0.5 берётся из конфига и не зависит от реальной волатильности.
Решение: Рассчитывать ATR динамически на основе последних N свечей.
PnL всегда равен нулю
Суть: При закрытии по TP/SL в StateManager передаётся gross_pnl=0.
Решение: Запрашивать историю сделок или брать PnL из WS ACCOUNT_UPDATE.
🟢 Рефакторинг и Чистота (Medium Priority)
Дублирование кода подписи в BinanceRestClient (нужно использовать общий _request).
Отладочные print() в критических модулях (заменить на JsonLogger).
Мёртвые параметры в стратегиях (min_confidence, min_wall_volume читаются, но не используются).
Отсутствие closes и volume валидации в стратегиях.
FAILED статус блокирует символ (не исключается из активных).
🛠️ 8. Принятые Архитектурные Решения (ADR)
ADR-1: Режим Хеджирования (Hedge Mode)
Платформа работает с positionSide=LONG/SHORT. Это позволяет держать разнонаправленные позиции, но требует явного указания стороны при отправке ордеров.
ADR-2: Exchange-Only для Защиты
TP и SL выставляются реальными ордерами на биржу, а не отслеживаются внутри платформы. Если платформа упадёт, защита сработает автономно.
ADR-3: Обработка Частичного Исполнения
TP1 закрывает 50% позиции, TP2 — оставшиеся 50%. SL изначально выставляется на 100% объёма (требует доработки после TP1).
ADR-4: Fallback на REST
Так как методы отправки ордеров через WS не реализованы, все команды идут через REST.
ADR-5: Миксины вместо God Object (v2.0)
Orchestrator разбит на три миксина: EventHandlers, RecoveryMixin, MonitorMixin. Это упрощает тестирование и поддержку.
ADR-6: Trader как источник истины для lot_size (v2.0)
Размер позиции берется из trader._get_lot_size(), а не из конфига напрямую. Trader знает конфигурацию биржи и минимальные лоты.
ADR-7: client_order_id = signal_id (v2.0)
Signal_id передается как client_order_id на биржу. Это позволяет WebSocket корректно связывать исполнения с паспортами.
ADR-8: JSONL для логирования (v2.0)
Переход на формат JSON Lines обеспечивает читаемость логов при аварийной остановке и упрощает ротацию.
📊 9. Структура Проекта (v2.0)
PLATO/
├── adapters/
│   ├── binance_rest.py      # REST клиент
│   ├── binance_ws.py        # WebSocket адаптер
│   └── channel_router.py    # Диспетчер каналов (не используется)
├── config/
│   ├── exchange.json        # Настройки подключения
│   ├── trading.json         # Параметры торговли
│   ├── risk.json            # Глобальный риск-менеджмент
│   ├── strategies.json      # Параметры стратегий
│   ── secrets.json         # API-ключи (не в Git!)
├── core/
│   ├── event_bus.py         # Шина событий
│   ├── types.py             # Типы данных и enums
│   ├── config_loader.py     # Загрузчик конфигов
│   ├── logger.py            # Консольный логгер
│   └── json_logger.py       # JSONL логгер с ротацией
├── strategies/
│   ├── base.py              # Базовый класс стратегии
│   ├── wall_fade.py         # Поиск стенок сопротивления
│   ├── absorption.py        # Дисбаланс объёмов
│   └── breakout.py          # Пробой уровней
├── trading/
│   ├── orchestrator.py      # Ядро (координация)
│   ├── event_handlers.py    # Обработчики событий
│   ├── recovery.py          # Восстановление (RecoveryMixin)
│   ├── monitor.py           # Мониторинг (MonitorMixin)
│   ├── passport.py          # Цифровой паспорт сделки
│   ├── passport_manager.py  # Кэш паспортов в памяти
│   ├── passport_repository.py # Персистентность на диск
│   ├── state_manager.py     # Конечный автомат
│   ├── trader.py            # Исполнитель ордеров
│   ├── exit_calculator.py   # Расчет SL/TP
│   ├── risk_manager.py      # Защитные ордера
│   ├── lifecycle_manager.py # TTL для лимиток
│   ── recovery_manager.py  # Восстановление после краша
├── logs/
│   ├── platform_log.jsonl   # Структурированный лог
│   └── passport_*.json      # Файлы паспортов
├── docs/
│   ├── 00_ARCHITECTURE.md   # Этот файл
│   ├── 01_CORE_MODULES.md   # Ядро платформы
│   ├── 02_ADAPTERS.md       # Адаптеры биржи
│   ├── 03_TRADING_CORE.md   # Торговое ядро
│   ├── 04_RISK_LIFECYCLE.md # Защита и восстановление
│   ├── 05_STRATEGIES_AND_MAIN.md # Стратегии и main.py
│   ├── 06_CONFIGS.md        # Конфигурационные файлы
│   ── 07_KNOWN_ISSUES.md   # Бэклог задач
├── main.py                  # Точка входа
└── README.md                # Краткое описание