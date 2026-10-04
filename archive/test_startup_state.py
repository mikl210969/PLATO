"""
Тест состояния платформы при старте с существующей позицией.
Цель: Увидеть СЫРОЙ ответ от REST и что присылает WS.
"""
import asyncio
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from adapters.binance_rest import BinanceRestClient
from adapters.binance_ws import BinanceWsAdapter

async def main():
    print("="*70)
    print(" НАЧАЛО ТЕСТА: ПРОВЕРКА СОСТОЯНИЯ ПРИ СТАРТЕ")
    print("="*70)
    
    symbol = "SOLUSDT"
    
    # Загрузка ключей
    secrets_path = os.path.join(os.path.dirname(__file__), 'secrets.json')
    try:
        with open(secrets_path, 'r', encoding='utf-8') as f:
            secrets = json.load(f)
        api_key = secrets.get('api_key', '')
        api_secret = secrets.get('api_secret', '')
    except Exception as e:
        print(f"❌ Не удалось загрузить secrets.json: {e}")
        return

    rest = BinanceRestClient(
        api_key=api_key, 
        api_secret=api_secret, 
        base_url="https://testnet.binancefuture.com", 
        timeout=30
    )
    ws = BinanceWsAdapter(base_url="wss://stream.binancefuture.com/ws")

    print("\n🔹 ШАГ 1: ПРЯМОЙ REST-ЗАПРОС (с выводом СЫРОГО ОТВЕТА)")
    print("-" * 70)
    try:
        # ВЫЗЫВАЕМ _request напрямую, чтобы увидеть сырой ответ
        raw_result = await rest._request('GET', '/fapi/v2/positionRisk', {'symbol': symbol}, signed=True)
        
        print("✅ СЫРОЙ ОТВЕТ ОТ BINANCE:")
        print(json.dumps(raw_result, indent=2, ensure_ascii=False))
        print("\n📊 АНАЛИЗ СТРУКТУРЫ:")
        print(f"   - Тип данных: {type(raw_result)}")
        if isinstance(raw_result, list):
            print(f"   - Длина списка: {len(raw_result)}")
            if len(raw_result) > 0:
                print(f"   - Первый элемент — тип: {type(raw_result[0])}")
                if isinstance(raw_result[0], dict):
                    print(f"   - positionAmt: {raw_result[0].get('positionAmt', 'NOT_FOUND')}")
                    print(f"   - entryPrice: {raw_result[0].get('entryPrice', 'NOT_FOUND')}")
                    print(f"   - symbol: {raw_result[0].get('symbol', 'NOT_FOUND')}")
        elif isinstance(raw_result, dict):
            print(f"   - Это словарь (не список!)")
            print(f"   - Ключи: {list(raw_result.keys())}")
        
        # Теперь вызываем парсер get_position и смотрим результат
        print("\n\n✅ РЕЗУЛЬТАТ ПОСЛЕ ПАРСИНГА (get_position):")
        position_data = await rest.get_position(symbol)
        print(json.dumps(position_data, indent=2, ensure_ascii=False))
        
        if position_data:
            size = position_data.get('size', 0)
            print(f"\n ВЫВОД: REST вернул размер = {size}")
            if size == 0.0:
                print("   ⚠️ ВНИМАНИЕ: Позиция на бирже есть (скриншот), но REST вернул 0.0!")
                print("   Это баг в методе get_position() — неправильный парсинг ответа.")
        else:
            print("\n👉 ВЫВОД: REST вернул None (ошибка/бан)")
            
    except Exception as e:
        print(f"❌ ОШИБКА REST: {e}")
        import traceback
        traceback.print_exc()

    print("\n\n🔹 ШАГ 2: WEBSOCKET (User Data Stream)")
    print("-" * 70)
    print("⏳ Подключаемся и ждем 10 секунд...")
    
    received_messages = []
    
    async def capture_ws_data(data):
        received_messages.append(data)
        print(f"📥 [WS] {data.get('e', 'UNKNOWN')}")

    ws_task = None
    try:
        await ws.connect()
        listen_key = await rest.get_listen_key()
        await ws.subscribe_user_data(listen_key)
        ws.on("ACCOUNT_UPDATE", capture_ws_data)
        ws.on("ORDER_TRADE_UPDATE", capture_ws_data)
        
        ws_task = asyncio.create_task(ws.run())
        await asyncio.sleep(10)
        
        print(f"\n👉 WS событий: {len(received_messages)}")
        if len(received_messages) == 0:
            print("   ⚠️ WS НЕ прислал данных (нормально для стабильной позиции)")
            
    except Exception as e:
        print(f"❌ ОШИБКА WS: {e}")
    finally:
        if ws_task:
            ws_task.cancel()
        if hasattr(rest, '_session') and rest._session:
            await rest._session.close()
            
        print("\n" + "="*70)
        print("✅ ТЕСТ ЗАВЕРШЕН. Отправь мне вывод ШАГА 1 (сырой ответ).")
        print("="*70)

if __name__ == "__main__":
    asyncio.run(main())