#  CONTEXT.md — Полное обновление

Создам актуальный файл, который отражает всё, что мы сделали. Это будет **единый источник правды** для быстрого входа в проект.

---

## 📄 Файл: `CONTEXT.md`

```markdown
# PLATO — Торговая платформа (Контекст проекта)

**Дата обновления:** 27 августа 2026  
**Версия:** 10.4.4 (Stable Core)  
**Статус:** ✅ Работает стабильно в production (testnet)

---

## 🎯 Что это такое

PLATO — автоматизированная торговая платформа для фьючерсов Binance (Hedge Mode).  
**Текущий символ:** SOLUSDT  
**Стратегии:** WallFade, Absorption, Breakout  
**Режим:** Internal Stop (TP/SL управляются локально, не выставляются на биржу)

---

## ✅ Что работает (после Шагов 1-10)

### Торговый цикл
- ✅ Сигнал → ORDER_SENT → OPEN → TP1 (PARTIAL_CLOSE) → TP2/SL (CLOSED)
- ✅ Короткие client_order_id для закрытий (C1_, C2_, CS_) ≤ 30 символов
- ✅ Корректное распознавание закрытий даже при обрезке ID биржей
- ✅ Умная реконсиляция объёма (если WS потерял partial, rest_verifier восстановит)
- ✅ PnL рассчитывается и сохраняется в паспорт

### Надёжность
- ✅ Защита от CancelledError (платформа не падает при разрывах WS)
- ✅ Стартовая реконсиляция за 1-2 секунды (загрузка паспортов + replay трейдов)
- ✅ Автоматическое создание RECOVERY-паспортов для orphan-позиций
- ✅ Автоматическое закрытие призраков (local > exchange)
- ✅ DriftMonitor по сумме позиций (не по первому паспорту)
- ✅ Верификатор ордеров с отменой после FILLED

### Синхронизация
- ✅ PassportRepository.load_all() — загрузка всех паспортов из JSON
- ✅ BinanceRestClient.get_user_trades() — replay истории за 24 часа
- ✅ BinanceRestClient.get_all_orders() — быстрый replay (2 запроса вместо N)
- ✅ Матрица решений: exchange vs local → RECOVERY или PHANTOM_CLEANUP

---

## 🔧 Ключевые архитектурные решения

### 1. Биржа = источник истины
- Локальное состояние — это кэш, который обязан себя проверять
- WS-события — оптимистичные подсказки
- REST-реконсиляция — арбитр при расхождениях

### 2. Короткие client_order_id
- Формат: `C1_PASS_YYYYMMDD_HHMMSS_XXXXXX` (30 символов)
- Binance режет до 35 символов → старый формат `CLOSE_TP1_HIT_PASS_...` (42 символа) терял хвост
- Fallback-парсер для legacy обрезанных ID

### 3. Двойная защита от наслоения позиций
- **При старте:** загрузка паспортов + replay трейдов + сверка с биржей
- **В runtime:** DriftMonitor сравнивает сумму локальных позиций vs биржа
- **В RiskManager:** авторитетный размер позиции с биржи, а не из паспорта

### 4. EventBus как шина событий
- Все модули общаются через события (POSITION_OPENED, ORDER_FILLED, DRIFT_DETECTED)
- Новые модули (Analytics, Advanced Risk) подписываются на шину, не ломая ядро

### 5. Stable Core vs Extensions
- **Stable Core** (блоки 1-4): Passport, StateManager, RiskManager, LifecycleManager — не трогаем
- **Extensions** (блоки 5-7): Analytics, Advanced Risk, Strategies — добавляем без риска

---

## 🐛 Исправленные критические баги

| Баг | Симптом | Решение |
|-----|---------|---------|
| Обрезка client_order_id | Закрытия TP/SL не распознавались, паспорта оставались OPEN | Короткий формат C1_/C2_/CS_ (Шаг 10.1) |
| Потеря объёма при входе | WS partial 4.43 → rest_verifier FILLED 7.0 игнорировался (noop) | Умная реконсиляция: если executed_qty != position_size → обновить (Шаг 10.2) |
| Наслоение позиций при рестарте | Память пустая → новая сделка поверх живой биржевой | Загрузка паспортов + replay трейдов (Шаг 10.4.3) |
| Медленный replay (80 сек) | N запросов get_order_status для каждого трейда | Один запрос get_all_orders + карта orderId→clientOrderId (Шаг 10.4.4) |
| CancelledError при разрывах WS | Платформа падала при нестабильной сети | Защита от каскадной отмены в main loop (Шаг 9.5) |
| DriftMonitor сравнивал с первым паспортом | При двух активных паспортах дрейф не детектился | Сравнение суммы локальных позиций vs биржа (Шаг 10.5) |

---

##  Структура проекта

```
PLATO/
├── main.py                          # Точка входа
├── core/
│   ├── event_bus.py                 # Шина событий
│   ├── config_loader.py             # Загрузка конфигов
│   ├── json_logger.py               # JSON-логирование
│   ├── logger.py                    # Стандартный логгер
│   ── types.py                     # Enum'ы (PassportStatus, OrderSide)
├── adapters/
│   ├── binance_rest.py              # REST клиент (ордера, позиции, трейды)
│   ├── binance_ws.py                # WebSocket адаптер (рынок + аккаунт)
│   └── channel_router.py            # Маршрутизация WS-каналов
├── trading/
│   ├── passport.py                  # TradePassport (SSOT сделки)
│   ├── passport_manager.py          # Менеджер паспортов (кэш в памяти)
│   ├── passport_repository.py       # Сохранение/загрузка JSON
│   ├── state_manager.py             # Контроль переходов статусов
│   ├── orchestrator.py              # Главный оркестратор (миксины)
│   ├── event_handlers.py            # Обработчики событий (WS → паспорт)
│   ├── risk_manager.py              # Внутренний стоп (TP1/TP2/SL)
│   ├── lifecycle_manager.py         # TTL для лимитных ордеров
│   ├── order_verifier.py            # REST-верификация ордеров
│   ├── drift_monitor.py             # Периодическая сверка с биржей
│   ├── trader.py                    # Исполнитель команд
│   ├── exit_calculator.py           # Расчёт SL/TP/TP1/TP2
│   ├── recovery.py                  # Стартовая реконсиляция
│   ├── monitor.py                   # Мониторинг зависших ордеров
│   ── position_monitor.py          # Мониторинг позиций (отключён)
├── strategies/
│   ├── wall_fade.py                 # Стратегия отскока от стены
│   ├── absorption.py                # Стратегия поглощения
│   └── breakout.py                  # Стратегия пробоя
├── tests/                           # Unit-тесты (41 тест, все зелёные)
│   ├── test_step9_close_order.py
│   ├── test_step10_1_short_client_order_id.py
│   ├── test_step10_2_volume_reconciliation.py
│   ├── test_step10_4_load_all.py
│   ├── test_step10_4_2_user_trades.py
│   └── test_step10_4_3_startup_reconciliation.py
├── passports/                       # JSON-файлы паспортов (runtime)
├── logs/                            # JSONL-логи (platform_log.jsonl)
├── config/
│   ├── trading.json                 # Конфиг торговли (лот, ATR, стратегии)
│   └── secrets.json                 # API ключи (в .gitignore)
└── docs/                            # Документация
    ├── 13_FUNCTIONAL_ARCHITECTURE.md
    ├── 10_STABLE_CORE.md
    └── CONTEXT.md                   # ← этот файл
```

---

## 🚀 Как запускать

### Development (рабочая машина)
```powershell
cd C:\Users\m.ongudushev\proga\PLATO
python main.py
```

### Production (домашняя машина)
```powershell
cd C:\Users\user\PLATO31\PLATO
git pull origin main
python main.py
```

### Тесты
```powershell
pytest tests/ -v
```

### Синхронизация между машинами
```powershell
# На машине-источнике (где внесены изменения)
git add .
git commit -m "описание"
git push origin main

# На машине-приёмнике
git pull origin main
# Перезапустить платформу (Ctrl+C → python main.py)
```

---

## 📊 Текущая статистика

| Метрика | Значение |
|---------|----------|
| Коммиты | ~15 |
| Unit-тесты | 41 (все зелёные) |
| Слоёв защиты | 9 (Verifier, DriftMonitor, Recovery, Sync, TTL, etc.) |
| Закрытых багов | 6 критических |
| Время старта | 1-2 секунды (recovery) |
| Время работы | 6+ часов без падений |

---

## 🎯 Бэклог (что впереди)

### Фаза 1: Аналитика (2-3 недели)
- [ ] Динамический ATR (запрос с биржи раз в час)
- [ ] Анализатор стакана (поиск крупных лимитных заявок)
- [ ] HVN-уровни (High Volume Nodes)
- [ ] Whale Detector (крупные сделки)

### Фаза 2: Advanced Risk (3-4 недели)
- [ ] Грейды A/B/C/REJECT (на основе R:R)
- [ ] Ступенчатый SL (SL1 50% + SL2 50%)
- [ ] Basis Stop (контроль изменения basis)
- [ ] Confidence Score (взвешенная оценка сигнала)

### Фаза 3: Мультивалютность (4-6 недель)
- [ ] Поддержка BTCUSDT, ETHUSDT
- [ ] Изоляция событий в EventBus по символу
- [ ] Общая vs раздельная реконсиляция

### Фаза 4: GUI и мониторинг (6-8 недель)
- [ ] Telegram-бот для уведомлений
- [ ] Веб-интерфейс для мониторинга
- [ ] Экспорт истории сделок (Win Rate, Drawdown, Equity)

---

## ⚠️ Известные ограничения

1. **Нестабильная сеть на домашней машине** — WS разрывы каждые 30-60 секунд, но платформа справляется
2. **Testnet Binance** — иногда возвращает таймауты, но retry-логика работает
3. **Один символ (SOLUSDT)** — мультивалютность в бэклоге
4. **Internal Stop** — TP/SL не выставляются на биржу, управляются локально (риск при падении платформы mitigated recovery)

---

## 📚 Связанные документы

- `docs/13_FUNCTIONAL_ARCHITECTURE.md` — полная архитектура с roadmap
- `docs/10_STABLE_CORE.md` — принципы стабильного ядра
- `docs/08_ANALYTICS_LAYER.md` — детали аналитики (будущая Фаза 1)
- `docs/09_IDENTIFIERS.md` — словарь идентификаторов
- `docs/12_IMPLEMENTATION_PLAN.md` — детальный план реализации

---

## 🆘 Быстрый старт для нового разработчика

1. Прочитай этот файл (CONTEXT.md) — 5 минут
2. Прочитай `docs/10_STABLE_CORE.md` — принципы ядра
3. Запусти тесты: `pytest tests/ -v`
4. Запусти платформу: `python main.py`
5. Смотри логи: `logs/platform_log.jsonl`
6. Смотри паспорта: `passports/*.json`

**Главное правило:** Stable Core (блоки 1-4) не трогаем без крайней необходимости. Новые фичи — только как Extensions.

---

**Конец CONTEXT.md**
```

---

## 📦 Коммит

```powershell
git add CONTEXT.md
git commit -m "docs: add CONTEXT.md — single source of truth for project status and architecture"
git push origin main
```

---

## 🎯 Что это даёт

1. **Быстрый вход** — любой разработчик (или ты через месяц) поймёт проект за 5 минут
2. **Актуальный статус** — видно, что работает, а что в бэклоге
3. **Архитектурные решения** — зафиксированы, чтобы не наступать на те же грабли
4. **Бэклог** — понятно, что делать дальше

Файл готов к коммиту. После этого у нас будет **полная документация** для продолжения работы в любом чате или сессии. 🚀