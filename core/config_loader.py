"""
Загрузка конфигов из JSON файлов.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigLoader:
    """Загрузчик конфигов."""

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)

    def load(self, name: str) -> Dict[str, Any]:
        """Загрузить конфиг по имени."""
        path = self.config_dir / f"{name}.json"
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_all(self) -> Dict[str, Dict]:
        """Загрузить все конфиги."""
        return {
            'exchange': self.load('exchange'),
            'trading': self.load('trading'),
            'risk': self.load('risk'),
            'strategies': self.load('strategies'),
        }

    def load_secrets(self) -> Dict[str, str]:
        """Загрузить секреты."""
        path = self.config_dir / "secrets.json"
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)    