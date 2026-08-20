#!/usr/bin/env python3
"""
Тестовый скрипт для проверки синтаксиса лимитного ордера на Binance Future Testnet.
"""

import asyncio
import sys
import time
import hashlib
import hmac
import aiohttp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.config_loader import ConfigLoader


async def test_limit_order_direct():
    """Тест: прямой запрос на создание лимитного ордера."""
    
    print("=" * 60)
    print("🧪 ТЕСТ: Прямой запрос на создание лимитного ордера")
    print("=" * 60)
    
    config = ConfigLoader().load_all()
    secrets = ConfigLoader().load_secrets()
    
    exchange_config = config.get('exchange', {})
    api_key = secrets.get('api_key', '') or exchange_config.get('api_key', '')
    api_secret = secrets.get('api_secret', '') or exchange_config.get('api_secret', '')
    
    symbol = exchange_config.get('symbol', 'SOLUSDT')
    base_url = exchange_config.get('rest_base_url', 'https://testnet.binancefuture.com')
    
    print(f"📋 Символ: {symbol}")
    print(f"📋 Base URL: {base_url}")
    
    # 2. Параметры ордера (БЕЗ reduceOnly)
    test_price = 76.50
    test_quantity = 7.0
    
    params = {
        'symbol': symbol,
        'side': 'BUY',
        'type': 'LIMIT',
        'timeInForce': 'GTC',
        'price': str(test_price),
        'quantity': str(test_quantity),
        'positionSide': 'LONG',
        'timestamp': int(time.time() * 1000),
        'recvWindow': 60000
    }
    
    print(f"\n📊 Параметры:")
    for k, v in params.items():
        print(f"   {k}: {v}")
    
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    query_string += f"&signature={signature}"
    
    print(f"\n🔑 Подпись: {signature[:20]}...")
    
    url = f"{base_url}/fapi/v1/order?{query_string}"
    headers = {"X-MBX-APIKEY": api_key}
    
    print(f"\n🚀 Отправка запроса...")
    
    timeout = aiohttp.ClientTimeout(total=30)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, timeout=timeout) as resp:
                data = await resp.json()
                print(f"\n📥 Ответ:")
                print(f"   Статус: {resp.status}")
                print(f"   Данные: {data}")
                
                if resp.status == 200 and 'orderId' in data:
                    print(f"\n   ✅ УСПЕШНО!")
                    print(f"   Order ID: {data.get('orderId')}")
                    print(f"   Client Order ID: {data.get('clientOrderId')}")
                    print(f"   Status: {data.get('status')}")
                else:
                    print(f"\n   ❌ ОШИБКА!")
                    print(f"   Code: {data.get('code')}")
                    print(f"   Msg: {data.get('msg')}")
                    
    except Exception as e:
        print(f"\n❌ Исключение: {e}")
    
    print("\n" + "=" * 60)
    print("✅ Тест завершен")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_limit_order_direct())