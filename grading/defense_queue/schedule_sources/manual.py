"""Датированные занятия из конфигурации; также контракт JSON-импорта."""


def validate(config):
    pass  # Общая проверка lessons выполняется до вызова адаптера.


def load(config, *, fetch, now, timezone):
    return [dict(row, source_kind=row.get('source_kind', 'manual' if config.get('source', 'manual') == 'manual' else 'university'))
            for row in config.get('lessons', [])]
