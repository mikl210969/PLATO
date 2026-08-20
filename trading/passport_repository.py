"""
Репозиторий паспортов — сохраняет/загружает с диска.
"""

import json
from pathlib import Path
from typing import Optional
from trading.passport import TradePassport


class PassportRepository:
    """Сохранение и загрузка паспортов."""

    def __init__(self, logs_dir: str = "logs"):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True)

    def save(self, passport: TradePassport):
        """Сохранить паспорт на диск."""
        file_path = self.logs_dir / f"passport_{passport.passport_id}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(passport.to_dict(), f, indent=2, ensure_ascii=False)

    def load(self, passport_id: str) -> Optional[TradePassport]:
        """Загрузить паспорт с диска."""
        file_path = self.logs_dir / f"passport_{passport_id}.json"
        if not file_path.exists():
            return None
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return TradePassport(**data)

    def delete(self, passport_id: str):
        """Удалить файл паспорта."""
        file_path = self.logs_dir / f"passport_{passport_id}.json"
        if file_path.exists():
            file_path.unlink()