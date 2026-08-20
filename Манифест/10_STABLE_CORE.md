
# 📄 `docs/10_STABLE_CORE.md`

```markdown
# 🛡️ Стабильное Ядро Платформы (Stable Core)

**Версия:** 1.0  
**Статус:** Проектирование  
**Назначение:** Определяет фундамент платформы, который не меняется при добавлении новых фич. Гарантирует, что расширения не ломают базовую функциональность.

---

## 🎯 Философия Stable Core

### Проблема

В предыдущих версиях платформы каждая новая доработка ломала старый функционал:
- Добавили миксины → сломался прямой вызов методов Orchestrator
- Исправили TTL → сломалась конвертация в market
- Добавили RecoveryMixin → конфликт со старым RecoveryManager

**Причина:** Отсутствие чётких границ между "базой" и "расширениями".

### Решение

Принцип **Stable Core**:
1. **Ядро** — минимальный набор компонентов, которые работают надёжно и не меняются.
2. **Контракты** — чёткие интерфейсы между ядром и расширениями.
3. **Расширения** — всё, что можно добавлять/менять без риска сломать базу.

---

## 📋 Состав Стабильного Ядра

### ✅ Ядро (НЕ МЕНЯЕТСЯ без крайней необходимости)

| Компонент | Файл | Ответственность |
| :--- | :--- | :--- |
| **EventBus** | `core/event_bus.py` | Доставка событий между модулями |
| **TradePassport** | `trading/passport.py` | Хранение состояния сделки (SSOT) |
| **StateManager** | `trading/state_manager.py` | Контроль переходов статусов |
| **PassportManager** | `trading/passport_manager.py` | Кэш паспортов в памяти |
| **PassportRepository** | `trading/passport_repository.py` | Сохранение паспортов на диск |
| **BinanceRestClient** | `adapters/binance_rest.py` | Отправка команд на биржу |
| **BinanceWsAdapter** | `adapters/binance_ws.py` | Получение данных с биржи |
| **Orchestrator (базовый)** | `trading/orchestrator.py` | Координация через миксины |
| **RiskManager (базовый)** | `trading/risk_manager.py` | Выставка TP1/TP2/SL |
| **LifecycleManager** | `trading/lifecycle_manager.py` | TTL для лимитных ордеров |
| **RecoveryMixin** | `trading/recovery.py` | Восстановление при старте |
| **ConfigLoader** | `core/config_loader.py` | Загрузка конфигов |
| **JsonLogger** | `core/json_logger.py` | Структурированное логирование |
| **ID Registry** | `core/identifiers.py` | Единый словарь идентификаторов |

### ⚠️ Расширения (МОГУТ МЕНЯТЬСЯ)

| Компонент | Файл | Почему это расширение |
| :--- | :--- | :--- |
| **Стратегии** | `strategies/*.py` | Будут добавляться новые, меняться логика |
| **ExitCalculator** | `trading/exit_calculator.py` | Сейчас статический ATR, потом динамический |
| **Confidence Score** | (будущее) | Будет добавляться позже |
| **Analytics Layer** | `trading/data_collector/` | Будет добавляться позже |
| **Position Manager** | (будущее) | Trailing, BE, heartbeat |
| **Normalization Layer** | (будущее) | MarketEvent для бирже-независимости |
| **Spot WS Adapter** | (будущее) | Для принципа "Спот = истина" |

---

## 🛡️ Принципы Защиты Ядра

### Принцип 1: Интерфейсы, а не реализации

**Правило:** Ядро работает с абстракциями, не с конкретными реализациями.

**Пример:**
```python
# Было — жёсткая зависимость
class Orchestrator:
    def __init__(self):
        self.exit_calc = ExitCalculator()  # Конкретная реализация

# Стало — работа через интерфейс
from abc import ABC, abstractmethod

class ExitCalculator(ABC):
    @abstractmethod
    def calculate(self, side, entry_price) -> Dict[str, float]:
        pass

class StaticATRCalculator(ExitCalculator):
    def __init__(self, atr_value):
        self.atr_value = atr_value
    
    def calculate(self, side, entry_price):
        # Текущая логика

class DynamicATRCalculator(ExitCalculator):
    def __init__(self, atr_calculator):
        self.atr_calculator = atr_calculator
    
    def calculate(self, side, entry_price):
        # Новая логика

class Orchestrator:
    def __init__(self, exit_calc: ExitCalculator):
        self.exit_calc = exit_calc  # Абстракция
```

**Результат:** Можно менять реализацию ExitCalculator без изменения Orchestrator.

---

### Принцип 2: События, а не прямые вызовы

**Правило:** Модули ядра общаются через EventBus, не через прямые вызовы методов.

**Пример:**
```python
# Было — прямая зависимость
class Orchestrator:
    def _on_signal(self, event):
        self.risk_manager.place_orders(passport)  # Прямой вызов

# Стало — через события
class Orchestrator:
    def _on_signal(self, event):
        await self.bus.publish(EventType.POSITION_OPENED, payload={...})
        # RiskManager сам подпишется и выполнит свою логику
```

**Результат:** Можно добавить новых подписчиков (например, Position Manager для trailing stop) без изменения Orchestrator.

---

### Принцип 3: Конфигурация, а не хардкод

**Правило:** Все настраиваемые параметры — в конфигах, не в коде.

**Пример:**
```python
# Было
if score >= 70:
    position_size = 1.0

# Стало
if score >= config['position_sizing']['full_entry_threshold']:
    position_size = config['position_sizing']['full_position_size']
```

**Результат:** Меняем поведение через конфиг, не трогая код.

---

### Принцип 4: Тесты для ядра

**Правило:** Каждый компонент ядра покрыт unit-тестами.

**Обязательные тесты:**
- Passport корректно меняет статусы
- StateManager не пропускает недопустимые переходы
- RiskManager выставляет ордера с правильными параметрами
- LifecycleManager отменяет ордер по TTL
- RecoveryMixin восстанавливает состояние после краша
- EventBus доставляет события всем подписчикам

**Результат:** При добавлении новой фичи запускаем тесты ядра. Если они проходят — ядро не сломано.

---

### Принцип 5: Версионирование ядра

**Правило:** Каждое изменение ядра сопровождается обновлением версии.

**Схема версионирования:**
- `v1.0.0` — Stable Core v1.0 (базовая функциональность)
- `v1.1.0` — Добавлен Dynamic ATR (обратная совместимость)
- `v2.0.0` — Переход на MarketEvent (breaking change)

**Результат:** Понятно, когда изменения ломают обратную совместимость.

---

## 📊 Архитектурные Контракты

### Контракт 1: EventBus

```python
# Сигнатура publish
await bus.publish(
    event_type: EventType,      # Enum, не строка
    source: EventSource,        # Enum
    payload: Dict,              # Данные события
    symbol: str,                # Инструмент
    correlation_id: str = None  # ID паспорта для трассировки
)

# Сигнатура subscribe
bus.subscribe(
    event_type: EventType,
    handler: Callable[[Event], Awaitable[None]]
)
```

**Гарантии:**
- Все обработчики запускаются параллельно
- Ошибки в одном обработчике не влияют на другие
- publish ждёт завершения всех обработчиков

---

### Контракт 2: TradePassport

```python
class TradePassport:
    # Обязательные поля
    passport_id: str
    symbol: str
    status: PassportStatus
    signal_id: str
    side: OrderSide
    entry_price: float
    
    # Обязательные методы
    def transition_to(self, new_status: PassportStatus, reason: str) -> bool
    def add_timeline_event(self, event_type: str, details: Dict) -> None
    def add_order(self, order: Dict) -> None
    def close(self, exit_reason: ExitReason, exit_price: float) -> None
    def to_dict(self) -> Dict
```

**Гарантии:**
- Статус меняется только через `transition_to()`
- Timeline только дополняется (append-only)
- `to_dict()` всегда возвращает валидный словарь для сериализации

---

### Контракт 3: StateManager

```python
class StateManager:
    def can_transition(self, current: PassportStatus, new: PassportStatus) -> bool
    def transition(self, passport: TradePassport, new_status: PassportStatus, reason: str) -> bool
    def handle_event(self, passport: TradePassport, event_type: EventType, data: Dict) -> bool
```

**Гарантии:**
- Недопустимые переходы отклоняются с логом
- Все переходы логируются в JSONL
- Синхронизация с биржей через `sync_with_exchange()`

---

### Контракт 4: RiskManager

```python
class RiskManager:
    # Подписка на события
    bus.subscribe(EventType.POSITION_OPENED, self._on_position_opened)
    
    # Обязательное поведение
    def _on_position_opened(self, event: Event) -> None:
        # Выставляет TP1, TP2, SL на биржу
        # Все ордера с reduce_only=True
        # Логирует в JSONL
```

**Гарантии:**
- TP/SL выставляются с `reduce_only=True`
- При ошибке выставления — retry или логирование
- Heartbeat проверяет активность биржевых ордеров

---

### Контракт 5: LifecycleManager

```python
class LifecycleManager:
    # Подписка на события
    bus.subscribe(EventType.PASSPORT_CREATED, self._on_passport_created)
    bus.subscribe(EventType.ORDER_FILLED, self._on_order_filled)
    
    # Обязательное поведение
    def _on_passport_created(self, event: Event) -> None:
        # Запускает TTL только для лимитных входных ордеров
        # Исключает SL-ордера
    
    def _on_ttl_expired(self, event: Event) -> None:
        # Публикует TTL_EXPIRED
        # НЕ конвертирует в market (только отмена)
```

**Гарантии:**
- TTL не применяется к SL-ордерам
- При истечении TTL — только отмена, не конвертация
- Таймеры восстанавливаются при перезапуске

---

## 🔄 Процесс Добавления Новых Фич

### Шаг 1: Определение типа изменения

**Вопрос:** Это изменение ядра или расширение?

**Критерии ядра:**
- Меняет поведение существующих компонентов
- Требует изменения контрактов
- Влияет на стабильность платформы

**Критерии расширения:**
- Добавляет новый модуль
- Работает через существующие контракты
- Не меняет поведение ядра

### Шаг 2: Для расширений

1. Создать новый модуль
2. Реализовать интерфейс (если нужен)
3. Подписаться на события через EventBus
4. Написать unit-тесты
5. Запустить тесты ядра — убедиться, что ничего не сломалось

### Шаг 3: Для изменений ядра

1. Обновить контракт (если нужно)
2. Реализовать изменение
3. Написать/обновить unit-тесты
4. Запустить все тесты ядра
5. Обновить версию ядра
6. Задокументировать breaking changes

---

## 📋 План Стабилизации Ядра

### Этап 1: Аудит (1-2 дня)
- [ ] Выделить компоненты ядра (см. таблицу выше)
- [ ] Найти все места, где ядро "протекает" (прямые вызовы вместо событий)
- [ ] Составить список "точек расширения"

### Этап 2: Введение интерфейсов (2-3 дня)
- [ ] Выделить интерфейсы для ExitCalculator, Strategy
- [ ] Рефакторинг Orchestrator для работы с интерфейсами
- [ ] Добавить фабрику для создания реализаций

### Этап 3: Стабилизация функционала (3-5 дней)
- [ ] Исправить фантомные ордера (heartbeat)
- [ ] Убедиться, что TTL работает корректно (отмена, не конвертация)
- [ ] Добавить `reduce_only=True` для TP/SL
- [ ] Реализовать перенос SL в безубыток после TP1
- [ ] Написать unit-тесты для всех компонентов ядра

### Этап 4: Документирование контрактов (1-2 дня)
- [ ] Описать интерфейсы в этом разделе Манифеста
- [ ] Добавить примеры использования
- [ ] Описать, как добавлять новые фичи без изменения ядра

### Этап 5: Тестирование на реальных сценариях (2-3 дня)
- [ ] Запустить платформу на тестнете
- [ ] Проверить все базовые сценарии
- [ ] Убедиться, что нет фантомных ордеров
- [ ] Зафиксировать версию ядра (v1.0 Stable)

---

##  Критерии Готовности Stable Core v1.0

1. **Все компоненты ядра покрыты unit-тестами**
2. **Нет прямых вызовов между модулями ядра** (только через EventBus)
3. **Все идентификаторы вынесены в `core/identifiers.py`**
4. **TTL работает корректно** (отмена, не конвертация)
5. **TP/SL выставляются с `reduce_only=True`**
6. **Heartbeat проверяет биржевые ордера**
7. **RecoveryMixin восстанавливает состояние после краша**
8. **Нет фантомных ордеров при перезапуске**
9. **Версия ядра зафиксирована** (v1.0.0)
10. **Документация контрактов актуальна**

---

## 🔗 Связь с Другими Разделами Манифеста

- **Раздел 0 (Архитектура):** Stable Core — это реализация принципов слабой связности и разделения ответственности.
- **Раздел 1 (Core Modules):** EventBus, Types, ConfigLoader — часть ядра.
- **Раздел 3 (Trading Core):** Passport, StateManager, Orchestrator — часть ядра.
- **Раздел 4 (Risk & Lifecycle):** RiskManager, LifecycleManager, RecoveryMixin — часть ядра.
- **Раздел 7 (Known Issues):** Многие блокеры — это недостатки ядра, которые нужно исправить.
- **Раздел 9 (Identifiers):** ID Registry — часть ядра, обеспечивает типобезопасность.

---

*Конец документа `10_STABLE_CORE.md`*
```

---

##  Что дальше?

Мы создали два фундаментальных раздела:
- **09_IDENTIFIERS.md** — единый словарь всех идентификаторов
- **10_STABLE_CORE.md** — принцип стабильного ядра и контракты

Теперь нужно:
1. Сохранить эти файлы в папку `docs/`
2. Закоммитить в Git
3. Обновить `00_ARCHITECTURE.md` — добавить ссылки на новые разделы
4. Обновить `07_KNOWN_ISSUES.md` — перенести решённые задачи в статус "Решается в рамках Stable Core"

**Готов продолжить с обновлением существующих файлов или сначала закоммитим новые?**