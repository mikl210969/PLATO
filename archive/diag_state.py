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

    async def signed_get(path, extra=None):
        params = {'timestamp': int(time.time()*1000), 'recvWindow': 5000}
        if extra: params.update(extra)
        qs = urlencode(sorted(params.items()))
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{qs}&signature={sig}"
        headers = {'X-MBX-APIKEY': key}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers) as r:
                text = await r.text()
                try:
                    return json.loads(text)
                except Exception:
                    return {'_raw': text, '_status': r.status}

    def is_error(obj):
        return isinstance(obj, dict) and ('code' in obj or '_raw' in obj)

    print("=== POSITIONS (fapi/v2/positionRisk) ===")
    data = await signed_get('/fapi/v2/positionRisk', {'symbol': 'SOLUSDT'})
    if is_error(data):
        print(f"  ❌ ERROR: {data}")
    else:
        for p in data:
            amt = float(p.get('positionAmt', 0))
            if amt != 0:
                print(f"  {p.get('positionSide')} | size={amt} | entry={p.get('entryPrice')} | unPnl={p.get('unRealizedProfit')} | lev={p.get('leverage')}")
        if not any(float(p.get('positionAmt', 0)) != 0 for p in data):
            print("  (нет открытых позиций)")

    print("\n=== OPEN ORDERS (fapi/v1/openOrders) ===")
    data = await signed_get('/fapi/v1/openOrders', {'symbol': 'SOLUSDT'})
    if is_error(data):
        print(f"  ❌ ERROR: {data}")
    elif not data:
        print("  (нет открытых ордеров)")
    else:
        for o in data:
            print(f"  id={o.get('orderId')} | client={o.get('clientOrderId')} | {o.get('side')} {o.get('positionSide')} | price={o.get('price')} qty={o.get('origQty')} | {o.get('status')} | reduceOnly={o.get('reduceOnly')}")

    print("\n=== LAST 15 ORDERS (allOrders) ===")
    data = await signed_get('/fapi/v1/allOrders', {'symbol': 'SOLUSDT', 'limit': 15})
    if is_error(data):
        print(f"  ❌ ERROR: {data}")
    else:
        for o in data:
            print(f"  id={o.get('orderId')} | client={o.get('clientOrderId')} | {o.get('side')} {o.get('positionSide')} | price={o.get('price')} qty={o.get('origQty')} filled={o.get('executedQty')} | {o.get('status')} | type={o.get('type')}")

    print("\n=== LAST 15 TRADES (userTrades) ===")
    data = await signed_get('/fapi/v1/userTrades', {'symbol': 'SOLUSDT', 'limit': 15})
    if is_error(data):
        print(f"  ❌ ERROR: {data}")
    else:
        for t in data:
            print(f"  orderId={t.get('orderId')} | {t.get('side')} {t.get('positionSide')} | price={t.get('price')} qty={t.get('qty')} | pnl={t.get('realizedPnl')} | commission={t.get('commission')} | maker={t.get('maker')}")

    print("\n=== ACCOUNT BALANCE ===")
    data = await signed_get('/fapi/v2/balance')
    if is_error(data):
        print(f"  ❌ ERROR: {data}")
    else:
        for a in data:
            if a.get('asset') in ('USDT', 'BUSD'):
                print(f"  {a.get('asset')}: balance={a.get('balance')} | avail={a.get('availableBalance')} | crossUnPnl={a.get('crossUnPnl')}")

asyncio.run(main())