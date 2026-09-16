"""
Binance REST API клиент с Circuit Breaker (автоматический выключатель при бане).

🔥 PRAGMATIC TRIO (2026-09-15):
#1 Startup Prefetch   — в main.py: exchangeInfo грузится один раз до старта задач
#2 Global Semaphore   — не более 3 параллельных REST-запросов одновременно
#3 Deduplication      — одинаковые GET-запросы в полёте выполняются один раз,
                        остальные вызывающие ждут тот же результат
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

# 🔥 ФАЗА 4: контекстная метка источника REST-запроса.
# Каждая фоновая задача один раз ставит своё имя — и все её запросы
# видны в логе с пометкой caller. Спаммер определяется мгновенно.
import contextvars
REST_CALLER = contextvars.ContextVar("rest_caller", default="unknown")

class BinanceRestClient:
    """Клиент для работы с Binance REST API."""

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://testnet.binancefuture.com", timeout: int = 60):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._critical_session: Optional[aiohttp.ClientSession] = None
        self.logger = get_logger(__name__)
        self.last_known_price = 0.0
        
        # 🔥 Circuit Breaker: время, до которого REST заблокирован
        self._ban_until = 0.0
        
        # 🔥 PRAGMATIC TRIO #2: Глобальный семафор — максимум 3 параллельных запроса
        self._request_semaphore = asyncio.Semaphore(3)
        
        # 🔥 PRAGMATIC TRIO #3: in-flight GET-запросы для дедупликации
        self._inflight: Dict[str, asyncio.Task] = {}
        
        # 🔥 Кэш exchangeInfo
        self._exchange_info_cache: Dict[str, Any] = {}
        self._exchange_info_last_update: float = 0.0
        self._exchange_info_cache_ttl: float = 3600.0  # 1 час
        
        # 🔥 Хардкод для SOLUSDT на случай если REST недоступен
        self._fallback_exchange_info = {
            "SOLUSDT": {
                "pricePrecision": 2,
                "quantityPrecision": 1,
                "stepSize": 0.1,
                "tickSize": 0.01,
                "minQty": 0.1,
                "maxQty": 1000000,
            }
        }

    def _ban_active(self) -> bool:
        """True, если IP сейчас забанен и REST-запросы слать нельзя."""
        return bool(self._ban_until) and time.time() < self._ban_until

    def _register_ban(self, error_text: str):
        """Парсит 'banned until <ms>' из ошибки -1003 и включает паузу."""
        m = re.search(r"banned until (\d+)", error_text)
        if m:
            until_sec = int(m.group(1)) / 1000.0
            self._ban_until = max(self._ban_until, until_sec + 5.0)
            wait_time = int(self._ban_until - time.time())
            self.logger.warning(
                f"🛑 [REST BREAKER] Получен -1003. REST-запросы приостановлены до "
                f"{time.strftime('%H:%M:%S', time.localtime(self._ban_until))} "
                f"(ждём {wait_time} сек)."
            )

    # ──────────────────────────────────────────────────────────────
    # PRAGMATIC TRIO #3: Deduplication обёртка
    # ──────────────────────────────────────────────────────────────

    def _cleanup_inflight(self, key: str, task: asyncio.Task):
        """Убрать задачу из in-flight, когда она завершилась."""
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = False,
        critical: bool = False
    ) -> Dict:
        """
        Точка входа для всех REST-запросов.
        🔥 PRAGMATIC TRIO #3: одинаковые GET-запросы, летящие одновременно,
        выполняются ОДИН раз; остальные вызывающие ждут тот же результат.
        POST/DELETE (ордера, отмены) никогда не дедуплицируются.
        """
        if method != 'GET':
            return await self._request_with_retry(method, path, params, signed, critical)

        key_params = '&'.join(f"{k}={v}" for k, v in sorted((params or {}).items()) if k != 'timestamp')
        dedup_key = f"GET:{path}:{key_params}"

        existing = self._inflight.get(dedup_key)
        if existing is not None and not existing.done():
            self.logger.debug(f"⏳ [REST DEDUP] Присоединяемся к летящему запросу: {dedup_key}")
            return await asyncio.shield(existing)

        task = asyncio.ensure_future(
            self._request_with_retry(method, path, params, signed, critical)
        )
        self._inflight[dedup_key] = task
        task.add_done_callback(lambda t: self._cleanup_inflight(dedup_key, t))
        self.logger.debug(f"🛫 [REST DEDUP] Новый запрос в полёте: {dedup_key}")

        return await asyncio.shield(task)

    # ──────────────────────────────────────────────────────────────
    # Базовый запрос с retry и семафором
    # ──────────────────────────────────────────────────────────────

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = False,
        critical: bool = False
    ) -> Dict:
        """
        Универсальный REST-запрос с retry-логикой.
        🔥 PRAGMATIC TRIO #2: каждая попытка проходит через глобальный семафор.
        """
        max_retries = 3 if method == 'GET' else 1
        last_error: Optional[Exception] = None

        # Разные таймауты: ордера 30с, справки 10с
        request_timeout = 30 if critical else 10

        for attempt in range(max_retries):
            try:
                await self._ensure_session(critical=critical)

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

                session = self._critical_session if critical else self._session
                if session is None:
                    raise RuntimeError("Session not initialized")

                timeout = aiohttp.ClientTimeout(total=request_timeout)

                # 🔥 PRAGMATIC TRIO #2: не более 3 параллельных запросов
                t0 = time.time()
                async with self._request_semaphore:
                    async with session.request(method, url, headers=headers, timeout=timeout) as resp:
                        data = await resp.json()
                        # 🔥 ФАЗА 4: каждый REST-запрос виден: кто, куда, сколько мс
                        self.logger.info(
                            f"📡 [REST] {method} {path} | caller={REST_CALLER.get()} | "
                            f"{int((time.time() - t0) * 1000)}ms"
                        )
                        if isinstance(data, dict) and 'code' in data:
                            error_msg = f"Binance API error: {data.get('msg', 'Unknown error')} (code: {data.get('code')})"
                            if data.get('code') == -1003:
                                self._register_ban(error_msg)
                            self.logger.error(error_msg)
                            raise Exception(error_msg)
                        return data

            except asyncio.TimeoutError as e:
                last_error = e
                if attempt < max_retries - 1:
                    self.logger.warning(f"⏳ [REST] Timeout on {method} {path}, retry {attempt + 1}/{max_retries}...")
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

            except aiohttp.ClientError as e:
                last_error = e
                if attempt < max_retries - 1:
                    self.logger.warning(f"⏳ [REST] Network error on {method} {path}: {e!r}, retry {attempt + 1}/{max_retries}...")
                    await asyncio.sleep(2 ** attempt)
                    continue
                break

        raise last_error if last_error else RuntimeError(f"REST request failed: {method} {path}")

    # ──────────────────────────────────────────────────────────────
    # Сессии
    # ──────────────────────────────────────────────────────────────

    async def _ensure_session(self, critical: bool = False):
        """Создать или пересоздать aiohttp сессию."""
        if critical:
            if self._critical_session is None or self._critical_session.closed:
                connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
                self._critical_session = aiohttp.ClientSession(connector=connector)
        else:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
                self._session = aiohttp.ClientSession(connector=connector)

    # ──────────────────────────────────────────────────────────────
    # Публичные методы
    # ──────────────────────────────────────────────────────────────

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

    def _sign(self, params: Dict[str, Any]) -> str:
        """Создаёт подпись для запроса."""
        query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def get_position_sync(self, symbol: str) -> Optional[Dict]:
        """Синхронная версия для вызова из синхронных handler'ов."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Мы в async контексте — используем run_until_complete
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.get_position(symbol))
                    return future.result(timeout=5)
            else:
                return asyncio.run(self.get_position(symbol))
        except Exception as e:
            self.logger.warning(f"⚠️ get_position_sync failed: {e}")
            return None

    def get_open_orders_sync(self, symbol: str) -> Optional[List[Dict]]:
        """Синхронная версия для вызова из синхронных handler'ов."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.get_open_orders_strict(symbol))
                    return future.result(timeout=5)
            else:
                return asyncio.run(self.get_open_orders_strict(symbol))
        except Exception as e:
            self.logger.warning(f"⚠️ get_open_orders_sync failed: {e}")
            return None

    def cancel_order_sync(self, symbol: str, order_id: str) -> Dict:
        """Синхронная версия для вызова из синхронных handler'ов."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self.cancel_order(symbol, order_id))
                    return future.result(timeout=5)
            else:
                return asyncio.run(self.cancel_order(symbol, order_id))
        except Exception as e:
            self.logger.warning(f"⚠️ cancel_order_sync failed: {e}")
            return {"success": False, "error": str(e)}

    async def get_position(self, symbol: str):
        """Получить позицию по символу."""
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_position({symbol}) пропущен — активен бан")
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
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_open_orders({symbol}) пропущен — активен бан")
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
        """Получить стакан с retry-логикой для транзиентных ошибок."""
        if self._ban_active():
            return {}
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = await self._request('GET', '/fapi/v1/depth', {'symbol': symbol, 'limit': limit})
                return result
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    self.logger.warning(f"⏳ [REST] get_orderbook timeout, retry {attempt+1}/{max_retries}...")
                    await asyncio.sleep(2 ** attempt)
                    continue
                self.logger.error(f"❌ [REST] get_orderbook timeout after {max_retries} retries")
                return {}
            except Exception as e:
                error_text = str(e)
                if "-1003" in error_text:
                    self._register_ban(error_text)
                self.logger.warning(f"⚠️ [REST] get_orderbook failed: {error_text}")
                return {}
        
        return {}

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
            result = await self._request('POST', '/fapi/v1/order', params, signed=True, critical=True)
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
            result = await self._request('POST', '/fapi/v1/order', params, signed=True, critical=True)
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

        if self._critical_session is None:
            await self._ensure_session(critical=True)
        session = self._critical_session
        if session is None:
            raise RuntimeError("Session not initialized")

        timeout = aiohttp.ClientTimeout(total=30)
        async with self._request_semaphore:
            async with session.post(url, headers=headers, timeout=timeout) as resp:
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

        if self._critical_session is None:
            await self._ensure_session(critical=True)
        session = self._critical_session
        if session is None:
            raise RuntimeError("Session not initialized")

        timeout = aiohttp.ClientTimeout(total=30)
        async with self._request_semaphore:
            async with session.post(url, headers=headers, timeout=timeout) as resp:
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
        if self._ban_active():
            self.logger.debug(f"⏸️ [REST] get_user_trades({symbol}) пропущен — активен бан")
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
            result = await self._request('DELETE', '/fapi/v1/order', params, signed=True, critical=True)
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
        
        if self._critical_session is not None:
            try:
                await self._critical_session.close()
            except Exception:
                pass
            self._critical_session = None

    async def get_open_orders_strict(self, symbol: str) -> Optional[List[Dict]]:
        """
        🔥 RECONCILER: как get_open_orders, но возвращает None при ЛЮБОЙ ошибке/бане,
        чтобы вызывающий отличал «на бирже пусто» от «биржа не ответила».
        Решения о отменах принимаются только при реальном ответе биржи.
        """
        if self._ban_active():
            return None
        try:
            result = await self._request('GET', '/fapi/v1/openOrders', {'symbol': symbol}, signed=True)
            return result if isinstance(result, list) else None
        except Exception as e:
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            self.logger.warning(f"⚠️ [REST] get_open_orders_strict failed: {error_text}")
            return None


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

    async def get_exchange_info(self, symbol: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Получить информацию о торговых правилах биржи.
        Кэширование на 1 час + fallback на хардкод.
        """
        if self._ban_active():
            if symbol and symbol in self._exchange_info_cache:
                return {"symbols": [self._exchange_info_cache[symbol]]}
            elif symbol and symbol in self._fallback_exchange_info:
                self.logger.warning(f"⚠️ [REST] Using fallback exchangeInfo for {symbol}")
                return {"symbols": [self._fallback_exchange_info[symbol]]}
            return {}
        
        current_time = time.time()
        cache_age = current_time - self._exchange_info_last_update
        
        if not force_refresh and cache_age < self._exchange_info_cache_ttl:
            if symbol:
                if symbol in self._exchange_info_cache:
                    return {"symbols": [self._exchange_info_cache[symbol]]}
            elif self._exchange_info_cache:
                return {"symbols": list(self._exchange_info_cache.values())}
        
        params = {}
        if symbol:
            params['symbol'] = symbol.upper()
            
        try:
            result = await self._request('GET', '/fapi/v1/exchangeInfo', params, signed=False)
            
            if 'symbols' in result and isinstance(result['symbols'], list):
                for sym_info in result['symbols']:
                    sym_name = sym_info.get('symbol')
                    if sym_name:
                        self._exchange_info_cache[sym_name] = sym_info
                
                self._exchange_info_last_update = current_time
                self.logger.info(f"✅ [REST] exchangeInfo cache updated ({len(self._exchange_info_cache)} symbols)")
            
            return result
            
        except Exception as e:
            error_text = str(e)
            if "-1003" in error_text:
                self._register_ban(error_text)
            
            if symbol:
                if symbol in self._exchange_info_cache:
                    self.logger.warning(f"⚠️ [REST] Failed to get exchange info, using cached data for {symbol}")
                    return {"symbols": [self._exchange_info_cache[symbol]]}
                elif symbol in self._fallback_exchange_info:
                    self.logger.warning(f"⚠️ [REST] Failed to get exchange info, using fallback for {symbol}")
                    return {"symbols": [self._fallback_exchange_info[symbol]]}
            
            self.logger.error(f"❌ [REST] Failed to get exchange info: {error_text}")
            return {}

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> list:
        """Получение исторических свечей с Binance Spot REST API."""
        import logging
        logger = logging.getLogger(__name__)

        if self._ban_active():
            logger.debug(f"⏸️ [REST] get_klines({symbol}) пропущен — активен бан")
            return []

        await self._ensure_session()
        session = self._session
        if session is None:
            return []

        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self._request_semaphore:
                async with session.get(url, params=params, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.json()
                    error_text = await response.text()
                    logger.error(f"Binance REST Klines error {response.status}: {error_text}")
                    return []
        except Exception as e:
            logger.error(f"Exception while fetching klines for {symbol}: {e}")
            return []

    async def close(self):
        """Закрыть сессии."""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._critical_session and not self._critical_session.closed:
            await self._critical_session.close()