"""
Binance REST API клиент с Circuit Breaker (автоматический выключатель при бане).
"""

import asyncio
import hashlib
import hmac
import time
import re
from typing import Dict, Any, Optional, List
import aiohttp
import traceback

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
        
        # 🔥 Circuit Breaker: время, до которого REST заблокирован
        self._ban_until = 0.0

    def _ban_active(self) -> bool:
        """True, если IP сейчас забанен и REST-запросы слать нельзя."""
        return bool(self._ban_until) and time.time() < self._ban_until

    def _register_ban(self, error_text: str):
        """Парсит 'banned until <ms>' из ошибки -1003 и включает паузу."""
        m = re.search(r"banned until (\d+)", error_text)
        if m:
            until_sec = int(m.group(1)) / 1000.0
            # +5 секунд запаса, чтобы точно не упереться в границу
            self._ban_until = max(self._ban_until, until_sec + 5.0)
            wait_time = int(self._ban_until - time.time())
            self.logger.warning(
                f"🛑 [REST BREAKER] Получен -1003. REST-запросы приостановлены до "
                f"{time.strftime('%H:%M:%S', time.localtime(self._ban_until))} "
                f"(ждём {wait_time} сек)."
            )

    async def get_listen_key(self) -> str:
        """Получить listen_key для user data stream."""
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_listen_key() пропущен — активен бан")
            return ''
        
        result = await self._request('POST', '/fapi/v1/listenKey', signed=True)
        return result.get('listenKey', '')

    async def renew_listen_key(self, listen_key: str) -> bool:
        """Продлить listen_key (keep-alive)."""
        if self._ban_active():
            return False
        
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
            
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(req_params.items())])
        
        if signed:
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            query_string += f"&signature={signature}"
        
        url = f"{self.base_url}{path}?{query_string}"
        headers = {"X-MBX-APIKEY": self.api_key}
        
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")
            
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        
        async with session.request(method, url, headers=headers, timeout=timeout) as resp:
            data = await resp.json()
            if isinstance(data, dict) and 'code' in data:
                error_msg = f"Binance API error: {data.get('msg', 'Unknown error')} (code: {data.get('code')})"
                # 🔥 Circuit Breaker: регистрируем бан при -1003
                if data.get('code') == -1003:
                    self._register_ban(error_msg)
                self.logger.error(error_msg)
                raise Exception(error_msg)
            return data

    # ─── Открытые методы ──────────────────────────────────────

    async def get_position(self, symbol: str):
        """Получить позицию по символу."""
        # 🔥 Circuit Breaker
        if self._ban_active():
            self.logger.debug(f"️ [REST] get_position({symbol}) пропущен — активен бан до {self._ban_until}")
            return None

        params = {'symbol': symbol}
        try:
            result = await self._request('GET', '/fapi/v2/positionRisk', params, signed=True)
            
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for positionRisk: {type(result)}")
                return None
            
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
                
                if abs(pos_amt) > 0.001:
                    total_size += pos_amt
                    if total_size != 0:
                        total_entry_price = (total_entry_price * (total_size - pos_amt) + entry_price * pos_amt) / total_size
                    total_pnl += unrealized_pnl
                    active_position = pos
            
            if active_position:
                return {
                    'symbol': symbol,
                    'side': 'short' if total_size < 0 else 'long',
                    'size': abs(total_size),
                    'entry_price': total_entry_price,
                    'unrealized_pnl': total_pnl
                }
            
            return {'symbol': symbol, 'side': 'none', 'size': 0.0, 'entry_price': 0.0, 'unrealized_pnl': 0.0}
            
        except Exception as e:
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"⚠️ [REST] Failed to get position: {error_text}")
            return None

    async def get_open_orders(self, symbol: str) -> List[Dict]:
        """Получить все открытые ордера по символу."""
        # 🔥 Circuit Breaker
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_open_orders({symbol}) пропущен — активен бан до {self._ban_until}")
            return []
        
        params = {'symbol': symbol}
        try:
            result = await self._request('GET', '/fapi/v1/openOrders', params, signed=True)
            
            if not isinstance(result, list):
                self.logger.error(f"Binance returned non-list for openOrders: {type(result)}")
                return []
            
            return result
            
        except Exception as e:
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"⚠️ [REST] Failed to get open orders: {error_text}")
            return []

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Dict:
        """Получить стакан."""
        if self._ban_active():
            return {}
        return await self._request('GET', '/fapi/v1/depth', {'symbol': symbol, 'limit': limit})

    async def get_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[Dict]:
        """Получить ордер по client_order_id."""
        if self._ban_active():
            return None
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
        if self._ban_active():
            return {'success': False, 'error': 'REST banned, order not sent'}
        
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
        if self._ban_active():
            return {'success': False, 'error': 'REST banned, order not sent'}
        
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
        """ВРЕМЕННОЕ РЕШЕНИЕ для Testnet. Используем LIMIT-ордер с reduceOnly=True."""
        if self._ban_active():
            return {'success': False, 'error': 'REST banned, order not sent'}
        
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

        self.logger.info(f" [REST] Creating SL as LIMIT (Testnet workaround): {symbol} {side} @ {limit_price} (stop: {stop_price})")

        if self._session is None:
            await self._ensure_session()
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")

        async with session.post(url, headers=headers) as resp:
            data = await resp.json()
            self.logger.info(f" [REST] LIMIT response: {data}")

        if isinstance(data, dict) and 'code' in data:
            error_msg = data.get('msg', 'Unknown error')
            if data.get('code') == -1003:
                self._register_ban(f"Binance API error: {error_msg} (code: -1003)")
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
        """ВРЕМЕННОЕ РЕШЕНИЕ для Testnet."""
        if self._ban_active():
            return {'success': False, 'error': 'REST banned, order not sent'}
        
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

        self.logger.info(f"🔍 [REST] Creating STOP_LIMIT as LIMIT (Testnet workaround): {symbol} {side} @ {limit_price} (stop: {stop_price})")

        if self._session is None:
            await self._ensure_session()
        session = self._session
        if session is None:
            raise RuntimeError("Session not initialized")

        async with session.post(url, headers=headers) as resp:
            data = await resp.json()
            self.logger.info(f"🔍 [REST] LIMIT response: {data}")

        if isinstance(data, dict) and 'code' in data:
            error_msg = data.get('msg', 'Unknown error')
            if data.get('code') == -1003:
                self._register_ban(f"Binance API error: {error_msg} (code: -1003)")
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
        """Получить историю трейдов пользователя."""
        # 🔥 Circuit Breaker
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_user_trades({symbol}) пропущен — активен бан до {self._ban_until}")
            return []
        
        params = {
            'symbol': symbol,
            'limit': min(limit, 1000)
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
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"⚠️ [REST] Failed to get user trades: {error_text}")
            return []

    async def get_all_orders(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000
    ) -> List[Dict]:
        """История ордеров с origClientOrderId."""
        if self._ban_active():
            return []
        
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
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"⚠️ [REST] Failed to get all orders: {error_text}")
            return []

    async def cancel_order(self, symbol: str, order_id: str) -> Dict:
        if self._ban_active():
            return {'success': False, 'error': 'REST banned, cancel not sent'}
        
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
        """Принудительно пересоздать aiohttp-сессию."""
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
        """Получить статус ордера с биржи."""
        if self._ban_active():
            return None
        
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
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"⚠️ [REST] Failed to get order status: {error_text}")
            return None

    async def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Получить информацию о торговых правилах биржи."""
        if self._ban_active():
            return {}
        
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
            
        try:
            result = await self._request('GET', '/fapi/v1/exchangeInfo', params, signed=False)
            return result
        except Exception as e:
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.error(f"️ [REST] Failed to get exchange info: {error_text}")
            return {}

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> list:
        """Получение исторических свечей с Binance Spot REST API."""
        import logging
        from aiohttp import ClientTimeout
        
        logger = logging.getLogger(__name__)
        
        # 🔥 Circuit Breaker (для Spot API используем тот же флаг)
        if self._ban_active():
            logger.debug(f"⏸️ [REST] get_klines({symbol}) пропущен — активен бан")
            return []
        
        url = f"https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit
        }
        
        try:
            timeout = ClientTimeout(total=10)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        error_text = await response.text()
                        logger.error(f"Binance REST Klines error {response.status}: {error_text}")
                        return []
        except Exception as e:
            logger.error(f"Exception while fetching klines for {symbol}: {e}")
            return []

    async def close(self):
        """Закрыть сессию."""
        if self._session and not self._session.closed:
            await self._session.close()