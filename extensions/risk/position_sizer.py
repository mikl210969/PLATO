"""
PositionSizer — динамический расчет размера позиции на основе риска в USDT.
Включает Fallback-механизм на случай сбоев API биржи.
"""
import logging
from decimal import Decimal
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class PositionSizer:
    def __init__(self, rest_client):
        self.rest = rest_client
        self._exchange_info_cache: Dict[str, Dict[str, Any]] = {}
        
        # 🔥 FALLBACK: Безопасные значения по умолчанию для основных пар.
        # Используются, если API биржи недоступен или возвращает ошибку.
        self._fallback_info = {
            "SOLUSDT": {"step_size": 0.1, "min_qty": 0.1, "min_notional": 5.0},
            "BTCUSDT": {"step_size": 0.001, "min_qty": 0.001, "min_notional": 5.0},
            "ETHUSDT": {"step_size": 0.001, "min_qty": 0.001, "min_notional": 5.0},
            "BNBUSDT": {"step_size": 0.01, "min_qty": 0.01, "min_notional": 5.0},
        }

    async def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получает и кэширует информацию о правилах торговли для конкретного символа."""
        symbol = symbol.upper()
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]

        try:
            # Запрашиваем инфо только для нужного символа (быстрее и экономит лимиты)
            exchange_info = await self.rest.get_exchange_info(symbol=symbol)
            symbols_list = exchange_info.get('symbols', [])
            
            if not symbols_list:
                logger.warning(f"⚠️ [PositionSizer] Пустой ответ от exchangeInfo для {symbol}. Применяем fallback.")
                return self._apply_fallback(symbol)

            for s in symbols_list:
                if s.get('symbol') == symbol:
                    filters = {f['filterType']: f for f in s.get('filters', [])}
                    
                    lot_filter = filters.get('LOT_SIZE', {})
                    # Binance иногда меняет название этого фильтра, проверяем оба варианта
                    notional_filter = filters.get('MIN_NOTIONAL', {}) or filters.get('NOTIONAL', {})
                    
                    info = {
                        'step_size': float(lot_filter.get('stepSize', 0.01)),
                        'min_qty': float(lot_filter.get('minQty', 0.0)),
                        'min_notional': float(notional_filter.get('minNotional', 5.0)),
                    }
                    self._exchange_info_cache[symbol] = info
                    logger.info(f"✅ [PositionSizer] Кэширована информация для {symbol}: {info}")
                    return info
            
            logger.warning(f"⚠️ [PositionSizer] Символ {symbol} не найден в ответе API. Применяем fallback.")
            return self._apply_fallback(symbol)
            
        except Exception as e:
            logger.error(f"❌ [PositionSizer] Ошибка получения exchangeInfo для {symbol} ({e}). Применяем fallback.")
            return self._apply_fallback(symbol)

    def _apply_fallback(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Применяет резервные настройки, если API недоступен."""
        if symbol in self._fallback_info:
            info = self._fallback_info[symbol]
            self._exchange_info_cache[symbol] = info
            logger.info(f"🛡️ [PositionSizer] Активирован FALLBACK для {symbol}: {info}")
            return info
        
        logger.error(f"🚫 [PositionSizer] Нет fallback-настроек для {symbol}. Сделка отклонена.")
        return None

    def _round_down(self, quantity: float, step_size: float) -> float:
        """Безопасное округление количества вниз до шага биржи с использованием Decimal."""
        try:
            qty_dec = Decimal(str(quantity))
            step_dec = Decimal(str(step_size))
            rounded = (qty_dec // step_dec) * step_dec
            return float(rounded)
        except Exception as e:
            logger.error(f"❌ [PositionSizer] Ошибка округления {quantity} с шагом {step_size}: {e}")
            return 0.0

    async def calculate(
        self, 
        symbol: str, 
        entry_price: float, 
        sl_price: float, 
        risk_usdt: float
    ) -> Optional[float]:
        """
        Рассчитывает безопасный размер позиции.
        Возвращает float (количество) или None, если риск слишком мал для минимальных требований биржи.
        """
        symbol_info = await self.get_symbol_info(symbol)
        if not symbol_info:
            logger.error(f"❌ [PositionSizer] Не удалось получить информацию о символе {symbol}")
            return None

        risk_distance = abs(entry_price - sl_price)
        if risk_distance == 0:
            logger.warning(f"⚠️ [PositionSizer] Расстояние до SL равно 0 для {symbol}")
            return None

        # 1. Базовый расчет: сколько монет купить/продать, чтобы потерять ровно risk_usdt при срабатывании SL
        raw_qty = risk_usdt / risk_distance

        # 2. Округление вниз до step_size (чтобы биржа не отклонила ордер)
        safe_qty = self._round_down(raw_qty, symbol_info['step_size'])

        # 3. Проверка минимального количества монет
        if safe_qty < symbol_info['min_qty']:
            logger.warning(
                f"🚫 [PositionSizer] Сигнал отклонен: расчетный лот ({safe_qty}) меньше минимального ({symbol_info['min_qty']}) "
                f"для {symbol}. Риск ({risk_usdt}$) слишком мал для текущего расстояния до SL ({risk_distance}$)."
            )
            return None

        # 4. Проверка минимальной стоимости ордера (notional value)
        order_value = safe_qty * entry_price
        if order_value < symbol_info['min_notional']:
            logger.warning(
                f"🚫 [PositionSizer] Сигнал отклонен: стоимость ордера ({order_value:.2f}$) меньше минимальной "
                f"({symbol_info['min_notional']}$) для {symbol}."
            )
            return None

        logger.info(
            f"✅ [PositionSizer] Расчет для {symbol}: Риск={risk_usdt}$, Дистанция SL={risk_distance:.4f}$, "
            f"Итоговый лот={safe_qty} (Стоимость: {order_value:.2f}$)"
        )
        
        return safe_qty