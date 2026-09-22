"""Базовый контракт Feature: вход из ленты, снимок метрик, свежесть."""
import time
from typing import Any, Dict


class Feature:
    """Абстрактный признак рынка.

    Контракт потомка:
      - on_trade / on_orderbook / on_kline  — вход из ленты (разводит FeatureSet)
      - _recalc(now)                        — пересчёт состояния по таймеру ленты
      - snapshot()                          — чистое чтение состояния (без мутаций)
      - is_fresh(now, max_age_sec)          — свежесть для фильтра сигналов
    """

    name: str = "base"

    def __init__(self, symbol: str, config: Dict[str, Any], log):
        self.symbol = symbol
        self.config = config
        self._log = log
        self._last_update_ts = 0.0
        self._last_recalc_ts = 0.0
        self._recalc_interval = float(config.get("recalc_interval_sec", 3.0))

    # --- входы из ленты (переопределяются потомками) ---
    def on_trade(self, price: float, qty: float, is_buy: bool, ts: float) -> None:
        pass

    def on_orderbook(self, bids, asks, ts: float) -> None:
        pass

    def on_kline(self, kline: Dict[str, Any]) -> None:
        pass

    # --- пересчёт состояния (вызывается из on_trade по интервалу) ---
    def _maybe_recalc(self, ts: float) -> None:
        if ts - self._last_recalc_ts >= self._recalc_interval:
            self._last_recalc_ts = ts
            self._recalc(ts)

    def _recalc(self, ts: float) -> None:
        pass

    # --- выход: только чтение ---
    def snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError

    def is_fresh(self, now: float, max_age_sec: float) -> bool:
        return (now - self._last_update_ts) <= max_age_sec