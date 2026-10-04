import asyncio, json, time, hashlib, hmac
from urllib.parse import urlencode
import aiohttp

async def main():
    cfg = json.load(open('config/exchange.json', encoding='utf-8'))
    try: sec = json.load(open('config/secrets.json', encoding='utf-8'))
    except Exception: sec = {}
    key = sec.get('api_key') or cfg.get('api_key')
    secret = sec.get('api_secret') or cfg.get('api_secret')
    base = cfg['rest_base_url']

    async def signed_post(params):
        params = dict(params)
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 5000
        qs = urlencode(sorted(params.items()))
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        headers = {'X-MBX-APIKEY': key}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{base}/fapi/v1/order?{qs}&signature={sig}", headers=headers) as r:
                return r.status, await r.text()

    async def cancel(order_id):
        params = {'symbol': 'SOLUSDT', 'orderId': order_id,
                  'timestamp': int(time.time() * 1000), 'recvWindow': 5000}
        qs = urlencode(sorted(params.items()))
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        headers = {'X-MBX-APIKEY': key}
        async with aiohttp.ClientSession() as s:
            async with s.delete(f"{base}/fapi/v1/order?{qs}&signature={sig}", headers=headers) as r:
                return await r.text()

    common = {'symbol': 'SOLUSDT', 'side': 'BUY', 'positionSide': 'SHORT',
              'quantity': '0.1', 'workingType': 'CONTRACT_PRICE'}

    print("=== ЗОНД A: STOP_MARKET без reduceOnly (stopPrice=99) ===")
    st, txt = await signed_post({**common, 'type': 'STOP_MARKET', 'stopPrice': '99.0',
                                 'newClientOrderId': 'PROBE_V2_A'})
    print(f"HTTP {st} | {txt}")
    r = json.loads(txt)
    if 'orderId' in r:
        print(f"✅ STOP_MARKET ПРИНЯТ (status={r.get('status')}). Отменяем...")
        print("   Отмена:", await cancel(r['orderId']))

    print("\n=== ЗОНД B: LIMIT ниже рынка без reduceOnly (price=70) ===")
    st, txt = await signed_post({**common, 'type': 'LIMIT', 'price': '70.0',
                                 'timeInForce': 'GTC', 'newClientOrderId': 'PROBE_V2_B'})
    print(f"HTTP {st} | {txt}")
    r = json.loads(txt)
    if 'orderId' in r:
        print(f"✅ LIMIT ПРИНЯТ (status={r.get('status')}). Отменяем...")
        print("   Отмена:", await cancel(r['orderId']))

    print("\n=== ЗОНД C: LIMIT с reduceOnly=true (ожидаем -1106) ===")
    st, txt = await signed_post({**common, 'type': 'LIMIT', 'price': '60.0',
                                 'timeInForce': 'GTC', 'reduceOnly': 'true',
                                 'newClientOrderId': 'PROBE_V2_C'})
    print(f"HTTP {st} | {txt}")

asyncio.run(main())