import asyncio
import hashlib
import hmac
import time
import aiohttp
from core.config_loader import ConfigLoader

async def test_position():
    secrets = ConfigLoader().load_secrets()
    api_key = secrets.get('api_key', '')
    api_secret = secrets.get('api_secret', '')

    base_url = "https://testnet.binancefuture.com"
    path = "/fapi/v2/positionRisk"
    params = {
        'symbol': 'SOLUSDT',
        'timestamp': int(time.time() * 1000),
        'recvWindow': 60000
    }

    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = hmac.new(api_secret.encode(), query_string.encode(), hashlib.sha256).hexdigest()
    query_string += f"&signature={signature}"

    url = f"{base_url}{path}?{query_string}"
    headers = {"X-MBX-APIKEY": api_key}

    print(f"🔍 URL: {url[:100]}...")
    print(f"🔍 Headers: {headers}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            print(f"📩 Response: {data}")

asyncio.run(test_position())