# 🛡️ Защита, Жизненный Цикл и Восстановление

Этот документ описывает три модуля, которые отвечают за **безопасность сделок**, **управление временем жизни ордеров** и **восстановление платформы после сбоев**. Это критически важные компоненты для устойчивости торговой системы.

---

## 🗺️ Общая схема взаимодействия

```mermaid
graph TD
    Exchange[🏦 Биржа] -->|WS: ORDER_TRADE_UPDATE| Orchestrator[🧠 Оркестратор]
    Orchestrator -->|POSITION_OPENED| RiskManager[🛡️ RiskManager]
    RiskManager -->|Выставляет TP1/TP2/SL| Trader[👐 Trader]
    Trader -->|REST| Exchange
    
    Orchestrator -->|PASSPORT_CREATED лимитный| Lifecycle[⏱️ LifecycleManager]
    Lifecycle -->|Запускает TTL-таймер| Timer[⏳ asyncio.Task]
    Timer -->|Истечение TTL| Lifecycle
    Lifecycle -->|TTL_EXPIRED| Orchestrator
    Orchestrator -->|Отмена/Конвертация| Trader
    
    Orchestrator -->|ORDER_FILLED/CANCELED| Lifecycle
    Lifecycle -->|Отмена таймера| Timer
    
    Restart[🔄 Перезапуск платформы] --> Recovery[♻️ RecoveryManager]
    Recovery -->|Загружает паспорта| Repo[💾 PassportRepository]
    Recovery -->|Запрашивает позицию/ордера| Trader
    Recovery -->|Корректирует статусы| PassportManager[🗂️ PassportManager]
    Recovery -->|Сохраняет| Repo