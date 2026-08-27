"""
Binance REST API клиент.
"""

import asyncio  # 🔥 ДОБАВИТЬ
import hashlib
import hmac
import time
from typing import Dict, Any, Optional, List
import aiohttp

from core.logger import get_logger

class BinanceRestClient:
    """Клиент для работы с Binance REST API."""

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://testnet.binancefuture.com", timeout: int = 30):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self.logger = get_logger(__name__)

    async def get_listen_key(self) -> str:
        """Получить listen_key для user data stream."""
        result = await self._request('POST', '/fapi/v1/listenKey', signed=True)
        return result.get('listenKey', '')

    async def renew_listen_key(self, listen_key: str) -> bool:
        """Продлить listen_key (keep-alive)."""
        try:
            await self._request('PUT', '/fapi/v1/listenKey', {'listenKey': listen_key}, signed=True)
            return True
        except Exception:
            return False    

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    def _sign(self, params: Dict[str, Any]) -> str:
        """Создаёт подпись для запроса."""
        # Сортируем параметры и формируем строку
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _request(self, method: str, path: str, params: Optional[Dict] = None, signed: bool = False) -> Dict:
        await self._ensure_session()
        
        if params is None:
            params = {}
        
        req_params = params.copy()
        
        if signed:
            req_params['timestamp'] = int(time.time() * 1000)
            req_params['recvWindow'] = 60000
            
        # Формируем строку запроса для подписи (сортировка по ключам обязательна)
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(req_params.items())])
        
        if signed:
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            query_string += f"&signature={signature}"
        
        # ВАЖНО: параметры уже в URL, не передаем их в session.request, чтобы aiohttp не перекодировал их
        url = f"{self.base_url}{path}?{query_string}"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")
            
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with session.request(method, url, headers=headers, timeout=timeout) as resp:
            data = await resp.json()
            # Binance возвращает код ошибки в поле 'code' при неудаче
            if isinstance(data, dict) and 'code' in data:
                error_msg = f"Binance API error: {data.get('msg', 'Unknown error')} (code: {data.get('code')})"
                self.logger.error(error_msg)
                raise Exception(error_msg)  # <-- КРИТИЧЕСКИ ВАЖНО: прерываем выполнение
            return data

    # ─── Открытые методы ──────────────────────────────────────

    async def get_position(self, symbol: str) -> Optional[Dict]:
        """Получить позицию по символу.
        
        Возвращает:
        - Dict с данными позиции при успехе (суммирует все positionSide в Hedge Mode)
        - None при ошибке (бан, таймаут, сеть) — КРИТИЧНО для защиты от ложных закрытий
        """
        params = {'symbol': symbol}
        try:
            result = await self._request('GET', '/fapi/v2/positionRisk', params, signed=True)
            
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for positionRisk: {type(result)}")
                return None
            
            # 🔥 ИЩЕМ АКТИВНУЮ ПОЗИЦИЮ (с ненулевым размером)
            # В Hedge Mode Binance возвращает несколько записей (LONG + SHORT)
            active_position = None
            total_size = 0.0
            total_entry_price = 0.0
            total_pnl = 0.0
            
            for pos in result:
                if not isinstance(pos, dict):
                    continue
                    
                pos_amt = float(pos.get('positionAmt', 0) or 0)
                entry_price = float(pos.get('entryPrice', 0) or 0)
                unrealized_pnl = float(pos.get('unRealizedProfit', 0) or 0)
                
                if abs(pos_amt) > 0.001:  # Ненулевая позиция
                    total_size += pos_amt
                    # Усредняем цену входа взвешенно по размеру
                    if total_size != 0:
                        total_entry_price = (total_entry_price * (total_size - pos_amt) + entry_price * pos_amt) / total_size
                    total_pnl += unrealized_pnl
                    active_position = pos
            
            if active_position:
                # Нашли активную позицию
                return {
                    'symbol': symbol,
                    'side': 'short' if total_size < 0 else 'long',
                    'size': abs(total_size),
                    'entry_price': total_entry_price,
                    'unrealized_pnl': total_pnl
                }
            
            # Все позиции нулевые
            return {'symbol': symbol, 'side': 'none', 'size': 0.0, 'entry_price': 0.0, 'unrealized_pnl': 0.0}
            
        except Exception as e:
            print(f"️ [REST] Failed to get position: {e}")
            return None

    async def get_open_orders(self, symbol: str) -> List[Dict]:
        """
        Получить все открытые ордера по символу.
        Возвращает список ордеров или пустой список при ошибке.
        """
        params = {'symbol': symbol}
        try:
            result = await self._request('GET', '/fapi/v1/openOrders', params, signed=True)
            
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for openOrders: {type(result)}")
                return []
            
            return result
            
        except Exception as e:
            print(f"⚠️ [REST] Failed to get open orders: {e}")
            return []

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        """Получить стакан."""
        return await self._request('GET', '/fapi/v1/depth', {'symbol': symbol, 'limit': limit})

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[Dict]:
        """Получить ордер по client_order_id."""
        result = await self._request('GET', '/fapi/v1/order', {
            'symbol': symbol,
            'origClientOrderId': client_order_id
        }, signed=True)
        return result if result.get('orderId') else None

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None,
        position_side: str = 'BOTH'
    ) -> Dict:
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': str(quantity),
            'positionSide': position_side,
        }
        
        if reduce_only:
            params['reduceOnly'] = 'true'
        if new_client_order_id:
            params['newClientOrderId'] = new_client_order_id

        try:
            result = await self._request('POST', '/fapi/v1/order', params, signed=True)
            return {
                'success': True,
                'order_id': result.get('orderId'),
                'client_order_id': result.get('clientOrderId'),
                'status': result.get('status', 'NEW'),
                'raw_response': result
            }
        except Exception as e:
            return {'success': False, 'order_id': None, 'client_order_id': None, 'status': 'FAILED', 'error': str(e)}

    async def create_limit_order(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None,
        position_side: str = 'BOTH'
    ) -> Dict:
        """Создать лимитный ордер."""
        
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'price': str(price),
            'quantity': str(quantity),
            'positionSide': position_side,
            'timestamp': int(time.time() * 1000),
            'recvWindow': 60000
        }
        
        # 🔥 ТОЛЬКО если reduce_only = True
        if reduce_only:
            params['reduceOnly'] = 'true'
            
        if new_client_order_id:
            params['newClientOrderId'] = new_client_order_id

        try:
            result = await self._request('POST', '/fapi/v1/order', params, signed=True)
            return {
                'success': True,
                'order_id': result.get('orderId'),
                'client_order_id': result.get('clientOrderId'),
                'status': result.get('status', 'NEW'),
                'raw_response': result
            }
        except Exception as e:
            return {
                'success': False,
                'order_id': None,
                'client_order_id': None,
                'status': 'FAILED',
                'error': str(e)
            }

    async def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None
    ) -> Dict:
        """
        ВРЕМЕННОЕ РЕШЕНИЕ для Testnet.
        Algo API не работает на testnet.binancefuture.com.
        Используем LIMIT-ордер с reduceOnly=True.
        """
        # Определяем цену для лимитного ордера
        # Для SHORT (SELL): SL выше цены, ставим лимит чуть выше stop_price
        # Для LONG (BUY): SL ниже цены, ставим лимит чуть ниже stop_price
        if side.upper() == 'SELL':
            limit_price = stop_price + 0.01
        else:
            limit_price = stop_price - 0.01

        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'price': str(limit_price),
            'quantity': str(quantity),
            'reduceOnly': 'true' if reduce_only else 'false',
            'timestamp': int(time.time() * 1000),
            'recvWindow': 60000
        }
        if new_client_order_id:
            params['newClientOrderId'] = new_client_order_id

        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        query_string += f"&signature={signature}"

        url = f"{self.base_url}/fapi/v1/order?{query_string}"
        headers = {"X-MBX-APIKEY": self.api_key}

        print(f"🔍 [REST] Creating SL as LIMIT (Testnet workaround): {symbol} {side} @ {limit_price} (stop: {stop_price})")

        if self._session is None:
            await self._ensure_session()
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")

        async with session.post(url, headers=headers) as resp:
            data = await resp.json()
            print(f"🔍 [REST] LIMIT response: {data}")

        if isinstance(data, dict) and 'code' in data:
            error_msg = data.get('msg', 'Unknown error')
            self.logger.error(f"Binance API error: {error_msg} (code: {data.get('code')})")
            return {'success': False, 'error': error_msg}

        return {
            'success': True,
            'order_id': data.get('orderId'),
            'client_order_id': data.get('clientOrderId'),
            'status': data.get('status'),
            'raw_response': data
        }

    async def create_stop_limit_order(
        self,
        symbol: str,
        side: str,
        stop_price: float,
        limit_price: float,
        quantity: float,
        reduce_only: bool = False,
        new_client_order_id: Optional[str] = None
    ) -> Dict:
        """
        ВРЕМЕННОЕ РЕШЕНИЕ для Testnet.
        Используем LIMIT-ордер с reduceOnly=True.
        """
        params = {
            'symbol': symbol,
            'side': side.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'price': str(limit_price),
            'quantity': str(quantity),
            'reduceOnly': 'true' if reduce_only else 'false',
            'timestamp': int(time.time() * 1000),
            'recvWindow': 60000
        }
        if new_client_order_id:
            params['newClientOrderId'] = new_client_order_id

        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        query_string += f"&signature={signature}"

        url = f"{self.base_url}/fapi/v1/order?{query_string}"
        headers = {"X-MBX-APIKEY": self.api_key}

        print(f"🔍 [REST] Creating STOP_LIMIT as LIMIT (Testnet workaround): {symbol} {side} @ {limit_price} (stop: {stop_price})")

        if self._session is None:
            await self._ensure_session()
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")

        async with session.post(url, headers=headers) as resp:
            data = await resp.json()
            print(f"🔍 [REST] LIMIT response: {data}")

        if isinstance(data, dict) and 'code' in data:
            error_msg = data.get('msg', 'Unknown error')
            self.logger.error(f"Binance API error: {error_msg} (code: {data.get('code')})")
            return {'success': False, 'error': error_msg}

        return {
            'success': True,
            'order_id': data.get('orderId'),
            'client_order_id': data.get('clientOrderId'),
            'status': data.get('status'),
            'raw_response': data
        } 

    async def get_user_trades(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500
    ) -> List[Dict]:
        """
        Получить историю трейдов пользователя.
        
        🔥 ШАГ 10.4.2: Используется для replay трейдов при стартовой реконсиляции.
        
        Args:
            symbol: Торговая пара (например, 'SOLUSDT')
            start_time: Начало периода в мс (Unix timestamp * 1000)
            end_time: Конец периода в мс
            limit: Максимальное количество записей (до 1000)
        
        Returns:
            Список трейдов с полями:
            - symbol, id, orderId
            - side, positionSide
            - price, qty, quoteQty
            - commission, commissionAsset
            - time, isBuyer, isMaker
            - isClosePosition, realizedPnl
        """
        params = {
            'symbol': symbol,
            'limit': min(limit, 1000)  # Binance лимит = 1000
        }
        
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        
        try:
            result = await self._request('GET', '/fapi/v1/userTrades', params, signed=True)
            
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for userTrades: {type(result)}")
                return []
            
            return result
            
        except Exception as e:
            self.logger.error(f"⚠️ [REST] Failed to get user trades: {e}")
            return []

    async def get_all_orders(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """
        🔥 ШАГ 10.4.4: История ордеров с origClientOrderId.
        Один вызов заменяет N вызовов get_order_status в replay.
        """
        params = {'symbol': symbol, 'limit': min(limit, 1000)}
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        try:
            result = await self._request('GET', '/fapi/v1/allOrders', params, signed=True)
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for allOrders: {type(result)}")
                return []
            return result
        except Exception as e:
            self.logger.error(f"⚠️ [REST] Failed to get all orders: {e}")
            return []

    async def get_all_orders(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """
        🔥 ШАГ 10.4.4: История ордеров с origClientOrderId.
        Один вызов заменяет N вызовов get_order_status в replay.
        """
        params = {'symbol': symbol, 'limit': min(limit, 1000)}
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time

        try:
            result = await self._request('GET', '/fapi/v1/allOrders', params, signed=True)
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for allOrders: {type(result)}")
                return []
            return result
        except Exception as e:
            self.logger.error(f"⚠️ [REST] Failed to get all orders: {e}")
            return []

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        params = {
            'symbol': symbol,
            'orderId': order_id
        }
        try:
            result = await self._request('DELETE', '/fapi/v1/order', params, signed=True)
            return {
                'success': True,
                'order_id': result.get('orderId'),
                'client_order_id': result.get('clientOrderId'),
                'status': result.get('status', 'CANCELED')
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    async def reset_session(self):
        """Принудительно пересоздать aiohttp-сессию (зависший сокет → новый)."""
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

    async def get_order_status(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Получить статус ордера с биржи.
        ВАЖНО: Binance не принимает оба идентификатора одновременно.
        Приоритет: orderId (если передан, client_order_id игнорируется).
        """
        if not order_id and not client_order_id:
            return None

        params = {'symbol': symbol}
        if order_id:
            params['orderId'] = order_id
        elif client_order_id:
            params['origClientOrderId'] = client_order_id

        try:
            result = await self._request('GET', '/fapi/v1/order', params, signed=True)
            return result if result.get('orderId') else None
        except Exception as e:
            print(f"⚠️ [REST] Failed to get order status: {e}")
            return None

    async def close(self):
        """Закрыть сессию."""
        if self._session and not self._session.closed:
            await self._session.close()