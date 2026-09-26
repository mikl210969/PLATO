from extensions.data_layer.db_manager import DatabaseManager

try:
    db = DatabaseManager()
    sol = db.execute("SELECT COUNT(*) FROM candles_1m WHERE symbol='SOLUSDT'")[0][0]
    btc = db.execute("SELECT COUNT(*) FROM candles_1m WHERE symbol='BTCUSDT'")[0][0]
    print("Свечей в БД: SOL=" + str(sol) + ", BTC=" + str(btc))
    
    last_sol = db.execute("SELECT timestamp FROM candles_1m WHERE symbol='SOLUSDT' ORDER BY timestamp DESC LIMIT 1")[0][0]
    print("Последняя свеча SOL записана в: " + str(last_sol))
    db.close()
except Exception as e:
    print("Ошибка: " + str(e))