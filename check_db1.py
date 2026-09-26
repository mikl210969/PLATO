from extensions.data_layer.db_manager import DatabaseManager
db = DatabaseManager()
sol = db.execute("SELECT COUNT(*) FROM candles_1m WHERE symbol='SOLUSDT'")[0][0]
btc = db.execute("SELECT COUNT(*) FROM candles_1m WHERE symbol='BTCUSDT'")[0][0]
print(f"Свечей в БД: SOL={sol}, BTC={btc}")
db.close()