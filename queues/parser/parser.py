"""Refresh teacher timetables and export dated semester lessons to YAML."""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import yaml
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleConfig:
    university: str
    teacher: str
    semester: str = "auto"
    starts_on: date | None = None
    ends_on: date | None = None
    first_week_start: date | None = None
    refresh_interval_days: int = 7
    timezone: str = "Europe/Moscow"
    teaching_weeks: int = 18
    estimated_end: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9-]+", self.university):
            raise ValueError("university must be a lowercase university code")
        if not re.fullmatch(r"[a-zA-Z0-9-]+", self.semester):
            raise ValueError("Invalid semester code")
        if not self.teacher.strip():
            raise ValueError("teacher must not be empty")
        if type(self.refresh_interval_days) is not int or self.refresh_interval_days < 1:
            raise ValueError("refresh-interval-days must be a positive integer")
        if type(self.teaching_weeks) is not int or self.teaching_weeks < 1:
            raise ValueError("teaching-weeks must be a positive integer")
        ZoneInfo(self.timezone)
        if self.semester == "auto":
            if any(x is not None for x in (self.starts_on, self.ends_on, self.first_week_start)):
                raise ValueError("Do not specify semester dates in auto mode")
            return
        if any(x is None for x in (self.starts_on, self.ends_on, self.first_week_start)):
            raise ValueError("Explicit semesters require all three dates")
        if self.starts_on > self.ends_on:
            raise ValueError("Semester end precedes its start")
        if self.first_week_start.weekday() != 0 or not (
            self.first_week_start <= self.starts_on < self.first_week_start + timedelta(days=7)
        ):
            raise ValueError("first-week-start must be the Monday of the first semester week")
        ZoneInfo(self.timezone)


class ScheduleParser:
    """One instance per process; provider results are normalized before saving."""

    def __init__(self, directory: str | Path = "schedule", providers=None):
        self.directory = Path(directory)
        self.providers = {"suai": self._fetch_suai} if providers is None else providers
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._lock = Lock()
        self._auto_cache = {}

    def path_for(self, config: ScheduleConfig) -> Path:
        teacher = re.sub(r"[^\w-]+", "-", config.teacher.strip()).strip("-")
        if not teacher:
            raise ValueError("Invalid teacher name")
        return self.directory / config.university / config.semester / f"{teacher}.yaml"

    def refresh(self, config: ScheduleConfig, *, force: bool = False) -> bool:
        """Return whether content changed; unsuccessful refreshes keep old YAML."""
        with self._lock:
            if config.semester == "auto":
                config = self._resolve_semester(config, force=force)
            provider = self.providers.get(config.university)
            if provider is None:
                raise ValueError(f"Unsupported university: {config.university}")
            path = self.path_for(config)
            old = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            now = datetime.now(ZoneInfo(config.timezone))
            settings = {
                "university": config.university,
                "teacher": config.teacher,
                "semester": {
                    "id": config.semester,
                    "starts-on": config.starts_on.isoformat(),
                    "ends-on": config.ends_on.isoformat(),
                    "first-week-start": config.first_week_start.isoformat(),
                    "end-estimated": config.estimated_end,
                },
                "timezone": config.timezone,
                "refresh-interval-days": config.refresh_interval_days,
            }
            if not isinstance(old, dict):
                raise ValueError(f"Invalid saved schedule: {path}")
            checked = old.get("last-checked-at")
            same_settings = all(old.get(k) == v for k, v in settings.items())
            if not force and checked and same_settings:
                previous = datetime.fromisoformat(checked)
                if previous.tzinfo is None:
                    raise ValueError("last-checked-at must include a timezone")
                if now < previous + timedelta(days=config.refresh_interval_days):
                    return False

            days, unscheduled = provider(config)
            # Stabilize order and remove identical duplicates from upstream pages.
            days = {
                day: self._normalize(lessons) for day, lessons in sorted(days.items())
            }
            # Past saved dates describe the previously published timetable.
            today = now.date().isoformat()
            if same_settings:
                days.update({k: v for k, v in old.get("days", {}).items() if k < today})
                days = dict(sorted(days.items()))
            content = settings | {"days": days, "unscheduled": self._normalize(unscheduled)}
            changed = any(old.get(k) != v for k, v in content.items())
            document = content | {
                "last-checked-at": now.isoformat(),
                "updated-at": now.isoformat() if changed else old["updated-at"],
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent, delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    yaml.safe_dump(document, stream, allow_unicode=True, sort_keys=False)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            logger.info("Schedule refreshed: %s (changed=%s)", path, changed)
            return changed

    @staticmethod
    def _normalize(lessons):
        unique = {yaml.safe_dump(x, sort_keys=True, allow_unicode=True): x for x in lessons}
        return sorted(unique.values(), key=lambda x: (
            x.get("starts-at", ""), x["subject"], x["kind"],
            tuple(x["groups"]), x.get("room") or "",
        ))

    def _fetch_suai(self, config):
        from .vendor.suai_rasp.fetch import Fetcher
        from .vendor.suai_rasp.model import DAY_NUM, LESSON_TIMES
        from .vendor.suai_rasp.parse import parse_directories, parse_lessons

        fetcher = Fetcher(cache_dir=self.directory / ".cache" / "suai", ttl=0)
        try:
            teachers = parse_directories(fetcher.index())["teachers"]
            query = config.teacher.strip()
            parts = query.split()
            if len(parts) == 3:
                query = f"{parts[0]} {parts[1][0]}.{parts[2][0]}."
            matches = [tid for tid, name in teachers.items()
                       if query.casefold() in name.casefold()]
            if len(matches) != 1:
                raise ValueError(f"Expected one teacher matching {config.teacher!r}, found {len(matches)}")
            lessons = parse_lessons(fetcher.by_teacher(matches[0]))
        finally:
            fetcher.session.close()
        if not lessons:
            raise ValueError("Source returned no lessons; previous schedule is preserved")
        days, unscheduled = {}, []
        for lesson in lessons:
            item = {
                "subject": lesson.subject,
                "kind": lesson.kind,
                "groups": sorted({group.name for group in lesson.groups}),
                "room": lesson.room.name if lesson.room else None,
            }
            if not lesson.in_grid:
                unscheduled.append(item)
                continue
            start, end = LESSON_TIMES[lesson.slot]
            item = item | {"starts-at": start, "ends-at": end}
            current = config.starts_on
            while current <= config.ends_on:
                week = (current - config.first_week_start).days // 7 + 1
                parity = 1 if week % 2 else 2
                if current.isoweekday() == DAY_NUM[lesson.day] and lesson.week in (0, parity):
                    days.setdefault(current.isoformat(), []).append(item)
                current += timedelta(days=1)
        return days, unscheduled

    def _resolve_semester(self, config, *, force=False):
        """Resolve current SUAI term from the published academic year and week."""
        if config.university != "suai":
            raise ValueError(f"Automatic semester not supported for {config.university}")
        now = datetime.now(ZoneInfo(config.timezone))
        cached = self._auto_cache.get(config)
        if cached and not force:
            checked, resolved = cached
            if now < checked + timedelta(days=config.refresh_interval_days):
                return resolved
        from .vendor.suai_rasp.fetch import Fetcher
        from .vendor.suai_rasp.parse import parse_meta

        fetcher = Fetcher(cache_dir=self.directory / ".cache" / "suai", ttl=0)
        try:
            meta = parse_meta(fetcher.index())
        finally:
            fetcher.session.close()
        months = ("января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября", "декабря")
        match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", meta.get("today", ""))
        if not match or match[2] not in months:
            raise ValueError("Cannot determine source date for automatic semester")
        source_date = date(int(match[3]), months.index(match[2]) + 1, int(match[1]))
        week = meta.get("week_number")
        if not isinstance(week, int) or not 1 <= week <= config.teaching_weeks:
            raise ValueError("Source week is outside the configured teaching period")
        if abs((now.date() - source_date).days) > 2:
            raise ValueError("Source date is stale; automatic semester was not updated")
        if meta.get("semester") not in ("Осенний", "Весенний"):
            raise ValueError("Source semester is missing")
        academic_year = meta.get("academic_year", "")
        if not re.fullmatch(r"\d{4}/\d{2}", academic_year):
            raise ValueError("Source academic year is missing")
        autumn = meta["semester"] == "Осенний"
        year = int(academic_year[:4]) + (0 if autumn else 1)
        first_week = source_date - timedelta(days=source_date.weekday(), weeks=week - 1)
        starts = max(date(year, 9 if autumn else 2, 1), first_week)
        resolved = replace(
            config, semester=f"{year}-{'autumn' if autumn else 'spring'}",
            starts_on=starts, first_week_start=first_week,
            ends_on=first_week + timedelta(weeks=config.teaching_weeks, days=-1),
            estimated_end=True,
        )
        self._auto_cache[config] = (now, resolved)
        return resolved

    def start(self, configs: list[ScheduleConfig]) -> None:
        paths = [self.path_for(c) for c in configs]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate teacher/university/semester configurations")
        for config in configs:
            if config.university not in self.providers:
                raise ValueError(f"Unsupported university: {config.university}")
        if not configs:
            return
        self.scheduler.add_job(
            self._refresh_due, "interval", hours=1, args=[configs],
            next_run_time=datetime.now(ZoneInfo("UTC")),
            max_instances=1, coalesce=True, id="schedule-refresh",
        )
        self.scheduler.start()

    def _refresh_due(self, configs):
        for config in configs:
            try:
                self.refresh(config)
            except Exception:
                # Background job boundary: isolate one source failure and retry next hour.
                logger.exception("Schedule refresh failed: %s / %s", config.university, config.teacher)

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    @staticmethod
    def load_configs(path: str | Path) -> list[ScheduleConfig]:
        path = Path(path)
        if not path.exists():
            return []
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        configs = []
        for item in raw["schedules"]:
            semester = item.get("semester", "auto")
            automatic = semester == "auto"
            configs.append(ScheduleConfig(
                university=item["university"], teacher=item["teacher"],
                semester="auto" if automatic else semester["id"],
                starts_on=None if automatic else date.fromisoformat(str(semester["starts-on"])),
                ends_on=None if automatic else date.fromisoformat(str(semester["ends-on"])),
                first_week_start=None if automatic else date.fromisoformat(str(semester["first-week-start"])),
                refresh_interval_days=item.get("refresh-interval-days", 7),
                timezone=item.get("timezone", "Europe/Moscow"),
                teaching_weeks=item.get("teaching-weeks", 18),
            ))
        return configs
