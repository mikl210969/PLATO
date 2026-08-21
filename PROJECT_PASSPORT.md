# 📘 Паспорт проекта PLATO

**Версия:** 1.0  
**Дата:** 21 августа 2026  
**Статус:** Фаза 0 — Стабилизация Stable Core

---

## 🎯 Что это такое

Торговая платформа для фьючерсов Binance/Bybit. **Спот = источник истины для анализа, фьючерс = инструмент исполнения.** Автоматизирует SL/TP на базе ATR/HVN с грейдами сделок A/B/C/REJECT и ступенчатым SL.

---

## 🔑 Ключевые принципы

1. **Event Bus как шина событий** — модули общаются через события, не через прямые вызовы.
2. **WS основной, REST фолбэк** — при потере WS автоматическое переключение на REST.
3. **Stable Core неприкосновенен** — блоки 1-4 не меняются при добавлении новых фич.
4. **Грейды сделок** — размер позиции зависит от R:R (A=100%, B=75%, C=50%, <1.5=REJECT).
5. **Graceful Degradation** — если аналитика недоступна, платформа работает в базовом режиме.

---

## 🏗️ Архитектура

```mermaid
graph TB
    subgraph UserLayer["👤 ПОЛЬЗОВАТЕЛЬСКИЙ СЛОЙ"]
        Config[" Конфиги"]
        Logs[" Логи"]
        Monitor["📊 Мониторинг"]
    end

    subgraph StableCore["🔵 STABLE CORE (ядро)"]
        subgraph Orchestrator["Главный оркестратор"]
            EventBus["EventBus"]
            Lifecycle["Lifecycle"]
        end
        subgraph Adapters["Адаптеры"]
            REST["REST клиент"]
            WS["WS адаптер"]
            Normalizer["Нормализатор"]
        end
        subgraph TradingCore["Торговое ядро"]
            Passport["Passport"]
            StateMgr["State Manager"]
            RiskBasic["Базовый Risk"]
            LifecycleMgr["Lifecycle Mgr"]
        end
    end

    subgraph Extensions[" EXTENSIONS"]
        subgraph Analytics["Analytics Engine"]
            Candles["Свечи/ATR"]
            OrderBook["Стакан/HVN"]
            Whales["Whale Detector"]
        end
        subgraph Strategies["Стратегии"]
            WallFade["WallFade"]
            Breakout["Breakout"]
            Absorption["Absorption"]
        end
        subgraph AdvancedRisk["Advanced Risk"]
            Grades["Грейды A/B/C"]
            StaircaseSL["Ступенчатый SL"]
            BasisStop["Basis Stop"]
            Confidence["Confidence Score"]
        end
    end

    subgraph DataStorage["️ СЛОЙ ХРАНЕНИЯ"]
        HotStorage[(" Hot Storage<br/>SQLite")]
        ColdStorage[("❄️ Cold Storage<br/>Parquet/JSONL")]
    end

    UserLayer --> Orchestrator
    Orchestrator --> Adapters
    Adapters --> TradingCore
    TradingCore --> EventBus
    EventBus --> Strategies
    Strategies --> AdvancedRisk
    AdvancedRisk --> TradingCore
    
    Adapters -->|сырые данные| ColdStorage
    Analytics -->|метрики| HotStorage
    HotStorage -->|чтение| Strategies
    HotStorage -->|чтение| AdvancedRisk
    ColdStorage -->|история| Analytics
    
    Binance[("🏦 Binance/Bybit")] <--> Adapters

    style StableCore fill:#e1f5ff,stroke:#01579b,stroke-width:3px
    style Extensions fill:#fff3e0,stroke:#e65100,stroke-width:3px
    style DataStorage fill:#f3e5f5,stroke:#7b1fa2,stroke-width:3px


📋 Описание блоков
Stable Core (Блоки 1-4) — Неприкосновенное ядро  


| Блок | Назначение | Ключевые файлы |
| :--- | :--- | :--- |
| **Пользовательский слой** | Конфиги, логи, мониторинг | `config/*.json`, `logs/*.jsonl` |
| **Главный оркестратор** | Координация, EventBus, lifecycle | `main.py`, `core/event_bus.py` |
| **Адаптеры** | WS/REST взаимодействие с биржей | `adapters/binance_*.py` |
| **Торговое ядро** | Passport, State, базовый Risk, TTL | `trading/passport.py`, `risk_manager.py`, `lifecycle_manager.py` |

### 🟠 Extensions (Блоки 5-7) — Расширения

| Блок | Назначение | Ключевые файлы |
| :--- | :--- | :--- |
| **Analytics Engine** | Расчет ATR, HVN, Imbalance, Whales | `trading/data_collector/*.py` (будущее) |
| **Стратегии** | Генерация сигналов | `strategies/wall_fade.py`, `breakout.py`, `absorption.py` |
| **Advanced Risk** | Грейды, ступенчатый SL, Basis Stop | `trading/advanced_risk/*.py` (будущее) |

### 🗄️ Data Storage (Блок 8) — Хранение данных

| Тип | Технология | Назначение |
| :--- | :--- | :--- |
| **Hot Storage** | SQLite | Текущие метрики (ATR, HVN, basis) для быстрого доступа |
| **Cold Storage** | Parquet/JSONL | История свечей, сделок, стаканов для бэктестов |

---

## 📊 Текущий статус

### ✅ Сделано
- WS/REST адаптеры с фолбэком
- Passport + State Manager
- Базовый Risk Manager (SL/TP)
- Lifecycle Manager (TTL)
- EventBus
- Манифест (12 файлов в `docs/`)

### 🟡 В работе (Фаза 0)
- Исправление R:R в `trading.json`
- Честный TTL (только отмена, без конвертации)
- `reduce_only=True` для SL/TP
- `core/identifiers.py`

### ⏳ Планируется (Фаза 1-3)
- Динамический ATR + HVN детектор
- Грейды A/B/C/REJECT
- Ступенчатый SL + Basis Stop
- Confidence Score

---

## 🎯 Ближайшие задачи (Спринт 1)

1. **Исправить R:R** в `config/trading.json` (SL=2×ATR, TP1=4×ATR, TP2=6×ATR)
2. **Честный TTL** — лимитки только отменяются, не конвертируются в market
3. **`reduce_only=True`** для всех защитных ордеров
4. **`core/identifiers.py`** — enum'ы для статусов и событий

---

## 🔗 Ссылки на детали

| Документ | Что содержит |
| :--- | :--- |
| `docs/13_FUNCTIONAL_ARCHITECTURE.md` | Полная функциональная схема + план внедрения |
| `docs/10_STABLE_CORE.md` | Принципы стабильного ядра, контракты |
| `docs/08_ANALYTICS_LAYER.md` | Детали аналитического слоя |
| `docs/09_IDENTIFIERS.md` | Словарь идентификаторов (enum'ы) |
| `docs/07_KNOWN_ISSUES.md` | Бэклог задач |
| `SL_TP.txt` | Архитектура SL/TP v2.2 (грейды, ступенчатый SL, basis) |
| `docs/hot_storage_schema.md` | Структура таблиц SQLite |

---

## ⚠️ Критические риски

1. **Рассинхрон спот/фьючерс** — данные приходят асинхронно, нужны timestamp и sequenceId.
2. **Ступенчатый SL на Binance** — требует двух отдельных STOP_MARKET ордеров и сложной логики управления.
3. **Basis Stop** — проверка каждые 10 сек требует свежих данных, иначе ложные срабатывания.

---

## 🤖 Как продолжить в новом чате

**Промпт для ИИ:**
> "Привет! Изучи `PROJECT_PASSPORT.md`. Мы на **Фазе 0, Задача 0.1** (исправление R:R). Соблюдай принципы Stable Core. Детали по ссылкам в разделе 'Ссылки на детали'."

---

*Конец документа `PROJECT_PASSPORT.md`*
```

---

##  Документ 2: `docs/hot_storage_schema.md`

Создай этот файл в папке `docs/`:

```markdown
# ️ Схема Hot Storage (SQLite)

**Версия:** 1.0  
**Дата:** 21 августа 2026  
**Назначение:** Структура таблиц для быстрого доступа к текущим метрикам.

---

## 📋 Таблицы

### 1. `current_metrics` — Текущие метрики по символу

| Поле | Тип | Описание |
| :--- | :--- | :--- |
| `symbol` | TEXT | Инструмент (например, 'SOLUSDT') |
| `atr_value` | REAL | Текущий ATR(14) |
| `atr_normalized` | REAL | ATR / цена (для сравнения волатильности) |
| `volatility_mode` | TEXT | 'high' / 'normal' / 'low' |
| `hvn_levels` | TEXT | JSON-массив уровней HVN: `[{"price": 84.5, "volume": 1000}, ...]` |
| `poc_price` | REAL | Point of Control (цена с макс. объемом) |
| `basis` | REAL | Разница между спотом и фьючерсом (%) |
| `basis_timestamp` | INTEGER | Время последнего обновления basis (ms) |
| `obi_value` | REAL | Order Book Imbalance (-1 до 1) |
| `whale_activity` | TEXT | JSON: `{"buy_volume": 50000, "sell_volume": 30000, "last_whale_time": 1234567890}` |
| `updated_at` | INTEGER | Время последнего обновления (ms) |

**Первичный ключ:** `symbol`  
**Индексы:** `updated_at` (для очистки старых данных)

---

### 2. `strategy_signals` — История сигналов стратегий

| Поле | Тип | Описание |
| :--- | :--- | :--- |
| `signal_id` | TEXT | Уникальный ID сигнала (UUID) |
| `symbol` | TEXT | Инструмент |
| `strategy_name` | TEXT | 'WallFade' / 'Breakout' / 'Absorption' |
| `side` | TEXT | 'LONG' / 'SHORT' |
| `entry_price` | REAL | Предлагаемая цена входа |
| `sl_price` | REAL | Цена стоп-лосса |
| `tp1_price` | REAL | Цена первого тейк-профита |
| `tp2_price` | REAL | Цена второго тейк-профита |
| `rr_ratio` | REAL | Risk/Reward ratio |
| `grade` | TEXT | 'A' / 'B' / 'C' / 'REJECT' |
| `confidence_score` | REAL | 0-100 |
| `position_size_pct` | REAL | 0.0-1.0 (50%, 75%, 100%) |
| `created_at` | INTEGER | Время создания (ms) |
| `status` | TEXT | 'pending' / 'accepted' / 'rejected' / 'executed' |

**Первичный ключ:** `signal_id`  
**Индексы:** `symbol`, `created_at`, `status`

---

### 3. `active_positions` — Активные позиции (кэш)

| Поле | Тип | Описание |
| :--- | :--- | :--- |
| `passport_id` | TEXT | ID паспорта сделки |
| `symbol` | TEXT | Инструмент |
| `side` | TEXT | 'LONG' / 'SHORT' |
| `entry_price` | REAL | Цена входа |
| `quantity` | REAL | Размер позиции |
| `sl1_price` | REAL | Цена SL1 (50%) |
| `sl2_price` | REAL | Цена SL2 (50%) |
| `tp1_price` | REAL | Цена TP1 (50%) |
| `tp2_price` | REAL | Цена TP2 (50%) |
| `be_price` | REAL | Цена Break-Even |
| `emergency_sl` | REAL | Аварийный SL (2R) |
| `basis_at_entry` | REAL | Basis на момент входа |
| `grade` | TEXT | Грейд сделки |
| `status` | TEXT | 'open' / 'partial_close' / 'closing' |
| `opened_at` | INTEGER | Время открытия (ms) |

**Первичный ключ:** `passport_id`  
**Индексы:** `symbol`, `status`

---

### 4. `order_cache` — Кэш биржевых ордеров

| Поле | Тип | Описание |
| :--- | :--- | :--- |
| `order_id` | TEXT | ID ордера на бирже |
| `passport_id` | TEXT | Связь с паспортом |
| `symbol` | TEXT | Инструмент |
| `role` | TEXT | 'ENTRY' / 'TP1' / 'TP2' / 'SL1' / 'SL2' / 'EMERGENCY_SL' |
| `side` | TEXT | 'BUY' / 'SELL' |
| `price` | REAL | Цена ордера |
| `quantity` | REAL | Размер |
| `status` | TEXT | 'new' / 'filled' / 'canceled' / 'expired' |
| `created_at` | INTEGER | Время создания (ms) |
| `updated_at` | INTEGER | Время последнего обновления (ms) |

**Первичный ключ:** `order_id`  
**Индексы:** `passport_id`, `status`

---

## 🔄 Правила использования

### Запись данных
- **Analytics Engine** пишет в `current_metrics` при обновлении метрик.
- **Стратегии** пишут в `strategy_signals` при генерации сигнала.
- **Торговое ядро** пишет в `active_positions` при открытии позиции.
- **Risk Manager** пишет в `order_cache` при выставлении ордера.

### Чтение данных
- **Стратегии** читают `current_metrics` для расчета сигналов.
- **Advanced Risk** читает `current_metrics` (basis, volatility) и `strategy_signals` для оценки грейда.
- **Торговое ядро** читает `active_positions` и `order_cache` для управления позицией.

### Очистка данных
- `current_metrics`: хранить только последние значения (UPDATE по `symbol`).
- `strategy_signals`: хранить последние 1000 записей, удалять старше 7 дней.
- `active_positions`: удалять записи со статусом 'closed' старше 24 часов.
- `order_cache`: хранить последние 500 записей, удалять старше 7 дней.

---

## 📊 Пример запросов

### Получить текущий ATR для SOLUSDT:
```sql
SELECT atr_value, volatility_mode FROM current_metrics WHERE symbol = 'SOLUSDT';
```

### Найти все активные позиции:
```sql
SELECT * FROM active_positions WHERE status = 'open';
```

### Получить последние 10 сигналов WallFade:
```sql
SELECT * FROM strategy_signals 
WHERE strategy_name = 'WallFade' 
ORDER BY created_at DESC 
LIMIT 10;
```

### Проверить, есть ли открытые ордера для паспорта:
```sql
SELECT * FROM order_cache 
WHERE passport_id = 'PASS_123' AND status IN ('new', 'open');
```

---

*Конец документа `hot_storage_schema.md`*
```

---

## 🚀 Твои действия:

1. **Создай файл `PROJECT_PASSPORT.md`** в корне проекта (рядом с `main.py`) и вставь первый блок кода.

2. **Создай файл `docs/hot_storage_schema.md`** и вставь второй блок кода.

3. **Выполни в PowerShell:**
```powershell
git add PROJECT_PASSPORT.md docs/hot_storage_schema.md
git commit -m "docs: add project passport and hot storage schema (SQLite)"
git push origin main
```

4. **Напиши "Готово!"**

После этого у тебя будет:
- ✅ Компактный "паспорт проекта" для быстрого контекста в новом чате
- ✅ Готовая схема SQLite для реализации в Фазе 1
- ✅ Полная картина архитектуры (Stable Core + Extensions + Data Storage)

**Готов создать файлы?**