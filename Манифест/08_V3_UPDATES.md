# 📋 Обновление файлов Манифеста 01-06

Отлично! Давай обновим каждый файл точечно, внося только необходимые изменения с учетом новых концепций (Stable Core, Analytics Layer, Identifiers). Я буду давать **конкретные блоки для замены/добавления**, чтобы ты мог быстро внести правки.

---

##  Файл 01: `docs/01_CORE_MODULES.md`

### Что меняем:
1. Добавляем раздел про `ID Registry` (`core/identifiers.py`)
2. Обновляем описание EventBus (типизированные события)
3. Добавляем связь со Stable Core

### Конкретные изменения:

**1. В начало файла, после заголовка, добавь:**

```markdown
**Версия:** 3.0 (Stable Core + Identifiers)  
**Дата обновления:** 20 августа 2026
```

**2. Найди раздел про EventBus и добавь после описания:**

```markdown
### 🔗 Связь с ID Registry (v3.0)

Все типы событий теперь централизованы в `core/identifiers.py` (enum `EventType`). Это обеспечивает:
- Типобезопасность при публикации/подписке
- Автодополнение в IDE
- Защиту от опечаток

**Пример использования:**
```python
from core.identifiers import EventType

# Было (хрупко):
await bus.publish("SIGNAL_GENERATED", ...)

# Стало (типобезопасно):
await bus.publish(EventType.SIGNAL_GENERATED, ...)
```

**Будущее (Analytics Layer):** EventBus будет расширен событиями `CANDLE_UPDATE`, `ORDERBOOK_UPDATE`, `TRADE_UPDATE`, `WHALE_DETECTED` для аналитического слоя.
```

**3. В конец файла добавь новый раздел:**

```markdown
---

## 📚 6. ID Registry (`core/identifiers.py`) — v3.0

### 📌 Назначение
Единый словарь всех идентификаторов платформы: статусов, событий, ролей ордеров, причин закрытия и т.д.

### ️ Роль в архитектуре
**"Словарь платформы".** Обеспечивает типобезопасность и защиту от опечаток. Часть Stable Core.

### 📋 Категории идентификаторов

| Категория | Enum | Количество значений |
| :--- | :--- | :--- |
| Статусы паспорта | `PassportStatus` | 13 |
| Типы событий | `EventType` | 17 |
| Типы ордеров | `OrderType` | 7 |
| Стороны | `OrderSide`, `PositionSide` | 5 |
| Роли ордеров | `OrderRole` | 4 |
| Причины закрытия | `ExitReason` | 8 |
| Источники событий | `EventSource` | 10 |
| Стратегии | `StrategyName` | 5 |
| Биржи | `Exchange` | 2 |
| Типы рыночных событий | `MarketEventType` | 6 |
| Уровни логирования | `LogLevel` | 5 |
| Метрики аналитики | `MetricType` | 11 |
| Режимы волатильности | `VolatilityMode` | 3 |
| Режимы входа | `EntryMode` | 2 |
| Типы спуфинга | `SpoofingType` | 6 |
| Типы китов | `WhaleType` | 3 |
| Статусы стен | `WallStatus` | 6 |

**Итого:** 17 категорий, ~113 значений.

### ️ Принципы использования
1. **Единственный источник истины** — все идентификаторы только здесь.
2. **`.value` при сериализации** — в JSON/логи/биржу передаем строку.
3. **Enum напрямую для сравнений** — в коде сравниваем через enum.

### 🔗 Полная документация
См. `docs/09_IDENTIFIERS.md` — детальное описание каждого enum'а с примерами.

### ⚠️ Техдолг
- 🔴 Сейчас идентификаторы разбросаны по коду как строковые литералы.
- 🟡 Нужна постепенная миграция модуль за модулем.
-  После миграции — написать тест на полноту покрытия.
```

---

## 📄 Файл 02: `docs/02_ADAPTERS.md`

### Что меняем:
1. Обновить статус ChannelRouter (мертв, но это нормально в контексте Stable Core)
2. Добавить упоминание о будущем Normalization Layer

### Конкретные изменения:

**1. В начало файла обнови версию:**

```markdown
**Версия:** 3.0 (Stable Core + Analytics Layer)  
**Дата обновления:** 20 августа 2026
```

**2. В разделе про ChannelRouter добавь примечание:**

```markdown
### 📋 Статус (v3.0)
⚠️ **Не используется в Stable Core.** `Trader` получает прямые ссылки на `BinanceRestClient` и `BinanceWsAdapter`, работая с REST напрямую. Router инициализируется в `main.py`, но никуда не передаётся.

**Почему это нормально:** В контексте Stable Core мы работаем только с фьючерсами Binance. Router понадобится при добавлении Spot WS Adapter и переходе на бирже-независимую архитектуру (Analytics Layer).

**План активации:** После реализации `Normalization Layer` (MarketEvent) и `SpotWsAdapter`.
```

**3. В конец файла добавь раздел:**

```markdown
---

## 🔮 4. Будущее: Normalization Layer (Analytics Layer)

### 📌 Назначение
Единый формат событий для всех бирж (`MarketEvent`), позволяющий платформе работать с Binance, Bybit и другими биржами без изменения бизнес-логики.

### 🏗️ Архитектура
```
Binance Spot WS ──┐
                  ├──→ Normalizer → MarketEvent → EventBus → Strategies
Binance Futures WS
```

### 📋 Структура MarketEvent
```python
@dataclass
class MarketEvent:
    exchange: str          # 'binance', 'bybit'
    symbol: str            # 'SOLUSDT'
    timestamp_ms: int      # UTC миллисекунды
    event_type: str        # 'trade', 'depth', 'candle', 'liquidation'
    sequence_id: int       # Для валидации порядка
    data: Dict             # Бирже-специфичные поля
```

### 🔗 Полная документация
См. `docs/08_ANALYTICS_LAYER.md` — детальное описание аналитического слоя.
```

---

##  Файл 03: `docs/03_TRADING_CORE.md`

### Что меняем:
1. Обновить описание Orchestrator (миксины)
2. Убрать упоминание старого RecoveryManager
3. Добавить связь со Stable Core

### Конкретные изменения:

**1. В начало файла обнови версию:**

```markdown
**Версия:** 3.0 (Stable Core + Миксины)  
**Дата обновления:** 20 августа 2026
```

**2. В разделе про Orchestrator добавь примечание:**

```markdown
### 🏗️ Архитектура миксинов (v2.0)

```python
class Orchestrator(EventHandlers, RecoveryMixin, MonitorMixin):
    def __init__(self, ...):
        # Инициализация ядра
        self.bus = event_bus
        self.passport_manager = passport_manager
        self.repository = passport_repository
        self.state_manager = state_manager
        self.config = config
        self.json_logger = json_logger
        self.traders: Dict[str, Trader] = {}
        self._running = False
        self._subscribe_to_events()
```

**Принцип Stable Core:** Orchestrator — часть ядра. Его интерфейс (методы `register_trader()`, `get_trader()`, `set_risk_manager()`, `start()`, `stop()`) не меняется. Внутренняя реализация может эволюционировать (добавление новых миксинов) без влияния на другие модули.
```

**3. В разделе про RecoveryMixin добавь:**

```markdown
### 🗑️ Удалено в v3.0: старый RecoveryManager

Ранее существовал отдельный класс `RecoveryManager` (файл `trading/recovery_manager.py`) с методом `recover()`. Он был удален, так как:
- Метод `get_order_status()` в `Trader` отключен
- Функционал полностью покрыт `RecoveryMixin`
- Дублировал логику startup recovery

**Результат:** Упрощена архитектура, устранен мертвый код.
```

---

##  Файл 04: `docs/04_RISK_LIFECYCLE.md`

### Что меняем:
1. Полностью убрать раздел про старый RecoveryManager
2. Обновить описание RiskManager (Страж, не Калькулятор)

### Конкретные изменения:

**1. В начало файла обнови версию:**

```markdown
**Версия:** 3.0 (Stable Core)  
**Дата обновления:** 20 августа 2026
```

**2. Найди раздел про RecoveryManager (если есть) и полностью удали его. Замени на:**

```markdown
## ♻️ 3. `trading/recovery.py` — Реаниматолог (RecoveryMixin, v3.0)

### 📌 Назначение
Восстанавливает состояние платформы после перезапуска. **В версии 3.0** вынесен в миксин `RecoveryMixin`, который наследуется `Orchestrator`.

### 🗑️ Удалено: старый RecoveryManager
Класс `RecoveryManager` (файл `trading/recovery_manager.py`) удален. Его функционал полностью покрыт `RecoveryMixin`. Метод `recover()` не работал из-за отключенного `get_order_status()`.
```

**3. В разделе про RiskManager добавь:**

```markdown
### 🛡️ Архитектурный сдвиг v3.0: От Калькулятора к Стражу

Ранее `RiskManager` сам рассчитывал уровни SL/TP. В версии 3.0 ответственность разделена:
1. **EventHandlers** запрашивает у `Trader` расчет уровней (`calculate_exit_levels`) и **сразу записывает их в паспорт** до публикации события.
2. **RiskManager** получает событие `POSITION_OPENED`, проверяет, что уровни уже проставлены (`sl_price != 0`), и регистрирует "стража" (выставляет ордера на биржу).

**Принцип Stable Core:** RiskManager — часть ядра. Его контракт (подписка на `POSITION_OPENED`, выставление TP/SL с `reduce_only=True`) не меняется.
```

---

##  Файл 05: `docs/05_STRATEGIES_AND_MAIN.md`

### Что меняем:
1. Обновить описание main.py (убран REST-опрос)
2. Добавить упоминание о будущем Event-Driven подходе
3. Обновить статус Breakout

### Конкретные изменения:

**1. В начало файла обнови версию:**

```markdown
**Версия:** 3.0 (Stable Core + REST-оптимизация)  
**Дата обновления:** 20 августа 2026
```

**2. В разделе про main.py найди описание основного цикла и обнови:**

```markdown
### ⚙️ Основной цикл (`_main_loop`) — v3.0

Цикл работает бесконечно с интервалом в 2 секунды (`await asyncio.sleep(2)`).

**Шаг 1: Настройка WebSocket и маршрутизация событий**
- Получает `listen_key` для User Data Stream.
- Подписывается на стакан (`depth`) и пользовательские данные (`user_data`).
- Маршрутизация WS → EventBus:
  - `ORDER_TRADE_UPDATE` (исполнение ордеров) → публикуется в EventBus.
  - `ACCOUNT_UPDATE` (изменение баланса/позиции) → публикуется в EventBus.
  - `depthUpdate` (стакан) → НЕ публикуется в EventBus, а сохраняется во внутренние переменные `self.ws_price` и `self.ws_orderbook`.
- Настраивает `on_ws_reconnect`: при обрыве связи обновляет `listen_key` и публикует `SYNC_REQUEST`.

**Шаг 2: Торговый цикл (Polling) — v3.0**

Каждые 2 секунды:
1. Получение цены: берёт `self.ws_price`. Если WS ещё не дал цену, делает fallback-запрос стакана через REST.
2. ✅ **Позиция НЕ запрашивается через REST** (исправлено 20 августа 2026). Позиция обновляется только через WS `ACCOUNT_UPDATE`.
3. Проверка занятости: если `is_symbol_busy()` → `continue`.
4. Формирование контекста: собирает `context = {'symbol', 'current_price', 'orderbook'}`. ⚠️ Нет `candles`!
5. Генерация сигналов: вызывает `_generate_signals(context)`.
6. Публикация: если сигналы есть и символ свободен, публикует только первый сигнал (`signals[0]`).

### 🚨 КРИТИЧЕСКИЕ ПРОБЛЕМЫ `main.py` (v3.0)

#### 🔴 Проблема 1: Стратегия Breakout мертва (нет свечей)
В контексте **НЕТ** поля `candles`! `BreakoutStrategy` проверяет `if not candles: return None` и никогда не сгенерирует сигнал.

**Решение:** добавить в `main.py` фоновую задачу, которая запрашивает свечи через REST (или собирает их из WS kline-стрима) и добавляет `candles` в `context`. **Планируется в рамках Analytics Layer.**

#### 🟡 Проблема 2: Polling вместо Event-Driven
`await asyncio.sleep(2)` означает, что платформа реагирует на рыночные изменения с задержкой до 2 секунд.

**Решение:** перейти на событийную модель (Event-Driven). Стратегии должны вызываться по событию `depthUpdate`. **Планируется в рамках Stable Core v1.1.**
```

---

##  Файл 06: `docs/06_CONFIGS.md`

### Что меняем:
1. Обновить описание trading.json (R:R будет исправлен)
2. Добавить упоминание о будущих конфигах для Analytics Layer

### Конкретные изменения:

**1. В начало файла обнови версию:**

```markdown
**Версия:** 3.0 (Stable Core)  
**Дата обновления:** 20 августа 2026
```

**2. В разделе про trading.json обнови описание проблемы R:R:**

```markdown
### 🚨 КРИТИЧЕСКАЯ ПРОБЛЕМА: Инвертированный Risk/Reward

Давай посчитаем реальные уровни для SOLUSDT (предположим, цена входа $150):
- ATR = 0.5 (статический, не зависит от волатильности).
- Stop-Loss = `entry_price - (ATR × 20.0)` = $150 - (0.5 × 20) = $140 (расстояние $10).
- Take-Profit 1 = `entry_price + (ATR × 2.0)` = $150 + (0.5 × 2) = $151 (расстояние $1).
- Take-Profit 2 = `entry_price + (ATR × 3.0)` = $150 + (0.5 × 3) = $151.5 (расстояние $1.5).

**Соотношение Риск/Прибыль (R:R):**
- Риск: $10 на сделку.
- Прибыль (TP1, 50% объёма): $1.
- Прибыль (TP2, 50% объёма): $1.5.
- Средняя прибыль = 0.5 × $1 + 0.5 × $1.5 = $1.25.
- **Итог: Платформа рискует $10, чтобы заработать $1.25. Соотношение R:R = 1 : 0.125.**

Это математически убыточная модель! Чтобы быть в безубытке, винрейт (процент побед) должен быть ~89%.

**Статус:**  **В работе** (Спринт Stable Core v1.0, задача #1).

**Рекомендации:**
- **Вариант 1:** Поменять местами: `atr_multiplier_sl: 2.0`, `atr_multiplier_tp1: 10.0`, `atr_multiplier_tp2: 15.0`.
- **Вариант 2:** Сделать `atr_value` динамическим (рассчитывать на основе свечей) и уменьшить его (например, до 0.05). **Планируется в рамках Analytics Layer.**
- **Вариант 3:** Добавить валидацию в `ExitCalculator`, которая проверяет, что `tp1_mult > sl_mult` и `tp2_mult > tp1_mult`.
```

**3. В конец файла добавь раздел:**

```markdown
---

## 🔮 6. Будущие конфиги (Analytics Layer)

При внедрении аналитического слоя появятся новые конфиги:

### `config/analytics.json`
```json
{
  "atr": {
    "period": 14,
    "outlier_filter_multiplier": 3.0
  },
  "whale_detector": {
    "min_window_trades": 100,
    "max_window_seconds": 120,
    "multiplier": 3.5,
    "absolute_minimum_usdt": 10000
  },
  "volume_profile": {
    "periods": ["1h", "4h", "1d"],
    "value_area_pct": 70
  }
}
```

### `config/strategies_v2.json` (Confidence Score)
```json
{
  "confidence_score": {
    "full_entry_threshold": 70,
    "reduced_entry_threshold": 50,
    "weights": {
      "absorption_confirmed": 30,
      "whale_cluster": 60,
      "real_wall": 25
    }
  }
}
```

**Связь:** `docs/08_ANALYTICS_LAYER.md`
```

---

##  Коммит всех изменений

После внесения правок выполни:

```powershell
git add docs/01_CORE_MODULES.md docs/02_ADAPTERS.md docs/03_TRADING_CORE.md docs/04_RISK_LIFECYCLE.md docs/05_STRATEGIES_AND_MAIN.md docs/06_CONFIGS.md
git commit -m "docs: update modules 01-06 with Stable Core, Analytics Layer, and Identifiers concepts"
git push origin main
```

---

## ✅ Итог

После этих обновлений Манифест будет полностью консистентным:
- **00_ARCHITECTURE.md** — главный обзор (обновлен)
- **01_CORE_MODULES.md** — ядро + ID Registry (обновлен)
- **02_ADAPTERS.md** — адаптеры + Normalization Layer (обновлен)
- **03_TRADING_CORE.md** — торговое ядро + миксины (обновлен)
- **04_RISK_LIFECYCLE.md** — защита + RecoveryMixin (обновлен)
- **05_STRATEGIES_AND_MAIN.md** — стратегии + REST-оптимизация (обновлен)
- **06_CONFIGS.md** — конфиги + будущие конфиги Analytics (обновлен)
- **07_KNOWN_ISSUES.md** — бэклог (обновлен)
- **08_ANALYTICS_LAYER.md** — аналитика (уже был)
- **09_IDENTIFIERS.md** — словарь идентификаторов (новый)
- **10_STABLE_CORE.md** — стабильное ядро (новый)
- **11_ROADMAP_AND_TASKS.md** — управление задачами (новый)

**Манифест v3.0 готов!** 🎉

**Что делаем дальше?**
- **Вариант А:** Переходим к реализации (создаем `core/identifiers.py`)
- **Вариант Б:** Начинаем с задачи #1 (исправление R:R в `trading.json`)
- **Вариант В:** Что-то еще?