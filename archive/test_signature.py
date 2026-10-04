#!/usr/bin/env python3
"""
Тест подписи Binance.
"""

import hashlib
import hmac
import time
import json
from core.config_loader import ConfigLoader


def test_signature():
    """Проверяет подпись запроса."""
    
    # Загружаем секреты
    secrets = ConfigLoader().load_secrets()
    api_key = secrets.get('api_key', '')
    api_secret = secrets.get('api_secret', '')
    
    print(f"🔑 API Key: {api_key[:10]}..." if api_key else "❌ API Key is empty!")
    print(f"🔑 API Secret: {api_secret[:10]}..." if api_secret else "❌ API Secret is empty!")
    
    if not api_key or not api_secret:
        print("❌ Keys are empty! Check config/secrets.json")
        return
    
    # Параметры запроса
    params = {
        'symbol': 'SOLUSDT',
        'timestamp': int(time.time() * 1000),
        'recvWindow': 60000
    }
    
    # Сортируем и формируем строку
    query_string = '&'.join([f"{k}={v}" for k, v in sorted(params.items())])
    print(f"📝 Query string: {query_string}")
    
    # Подпись
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    print(f"✅ Signature: {signature}")
    
    # Проверяем длину
    print(f"📏 Signature length: {len(signature)} (should be 64)")
    
    # Проверяем, что в ключах нет пробелов
    if ' ' in api_key or ' ' in api_secret:
        print("❌ Keys contain spaces! Remove them.")
    else:
        print("✅ No spaces in keys")


if __name__ == "__main__":
    test_signature()