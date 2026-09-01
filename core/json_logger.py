"""
JSON Logger — структурированное логирование в формате JSONL (JSON Lines).
Поддерживает автоматическую ротацию файлов.

🔥 ИСПРАВЛЕНО:
- Файл держится открытым постоянно (не открывается/закрывается на каждую запись)
- Добавлен flush() после каждой записи (мгновенное сохранение)
- Ошибки логируются в stderr (а не молча игнорируются)
- Reopen() для восстановления после ошибок
"""

import json
import sys
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
        self._file_handle = None  # 🔥 НОВОЕ: держим файл открытым

        if self.enabled:
            self.log_dir.mkdir(exist_ok=True)
            self._file_path = self.log_dir / "platform_log.jsonl"
            self._open_file()

    def _open_file(self):
        """🔥 Открыть файл для добавления (append mode)."""
        if not self._file_path:
            return
        try:
            self._ensure_file()
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')
        except Exception as e:
            print(f"❌ [JsonLogger] Failed to open file: {e}", file=sys.stderr)
            self._file_handle = None

    def _ensure_file(self):
        if self._file_path and not self._file_path.exists():
            self._file_path.touch()

    def _close_file(self):
        """🔥 Закрыть текущий файл."""
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def _rotate_if_needed(self):
        """Проверить размер файла и при необходимости сделать ротацию."""
        if not self._file_path or not self._file_path.exists():
            return
        
        try:
            if self._file_path.stat().st_size >= self.max_bytes:
                # Закрываем текущий файл
                self._close_file()
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                new_name = f"platform_log_{timestamp}.jsonl"
                rotated_path = self._file_path.with_name(new_name)
                
                # Переименовываем текущий файл
                self._file_path.rename(rotated_path)
                
                # Открываем новый пустой файл
                self._open_file()
        except Exception as e:
            print(f"❌ [JsonLogger] Rotation failed: {e}", file=sys.stderr)

    def log(self, module: str, event: str, data: Dict[str, Any], level: str = "INFO", correlation_id: Optional[str] = None):
        if not self.enabled or not self._file_path:
            return

        self._rotate_if_needed()

        # 🔥 Проверка: если файл закрыт (например, после ошибки) — пробуем открыть заново
        if self._file_handle is None or self._file_handle.closed:
            self._open_file()
            if self._file_handle is None:
                # Не можем писать — логируем в stderr как последнее средство
                print(f"❌ [JsonLogger] Cannot write to {self._file_path}: file handle is None", file=sys.stderr)
                return

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
            self._file_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file_handle.flush()  # 🔥 НОВОЕ: мгновенный сброс на диск
        except Exception as e:
            # Логируем в stderr вместо молчаливого игнорирования
            print(f"❌ [JsonLogger] Write failed: {e}", file=sys.stderr)
            # Пытаемся восстановить дескриптор
            self._close_file()
            self._open_file()

    def close(self):
        """Закрыть файл при завершении работы."""
        self._close_file()