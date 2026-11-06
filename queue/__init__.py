"""Очередь на защиту с совместимостью со стандартным модулем queue.

Зависимости приложения используют queue.Queue, LifoQueue и Empty. Сохраняем
стандартный API; предметная очередь находится в queue.domain.queue.
"""

import importlib.util
import sys
import sysconfig
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_labgrader_stdlib_queue", Path(sysconfig.get_path("stdlib")) / "queue.py"
)
if _spec is None or _spec.loader is None:
    raise ImportError("Не удалось загрузить стандартный модуль queue")

_stdlib_queue = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _stdlib_queue
_spec.loader.exec_module(_stdlib_queue)

__all__ = _stdlib_queue.__all__
for _name in __all__:
    globals()[_name] = getattr(_stdlib_queue, _name)
