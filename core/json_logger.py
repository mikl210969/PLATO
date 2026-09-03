"""
JSON Logger — структурированное логирование с фильтрацией и ротацией.
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class JsonLogger:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.log_level = config.get('level', 'INFO').upper()
        self.max_bytes = config.get('max_file_size_mb', 5) * 1024 * 1024
        self.max_backups = config.get('max_backup_files', 3)
        
        self.core_modules = set(config.get('core_modules', []))
        self.optional_modules = config.get('optional_modules', {})
        
        self.log_dir = Path("logs")
        self._file_path: Optional[Path] = None
        self._file_handle = None
        self._write_count = 0  # Счетчик для оптимизации ротации

        self.log_dir.mkdir(exist_ok=True)
        self._file_path = self.log_dir / "platform_log.jsonl"
        self._open_file()

    def _open_file(self):
        if not self._file_path:
            return
        try:
            if not self._file_path.exists():
                self._file_path.touch()
            self._file_handle = open(self._file_path, 'a', encoding='utf-8')
        except Exception as e:
            print(f"❌ [JsonLogger] Failed to open file: {e}", file=sys.stderr)
            self._file_handle = None

    def _close_file(self):
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def _rotate_if_needed(self):
        if not self._file_path or not self._file_path.exists():
            return
        
        try:
            if self._file_path.stat().st_size >= self.max_bytes:
                self._close_file()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                new_name = f"platform_log_{timestamp}.jsonl"
                rotated_path = self._file_path.with_name(new_name)
                
                counter = 0
                while rotated_path.exists():
                    counter += 1
                    rotated_path = self._file_path.with_name(f"platform_log_{timestamp}_{counter}.jsonl")
                
                self._file_path.rename(rotated_path)
                self._cleanup_old_backups()
                self._open_file()
        except Exception as e:
            print(f"❌ [JsonLogger] Rotation failed: {e}", file=sys.stderr)

    def _cleanup_old_backups(self):
        """Удаляет старые файлы, оставляя только max_backups."""
        try:
            files = sorted(self.log_dir.glob("platform_log_*.jsonl"), key=os.path.getmtime, reverse=True)
            for old_file in files[self.max_backups:]:
                old_file.unlink()
        except Exception:
            pass

    def _should_log(self, module: str, level: str) -> bool:
        """Проверяет, нужно ли логировать событие по правилам конфига."""
        levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        
        # 1. Проверка глобального уровня
        if levels.get(level, 1) < levels.get(self.log_level, 1):
            return False
            
        # 2. Проверка WS-модуля (сырые данные)
        if module == "ws" and not self.optional_modules.get("ws", False):
            return False
            
        # 3. Проверка опциональных модулей
        if module not in self.core_modules:
            if not self.optional_modules.get(module, False):
                return False
                
        return True

    def log(self, module: str, event: str, data: Dict[str, Any], level: str = "INFO", correlation_id: Optional[str] = None):
        if not self._should_log(module, level):
            return

        self._write_count += 1
        # Проверяем ротацию раз в 100 записей для производительности
        if self._write_count >= 100:
            self._rotate_if_needed()
            self._write_count = 0

        if self._file_handle is None or self._file_handle.closed:
            self._open_file()
            if self._file_handle is None:
                print(f"❌ [JsonLogger] Cannot write: file handle is None", file=sys.stderr)
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
            self._file_handle.flush()
        except Exception as e:
            print(f"❌ [JsonLogger] Write failed: {e}", file=sys.stderr)
            self._close_file()
            self._open_file()

    def close(self):
        self._close_file()

import logging

class JsonLoggerHandler(logging.Handler):
    """
    Мост между стандартным Python logging и нашим JsonLogger.
    Перехватывает все logger.info() и записывает их в JSON-файл.
    """
    def __init__(self, json_logger_instance):
        super().__init__()
        self.json_logger = json_logger_instance

    def emit(self, record):
        try:
            # Извлекаем имя модуля (например, '__main__', 'trading.order_verifier')
            module = record.name
            
            # Пытаемся выделить суть события из сообщения (до двоеточия или первые 20 символов)
            msg = record.getMessage()
            event = msg.split(':')[0].strip() if ':' in msg else msg[:30].strip()
            
            # Данные для JSON
            data = {
                "message": msg,
                "file": record.filename,
                "line": record.lineno
            }
            
            # Отправляем в наш JsonLogger
            self.json_logger.log(
                module=module,
                event=event,
                data=data,
                level=record.levelname
            )
        except Exception:
            # Если что-то пошло не так, не ломаем основную программу
            self.handleError(record)