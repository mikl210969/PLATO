"""
JSON Logger — структурированное логирование в формате JSONL (JSON Lines).
Поддерживает автоматическую ротацию файлов.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class JsonLogger:
    def __init__(self, log_dir: str = "logs", enabled: bool = True, max_bytes: int = 5 * 1024 * 1024):
        self.log_dir = Path(log_dir)
        self.enabled = enabled
        self.max_bytes = max_bytes
        self._file_path: Optional[Path] = None

        if self.enabled:
            self.log_dir.mkdir(exist_ok=True)
            self._file_path = self.log_dir / "platform_log.jsonl"
            self._ensure_file()

    def _ensure_file(self):
        if self._file_path and not self._file_path.exists():
            self._file_path.touch()

    def _rotate_if_needed(self):
        if not self._file_path or not self._file_path.exists():
            return
        
        if self._file_path.stat().st_size >= self.max_bytes:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"platform_log_{timestamp}.jsonl"
            rotated_path = self._file_path.with_name(new_name)
            
            # Переименовываем текущий файл
            self._file_path.rename(rotated_path)
            # Создаем новый пустой файл
            self._file_path.touch()

    def log(self, module: str, event: str, data: Dict[str, Any], level: str = "INFO", correlation_id: Optional[str] = None):
        if not self.enabled:
            return

        self._rotate_if_needed()

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "module": module,
            "event": event,
            "level": level,
            "data": data
        }
        
        if correlation_id:
            entry["correlation_id"] = correlation_id

        try:
            with open(self._file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Fail silently to not crash the trading platform

    def close(self):
        pass  # В формате JSONL закрывать файл специальным образом не нужно