# 🧠 Торговое Ядро (Trading Core)

Этот документ описывает модули, которые принимают решения, управляют состоянием сделок и исполняют команды. Это сердце платформы `PLAT_WALLS_NEW`.

---

## 🗺️ Общая схема взаимодействия

```mermaid
graph TD
    Signal[📊 Сигнал от стратегии] --> Orchestrator[🧠 Orchestrator]
    Orchestrator -->|Создаёт| Passport[📖 TradePassport]
    Orchestrator -->|Рассчитывает уровни| ExitCalc[📐 ExitCalculator]
    Orchestrator -->|Отправляет ордер| Trader[👐 Trader]
    Trader -->|REST| Exchange[🏦 Биржа]
    Exchange -->|WS: ORDER_TRADE_UPDATE| Orchestrator
    Orchestrator -->|Обновляет| Passport
    Orchestrator -->|Проверяет переходы| StateManager[🚦 StateManager]
    Passport -->|Хранится в| PassportManager[🗂️ PassportManager]
    Passport -->|Сохраняется в| PassportRepository[💾 PassportRepository]