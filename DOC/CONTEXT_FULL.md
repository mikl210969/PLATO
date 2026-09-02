# 📋 PLATO — Контекст платформы (для вставки в новый чат)

> **Дата актуальности:** 2026-09-02 | **Статус:** Спринт 5 завершён, Спринт 6 в работе  
> **Биржа:** Binance (testnet + live) | **Символ:** SOLUSDT (основной), BTCUSDT (контекст)

| # | Раздел / Модуль | Описание |
|---|---|---|
| **1** | **🏛️ Общее описание** | Модульная, событийно-ориентированная платформа для алгоритмического трейдинга крипты на Binance. Специализация: **Order Flow / микроструктура** (стакан, дельта, поглощения, HVN). Анализ на **Spot** (чистая ликвидность), исполнение на **Futures** (Hedge Mode). |
| **2** | **🛠️ Стек** | Python 3.11+, `asyncio`, собственные адаптеры Binance REST/WS, SQLite (`plato_metrics.db`) + JSONL cold storage, JSON-конфиги, pytest. |
| **3** | **🧠 Архитектура** | **Event-Driven** через `EventBus` (pub/sub). WS — данные, REST — команды + fallback. Слои: Adapters → Core → Analytics → Strategies → Trading → Risk. |
| **4** | **📜 SSOT: TradePassport** | `dataclass` — единый источник правды по сделке. ID: `PASS_YYYYMMDD_HHMMSS_<uuid6>`. Поля: сигнал, SL/TP1/TP2, позиция, ордера, таймлайн, PnL. Сохраняется в `passports/*.json`. |
| **5** | **🧩 Orchestrator** | «Мозг». Наследует 4 миксина: `EventHandlersMixin`, `MonitorMixin`, `RecoveryMixin`, `PositionMonitor`. Принимает сигналы → создаёт паспорта → командует Trader/RiskManager. |
| **6** | **✋ Trader** | «Руки». Только отправка ордеров (`market`/`limit`/`stop_market`/`stop_limit`) и возврат результата. Hedge Mode: `position_side` явно, `reduceOnly` **НЕ шлётся** (-1106). |
| **7** | **🛡️ RiskManager** | **Internal Stop** — следит за ценой через WS `PRICE_UPDATE`. TP1 → 50% + SL в BE. TP2/SL → полный close. Идемпотентность через флаги `*_done`. Свежесть цены: `max_price_age_sec=3.0`. |
| **8** | **🔁 StateManager** | Машина состояний паспорта. Карта переходов: `SIGNAL_GENERATED → ORDER_SENT → ORDER_ACK → LIMIT_ON_BOOK → OPEN → PARTIAL_CLOSE → CLOSED`. Терминальные: `CLOSED/CANCELED/FAILED`. Идемпотентные переходы. |
| **9** | **🔍 DriftMonitor** | Каждые 30с сверяет локальное состояние с биржей. **Force Sync**: если `ORDER_SENT` + на бирже `size>0` → автовосстановление в `OPEN` + `POSITION_OPENED`. Восстанавливает `EXTERNAL_CLOSE` через `get_user_trades`. |
| **10** | **🧾 OrderVerifier** | REST-fallback: опрос биржи каждые 5с (до 20 попыток) для подтверждения статуса. Публикует `ORDER_FILLED`/`ORDER_CANCELED` если WS молчит. |
| **11** | **⏱️ LifecycleManager** | TTL-таймеры для лимитных ордеров. Белый список статусов (`ORDER_SENT/ORDER_ACK/LIMIT_ON_BOOK`). Отмена таймеров при `ORDER_FILLED`/`POSITION_CLOSED`. |
| **12** | **🔐 Pre-Trade Gate** | Блок новых сигналов при: активный `drift_monitor`, активный `verifier`, занятый символ (`is_symbol_busy`). |
| **13** | **📊 PositionSizer** | `qty = risk_usdt / |entry - sl|`. Cap `max_position_size=5.0`. Округление `Decimal` до `step_size`. Fallback-настройки SOL/BTC/ETH/BNB. Возвращает `None` если < `min_qty`/`min_notional`. |
| **14** | **🎯 GradeCalculator** | R:R ≥ 2.5 → **A** (100%), 2.0–2.49 → **B** (75%), 1.5–1.99 → **C** (50%), <1.5 → **REJECT**. |
| **15** | **📐 StopLevels** | SL1 = edge ∓ ATR-буфер (high=0.5, normal=0.3, low=0.2) — 50%. SL2 = SL1 ∓ 1ATR — 50%. BE = entry ∓ 0.25ATR. Emergency = entry ∓ 2R. Конвертация spot→futures через basis + 0.05%. |
| **16** | **👁️ AdvancedRiskService** | **Теневой режим** — подписан на `SIGNAL_GENERATED`, считает grade + risk_plan, но **не исполняет**. Логирует `SHADOW_APPROVE/REJECT`. Готов к интеграции. |
| **17** | **📈 DeltaAnalyzer** | Скользящее окно 30 мин. Кумулятивная дельта + `delta_velocity` (за 3 сек). Агрессия по полю `m` Binance aggTrade. |
| **18** | **🔎 AbsorptionDetector** | Условия: `|velocity|≥5000` + `price_movement≤0.05%` + `|imbalance|≥0.3`. `velocity>0` → покупатели в ask-стену → **BEARISH**. Cooldown 30с. |
| **19** | **📉 VolatilityFilter** | Реальный ATR из свечей Binance Spot (1m, period=14). Кэш 60с. Режимы: `low` (<0.3%), `normal`, `high` (>1.5%). |
| **20** | **💱 BasisMonitor** | Спред Spot/Futures. Фильтр шума (3 тика). Basis Stop при изменении >1.5%. Сохранение в SQLite. |
| **21** | **🐋 DeltaMonitor** | 5-мин свечи, история 24 свечи. Дивергенции (BULLISH/BEARISH). Режимы: `IMPULSIVE` (|delta|>100), `FLAT` (<5 + FLAT тренд), `NORMAL`. Публикует `BTC_CONTEXT_UPDATED` / `CONTEXT_UPDATED_{SYMBOL}` каждые 5с. |
| **22** | **🎯 WallFadeV3** | Отскок от Micro-HVN. SL якорится к HVN. Фильтры: Macro-HVN, BTC-тренд, SOL-дельта, дивергенции. Блок в `IMPULSIVE` режиме. Confidence: база 0.50 + стена +0.25 + детекторы + дивергенция +0.20. Порог отсечения 0.50. |
| **23** | **🧲 AbsorptionV2** | Событийная: вход сразу после `ABSORPTION_DETECTED`. SL за уровнем поглощения ∓ 0.3 ATR. R:R жёстко 2.0. Штрафы за контртренд BTC/SOL. Порог 0.50. |
| **24** | **💥 BreakoutV1** | Пробой стены. Гибридное исполнение (limit 5с → fallback market). Анализ ликвидности за стеной. Блок в `FLAT` режиме. R:R 2.5. Штраф ×0.4 за пробой против BTC-тренда. |
| **25** | **🔥 Smart Sizing** | Адаптивный риск под BTC-тренд: совпадение ×1.5, FLAT ×1.0, контртренд ×0.5, IMPULSIVE ×0.7. |
| **26** | **🎚️ Adaptive SL** | Корректировка SL по дельте символа: сильная дельта в нашу сторону (>100) → ×0.6, слабая (<30) → ×1.4. |
| **27** | **🔄 Recovery (Шаг 10.4)** | Стартовая реконсиляция: загрузка паспортов из repo → replay трейдов за 24ч → сверка с биржей → создание `RECOVERY`-паспортов для orphan-позиций / закрытие призраков. |
| **28** | **📝 client_order_id** | Формат входа: `{SYMBOL_4}_{STRATEGY_10}_{PASS_SHORT}_{TS}`[:35]. Закрытие: `C1_/C2_/CS_/CE_` + passport_id. Жёсткая валидация (защита от пустых/пробелов). |
| **29** | **🧪 Тесты** | 24+ unit-теста: идемпотентность, reconciliation, recovery, drift monitor, TTL, pre-trade gate, race conditions, external close. Все зелёные. |
| **30** | **⚠️ Известные проблемы** | WS-разрывы ~30-60с (code 1006). REST-таймауты. `WinError 121` на Windows. Зомби-паспорта в `SIGNAL_GENERATED` при сбое отправки (карта переходов не разрешает → FAILED). |
| **31** | **🎯 Текущие задачи (Спринт 6)** | P1: Динамический PositionSizer (уже реализован, нужна интеграция в `signal_handler`). P2: Стресс-тесты + калибровка. P3 (отложен): Live Dashboard. |
| **32** | **📏 Правила кода** | `dataclasses`/`TypedDict`, JSON-логи, `asyncio`, изолированные тесты с моками, миксины для расширения Orchestrator, SSOT через Passport, Hedge Mode без `reduceOnly`. |
| **33** | **📂 Ключевые файлы** | `main.py` (точка входа, Platform class), `trading/orchestrator.py`, `trading/passport.py`, `extensions/risk/position_sizer.py`, `extensions/analytics/absorption_detector.py`, `strategies/{wall_fade_v3,absorption_v2,breakout_v1}.py`, `DOC/Состояние_2026_08_30.txt`. |
| **34** | **🔌 EventBus события** | `SIGNAL_GENERATED`, `ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `POSITION_OPENED/CLOSED/CLOSING`, `ORDER_FILLED/PARTIAL/CANCELED`, `TTL_EXPIRED`, `DRIFT_DETECTED`, `SYNC_REQUEST`, `ABSORPTION_DETECTED`, `BREAKOUT_OPPORTUNITY`, `BTC_CONTEXT_UPDATED`, `CONTEXT_UPDATED_{SYM}`, `DIVERGENCE_DETECTED`, `ATR_UPDATED`, `BASIS_UPDATED`, `PRICE_UPDATE`. |

---

### 🚀 Быстрый старт в новом чате

```
Привет! Я работаю над платформой PLATO — алгоритмический трейдинг на Binance (Order Flow / микроструктура). 
Вот полный контекст проекта: [ВСТАВИТЬ ТАБЛИЦУ ВЫШЕ]

