"""Registry of schedule sources, keyed by the string used in schedules YAML.

This is the second half of the Strategy + Registry pattern: sources register
themselves at import time via the @register_source decorator, and the
factory in __init__.py looks them up by name. To add a new university, you
only create a new module + decorate the class — neither the factory, nor
main.py, nor the queue code is touched.
"""
from __future__ import annotations

from typing import Type

from .base import ScheduleSource, ScheduleConfigError


_REGISTRY: dict[str, Type[ScheduleSource]] = {}


def register_source(name: str):
    """Class decorator: register a ScheduleSource implementation under `name`.

    Example:

        @register_source('spbu')
        class SpbuScheduleSource(ScheduleSource):
            ...

    Then in schedules/<course_id>.yaml, under any group:

        groups:
          "4432":
            source: spbu
            ...
    """
    def decorator(cls: Type[ScheduleSource]) -> Type[ScheduleSource]:
        if not issubclass(cls, ScheduleSource):
            raise TypeError(f"{cls!r} must inherit ScheduleSource")
        if name in _REGISTRY:
            raise RuntimeError(
                f"Schedule source '{name}' is already registered "
                f"({_REGISTRY[name].__name__}); cannot register {cls.__name__}"
            )
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_source_class(name: str) -> Type[ScheduleSource]:
    """Look up a registered source class by its string name.

    Raises ScheduleConfigError if unknown, with a helpful message listing
    known sources and how to add a new one — same UX as JoinConfigError.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ScheduleConfigError(
            f"Unknown schedule source {name!r}. "
            f"Known sources: {sorted(_REGISTRY)}. "
            "To add a new university, implement ScheduleSource in "
            "grading/schedule/<name>.py and decorate it with "
            f"@register_source({name!r})."
        )


def known_sources() -> list[str]:
    """Names of all registered sources, sorted. Used for error messages
    and for an admin 'what sources are available' endpoint if needed."""
    return sorted(_REGISTRY)
