# ⚙️ Конфигурационные файлы (Configs)

Этот документ описывает все конфигурационные файлы платформы, их структуру, назначение каждого параметра и **критические проблемы**, обнаруженные при анализе.

**Расположение:** `config/`
**Загрузчик:** `core/config_loader.py` → `ConfigLoader.load_all()`

---

## 📋 Общая структура конфигов

| Файл | Назначение | Загружается через |
| :--- | :--- | :--- |
| `exchange.json` | Настройки подключения к бирже | `load_all()` + `load_secrets()` |
| `trading.json` | Параметры торговли | `load_all()` |
| `risk.json` | Глобальный риск-менеджмент | `load_all()` |
| `strategies.json` | Параметры стратегий | `load_all()` |
| `secrets.json` | API-ключи (не должен быть в Git) | `load_secrets()` |

---

## 🔌 1. `config/exchange.json` — Настройки подключения

### Текущее содержимое:
```json
{
    "symbol": "SOLUSDT",
    "testnet": true,
    "api_key": "lLUSywh...",
    "api_secret": "q4IXVti...",
    "rest_base_url": "https://testnet.binancefuture.com",
    "ws_base_url": "wss://stream.binancefuture.com/ws"
}