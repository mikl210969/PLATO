from extensions.data_layer.db_manager import DatabaseManager
db = DatabaseManager()
rows = db.execute("SELECT COUNT(*) as cnt FROM candles_1m WHERE symbol='SOLUSDT'")
print(f"Свечей SOLUSDT в БД: {rows[0]['cnt']}")
db.close()