"""HVN Calculator — расчет уровней высокого объема (Micro и Macro) без scipy."""
import logging
import time
import json
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class HVNCalculator:
    def __init__(
        self,
        db_manager: Any,
        cold_storage_path: str = "data/cold_storage",
        price_step_pct: float = 0.001,
        min_prominence_pct: float = 10.0,
        top_n: int = 5
    ):
        self.db_manager = db_manager
        self.cold_storage_path = Path(cold_storage_path)
        self.price_step_pct = price_step_pct
        self.min_prominence_pct = min_prominence_pct
        self.top_n = top_n

    def _load_trades(self, symbol: str, lookback_minutes: int = 60) -> pd.DataFrame:
        """Загружает тиковые данные из Cold Storage за указанные минуты."""
        tick_file = self.cold_storage_path / f"{symbol}_trades.jsonl"
        
        if not tick_file.exists():
            return pd.DataFrame()

        try:
            cutoff_time = time.time() - (lookback_minutes * 60)
            records = []
            
            with open(tick_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # 🔥 ИСПРАВЛЕНИЕ: Пропускаем пустые строки и невалидный JSON
                    if not line:
                        continue
                    
                    try:
                        record = json.loads(line)
                        if record.get("timestamp", 0) >= cutoff_time:
                            records.append(record)
                    except json.JSONDecodeError:
                        # Игнорируем битые строки в JSONL (например, BOM или обрывы)
                        continue
            
            if not records:
                return pd.DataFrame()
                
            return pd.DataFrame(records)
            
        except Exception as e:
            logger.error(f"Ошибка при загрузке тиков для {symbol}: {e}")
            return pd.DataFrame()

    def calculate_hvn(self, symbol: str, lookback_minutes: int = 60) -> List[Dict[str, Any]]:
        """Рассчитывает HVN уровни за заданный период в минутах."""
        df = self._load_trades(symbol, lookback_minutes=lookback_minutes)
        
        if df.empty:
            return []

        min_price = float(df['price'].min())
        max_price = float(df['price'].max())
        bin_size = max(min_price * self.price_step_pct, 0.01)
        
        num_bins = max(int((max_price - min_price) / bin_size) + 1, 3)

        # Создаем корзины цен
        df['price_bin'] = pd.cut(df['price'], bins=num_bins, labels=False)
        
        # Агрегируем объем по корзинам
        volume_profile = df.groupby('price_bin').agg(
            volume=('value_usdt', 'sum'),
            price=('price', 'mean')
        ).reset_index()

        if len(volume_profile) < 3:
            return []

        # 🔥 ПОИСК ЛОКАЛЬНЫХ МАКСИМУМОВ БЕЗ SCIPY (через Pandas shift)
        volume_profile['vol_left'] = volume_profile['volume'].shift(1).fillna(0.0)
        volume_profile['vol_right'] = volume_profile['volume'].shift(-1).fillna(0.0)
        
        # Локальный максимум: текущий объем строго больше левого и правого соседа
        is_local_max = (volume_profile['volume'] > volume_profile['vol_left']) & \
                       (volume_profile['volume'] > volume_profile['vol_right'])
                       
        local_maxima = volume_profile[is_local_max]
        
        hvn_levels = []
        for _, row in local_maxima.iterrows():
            # 🔥 ЯВНОЕ ПРИВЕДЕНИЕ ТИПОВ К FLOAT для удовлетворения Pylance
            current_vol = float(row['volume'])
            left_vol = float(row['vol_left'])
            right_vol = float(row['vol_right'])
            price = float(row['price'])
            
            min_neighbor_vol = min(left_vol, right_vol)
            
            if min_neighbor_vol > 0:
                prominence = ((current_vol - min_neighbor_vol) / min_neighbor_vol) * 100.0
            else:
                prominence = 100.0
                
            if prominence >= self.min_prominence_pct:
                hvn_levels.append({
                    "price": price,
                    "volume": current_vol,
                    "strength": float(prominence),
                    "lookback_minutes": lookback_minutes
                })

        # Сортируем по силе (strength) и берем топ-N
        hvn_levels.sort(key=lambda x: x['strength'], reverse=True)
        return hvn_levels[:self.top_n]

    def save_hvn_to_db(self, symbol: str, hvn_levels: List[Dict[str, Any]], lookback_minutes: int = 60):
        """Сохраняет рассчитанные уровни в Hot Storage (SQLite) с защитой от разных API."""
        if not hvn_levels:
            return
            
        try:
            # Пытаемся найти правильный объект для выполнения SQL (адаптация под разные реализации DB Manager)
            db_obj = getattr(self.db_manager, 'conn', None) or \
                     getattr(self.db_manager, '_conn', None) or \
                     getattr(self.db_manager, 'connection', None) or \
                     self.db_manager

            if hasattr(db_obj, 'execute'):
                db_obj.execute(
                    "DELETE FROM hvn_levels WHERE symbol = ? AND lookback_minutes = ?",
                    (symbol, lookback_minutes)
                )
                
                current_ts = time.time()
                for level in hvn_levels:
                    db_obj.execute(
                        """INSERT INTO hvn_levels 
                           (symbol, price, volume, strength, lookback_minutes, updated_at) 
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (symbol, level['price'], level['volume'], level['strength'], lookback_minutes, current_ts)
                    )
                
                if hasattr(db_obj, 'commit'):
                    db_obj.commit()
                    
                logger.info(f"✅ [{symbol}] Сохранено {len(hvn_levels)} HVN уровней ({lookback_minutes} мин) в БД")
            else:
                # Если метода execute нет вообще, просто логируем уровни, чтобы мы их видели!
                logger.info(f"⚠️ [{symbol}] DB Manager не поддерживает execute. HVN уровни: {hvn_levels}")
                
        except Exception as e:
            logger.error(f"Ошибка сохранения HVN в БД: {e}")