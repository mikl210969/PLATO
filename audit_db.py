import sqlite3
import os

# Найди все sqlite файлы в папке
db_files = [f for f in os.listdir('.') if f.endswith('.sqlite') or f.endswith('.db')]
print(f"Найдено баз: {db_files}\n")

for db_file in db_files:
    print(f"--- Анализ {db_file} ---")
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        
        # 1. Смотрим таблицы
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"Таблицы: {[t[0] for t in tables]}")
        
        # 2. Смотрим количество строк в каждой таблице
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            print(f"  -> {table_name}: {count} строк")
            
            # 3. Смотрим колонки первой таблицы (чтобы понять структуру)
            if table_name == tables[0][0]:
                cursor.execute(f"PRAGMA table_info({table_name})")
                cols = [col[1] for col in cursor.fetchall()]
                print(f"     Колонки: {cols}")
                
        conn.close()
    except Exception as e:
        print(f"Ошибка чтения: {e}")
    print("\n")