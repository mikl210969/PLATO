# 🏛️ Архитектурный Манифест: PLAT_WALLS_NEW

**Версия документа:** 1.0 (Сформировано по итогам полного ревью кода)
**Назначение:** Единый источник правды об архитектуре торговой платформы для автоматической торговли фьючерсами на Binance.

---

## 🎯 1. Назначение и Философия

`PLAT_WALLS_NEW` — это событийно-ориентированная (Event-Driven) торговая платформа, построенная на принципах слабой связности (loose coupling) и строгого разделения ответственности. 

### Три Столпа Архитектуры:
1. **Биржа — Абсолютный Источник Истины.** Внутреннее состояние платформы может устареть или повредиться. Единственная правда — это состояние позиции и ордеров на самой бирже. Все модули (особенно `RecoveryManager` и `StateManager`) постоянно сверяются с биржей.
2. **Паспорт (Passport) — Внутренний SSOT.** Для самой платформы единым источником правды является `TradePassport`. Ни один модуль не хранит состояние сделки у себя — они читают и пишут только в Паспорт.
3. **EventBus — Нервная Система.** Модули не вызывают методы друг друга напрямую (за редкими исключениями). Они публикуют и слушают события, что позволяет легко добавлять новые компоненты без переписывания старых.

---

## 🗺️ 2. Анатомия Платформы (Роли Компонентов)

Платформа построена по метафорическому принципу "организма":

| Метафора | Компонент | Файл | Ответственность |
| :--- | :--- | :--- | :--- |
| 🧠 **Мозг** | `Orchestrator` | `trading/orchestrator.py` | Принимает решения. Слушает сигналы, создаёт паспорта, дирижирует остальными модулями. |
| 👐 **Руки** | `Trader` | `trading/trader.py` | Только исполняет команды. Формирует запросы к API биржи, не думает о стратегии. |
| 🛡️ **Щит** | `RiskManager` | `trading/risk_manager.py` | Выставляет защитные ордера (TP1, TP2, SL) на биржу сразу после открытия позиции. |
| 👂 **Уши** | `BinanceWsAdapter` | `adapters/binance_ws.py` | Слушает рынок (стакан) и аккаунт (исполнения, позиции) через WebSocket. |
| 🌉 **Мост** | `BinanceRestClient` | `adapters/binance_rest.py` | Отправляет команды на биржу через REST API (с HMAC-подписью). |
| 📖 **Паспорт** | `TradePassport` | `trading/passport.py` | Dataclass, хранящий всю историю и состояние конкретной сделки. |
| 🚦 **Регулировщик** | `StateManager` | `trading/state_manager.py` | Конечный автомат. Контролирует допустимые переходы статусов Паспорта. |
| ⏱️ **Часовщик** | `LifecycleManager` | `trading/lifecycle_manager.py` | Следит за TTL лимитных входных ордеров (отменяет или конвертирует в маркет). |
| ♻️ **Реаниматолог** | `RecoveryManager` | `trading/recovery_manager.py` | Восстанавливает состояние платформы после краша или перезапуска. |
| 📊 **Аналитики** | Стратегии | `strategies/*.py` | Генерируют сигналы на основе рыночных данных. Не знают про ордера и паспорта. |

---

## 🔄 3. Жизненный Цикл Сделки (Data Flow)

Типовой сценарий от сигнала до закрытия:

```mermaid
sequenceDiagram
    participant Strategy as 📊 Стратегия
    participant Main as 🔌 main.py
    participant Bus as 📡 EventBus
    participant Orch as 🧠 Оркестратор
    participant Trader as 👐 Треjder
    participant Exchange as 🏦 Биржа
    participant Risk as 🛡️ Риск-Менеджер

    Strategy->>Main: Возвращает Signal
    Main->>Bus: Публикует SIGNAL_GENERATED
    Bus->>Orch: Доставляет событие
    Orch->>Orch: Проверяет is_symbol_busy()
    Orch->>Orch: Создаёт TradePassport
    Orch->>Trader: calculate_exit_levels() (SL/TP)
    Orch->>Trader: execute_order() (MARKET/LIMIT)
    Trader->>Exchange: REST POST /order
    Exchange-->>Trader: Подтверждение
    Trader-->>Orch: Результат
    Orch->>Bus: Публикует POSITION_OPENED
    Bus->>Risk: Доставляет событие
    Risk->>Trader: Выставляет TP1, TP2, SL
    Trader->>Exchange: REST POST /order (x3)
    
    Note over Exchange,Risk: Позиция открыта, защита на бирже
    
    Exchange-->>Main: WS: ORDER_TRADE_UPDATE (TP1 FILLED)
    Main->>Bus: Публикует событие
    Bus->>Orch: Доставляет
    Orch->>Orch: Обновляет Паспорт (PARTIAL_CLOSE)