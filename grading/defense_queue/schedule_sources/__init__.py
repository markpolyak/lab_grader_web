"""Доверенные адаптеры, поставляемые вместе с приложением.

Новый вуз: модуль <source>.py с validate(config) и load(config, *, fetch, now,
timezone). Ядро очереди не меняется. Имя модуля приходит только из конфигурации.
"""
from importlib import import_module
import re


def adapter(source):
    if not isinstance(source, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', source):
        raise ValueError('Нужно имя установленного адаптера расписания')
    name = 'manual' if source == 'import' else source
    module_name = f'{__name__}.{name}'
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise  # Ошибка зависимости внутри существующего адаптера не маскируется.
        raise ValueError(f'Адаптер расписания {source} не установлен') from None
    if not callable(getattr(module, 'validate', None)) or not callable(getattr(module, 'load', None)):
        raise ValueError('Адаптер должен предоставлять validate и load')
    return module
