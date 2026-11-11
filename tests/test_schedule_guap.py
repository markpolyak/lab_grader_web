"""
Тесты источника расписания guap (grading/schedule/guap.py и suai_rasp.py).

Страницы в tests/fixtures/guap - сокращённые копии настоящих страниц
https://guap.ru/rasp от 05.11.2026 (шапка «▼ 10 нижняя (четная) неделя»,
«Сегодня четверг, 5 ноября 2026 г.»). Сеть не используется: HTTP подменяет
responses, незарегистрированный адрес даёт ConnectionError, время передаётся
параметром now.
"""
import io
import os
import re
import socket
import sys
import threading
import time as time_module
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
import responses
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.schedule import (  # noqa: E402
    ERROR_BAD_PAGE,
    ERROR_FETCH_FAILED,
    ERROR_NO_LESSONS,
    ERROR_SNAPSHOT_TOO_OLD,
    SOURCE_TZ_MSK,
    STATE_OK,
    STATE_STALE,
    STATE_UNAVAILABLE,
    ScheduleConfigError,
    ScheduleUnavailable,
    SourceContext,
    build_schedule_source,
    reset_schedule_caches,
)
from grading.defense_queue import QueueConfigError, parse_queue_config  # noqa: E402
from grading.schedule import guap, suai_rasp  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "guap"

MSK = SOURCE_TZ_MSK
UTC5 = timezone(timedelta(hours=5))

URL = "https://guap.ru/rasp?pr=39614"
KEY = "guap:pr=39614"
OS = "Операционные системы"
LAB = "Лабораторное занятие"

# Момент, когда скачаны страницы фикстур.
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=MSK)

MONDAY = date(2026, 11, 9)      # неделя 11, верхняя (▲)
TUESDAY = date(2026, 11, 10)
THURSDAY = date(2026, 11, 5)    # неделя 10, нижняя (▼)


def page(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


POLYAK = page("prep_polyak_39614")
REPO = Path(__file__).parent.parent


class FakeSnapshots:
    """SnapshotStore в памяти: так же, как QueueStore, хранит одну копию на ключ."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.saves = []

    def load_snapshot(self, key):
        return self.rows.get(key)

    def save_snapshot(self, key, fetched_at, payload):
        self.saves.append((key, fetched_at))
        self.rows[key] = (fetched_at, payload)

    def snapshot_fetched_at(self, key):
        row = self.rows.get(key)
        return row[0] if row else None


class BrokenSnapshots(FakeSnapshots):
    """Хранилище, которое не может ни читать, ни писать (диск, блокировка)."""

    def load_snapshot(self, key):
        raise OSError("disk I/O error")

    def save_snapshot(self, key, fetched_at, payload):
        raise OSError("disk I/O error")

    def snapshot_fetched_at(self, key):
        raise OSError("disk I/O error")


def make_source(store=None, tz=MSK, **cfg):
    """Источник guap курса ОС; ключи конфига передаются с подчёркиваниями."""
    full = {"source": "guap", "teacher-id": 39614, "subject": OS}
    full.update({k.replace("_", "-"): v for k, v in cfg.items()})
    full = {k: v for k, v in full.items() if v is not None}
    return build_schedule_source(full, SourceContext(snapshots=store, course_tz=tz))


def summary(lessons):
    """(начало, конец, группы, тип, предмет) в московском времени."""
    return [
        (
            lesson.starts_at.astimezone(MSK).strftime("%H:%M"),
            lesson.ends_at.astimezone(MSK).strftime("%H:%M"),
            lesson.groups,
            lesson.lesson_type,
            lesson.subject,
        )
        for lesson in lessons
    ]


@pytest.fixture(autouse=True)
def clean_caches():
    reset_schedule_caches()
    yield
    reset_schedule_caches()


@pytest.fixture
def rs():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def serve(rs, body=POLYAK, status=200, url=URL):
    rs.add(responses.GET, url, body=body, status=status, content_type="text/html; charset=utf-8")


def site_down(rs, url=URL):
    rs.add(responses.GET, url, body=requests.ConnectionError("guap.ru is down"))


class Drip(io.RawIOBase):
    """Тело ответа, которое приходит по байту с паузой: сайт «капает», таймаут чтения не срабатывает."""

    def __init__(self, data: bytes, delay: float):
        self.data = data
        self.delay = delay

    def readable(self):
        return True

    def readinto(self, buffer):
        time_module.sleep(self.delay)
        if not self.data:
            return 0
        buffer[0] = self.data[0]
        self.data = self.data[1:]
        return 1


def serve_drip(rs, delay=0.02):
    rs.add(responses.GET, URL, body=io.BufferedReader(Drip(POLYAK.encode(), delay)),
           content_type="text/html; charset=utf-8")


def hang_site(rs, started, release):
    """Сайт не отвечает, пока тест не отпустит release."""
    def callback(request):
        started.set()
        release.wait(5)
        return 200, {"Content-Type": "text/html; charset=utf-8"}, POLYAK
    rs.add_callback(responses.GET, URL, callback=callback)


class HeaderDripServer:
    """
    Сайт на локальном сокете: первому соединению отдаёт строку статуса и
    капает заголовок по байту, пока тест не отпустит release; requests.get
    при этом не возвращается, таймаут чтения не срабатывает. Следующие
    соединения получают страницу целиком.
    """

    def __init__(self, delay=0.05):
        self.delay = delay
        self.connections = 0
        self.release = threading.Event()
        self.closed = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.05)
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}/rasp?pr=39614"
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.closed.is_set():
            try:
                conn, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            with conn:
                conn.settimeout(5)
                try:
                    conn.recv(65536)
                    if self.connections == 1:
                        conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                        while not self.release.wait(self.delay):
                            conn.sendall(b"a")
                    else:
                        body = POLYAK.encode()
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                            b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
                        )
                except OSError:
                    pass

    def close(self):
        self.release.set()
        self.closed.set()
        self.thread.join(5)
        self.listener.close()


@pytest.fixture
def header_drip(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    server = HeaderDripServer()
    yield server
    server.close()
    for thread in list(guap._stuck_fetches):
        thread.join(5)


def guap_source(store=None, url=URL):
    """GuapSource курса ОС, который качает страницу с url."""
    source = make_source(store).source
    source.url = url
    return source


def timed(call):
    """(результат или исключение, секунды)."""
    t0 = time_module.monotonic()
    try:
        result = call()
    except Exception as e:
        result = e
    return result, time_module.monotonic() - t0


def timed_or_hung(call, limit=5):
    """timed(call) в отдельном потоке; зависший вызов проваливает тест, а не весь прогон."""
    done = {}
    worker = threading.Thread(target=lambda: done.update(result=timed(call)), daemon=True)
    worker.start()
    worker.join(limit)
    assert not worker.is_alive(), f"the call has not returned in {limit} s"
    return done["result"]


# ---------------------------------------------------------------------------
# Разбор страниц (suai_rasp)
# ---------------------------------------------------------------------------


class TestParser:
    def test_meta_of_teacher_page(self):
        meta = suai_rasp.parse_meta(POLYAK)
        assert meta["week_number"] == 10
        assert meta["week_parity"] == suai_rasp.WEEK_EVEN
        assert meta["today"] == "четверг, 5 ноября 2026"
        assert meta["built"] == "2026-10-23"
        assert meta["academic_year"] == "2026/27"

    @pytest.mark.parametrize("name, kind, text", [
        ("prep_polyak_39614", suai_rasp.BANNER_FILTER, "Фильтр: Поляк М.Д., старший преподаватель"),
        ("group_4431", suai_rasp.BANNER_FILTER, "Фильтр: гр. 4431"),
        ("invalid_gr_1", suai_rasp.BANNER_NO_MATCH, "Совпадений не обнаружено."),
        ("rasp_index", suai_rasp.BANNER_OTHER,
         "Выберите группу, преподавателя, кафедру или аудиторию в форме поиска."),
    ])
    def test_result_banner(self, name, kind, text):
        assert suai_rasp.parse_result_banner(page(name)) == (kind, text)

    def test_result_banner_missing(self):
        assert suai_rasp.parse_result_banner("<html><body>Service Unavailable</body></html>") == (
            suai_rasp.BANNER_OTHER, "")

    def test_teacher_page_lessons(self):
        lessons = suai_rasp.parse_lessons(POLYAK)
        got = [
            (l.day, l.slot, l.week, l.kind, l.subject, l.start, l.end,
             [t.id for t in l.teachers], [g.name for g in l.groups])
            for l in lessons
        ]
        assert got == [
            ("Понедельник", 4, suai_rasp.WEEK_ANY, LAB, OS, "15:10", "16:40", [39614], ["4431"]),
            ("Понедельник", 6, suai_rasp.WEEK_ANY, LAB, OS, "18:40", "20:10", [39614], ["4434"]),
            ("Вторник", 1, suai_rasp.WEEK_ANY, "Лекция", OS, "09:30", "11:00", [39614],
             ["4431", "4432", "4434"]),
            ("Вторник", 3, suai_rasp.WEEK_ANY, LAB, OS, "13:00", "14:30", [39614], ["4432"]),
        ]
        assert lessons[0].room == suai_rasp.Ref(110, "23-09 (Б. Морская 67)")
        assert lessons[0].chair == suai_rasp.Ref(5, "Кафедра 43")
        assert lessons[0].teachers == [suai_rasp.Ref(39614, "Поляк М.Д., старший преподаватель")]

    def test_group_page_week_markers(self):
        lessons = suai_rasp.parse_lessons(page("group_4431"))
        assert len(lessons) == 22
        assert all(l.in_grid for l in lessons)
        monday_3 = [(l.week, l.subject) for l in lessons if l.day == "Понедельник" and l.slot == 3]
        assert monday_3 == [
            (suai_rasp.WEEK_ODD, "Разработка и проектирование интеллектуальных программных средств машинного обучения"),
            (suai_rasp.WEEK_EVEN, "Технология разработки серверных информационных систем"),
        ]

    def test_out_of_grid_card(self):
        lessons = suai_rasp.parse_lessons(page("group_4432"))
        out = [l for l in lessons if not l.in_grid]
        assert len(lessons) == 16
        assert len(out) == 1
        assert (out[0].day, out[0].slot, out[0].start, out[0].kind) == (None, None, None, "Курсовой проект")

    def test_kind_trailing_space_is_cleaned(self):
        kinds = {l.kind for l in suai_rasp.parse_lessons(page("group_4431"))}
        assert "Практическое занятие" in kinds

    def test_slot_time_taken_from_page(self):
        html = POLYAK.replace("(15:10—16:40)", "(15:00—16:30)")
        monday_4 = suai_rasp.parse_lessons(html)[0]
        assert (monday_4.slot, monday_4.start, monday_4.end) == (4, "15:00", "16:30")

    def test_slot_time_fallback_to_table(self):
        html = POLYAK.replace('<small class="opacity-50">(15:10—16:40)</small>', "")
        monday_4 = suai_rasp.parse_lessons(html)[0]
        assert (monday_4.slot, monday_4.start, monday_4.end) == (4, "15:10", "16:40")

    def test_no_content_block(self):
        assert suai_rasp.parse_lessons("<html><body><p>nothing</p></body></html>") == []


# ---------------------------------------------------------------------------
# Дата и чётность
# ---------------------------------------------------------------------------


class TestWeeks:
    @pytest.mark.parametrize("text, expected", [
        ("четверг, 5 ноября 2026", date(2026, 11, 5)),
        ("понедельник, 31 августа 2026", date(2026, 8, 31)),
        ("вторник, 1 Сентября 2026", date(2026, 9, 1)),
        ("пятница, 31 февраля 2026", None),
        ("5 brumaire 2026", None),
        ("", None),
        (None, None),
    ])
    def test_parse_today(self, text, expected):
        assert guap.parse_today(text) == expected

    @pytest.mark.parametrize("day, week", [
        (date(2026, 11, 2), 10),
        (date(2026, 11, 5), 10),
        (date(2026, 11, 8), 10),
        (date(2026, 11, 9), 11),
        (date(2026, 10, 26), 9),
        (date(2026, 9, 1), 1),
        (date(2026, 8, 31), 1),
    ])
    def test_week_number_from_anchor(self, day, week):
        assert guap.week_number(day, date(2026, 11, 5), 10) == week

    def test_parity_from_header(self):
        parsed = guap.parse_teacher_page(POLYAK, 39614, date(2026, 11, 5))
        assert (parsed.anchor_day, parsed.anchor_week) == (date(2026, 11, 5), 10)
        assert parsed.parity_on(date(2026, 11, 2)) == suai_rasp.WEEK_EVEN
        assert parsed.parity_on(date(2026, 11, 8)) == suai_rasp.WEEK_EVEN
        assert parsed.parity_on(date(2026, 11, 9)) == suai_rasp.WEEK_ODD
        assert parsed.parity_on(date(2026, 10, 26)) == suai_rasp.WEEK_ODD

    def test_parity_follows_header_marker(self):
        # Карточки помечены теми же ▲/▼, что и шапка, поэтому верхняя/нижняя
        # берётся из шапки, даже если не совпадает с чётностью номера недели.
        html = POLYAK.replace("▼&nbsp;10&nbsp;нижняя (четная)", "▲&nbsp;10&nbsp;верхняя (нечетная)")
        parsed = guap.parse_teacher_page(html, 39614, date(2026, 11, 5))
        assert parsed.parity_on(date(2026, 11, 2)) == suai_rasp.WEEK_ODD
        assert parsed.parity_on(date(2026, 11, 9)) == suai_rasp.WEEK_EVEN


# ---------------------------------------------------------------------------
# Занятия курса
# ---------------------------------------------------------------------------


class TestLessons:
    def test_polyak_labs_on_monday(self, rs):
        serve(rs)
        lessons = make_source().lessons_on(MONDAY, NOW)
        assert summary(lessons) == [
            ("15:10", "16:40", ("4431",), LAB, OS),
            ("18:40", "20:10", ("4434",), LAB, OS),
        ]
        assert lessons[0].key == "12:10-13:40|4431|Лабораторное занятие|Операционные системы"
        assert lessons[0].starts_at == datetime(2026, 11, 9, 15, 10, tzinfo=MSK)

    def test_lecture_filtered_out(self, rs):
        serve(rs)
        assert summary(make_source().lessons_on(TUESDAY, NOW)) == [("13:00", "14:30", ("4432",), LAB, OS)]

    def test_lecture_kept_when_type_configured(self, rs):
        serve(rs)
        lessons = make_source(lesson_types=[LAB, "Лекция"]).lessons_on(TUESDAY, NOW)
        assert summary(lessons) == [
            ("09:30", "11:00", ("4431", "4432", "4434"), "Лекция", OS),
            ("13:00", "14:30", ("4432",), LAB, OS),
        ]

    def test_no_lessons_on_other_days(self, rs):
        serve(rs)
        source = make_source()
        assert source.lessons_on(THURSDAY, NOW) == []
        assert source.lessons_on(date(2026, 11, 8), NOW) == []

    def test_subject_filter(self, rs):
        serve(rs)
        assert make_source(subject="Базы данных").lessons_on(MONDAY, NOW) == []

    def test_without_subject_filter(self, rs):
        serve(rs)
        assert len(make_source(subject=None).lessons_on(MONDAY, NOW)) == 2

    def test_teacher_filter_and_parity(self, rs):
        # На странице группы 4431 у Путиловой (pr=39503) «Проектирование баз
        # данных»: пн 2 пара ▲ и пн 6 пара ▼. ЛР Поляка в тот же день не берётся.
        serve(rs, page("group_4431"), url="https://guap.ru/rasp?pr=39503")
        source = make_source(teacher_id=39503, subject=None)
        assert summary(source.lessons_on(date(2026, 11, 2), NOW)) == [
            ("18:40", "20:10", ("4431",), LAB, "Проектирование баз данных"),
        ]
        assert summary(source.lessons_on(date(2026, 11, 9), NOW)) == [
            ("11:10", "12:40", ("4431",), LAB, "Проектирование баз данных"),
        ]

    def test_double_lab_merged(self, rs):
        # Чт 1 и 2 пары ▼: ЛР «Основы программной инженерии» у Зянчурина (pr=2869).
        serve(rs, page("group_4431"), url="https://guap.ru/rasp?pr=2869")
        source = make_source(teacher_id=2869, subject="Основы программной инженерии")
        lessons = source.lessons_on(THURSDAY, NOW)
        assert summary(lessons) == [("09:30", "12:40", ("4431",), LAB, "Основы программной инженерии")]
        assert lessons[0].key == "06:30-09:40|4431|Лабораторное занятие|Основы программной инженерии"
        assert source.lessons_on(date(2026, 11, 12), NOW) == []

    @pytest.mark.parametrize("name, teacher_id, subject, day, groups", [
        # Вт 3 и 4 пары: «Проектная деятельность» у 4431 и ещё семи групп.
        ("group_4431", 3193, "Проектная деятельность", TUESDAY,
         ("2440", "2443", "2454", "3421", "3423", "3525кс", "4431", "м450")),
        # Чт 3 и 4 пары ▼: «Технический перевод» у 4432.
        ("group_4432", 2973, "Технический перевод", THURSDAY, ("4432",)),
    ])
    def test_double_pair_across_lunch_merged(self, rs, name, teacher_id, subject, day, groups):
        # Между 3-й (до 14:30) и 4-й (с 15:10) парой обед 40 минут, но это одно
        # сдвоенное занятие и одна очередь.
        serve(rs, page(name), url=f"https://guap.ru/rasp?pr={teacher_id}")
        source = make_source(teacher_id=teacher_id, subject=subject, lesson_types=["Практическое занятие"])
        assert summary(source.lessons_on(day, NOW)) == [
            ("13:00", "16:40", groups, "Практическое занятие", subject),
        ]

    def test_anchor_taken_from_header(self, rs):
        # Страница скачана через неделю после даты в шапке: чётность считается
        # от «Сегодня ... 5 ноября» и «10 неделя», а не от даты загрузки.
        serve(rs, page("group_4431"), url="https://guap.ru/rasp?pr=39503")
        source = make_source(teacher_id=39503, subject=None)
        later = datetime(2026, 11, 12, 10, 0, tzinfo=MSK)
        assert summary(source.lessons_on(date(2026, 11, 16), later))[0][:2] == ("18:40", "20:10")

    def test_anchor_falls_back_to_download_date(self, rs):
        html = page("group_4431")
        start = html.index("Сегодня <nobr>")
        html = html[:start] + html[html.index("</nobr>", start) + len("</nobr>"):]
        serve(rs, html, url="https://guap.ru/rasp?pr=39503")
        source = make_source(teacher_id=39503, subject=None)
        later = datetime(2026, 11, 12, 10, 0, tzinfo=MSK)
        # Неделя загрузки (9-15 ноября) считается 10-й, нижней.
        assert summary(source.lessons_on(date(2026, 11, 9), later))[0][:2] == ("18:40", "20:10")
        assert summary(source.lessons_on(date(2026, 11, 16), later))[0][:2] == ("11:10", "12:40")

    def test_times_are_moscow_for_any_course_timezone(self, rs):
        serve(rs)
        lessons = make_source(tz=UTC5).lessons_on(MONDAY, NOW)
        assert lessons[0].starts_at == datetime(2026, 11, 9, 15, 10, tzinfo=MSK)
        assert lessons[0].starts_at.utcoffset() == timedelta(hours=3)
        assert lessons[0].starts_at.astimezone(UTC5).strftime("%H:%M") == "17:10"

    def test_time_from_page_used(self, rs):
        serve(rs, POLYAK.replace("(15:10—16:40)", "(15:00—16:30)"))
        assert summary(make_source().lessons_on(MONDAY, NOW))[0][:2] == ("15:00", "16:30")

    def test_lesson_without_groups_skipped(self, rs):
        html = POLYAK.replace('гр:&nbsp;<a class="" href="?gr=7018">4431</a>', "", 1)
        serve(rs, html)
        assert summary(make_source().lessons_on(MONDAY, NOW)) == [("18:40", "20:10", ("4434",), LAB, OS)]


# ---------------------------------------------------------------------------
# Загрузка, кэш, сохранённая копия
# ---------------------------------------------------------------------------


class TestDownloadAndCopy:
    def test_request(self, rs):
        serve(rs)
        make_source().lessons_on(MONDAY, NOW)
        assert len(rs.calls) == 1
        assert rs.calls[0].request.url == URL
        assert rs.calls[0].request.headers["User-Agent"] == guap.USER_AGENT
        assert rs.calls[0].request.headers["Accept-Encoding"] == "identity"

    def test_page_saved_after_download(self, rs):
        serve(rs)
        store = FakeSnapshots()
        source = make_source(store)
        source.lessons_on(MONDAY, NOW)
        assert store.rows[KEY] == (NOW, POLYAK)
        status = source.status(NOW)
        assert (status.source, status.state, status.fetched_at, status.error) == ("guap", STATE_OK, NOW, None)

    def test_site_down_uses_saved_copy(self, rs):
        site_down(rs)
        saved_at = NOW - timedelta(days=1)
        store = FakeSnapshots({KEY: (saved_at, POLYAK)})
        source = make_source(store)
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_STALE, saved_at, ERROR_FETCH_FAILED)
        assert store.rows[KEY] == (saved_at, POLYAK)

    def test_site_down_without_copy(self, rs):
        site_down(rs)
        source = make_source(FakeSnapshots())
        with pytest.raises(ScheduleUnavailable, match=ERROR_FETCH_FAILED):
            source.lessons_on(MONDAY, NOW)
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_UNAVAILABLE, None, ERROR_FETCH_FAILED)

    def test_http_error_is_fetch_failure(self, rs):
        serve(rs, "<html>Service Unavailable</html>", status=503)
        saved_at = NOW - timedelta(hours=2)
        source = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)}))
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert source.status(NOW).error == ERROR_FETCH_FAILED

    def test_copy_usable_for_14_days(self, rs):
        site_down(rs)
        saved_at = NOW - guap.SNAPSHOT_MAX_AGE
        source = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)}))
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert source.status(NOW).state == STATE_STALE

    def test_copy_older_than_14_days(self, rs):
        site_down(rs)
        saved_at = NOW - guap.SNAPSHOT_MAX_AGE - timedelta(minutes=1)
        source = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)}))
        with pytest.raises(ScheduleUnavailable, match=ERROR_SNAPSHOT_TOO_OLD):
            source.lessons_on(MONDAY, NOW)
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (
            STATE_UNAVAILABLE, saved_at, ERROR_SNAPSHOT_TOO_OLD)

    def test_cached_page_expires_after_14_days_too(self, rs):
        serve(rs)
        source = make_source()
        source.lessons_on(MONDAY, NOW)
        rs.replace(responses.GET, URL, body=requests.ConnectionError("down"))
        with pytest.raises(ScheduleUnavailable, match=ERROR_SNAPSHOT_TOO_OLD):
            source.lessons_on(MONDAY, NOW + timedelta(days=15))

    @pytest.mark.parametrize("body, code", [
        (page("invalid_gr_1"), ERROR_NO_LESSONS),
        (page("rasp_index"), ERROR_BAD_PAGE),
        (POLYAK.replace("▼&nbsp;10&nbsp;нижняя (четная) неделя", ""), ERROR_BAD_PAGE),
        (POLYAK.replace('id="result"', 'id="results"'), ERROR_BAD_PAGE),
        (POLYAK.replace("?pr=39614", "?pr=1"), ERROR_NO_LESSONS),
        ("<html><body>Технические работы</body></html>", ERROR_BAD_PAGE),
        ("", ERROR_BAD_PAGE),
    ], ids=["no_match", "empty_form", "no_week", "no_banner", "other_teacher", "stub", "empty"])
    def test_rejected_page_keeps_copy(self, rs, body, code):
        serve(rs, body)
        saved_at = NOW - timedelta(hours=1)
        store = FakeSnapshots({KEY: (saved_at, POLYAK)})
        source = make_source(store)
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert store.rows[KEY] == (saved_at, POLYAK)
        assert store.saves == []
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_STALE, saved_at, code)

    def test_rejected_page_without_copy(self, rs):
        serve(rs, page("invalid_gr_1"))
        with pytest.raises(ScheduleUnavailable, match=ERROR_NO_LESSONS):
            make_source(FakeSnapshots()).lessons_on(MONDAY, NOW)

    def test_pause_after_error(self, rs):
        site_down(rs)
        source = make_source(FakeSnapshots())
        with pytest.raises(ScheduleUnavailable):
            source.lessons_on(MONDAY, NOW)
        rs.replace(responses.GET, URL, body=POLYAK)
        with pytest.raises(ScheduleUnavailable):
            source.lessons_on(MONDAY, NOW + timedelta(minutes=4, seconds=59))
        assert len(rs.calls) == 1
        assert len(source.lessons_on(MONDAY, NOW + guap.RETRY_AFTER_ERROR)) == 2
        assert len(rs.calls) == 2
        assert source.status(NOW + guap.RETRY_AFTER_ERROR).state == STATE_OK

    def test_cache_ttl(self, rs):
        serve(rs)
        store = FakeSnapshots()
        source = make_source(store)
        source.lessons_on(MONDAY, NOW)
        source.lessons_on(TUESDAY, NOW + timedelta(minutes=29))
        source.status(NOW + timedelta(minutes=29))
        assert len(rs.calls) == 1
        source.lessons_on(MONDAY, NOW + guap.CACHE_TTL)
        assert len(rs.calls) == 2
        assert store.rows[KEY][0] == NOW + guap.CACHE_TTL

    def test_cache_shared_by_courses_of_one_teacher(self, rs):
        serve(rs)
        make_source().lessons_on(MONDAY, NOW)
        assert make_source(lesson_types=[LAB, "Лекция"]).lessons_on(TUESDAY, NOW)
        assert len(rs.calls) == 1

    def test_restart_with_fresh_copy_needs_no_network(self, rs):
        saved_at = NOW - timedelta(minutes=10)
        source = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)}))
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert len(rs.calls) == 0
        status = source.status(NOW)
        assert (status.state, status.fetched_at) == (STATE_OK, saved_at)

    def test_restart_with_old_copy_downloads(self, rs):
        serve(rs)
        store = FakeSnapshots({KEY: (NOW - timedelta(hours=2), POLYAK)})
        make_source(store).lessons_on(MONDAY, NOW)
        assert len(rs.calls) == 1
        assert store.rows[KEY][0] == NOW

    def test_second_process_copy_picked_up(self, rs):
        serve(rs)
        store = FakeSnapshots()
        source = make_source(store)
        source.lessons_on(MONDAY, NOW)
        # Другой процесс backend скачал более новую сборку и сохранил её в БД.
        store.save_snapshot(KEY, NOW + timedelta(minutes=10), POLYAK.replace("(15:10—16:40)", "(15:20—16:50)"))
        lessons = source.lessons_on(MONDAY, NOW + timedelta(minutes=15))
        assert summary(lessons)[0][:2] == ("15:20", "16:50")
        assert len(rs.calls) == 1
        assert source.status(NOW + timedelta(minutes=15)).fetched_at == NOW + timedelta(minutes=10)

    def test_second_process_success_clears_stale_state(self, rs):
        site_down(rs)
        store = FakeSnapshots({KEY: (NOW - timedelta(hours=1), POLYAK)})
        source = make_source(store)
        source.lessons_on(MONDAY, NOW)
        assert source.status(NOW).state == STATE_STALE
        store.save_snapshot(KEY, NOW + timedelta(minutes=1), POLYAK)
        status = source.status(NOW + timedelta(minutes=2))
        assert (status.state, status.error) == (STATE_OK, None)
        assert len(rs.calls) == 1

    def test_broken_saved_copy_ignored(self, rs):
        serve(rs)
        store = FakeSnapshots({KEY: (NOW - timedelta(minutes=5), "<html>garbage</html>")})
        source = make_source(store)
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert len(rs.calls) == 1
        assert store.rows[KEY] == (NOW, POLYAK)

    def test_storage_failure_does_not_break_schedule(self, rs):
        serve(rs)
        source = make_source(BrokenSnapshots())
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert source.status(NOW).state == STATE_OK

    def test_unreadable_copy_falls_back_to_site(self, rs):
        class UnreadableCopy(FakeSnapshots):
            def load_snapshot(self, key):
                raise OSError("database is locked")

        serve(rs)
        store = UnreadableCopy({KEY: (NOW - timedelta(minutes=5), POLYAK)})
        assert len(make_source(store).lessons_on(MONDAY, NOW)) == 2
        assert len(rs.calls) == 1

    def test_parser_crash_is_bad_page(self, rs, monkeypatch):
        def crash(*args, **kwargs):
            raise AttributeError("'NoneType' object has no attribute 'get'")

        serve(rs)
        monkeypatch.setattr(guap, "parse_teacher_page", crash)
        source = make_source(None)
        with pytest.raises(ScheduleUnavailable, match=ERROR_BAD_PAGE):
            source.lessons_on(MONDAY, NOW)
        assert source.status(NOW).error == ERROR_BAD_PAGE

    def test_without_store(self, rs):
        serve(rs)
        source = make_source(None)
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert source.status(NOW).state == STATE_OK

    def test_reset_caches_forgets_page(self, rs):
        serve(rs)
        make_source().lessons_on(MONDAY, NOW)
        reset_schedule_caches()
        make_source().lessons_on(MONDAY, NOW)
        assert len(rs.calls) == 2


class TestSingleFlight:
    def _slow_site(self, rs, started, release):
        def callback(request):
            started.set()
            release.wait(5)
            return 200, {"Content-Type": "text/html; charset=utf-8"}, POLYAK
        rs.add_callback(responses.GET, URL, callback=callback)

    def test_copy_served_while_download_in_progress(self, rs):
        started, release = threading.Event(), threading.Event()
        self._slow_site(rs, started, release)
        store = FakeSnapshots({KEY: (NOW - timedelta(hours=1), POLYAK)})
        results = {}

        def first():
            results["first"] = make_source(store).lessons_on(MONDAY, NOW)

        worker = threading.Thread(target=first)
        worker.start()
        assert started.wait(5)
        # Пока первая загрузка висит, второй запрос сразу получает копию.
        assert len(make_source(store).lessons_on(MONDAY, NOW)) == 2
        release.set()
        worker.join(5)
        assert len(results["first"]) == 2
        assert len(rs.calls) == 1

    def test_one_download_for_concurrent_requests(self, rs):
        started, release = threading.Event(), threading.Event()
        self._slow_site(rs, started, release)
        results = []

        def call():
            results.append(make_source(FakeSnapshots()).lessons_on(MONDAY, NOW))

        workers = [threading.Thread(target=call) for _ in range(3)]
        workers[0].start()
        assert started.wait(5)
        for w in workers[1:]:
            w.start()
        time_module.sleep(0.1)
        release.set()
        for w in workers:
            w.join(5)
        assert [len(r) for r in results] == [2, 2, 2]
        assert len(rs.calls) == 1


class TestSlowSite:
    """Медленный или зависший сайт держит не дольше нескольких секунд один запрос, а не всю очередь."""

    def test_dripping_page_cut_by_budget(self, rs, monkeypatch):
        monkeypatch.setattr(guap, "FETCH_BUDGET_SECONDS", 0.3)
        serve_drip(rs)
        store = FakeSnapshots()
        source = make_source(store)
        result, seconds = timed(lambda: source.lessons_on(MONDAY, NOW))
        assert isinstance(result, ScheduleUnavailable) and str(result) == ERROR_FETCH_FAILED
        assert seconds < 1.5
        assert store.saves == []
        status = source.status(NOW)
        assert (status.state, status.error) == (STATE_UNAVAILABLE, ERROR_FETCH_FAILED)
        # Пауза после ошибки: следующий запрос на сайт не идёт.
        result, seconds = timed(lambda: source.lessons_on(MONDAY, NOW + timedelta(minutes=1)))
        assert isinstance(result, ScheduleUnavailable)
        assert seconds < 0.25
        assert len(rs.calls) == 1

    def test_dripping_page_with_copy(self, rs, monkeypatch):
        monkeypatch.setattr(guap, "FETCH_BUDGET_SECONDS", 0.3)
        serve_drip(rs)
        saved_at = NOW - timedelta(hours=1)
        source = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)}))
        result, seconds = timed(lambda: source.lessons_on(MONDAY, NOW))
        assert len(result) == 2
        assert seconds < 1.5
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_STALE, saved_at, ERROR_FETCH_FAILED)

    def test_page_within_budget_is_taken(self, rs, monkeypatch):
        # Срок - на всю загрузку, а не на каждое чтение: страница, пришедшая
        # по кускам вовремя, принимается целиком.
        monkeypatch.setattr(guap, "READ_CHUNK", 512)
        serve(rs)
        assert len(make_source().lessons_on(MONDAY, NOW)) == 2

    def test_without_copy_waits_shortly_then_pauses(self, rs, monkeypatch):
        monkeypatch.setattr(guap, "FETCH_WAIT_SECONDS", 0.5)
        started, release = threading.Event(), threading.Event()
        hang_site(rs, started, release)
        results = {}

        def first():
            results["first"] = make_source(FakeSnapshots()).lessons_on(MONDAY, NOW)

        worker = threading.Thread(target=first)
        worker.start()
        try:
            assert started.wait(5)
            source = make_source(FakeSnapshots())
            # Копии нет, страницу качает другой запрос: ждём недолго.
            result, seconds = timed(lambda: source.lessons_on(MONDAY, NOW))
            assert isinstance(result, ScheduleUnavailable) and str(result) == ERROR_FETCH_FAILED
            assert 0.45 < seconds < 2
            # Дальше - сразу, не ожидая и не качая второй раз.
            result, seconds = timed(lambda: source.lessons_on(MONDAY, NOW + timedelta(seconds=10)))
            assert isinstance(result, ScheduleUnavailable)
            assert seconds < 0.25
            status, seconds = timed(lambda: source.status(NOW + timedelta(seconds=10)))
            assert (status.state, status.error) == (STATE_UNAVAILABLE, ERROR_FETCH_FAILED)
            assert seconds < 0.25
        finally:
            release.set()
            worker.join(5)
        # Загрузка закончилась: её страница заменила паузу.
        assert len(results["first"]) == 2
        assert len(make_source().lessons_on(MONDAY, NOW + timedelta(seconds=20))) == 2
        assert len(rs.calls) == 1

    def test_status_does_not_download(self, rs):
        serve(rs)
        status = make_source(FakeSnapshots()).status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_UNAVAILABLE, None, ERROR_FETCH_FAILED)
        saved_at = NOW - timedelta(hours=1)
        status = make_source(FakeSnapshots({KEY: (saved_at, POLYAK)})).status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_OK, saved_at, None)
        assert len(rs.calls) == 0

    def test_status_does_not_wait_for_download(self, rs):
        started, release = threading.Event(), threading.Event()
        hang_site(rs, started, release)
        worker = threading.Thread(target=lambda: timed(lambda: make_source().lessons_on(MONDAY, NOW)))
        worker.start()
        try:
            assert started.wait(5)
            status, seconds = timed(lambda: make_source().status(NOW))
            assert status.state == STATE_UNAVAILABLE
            assert seconds < 0.25
        finally:
            release.set()
            worker.join(5)

    def test_dripping_headers_cut_by_budget(self, header_drip, monkeypatch):
        # requests.get возвращается только после всех заголовков, срок
        # покрывает и их: запрос не висит, замок загрузки свободен.
        monkeypatch.setattr(guap, "FETCH_BUDGET_SECONDS", 0.5)
        source = guap_source(FakeSnapshots(), header_drip.url)
        result, seconds = timed_or_hung(lambda: source.lessons_on(MONDAY, NOW))
        assert isinstance(result, ScheduleUnavailable) and str(result) == ERROR_FETCH_FAILED
        assert 0.45 < seconds < 1.5
        assert not guap._fetch_lock(KEY).locked()
        status = source.status(NOW)
        assert (status.state, status.error) == (STATE_UNAVAILABLE, ERROR_FETCH_FAILED)

    def test_dripping_headers_with_copy(self, header_drip, monkeypatch):
        monkeypatch.setattr(guap, "FETCH_BUDGET_SECONDS", 0.5)
        saved_at = NOW - timedelta(hours=1)
        source = guap_source(FakeSnapshots({KEY: (saved_at, POLYAK)}), header_drip.url)
        result, seconds = timed_or_hung(lambda: source.lessons_on(MONDAY, NOW))
        assert len(result) == 2
        assert seconds < 1.5
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_STALE, saved_at, ERROR_FETCH_FAILED)

    def test_hanging_download_blocks_new_ones(self, header_drip, monkeypatch):
        monkeypatch.setattr(guap, "FETCH_BUDGET_SECONDS", 0.3)
        source = guap_source(FakeSnapshots(), header_drip.url)
        timed_or_hung(lambda: source.lessons_on(MONDAY, NOW))
        assert len(guap._stuck_fetches) == 1 and guap._stuck_fetches[0].is_alive()
        threads = threading.active_count()
        # После паузы поток прошлой загрузки ещё висит: новый не запускается,
        # ответ сразу, с копией - по копии.
        later = NOW + guap.RETRY_AFTER_ERROR
        result, seconds = timed(lambda: source.lessons_on(MONDAY, later))
        assert isinstance(result, ScheduleUnavailable) and str(result) == ERROR_FETCH_FAILED
        assert seconds < 0.25
        with_copy = guap_source(FakeSnapshots({KEY: (NOW - timedelta(hours=1), POLYAK)}), header_drip.url)
        result, seconds = timed(lambda: with_copy.lessons_on(MONDAY, later + guap.RETRY_AFTER_ERROR))
        assert len(result) == 2
        assert seconds < 0.25
        assert threading.active_count() == threads
        assert header_drip.connections == 1
        assert not guap._fetch_lock(KEY).locked()
        # Сайт закрыл соединение, поток закончился: следующая загрузка идёт без рестарта.
        header_drip.release.set()
        stuck = guap._stuck_fetches[0]
        stuck.join(5)
        assert not stuck.is_alive()
        assert len(source.lessons_on(MONDAY, later + 3 * guap.RETRY_AFTER_ERROR)) == 2
        assert header_drip.connections == 2
        assert source.status(later + 3 * guap.RETRY_AFTER_ERROR).state == STATE_OK

    def test_page_size_limit(self, rs, monkeypatch):
        size = len(POLYAK.encode())
        monkeypatch.setattr(guap, "MAX_PAGE_BYTES", size)
        serve(rs)
        assert len(make_source().lessons_on(MONDAY, NOW)) == 2

        reset_schedule_caches()
        monkeypatch.setattr(guap, "MAX_PAGE_BYTES", size - 1)
        saved_at = NOW - timedelta(hours=1)
        store = FakeSnapshots({KEY: (saved_at, POLYAK)})
        source = make_source(store)
        assert len(source.lessons_on(MONDAY, NOW)) == 2
        assert store.saves == []
        status = source.status(NOW)
        assert (status.state, status.fetched_at, status.error) == (STATE_STALE, saved_at, ERROR_BAD_PAGE)

    def test_huge_page_rejected(self, rs):
        serve(rs, POLYAK + " " * guap.MAX_PAGE_BYTES)
        with pytest.raises(ScheduleUnavailable, match=ERROR_BAD_PAGE):
            make_source(FakeSnapshots()).lessons_on(MONDAY, NOW)

    def test_whole_download_fits_frontend_timeout(self):
        # Загрузку ждёт один запрос и не дольше срока; ожидание чужой загрузки
        # без копии кончается раньше.
        assert guap.FETCH_WAIT_SECONDS + guap.FETCH_BUDGET_SECONDS < 10
        assert guap.FETCH_WAIT_SECONDS <= 1


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        source = make_source(subject=None).source
        assert isinstance(source, guap.GuapSource)
        assert (source.teacher_id, source.subject, source.lesson_types) == (39614, None, (LAB,))
        assert source.url == URL

    def test_values_normalized(self):
        source = make_source(teacher_id=" 39614 ", subject=" Операционные\xa0 системы ",
                             lesson_types="Лабораторное  занятие").source
        assert (source.teacher_id, source.subject, source.lesson_types) == (39614, OS, (LAB,))

    @pytest.mark.parametrize("cfg", [
        {"teacher_id": None},
        {"teacher_id": True},
        {"teacher_id": 0},
        {"teacher_id": -5},
        {"teacher_id": "abc"},
        {"teacher_id": "39614&gr=1"},
        {"teacher_id": 39614.0},
        {"subject": 5},
        {"subject": "  "},
        {"lesson_types": []},
        {"lesson_types": [""]},
        {"lesson_types": [1]},
        {"lesson_types": {"Лабораторное занятие": 1}},
    ])
    def test_errors(self, cfg):
        with pytest.raises(ScheduleConfigError):
            make_source(**cfg)

    def test_unknown_key(self):
        with pytest.raises(ScheduleConfigError, match="teacher_id"):
            build_schedule_source({"source": "guap", "teacher_id": 39614}, SourceContext(None, MSK))

    def test_config_check_does_not_touch_network(self, rs):
        make_source(None)
        assert len(rs.calls) == 0

    @pytest.mark.parametrize("hours", [-6, -2, 0, 3, 5])
    def test_course_timezone_within_one_day(self, hours):
        assert make_source(tz=timezone(timedelta(hours=hours))).name == "guap"

    @pytest.mark.parametrize("hours, name", [
        (6, "UTC+6"), (8, "UTC+8"), (14, "UTC+14"), (-7, "UTC-7"), (-12, "UTC-12"),
    ])
    def test_course_timezone_too_far(self, hours, name):
        with pytest.raises(ScheduleConfigError, match=re.escape(f"timezone курса {name}:")) as e:
            make_source(tz=timezone(timedelta(hours=hours)))
        assert "от UTC-6 до UTC+5" in str(e.value)

    def test_timezone_range_is_where_the_grid_fits_one_day(self):
        # С 09:30 (1-я пара) до 21:50 (7-я) по Москве - один местный день
        # ровно в поясах COURSE_UTC_OFFSETS.
        first, last = suai_rasp.LESSON_TIMES[1][0], suai_rasp.LESSON_TIMES[7][1]
        assert (first, last) == ("09:30", "21:50")

        def fits(hours):
            tz = timezone(timedelta(hours=hours))
            starts = guap._at(MONDAY, first).astimezone(tz)
            ends = guap._at(MONDAY, last).astimezone(tz)
            return starts.date() == ends.date()

        low, high = (int(o / timedelta(hours=1)) for o in guap.COURSE_UTC_OFFSETS)
        assert [h for h in range(-12, 15) if fits(h)] == list(range(low, high + 1))

    def test_real_course_with_far_timezone_rejected(self):
        # Курс ОС с поясом UTC+8: 6-я пара 18:40-20:10 МСК шла бы через
        # местную полночь, и запись гасла бы посреди пары.
        text = (REPO / "courses" / "operating-systems-2026.yaml").read_text(encoding="utf-8")
        course = yaml.safe_load(text)["course"]
        assert course["queue"]["schedule"]["source"] == "guap"
        assert parse_queue_config(course) is not None
        course["timezone"] = "UTC+8"
        with pytest.raises(QueueConfigError, match="timezone курса UTC\\+8"):
            parse_queue_config(course)
