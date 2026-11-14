from abc import ABC, abstractmethod
from typing import Dict, Type

import numpy as np


class QueueModel(ABC):
    """Базовый класс. Наследники авто-регистрируются в QueueModel._registry."""

    name: str = None            # уникальный идентификатор (CLI-имя, имя папки)
    description: str = ""
    _registry: Dict[str, Type["QueueModel"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name is not None:
            if cls.name in QueueModel._registry:
                raise ValueError(f"Модель '{cls.name}' уже зарегистрирована")
            QueueModel._registry[cls.name] = cls

    @classmethod
    def get(cls, name: str) -> Type["QueueModel"]:
        return cls._registry[name]

    @classmethod
    def all(cls) -> Dict[str, Type["QueueModel"]]:
        return dict(cls._registry)

    @abstractmethod
    def order_queue(self, state) -> "np.ndarray":
        """
        Вернуть массив индексов студентов (подмножество state.active())
        в порядке попытки сдачи на текущей неделе.
        Первые `CAPACITY` из результата реально попробуют сдать.
        """
        ...