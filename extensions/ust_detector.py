"""
UstDetector: Детектор уровней смены тренда для Live-режима.
Находит УСТ, сохраняет в БД и предоставляет метод поиска для стратегий.
"""
import pandas as pd
import numpy as np
import time
import json
from pathlib import Path
from typing import Optional, Dict, List, Any

from extensions.data_layer.db_manager import DatabaseManager
from core.logger import get_logger

logger = get_logger(__name__)

class UstDetector:
    def __init__(self, symbol: str, rest_client: Any, db_manager: DatabaseManager):
        self.symbol = symbol
        self.rest = rest_client
        self.db = db_manager
        self._ensure_table_exists()
        
        # Параметры детектора (как в тесте)
        self.min_distance_pct = 0.005  # 0.5% кластеризация
        self.lookback_candles = 200    # Берем последние ~16 часов 1m свечей

    def _ensure_table_exists(self):
        """Создает таблицу ust_levels, если её нет (или пересоздает при изменении схемы)"""
        # Удаляем старую таблицу с неправильной схемой (где было level_price)
        self.db.execute("DROP TABLE IF EXISTS ust_levels")
        
        query = """
            CREATE TABLE ust_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '5m',
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                coefficient REAL NOT NULL,
                created_at REAL NOT NULL,
                is_active INTEGER DEFAULT 1,
                deactiv_reason TEXT,
                breakout_type TEXT,
                metadata TEXT
            )
        """
        self.db.execute(query)
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_ust_active ON ust_levels(symbol, is_active) WHERE is_active = 1")
        logger.info(f"✅ UstDetector: Таблица ust_levels пересоздана с правильной схемой для {self.symbol}")

    def _calculate_atr(self, df: pd.DataFrame, period=14) -> pd.DataFrame:
        df['prev_close'] = df['close'].shift(1)
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['prev_close']), abs(df['low'] - df['prev_close']))
        )
        df['atr'] = df['tr'].rolling(window=period).mean()
        return df

    def _find_swing_points(self, df: pd.DataFrame, lookback=5) -> pd.DataFrame:
        df['swing_low'] = False
        df['swing_high'] = False
        for i in range(lookback, len(df) - lookback):
            if df['low'].iloc[i] == df['low'].iloc[i-lookback:i+lookback+1].min():
                df.loc[df.index[i], 'swing_low'] = True
            if df['high'].iloc[i] == df['high'].iloc[i-lookback:i+lookback+1].max():
                df.loc[df.index[i], 'swing_high'] = True
        return df

    def _is_valid_breakout(self, df: pd.DataFrame, level_price: float, current_index: int, direction: str):
        if current_index < 2:
            return False, ""
        
        current = df.iloc[current_index]
        prev_1 = df.iloc[current_index - 1]
        prev_2 = df.iloc[current_index - 2]
        
        avg_volume = df['volume'].rolling(window=20).mean().iloc[current_index]
        atr = df['atr'].iloc[current_index]
        
        if direction == 'bull':
            if current['low'] <= level_price: return False, ""
            body_size = abs(current['close'] - current['open'])
            is_impulse = (body_size > 1.5 * atr) and (current['volume'] > 1.5 * avg_volume)
            is_3_candles = (current['close'] > prev_1['close'] > prev_2['close']) and \
                           (prev_2['low'] > level_price) and \
                           (max(current['volume'], prev_1['volume'], prev_2['volume']) > 1.2 * avg_volume)
            if is_impulse: return True, "Impulse"
            if is_3_candles: return True, "3 Candles"
        else: # bear
            if current['high'] >= level_price: return False, ""
            body_size = abs(current['close'] - current['open'])
            is_impulse = (body_size > 1.5 * atr) and (current['volume'] > 1.5 * avg_volume)
            is_3_candles = (current['close'] < prev_1['close'] < prev_2['close']) and \
                           (prev_2['high'] < level_price) and \
                           (max(current['volume'], prev_1['volume'], prev_2['volume']) > 1.2 * avg_volume)
            if is_impulse: return True, "Impulse"
            if is_3_candles: return True, "3 Candles"
            
        return False, ""

    async def scan_and_save_levels(self):
        """Сканирует рынок и сохраняет новые УСТ в БД"""
        try:
            # 1. Получаем 1m свечи через REST (последние 200)
            klines = await self.rest.get_klines(self.symbol, interval='1m', limit=self.lookback_candles)
            if not klines:
                return
            
            # 2. Формируем DataFrame
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_vol', 'trades', 'taker_buy', 'taker_quote', 'ignore'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
            
            # 3. Агрегируем в 5m
            df_5m = df.resample('5min').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            
            df_5m = self._find_swing_points(df_5m, lookback=3)
            df_5m = self._calculate_atr(df_5m)
            
            swings = []
            for i in range(len(df_5m)):
                if df_5m['swing_low'].iloc[i]:
                    swings.append({'time': df_5m.index[i], 'price': df_5m['low'].iloc[i], 'type': 'low', 'index': i})
                elif df_5m['swing_high'].iloc[i]:
                    swings.append({'time': df_5m.index[i], 'price': df_5m['high'].iloc[i], 'type': 'high', 'index': i})
            
            new_levels_count = 0
            last_price = None
            
            # 4. Ищем пробои
            for swing in swings:
                if last_price and abs(swing['price'] - last_price) / last_price < self.min_distance_pct:
                    continue
                
                direction = 'bull' if swing['type'] == 'low' else 'bear'
                breakout_found = False
                
                for j in range(swing['index'] + 5, min(swing['index'] + 100, len(df_5m))):
                    is_brk, brk_type = self._is_valid_breakout(df_5m, swing['price'], j, direction)
                    if is_brk:
                        breakout_found = True
                        coeff = 0.5 + (0.3 if brk_type == "Impulse" else 0.0)
                        
                        # Проверка объема на пробое для бонуса
                        vol = df_5m['volume'].iloc[j]
                        avg_vol = df_5m['volume'].rolling(window=20).mean().iloc[j]
                        if vol > avg_vol * 2.0: coeff += 0.2
                        
                        self._save_level_to_db(
                            price=swing['price'],
                            direction=direction,
                            coefficient=min(coeff, 1.0),
                            breakout_type=brk_type,
                            created_at=swing['time'].timestamp()
                        )
                        new_levels_count += 1
                        last_price = swing['price']
                        break # Переходим к следующему swing point
            
            if new_levels_count > 0:
                logger.info(f"🎯 [UST DETECTOR] Найдено и сохранено {new_levels_count} новых уровней УСТ для {self.symbol}")
                self.export_to_json() # Обновляем JSON для графиков
                
        except Exception as e:
            logger.error(f"❌ [UST DETECTOR] Ошибка сканирования: {e}")

    def _save_level_to_db(self, price: float, direction: str, coefficient: float, breakout_type: str, created_at: float):
        """Проверяет, нет ли уже такого уровня, и сохраняет"""
        # Простая проверка на дубликат по цене (±0.1%)
        check_query = "SELECT id FROM ust_levels WHERE symbol=? AND is_active=1 AND ABS(price - ?) / ? < 0.001"
        exists = self.db.execute(check_query, (self.symbol, price, price))
        
        if not exists:
            query = """
                INSERT INTO ust_levels (symbol, direction, price, coefficient, created_at, breakout_type, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """
            self.db.execute(query, (self.symbol, direction, price, coefficient, created_at, breakout_type))

    def find_nearest_ust(self, current_price: float, direction: str, max_distance_pct: float = 0.005) -> Optional[Dict]:
        """
        Ищет ближайший активный УСТ для стратегии.
        direction: 'long' (ищем поддержку ниже цены) или 'short' (ищем сопротивление выше)
        """
        if direction == 'long':
            # Ищем поддержку (bull), которая ниже текущей цены, но не дальше max_distance_pct
            query = """
                SELECT price, coefficient, direction FROM ust_levels 
                WHERE symbol=? AND is_active=1 AND direction='bull' 
                AND price <= ? AND price >= ?
                ORDER BY coefficient DESC, ABS(price - ?) ASC
                LIMIT 1
            """
            min_price = current_price * (1 - max_distance_pct)
            params = (self.symbol, current_price, min_price, current_price)
        else:
            # Ищем сопротивление (bear), которое выше текущей цены
            query = """
                SELECT price, coefficient, direction FROM ust_levels 
                WHERE symbol=? AND is_active=1 AND direction='bear' 
                AND price >= ? AND price <= ?
                ORDER BY coefficient DESC, ABS(price - ?) ASC
                LIMIT 1
            """
            max_price = current_price * (1 + max_distance_pct)
            params = (self.symbol, current_price, max_price, current_price)
            
        result = self.db.execute(query, params)
        if result:
            row = result[0]
            return {"price": row['price'], "coefficient": row['coefficient'], "direction": row['direction']}
        return None

    def export_to_json(self):
        """Экспортирует активные уровни в JSON для визуализации на графиках"""
        query = "SELECT price, direction, coefficient, breakout_type, created_at FROM ust_levels WHERE symbol=? AND is_active=1 ORDER BY created_at DESC"
        results = self.db.execute(query, (self.symbol,))
        
        levels = []
        for row in results:
            levels.append({
                "price": row['price'],
                "direction": row['direction'],
                "coefficient": row['coefficient'],
                "breakout_type": row['breakout_type'],
                "timestamp": row['created_at']
            })
            
        # 🔥 Создаем папку logs, если её нет
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        
        json_path = logs_dir / f"active_ust_levels_{self.symbol}.json"  # <-- СТАЛО
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(levels, f, indent=2)