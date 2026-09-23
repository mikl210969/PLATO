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
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA cache_size=10000")
            logger.info(f"✅ SQLite подключен в режиме WAL: {self.db_path}")
        return self._conn

    def _init_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        # 1. Таблица версионирования схемы
        cursor.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")

        # 2. Таблица горячих метрик
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol_metric_time ON market_metrics(symbol, metric_type, timestamp DESC)")

        # 3. Таблица HVN уровней
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
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hvn_symbol_time ON hvn_levels(symbol, lookback_minutes, updated_at DESC)")

        # 🔥 4. НОВОЕ: Таблица 1-минутных свечей (для расчета baseline и истории)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candles_1m (
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL, -- Начало минуты (unix epoch)
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, timestamp)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_candles_time ON candles_1m(symbol, timestamp DESC)")

        # Миграция версии
        cursor.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1")
        row = cursor.fetchone()
        current_version = row['version'] if row else 0
        if current_version < 4:
            cursor.execute("INSERT INTO schema_version (version) VALUES (4)")
            conn.commit()
            logger.info(f"✅ Схема БД обновлена до v4 (добавлена candles_1m)")

    def execute(self, query: str, params: tuple = ()) -> List[Any]:
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

    # 🔥 НОВОЕ: Метод для сохранения или обновления 1-минутной свечи (UPSERT)
    def upsert_candle_1m(self, symbol: str, timestamp: int, open_p: float, high: float, low: float, close: float, volume: float):
        query = """
            INSERT INTO candles_1m (symbol, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timestamp) DO UPDATE SET
                high = MAX(excluded.high, candles_1m.high),
                low = MIN(excluded.low, candles_1m.low),
                close = excluded.close,
                volume = candles_1m.volume + excluded.volume
        """
        self.execute(query, (symbol, timestamp, open_p, high, low, close, volume))

    # 🔥 НОВОЕ: Получить средние объемы за последние N минут для расчета baseline
    def get_avg_volume_history(self, symbol: str, limit_minutes: int = 1440) -> float:
        query = """
            SELECT AVG(volume) as avg_vol FROM candles_1m 
            WHERE symbol = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """
        result = self.execute(query, (symbol, limit_minutes))
        if result and result[0]['avg_vol'] is not None:
            return float(result[0]['avg_vol'])
        return 0.0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None