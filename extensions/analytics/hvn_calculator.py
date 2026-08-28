"""HVN Calculator — расчет High Volume Nodes (Модуль 1.4, Стратегии.txt v2.0).

Фоновый джоб:
- Читает тиковые данные из Cold Storage (Parquet) за последние N часов
- Группирует по ценовым уровням (шаг 0.1% от текущей цены)
- Находит локальные максимумы объёма (HVN)
- Записывает топ-10 HVN в SQLite (Hot Storage)
- Публикует METRIC_UPDATED в EventBus

Запускается каждые 10-15 минут через asyncio.create_task (не блокирует основной цикл).
"""
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

import pandas as pd
from extensions.data_layer.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class HVNCalculator:
    def __init__(
        self,
        db_manager: DatabaseManager,
        cold_storage_path: str = "data/cold_storage",
        lookback_hours: int = 24,
        price_step_pct: float = 0.001,  # 0.1% шаг группировки
        min_prominence_pct: float = 10.0,  # Минимальная "выпуклость" HVN
        top_n: int = 10,  # Топ-N HVN для записи в БД
    ):
        self.db = db_manager
        self.cold_storage_path = Path(cold_storage_path)
        self.lookback_hours = lookback_hours
        self.price_step_pct = price_step_pct
        self.min_prominence_pct = min_prominence_pct
        self.top_n = top_n

    def calculate_hvn(
        self,
        symbol: str,
        current_price: Optional[float] = None,
    ) -> List[Dict[str, float]]:
        """
        Рассчитать High Volume Nodes для символа.
        
        Args:
            symbol: Торговая пара (например, "SOLUSDT")
            current_price: Текущая цена (для определения шага группировки).
                          Если None, берется последняя цена из данных.
        
        Returns:
            Список HVN уровней в формате:
            [
                {"price": 140.5, "volume": 125000.0, "strength": 0.85},
                {"price": 138.2, "volume": 98000.0, "strength": 0.62},
                ...
            ]
        """
        # 1. Читаем тиковые данные из Cold Storage
        trades_df = self._load_trades(symbol)
        if trades_df.empty:
            logger.warning(f"[{symbol}] Нет тиковых данных для расчета HVN")
            return []

        # 2. Определяем текущую цену (если не передана)
        if current_price is None:
            current_price = float(trades_df['price'].iloc[-1])
        else:
            current_price = float(current_price)  # 🔥 Явное приведение для Pylance

        # 3. Группируем по ценовым уровням
        volume_profile = self._build_volume_profile(trades_df, current_price)

        # 4. Находим локальные максимумы (HVN)
        hvn_levels = self._find_local_maxima(volume_profile)

        # 5. Берем топ-N по объёму
        hvn_levels.sort(key=lambda x: x['volume'], reverse=True)
        top_hvn = hvn_levels[:self.top_n]

        logger.info(
            f"[{symbol}] Рассчитано {len(top_hvn)} HVN уровней "
            f"(всего найдено {len(hvn_levels)})"
        )
        return top_hvn

    def save_hvn_to_db(self, symbol: str, hvn_levels: List[Dict[str, float]]) -> None:
        """Сохранить HVN уровни в Hot Storage (SQLite)."""
        import time
        import json

        now = int(time.time())
        expires_at = now + 900  # TTL 15 минут (джоб запускается каждые 10-15 мин)

        # Очищаем старые HVN для этого символа
        self.db.execute_query(
            "DELETE FROM hvn_levels WHERE symbol = ?",
            (symbol,)
        )

        # Вставляем новые уровни
        for rank, hvn in enumerate(hvn_levels, start=1):
            metadata = json.dumps({
                "rank": rank,
                "strength": hvn.get("strength", 0.0),
                "volume_usdt": hvn.get("volume", 0.0),
            })
            self.db.execute_query(
                """
                INSERT INTO hvn_levels (symbol, price, volume, rank, timestamp, expires_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, hvn["price"], hvn["volume"], rank, now, expires_at, metadata)
            )

        logger.info(f"[{symbol}] Сохранено {len(hvn_levels)} HVN уровней в БД")

    async def run_background_job(self, symbols: List[str], interval_seconds: int = 600):
        """
        Фоновый джоб: периодически пересчитывает HVN для списка символов.
        
        Args:
            symbols: Список торговых пар ["SOLUSDT", "BTCUSDT", ...]
            interval_seconds: Интервал перезапуска (по умолчанию 600 сек = 10 мин)
        """
        logger.info(f"HVN background job запущен (интервал: {interval_seconds} сек)")
        
        while True:
            for symbol in symbols:
                try:
                    hvn_levels = self.calculate_hvn(symbol)
                    if hvn_levels:
                        self.save_hvn_to_db(symbol, hvn_levels)
                except Exception as e:
                    logger.error(f"[{symbol}] Ошибка при расчете HVN: {e}", exc_info=True)
            
            await asyncio.sleep(interval_seconds)

    # ------------------------------------------------------------ internal
    def _load_trades(self, symbol: str) -> pd.DataFrame:
        """Загрузить тиковые данные из Cold Storage (Parquet или JSONL)."""
        import time
        import json
        import pandas as pd

        now = time.time()
        cutoff = now - (self.lookback_hours * 3600)

        # 1. Сначала ищем Parquet файлы (для больших исторических данных)
        parquet_files = list(self.cold_storage_path.glob(f"{symbol}_trades_*.parquet"))
        if parquet_files:
            trades_list = []
            for file_path in parquet_files:
                try:
                    df = pd.read_parquet(file_path)
                    if 'timestamp' in df.columns:
                        df = df[df['timestamp'] >= cutoff]
                    trades_list.append(df)
                except Exception as e:
                    logger.warning(f"Не удалось прочитать {file_path}: {e}")
            if trades_list:
                return pd.concat(trades_list, ignore_index=True)

        # 2. Если Parquet нет, читаем живой JSONL файл (fallback для текущей сессии)
        jsonl_file = self.cold_storage_path / f"{symbol}_trades.jsonl"
        if jsonl_file.exists():
            trades_list = []
            try:
                with open(jsonl_file, "r", encoding="utf-8") as f:
                    for line in f:
                        record = json.loads(line)
                        if record.get("timestamp", 0) >= cutoff:
                            trades_list.append(record)
                if trades_list:
                    return pd.DataFrame(trades_list)
            except Exception as e:
                logger.warning(f"Ошибка чтения JSONL {jsonl_file}: {e}")

        return pd.DataFrame()

    def _build_volume_profile(
        self,
        trades_df: pd.DataFrame,
        current_price: float,
    ) -> Dict[float, float]:
        price_step = current_price * self.price_step_pct
        raw_profile = defaultdict(float)

        for _, row in trades_df.iterrows():
            price = row['price']
            idx = round(price / price_step)          # целый индекс уровня
            volume_usdt = row.get('value_usdt', row['quantity'] * price)
            raw_profile[idx] += volume_usdt

        # Непрерывная сетка по целым индексам: пустые уровни = 0.
        # Ключи вычисляются ОДНОЙ операцией idx*step — без float-накопления,
        # иначе dict.get() промахивается мимо ключей и профиль обнуляется.
        if not raw_profile:
            return {}
        grid = {}
        for idx in range(min(raw_profile), max(raw_profile) + 1):
            grid[idx * price_step] = raw_profile.get(idx, 0.0)
        return grid

    def _smooth_profile(self, volume_profile: Dict[float, float]) -> Dict[float, float]:
        """Сглаживание: сумма объёма по 3 соседним уровням.
        Превращает плато (кластеры) в холмы — так детектируются реальные HVN."""
        prices = sorted(volume_profile.keys())
        vols = [volume_profile[p] for p in prices]
        smoothed = {}
        for i, p in enumerate(prices):
            window = vols[max(0, i - 1): i + 2]
            smoothed[p] = sum(window)
        return smoothed

    def _find_local_maxima(
        self,
        volume_profile: Dict[float, float],
    ) -> List[Dict[str, float]]:
        if not volume_profile:
            return []

        smoothed = self._smooth_profile(volume_profile)
        prices = sorted(smoothed.keys())

        # Фильтр шума: объём >= 2x медианы ненулевых уровней
        nonzero = sorted(v for v in smoothed.values() if v > 0)
        if not nonzero:
            return []
        median = nonzero[len(nonzero) // 2]
        min_volume = median * 2.0

        hvn_levels = []
        for i, price in enumerate(prices):
            volume = smoothed[price]
            if volume < min_volume:
                continue

            left = smoothed[prices[i - 1]] if i > 0 else 0.0
            right = smoothed[prices[i + 1]] if i < len(prices) - 1 else 0.0

            # Локальный максимум на сглаженном профиле
            if volume > left and volume > right:
                max_neighbor = max(left, right)
                if max_neighbor > 0:
                    prominence_pct = ((volume - max_neighbor) / max_neighbor) * 100
                else:
                    prominence_pct = 100.0

                if prominence_pct >= self.min_prominence_pct:
                    hvn_levels.append({
                        "price": price,
                        "volume": volume,
                        "strength": min(prominence_pct / 100.0, 1.0),
                    })

        return hvn_levels

    def get_cached_hvn(self, symbol: str) -> List[Dict[str, float]]:
        """Получить кэшированные HVN из БД (для чтения стратегиями)."""
        import json

        query = """
            SELECT price, volume, metadata
            FROM hvn_levels
            WHERE symbol = ? AND expires_at > ?
            ORDER BY rank ASC
        """
        import time
        now = int(time.time())
        rows = self.db.execute_query(query, (symbol, now))

        if not rows:
            return []

        hvn_levels = []
        for row in rows:
            metadata = json.loads(row['metadata']) if row['metadata'] else {}
            hvn_levels.append({
                "price": row['price'],
                "volume": row['volume'],
                "strength": metadata.get("strength", 0.0),
            })

        return hvn_levels