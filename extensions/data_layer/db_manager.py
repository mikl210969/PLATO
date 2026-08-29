import sqlite3
import logging
from pathlib import Path
from typing import Optional, Any, List

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: str = "extensions/data_layer/plato_metrics.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False безопасен при использовании только одной записывающей операции за раз 
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # КРИТИЧЕСКИ ВАЖНО: Включаем WAL-режим для неблокирующих чтений
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=10000")
            logger.info(f"SQLite подключен в режиме WAL: {self.db_path}")
        return self._conn

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. Таблица версионирования схемы
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. Таблица горячих метрик (schema v1)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                value REAL NOT NULL,
                timestamp INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                metadata TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbol_metric_time
            ON market_metrics(symbol, metric_type, timestamp DESC)
        """)

        # 3. Таблица HVN уровней (🔥 ОБНОВЛЕННАЯ schema v3)
        # ИСПРАВЛЕНО: Колонки теперь точно соответствуют тому, что шлет HVNCalculator
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hvn_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                strength REAL NOT NULL,
                lookback_minutes INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hvn_symbol_time
            ON hvn_levels(symbol, lookback_minutes, updated_at DESC)
        """)

        # Миграция версии (идемпотентно для старых БД)
        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        current_version = row['version'] if row else 0
        if current_version < 3:
            cursor.execute("INSERT INTO schema_version (version) VALUES (3)")
            conn.commit()
            logger.info(f"✅ Схема БД обновлена до v3 (была v{current_version})")

    def execute(self, query: str, params: tuple = ()) -> List[Any]:
        """Универсальный метод для выполнения запросов (INSERT, UPDATE, DELETE, SELECT)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            if query.strip().upper().startswith("SELECT"):
                return cursor.fetchall()
            conn.commit()
            return []
        except Exception as e:
            logger.error(f"❌ Ошибка выполнения SQL: {e}\nQuery: {query}\nParams: {params}")
            raise

    def execute_query(self, query: str, params: tuple = ()) -> list:
        """Обратная совместимость со старым кодом, который использует это имя метода."""
        return self.execute(query, params)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None