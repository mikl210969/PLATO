import asyncio, json, time, hashlib, hmac
from urllib.parse import urlencode
import aiohttp

async def main():
    # Загрузка ключей
    cfg = json.load(open('config/exchange.json', encoding='utf-8'))
    try:
        sec = json.load(open('config/secrets.json', encoding='utf-8'))
    except Exception:
        sec = {}
    key = sec.get('api_key') or cfg.get('api_key')
    secret = sec.get('api_secret') or cfg.get('api_secret')
    base = cfg['rest_base_url']

    async def signed_request(method, path, params):
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 5000
        qs = urlencode(sorted(params.items()))
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{qs}&signature={sig}"
        headers = {'X-MBX-APIKEY': key}
        async with aiohttp.ClientSession() as s:
            async with s.request(method, url, headers=headers) as r:
                return r.status, await r.text()

    print("=" * 60)
    print("🧪 ТЕСТ: принимает ли Testnet настоящий STOP_MARKET?")
    print("=" * 60)
    print()
    print("Параметры тестового ордера:")
    print("  • symbol       = SOLUSDT")
    print("  • side         = BUY (для защиты SHORT)")
    print("  • positionSide = SHORT (Hedge Mode)")
    print("  • type         = STOP_MARKET  ← то, что проверяем")
    print("  • stopPrice    = 99.0 (далеко выше рынка ~75.7, не исполнится)")
    print("  • quantity     = 0.1 (минимальный объём)")
    print("  • reduceOnly   = true")
    print("  • workingType  = CONTRACT_PRICE")
    print()

    # Шаг 1: Отправка STOP_MARKET
    print("📤 Отправляем POST /fapi/v1/order ...")
    status, text = await signed_request('POST', '/fapi/v1/order', {
        'symbol': 'SOLUSDT',
        'side': 'BUY',
        'positionSide': 'SHORT',
        'type': 'STOP_MARKET',
        'stopPrice': '99.0',
        'quantity': '0.1',
        'reduceOnly': 'true',
        'closePosition': 'false',
        'workingType': 'CONTRACT_PRICE',
        'newClientOrderId': 'TEST_STOP_MARKET_PROBE'
    })

    print(f"\n📥 HTTP {status}")
    print(f"📥 Ответ: {text}")
    print()

    try:
        resp = json.loads(text)
    except Exception as e:
        print(f"❌ Не удалось распарсить ответ: {e}")
        return

    # Шаг 2: Интерпретация результата
    if 'orderId' in resp:
        print("=" * 60)
        print("✅ УСПЕХ! Testnet ПРИНИМАЕТ настоящие STOP_MARKET!")
        print("=" * 60)
        print(f"   orderId     : {resp['orderId']}")
        print(f"   status      : {resp.get('status')}")
        print(f"   type        : {resp.get('type')}")
        print(f"   reduceOnly  : {resp.get('reduceOnly')}")
        print()

        # Шаг 3: Отмена тестового ордера
        print("🗑️  Отменяем тестовый ордер (чтобы не болтался на бирже)...")
        del_status, del_text = await signed_request('DELETE', '/fapi/v1/order', {
            'symbol': 'SOLUSDT',
            'orderId': resp['orderId']
        })
        print(f"   Отмена: HTTP {del_status}")
        try:
            del_resp = json.loads(del_text)
            print(f"   Статус после отмены: {del_resp.get('status')}")
        except Exception:
            print(f"   Сырой ответ: {del_text[:200]}")
        print()
        print("🎯 ВЫВОД: можно смело переводить SL в RiskManager на STOP_MARKET.")

    else:
        print("=" * 60)
        print("❌ ПРОВАЛ! Testnet ОТКЛОНЯЕТ STOP_MARKET")
        print("=" * 60)
        print(f"   code: {resp.get('code')}")
        print(f"   msg : {resp.get('msg')}")
        print()
        print("🎯 ВЫВОД: нужно использовать STOP_LIMIT, либо оставить LIMIT с reduce_only=True,")
        print("         либо проверить, что причина отказа (возможно, проблема с positionSide).")
        print()

        # Дополнительная попытка: STOP_MARKET без positionSide (One-Way mode)
        print("🔄 Дополнительная попытка: STOP_MARKET БЕЗ positionSide (One-Way mode)...")
        status2, text2 = await signed_request('POST', '/fapi/v1/order', {
            'symbol': 'SOLUSDT',
            'side': 'BUY',
            'type': 'STOP_MARKET',
            'stopPrice': '99.0',
            'quantity': '0.1',
            'reduceOnly': 'true',
            'closePosition': 'false',
            'workingType': 'CONTRACT_PRICE',
            'newClientOrderId': 'TEST_STOP_MARKET_PROBE_2'
        })
        print(f"   HTTP {status2}")
        print(f"   Ответ: {text2}")

if __name__ == "__main__":
    asyncio.run(main())