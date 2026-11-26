"""运行日志：内存环形缓冲 + data/logs.jsonl 追加写，供面板展示。"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import DATA_DIR

LOG_PATH = DATA_DIR / "logs.jsonl"
_buffer: deque[dict[str, Any]] = deque(maxlen=500)
_lock = threading.Lock()


def log(message: str, level: str = "info", **extra: Any) -> dict[str, Any]:
    entry = {
        "ts": int(time.time()),
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "level": level,
        "message": message,
        **extra,
    }
    with _lock:
        _buffer.append(entry)
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return entry


def recent(limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
    with _lock:
        items = list(_buffer)
    if level:
        items = [i for i in items if i.get("level") == level]
    return items[-limit:][::-1]
