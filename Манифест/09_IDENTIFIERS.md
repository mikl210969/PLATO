# 📄 `docs/09_IDENTIFIERS.md`

```markdown
# 📚 Единый Словарь Идентификаторов (ID Registry)

**Версия:** 1.0  
**Файл:** `core/identifiers.py`  
**Статус:** Проектирование  
**Назначение:** Централизованный реестр всех констант, enum'ов и идентификаторов платформы. Единый источник истины для всех строковых литералов в коде.

---

## 🎯 Философия и Принципы

### Проблема, которую решает ID Registry

До введения единого словаря идентификаторы (статусы, типы событий, роли ордеров) были разбросаны по всему коду в виде строковых литералов:

```python
# Было — хрупко, подвержено опечаткам
if passport.status == "OPEN":
    ...
await bus.publish("SIGNAL_GENERATED", ...)
order['role'] = "TP1"
```

**Риски:**
- Опечатки не ловятся на этапе разработки (`"SINGAL_GENERATED"` вместо `"SIGNAL_GENERATED"`)
- Рефакторинг требует поиска по всему коду
- Нет автодополнения в IDE
- Невозможно итерироваться по всем возможным значениям

### Решение

```python
# Стало — типобезопасно, с автодополнением
if passport.status == PassportStatus.OPEN.value:
    ...
await bus.publish(EventType.SIGNAL_GENERATED, ...)
order['role'] = OrderRole.TP1.value
```

### Принципы использования

1. **Единственный источник истины** — все идентификаторы определены только в `core/identifiers.py`.
2. **Типобезопасность** — использование enum'ов вместо строковых литералов.
3. **Автодополнение** — IDE подсказывает доступные значения.
4. **Защита от опечаток** — компилятор/линтер ловит ошибки на этапе разработки.
5. **`.value` при сериализации** — при передаче в JSON/логи/биржу используем `.value`.
6. **Enum напрямую для сравнений** — в коде сравниваем через enum, не через строку.

---

## 📋 Категории идентификаторов

### 1. Статусы Паспорта (`PassportStatus`)

Описывает весь жизненный цикл сделки.

```python
class PassportStatus(Enum):
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    ORDER_SENT = "ORDER_SENT"
    ORDER_ACK = "ORDER_ACK"
    LIMIT_ON_BOOK = "LIMIT_ON_BOOK"
    OPEN = "OPEN"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"      # Для recovery (REST недоступен)
    RECOVERY = "RECOVERY"    # Для recovery (позиция найдена)
```

**Терминальные статусы** (из них нет переходов): `CLOSED`, `CANCELED`, `FAILED`.

**Активные статусы** (символ считается занятым): все, кроме `CLOSED` и `CANCELED`.

⚠️ **Техдолг:** `FAILED` должен быть терминальным, но сейчас не исключается из активных в `PassportManager.get_active()`.

---

### 2. Типы Событий (`EventType`)

Все события, которые публикуются в EventBus.

```python
class EventType(Enum):
    # Сигналы и паспорта
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    PASSPORT_CREATED = "PASSPORT_CREATED"
    
    # Позиции
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSING = "POSITION_CLOSING"
    POSITION_CLOSED = "POSITION_CLOSED"
    
    # Ордера
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELED = "ORDER_CANCELED"
    ORDER_PARTIAL = "ORDER_PARTIAL"
    
    # Аккаунт
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    
    # Жизненный цикл
    TTL_EXPIRED = "TTL_EXPIRED"
    
    # Срабатывание защиты
    TP1_FILLED = "TP1_FILLED"
    TP2_FILLED = "TP2_FILLED"
    SL_FILLED = "SL_FILLED"
    
    # Восстановление
    SYNC_REQUEST = "SYNC_REQUEST"
    WS_RECONNECT_FORCED = "WS_RECONNECT_FORCED"
    
    # Аналитический слой (будущее)
    CANDLE_UPDATE = "CANDLE_UPDATE"
    ORDERBOOK_UPDATE = "ORDERBOOK_UPDATE"
    TRADE_UPDATE = "TRADE_UPDATE"
    WHALE_DETECTED = "WHALE_DETECTED"
    SPOOFING_DETECTED = "SPOOFING_DETECTED"
```

️ **Техдолг:** Сейчас типы событий передаются как сырые строки. Нужно мигрировать на enum.

---

### 3. Типы Ордеров (`OrderType`)

```python
class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    STOP = "STOP"              # Binance legacy
    TAKE_PROFIT = "TAKE_PROFIT"  # Binance legacy
```

---

### 4. Стороны Ордеров и Позиций

```python
class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class PositionSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"  # One-way mode (не используется, платформа в Hedge Mode)
```

---

### 5. Роли Ордеров (`OrderRole`)

Используется для идентификации назначения ордера в рамках паспорта.

```python
class OrderRole(Enum):
    ENTRY = "ENTRY"      # Входной ордер
    TP1 = "TP1"          # Первый тейк-профит (50%)
    TP2 = "TP2"          # Второй тейк-профит (50%)
    SL = "SL"            # Стоп-лосс (100%)
```

⚠️ **Техдолг:** Сейчас роль определяется по префиксу `client_order_id` (`TP1_`, `SL_`). Нужно ввести явное поле `role` в структуре ордера.

---

### 6. Причины Закрытия (`ExitReason`)

```python
class ExitReason(Enum):
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    SL_HIT = "SL_HIT"
    EXTERNAL_CLOSE = "EXTERNAL_CLOSE"      # Закрытие вручную или внешним фактором
    TTL_EXPIRED = "TTL_EXPIRED"            # Лимитный ордер не исполнился
    MANUAL_CLOSE = "MANUAL_CLOSE"          # Ручное закрытие через терминал
    RECOVERY_CLOSE = "RECOVERY_CLOSE"      # Закрытие при восстановлении
    RISK_LIMIT = "RISK_LIMIT"              # Превышен лимит риска
    CONNECTION_LOST = "CONNECTION_LOST"    # Потеря связи > 10 сек
```

---

### 7. Источники Событий (`EventSource`)

```python
class EventSource(Enum):
    STRATEGY = "strategy"
    ORCHESTRATOR = "orchestrator"
    EVENT_HANDLERS = "event_handlers"
    BINANCE_WS = "binance_ws"
    BINANCE_REST = "binance_rest"
    RISK_MANAGER = "risk_manager"
    LIFECYCLE_MANAGER = "lifecycle_manager"
    RECOVERY_MIXIN = "recovery_mixin"
    MONITOR_MIXIN = "monitor_mixin"
    ANALYTICS = "analytics"
    PLATFORM = "platform"  # main.py
```

---

### 8. Стратегии (`StrategyName`)

```python
class StrategyName(Enum):
    WALL_FADE = "WallFade"
    ABSORPTION = "Absorption"
    BREAKOUT = "Breakout"
    LARGE_ORDER_FOLLOW = "LargeOrderFollow"    # Будущее
    HVN_REVERSAL = "HVNReversal"              # Будущее
```

---

### 9. Биржи (`Exchange`)

```python
class Exchange(Enum):
    BINANCE = "binance"
    BYBIT = "bybit"
```

---

### 10. Типы Рыночных Событий (`MarketEventType`)

Для аналитического слоя (Раздел 8 Манифеста).

```python
class MarketEventType(Enum):
    TRADE = "trade"
    DEPTH = "depth"
    CANDLE = "candle"
    LIQUIDATION = "liquidation"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
```

---

### 11. Уровни Логирования (`LogLevel`)

```python
class LogLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
```

---

### 12. Метрики Аналитики (`MetricType`)

```python
class MetricType(Enum):
    ATR = "atr"
    NORMALIZED_ATR = "normalized_atr"
    OBI = "obi"                    # Order Book Imbalance
    CVD = "cvd"                    # Cumulative Volume Delta
    HVN = "hvn"                    # High Volume Node
    LVN = "lvn"                    # Low Volume Node
    POC = "poc"                    # Point of Control
    VAH = "vah"                    # Value Area High
    VAL = "val"                    # Value Area Low
    WHALE_THRESHOLD = "whale_threshold"
    ATS = "ats"                    # Average Trade Size
```

---

### 13. Режимы Волатильности (`VolatilityMode`)

```python
class VolatilityMode(Enum):
    HIGH = "high"      # ATR > 2.5 × среднего
    NORMAL = "normal"  # 0.5 × среднего ≤ ATR ≤ 2.5 × среднего
    LOW = "low"        # ATR < 0.5 × среднего
```

---

### 14. Режимы Входа (`EntryMode`)

```python
class EntryMode(Enum):
    AGGRESSIVE = "aggressive"      # Market entry, быстрый TP
    CONSERVATIVE = "conservative"  # Limit entry на откате, trailing
```

---

### 15. Типы Детекции Спуфинга (`SpoofingType`)

```python
class SpoofingType(Enum):
    REPOSITIONING = "repositioning"  # Легальное перемещение стены
    SPOOFING = "spoofing"            # Фейковая стена
    SUSPICIOUS = "suspicious"        # Подозрительная активность
    NEW_WALL = "new_wall"            # Новая стена (< 2 сек)
    STABLE = "stable"                # Стабильная стена (> 5 сек)
    WEAKENING = "weakening"          # Объем падает > 30% за 5 сек
```

---

### 16. Типы Китов (`WhaleType`)

```python
class WhaleType(Enum):
    WHALE_BUY = "whale_buy"
    WHALE_SELL = "whale_sell"
    WHALE_CLUSTER = "whale_cluster"  # 2+ кита за 2 сек
```

---

### 17. Статусы Стен (`WallStatus`)

```python
class WallStatus(Enum):
    DETECTED = "detected"
    CONFIRMED = "confirmed"       # Возраст > 2 сек
    REPOSITIONING = "repositioning"
    SPOOFED = "spoofed"
    DISAPPEARED = "disappeared"
    ABSORBED = "absorbed"         # Поглощена рыночными ордерами
```

---

## 🔄 Миграция со Строковых Литералов

### Пошаговый план

**Шаг 1: Создание файла**
```python
# core/identifiers.py
from enum import Enum

class PassportStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    # ... и т.д.
```

**Шаг 2: Точечная замена в модулях**
```python
# Было:
if passport.status == "OPEN":

# Стало:
from core.identifiers import PassportStatus
if passport.status == PassportStatus.OPEN.value:
```

**Шаг 3: Валидация**
Написать тест, который проверяет, что все строковые литералы в бизнес-логике соответствуют значениям из enum'ов.

### Правила миграции

1. **Модуль за модулем** — не менять всё сразу.
2. **С тестами после каждого модуля** — запускать unit-тесты.
3. **Обратная совместимость** — старые строки должны работать до полной миграции.

---

## 📊 Статистика

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
| **Итого** | **17 категорий** | **~113 значений** |

---

## ⚠️ Правила Использования

1. **Никогда не использовать строковые литералы** для идентификаторов, которые есть в словаре.
2. **Добавлять новые идентификаторы только в `core/identifiers.py`**.
3. **Использовать `.value`** при передаче в JSON/логи/биржу.
4. **Использовать enum напрямую** для сравнений в коде.
5. **Документировать** каждое новое значение в этом файле.

---

## 🔗 Связь с Другими Разделами Манифеста

- **Раздел 1 (Core Modules):** `EventBus` использует `EventType`, `JsonLogger` использует `LogLevel`.
- **Раздел 3 (Trading Core):** `TradePassport` использует `PassportStatus`, `StateManager` оперирует переходами между статусами.
- **Раздел 5 (Strategies):** Стратегии используют `StrategyName`, `Signal` содержит `side` из `OrderSide`.
- **Раздел 8 (Analytics Layer):** Аналитика использует `MarketEventType`, `MetricType`, `VolatilityMode`.

---

*Конец документа `09_IDENTIFIERS.md`*
```

---

