"""
Тесты секретных ссылок и окна доступности лабораторной работы.

См. docs/SECRET_JOIN_LINKS_PLAN.md §14 (список тестов) и §15 (чек-лист
приёмки). Модуль grading/join_links.py не знает ни о FastAPI, ни о Google
Sheets, поэтому тесты здесь работают с чистыми словарями конфига.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grading.join_links import (  # noqa: E402
    STATE_CLOSED,
    STATE_NOT_OPEN,
    STATE_OPEN,
    TOKEN_LENGTH,
    TOKEN_RE,
    JoinConfigError,
    check_token_collisions,
    compute_token,
    is_secret_lab,
    join_identity,
    lab_token,
    parse_join_config,
    parse_window,
    resolve_token,
)

SECRET = "test_secret_key"
OTHER_SECRET = "another_secret_key"

MSK = timezone(timedelta(hours=3))


def secret_lab(**overrides) -> dict:
    """Конфиг секретной лабы (контрольной) с окном."""
    join = {
        "link": "secret",
        "opens-at": "2026-10-15 10:00",
        "closes-at": "2026-10-15 11:30",
        "revision": 1,
    }
    join.update(overrides.pop("join", {}))
    lab = {
        "github-prefix": "kr1",
        "short-name": "Тест / КР",
        "template-repo": "org/kr1-template",
        "join": join,
    }
    lab.update(overrides)
    return lab


def course_with(labs: dict, **extra) -> dict:
    course = {"name": "ОС", "github": {"organization": "org"}, "labs": labs}
    course.update(extra)
    return course


class TestComputeToken:
    def test_is_deterministic(self):
        assert compute_token(SECRET, "os-2026\n7", 1) == compute_token(SECRET, "os-2026\n7", 1)

    def test_length_and_alphabet(self):
        token = compute_token(SECRET, "os-2026\n7", 1)
        assert len(token) == TOKEN_LENGTH == 10
        assert TOKEN_RE.match(token)

    def test_depends_on_course_id(self):
        assert compute_token(SECRET, "os-2026\n7", 1) != compute_token(SECRET, "stats-2026\n7", 1)

    def test_depends_on_lab_key(self):
        assert compute_token(SECRET, "os-2026\n7", 1) != compute_token(SECRET, "os-2026\n8", 1)

    def test_depends_on_revision(self):
        """Увеличение revision - это и есть отзыв ссылки."""
        assert compute_token(SECRET, "os-2026\n7", 1) != compute_token(SECRET, "os-2026\n7", 2)

    def test_depends_on_secret_key(self):
        """Смена SECRET_KEY отзывает все секретные ссылки (docs/DEPLOYMENT.md)."""
        assert compute_token(SECRET, "os-2026\n7", 1) != compute_token(OTHER_SECRET, "os-2026\n7", 1)

    def test_link_is_no_longer_than_a_github_classroom_link(self):
        """`https://<host>/j/<token>` не длиннее classroom.github.com/a/AbCd1234."""
        token = compute_token(SECRET, "os-2026\n7", 1)
        assert len(f"/j/{token}") <= len("/a/AbCd1234") + 2


class TestJoinIdentity:
    def test_defaults_to_course_and_lab_key(self):
        assert join_identity("os-2026", "7", secret_lab()) == "os-2026\n7"

    def test_explicit_id_overrides_names(self):
        lab = secret_lab(join={"id": "kr-2026-autumn"})
        assert join_identity("os-2026", "7", lab) == "kr-2026-autumn"

    def test_explicit_id_survives_course_rename_and_lab_rekey(self):
        """Ради этого join.id и существует (§3.1, §13 плана)."""
        lab = secret_lab(join={"id": "kr-2026-autumn"})
        before = lab_token(SECRET, "os-2026", "7", lab)
        after = lab_token(SECRET, "operating-systems-2026", "kr", lab)
        assert before == after

    def test_without_explicit_id_rename_changes_the_token(self):
        lab = secret_lab()
        assert lab_token(SECRET, "os-2026", "7", lab) != lab_token(SECRET, "os-2027", "7", lab)

    def test_blank_id_falls_back_to_course_and_lab_key(self):
        lab = secret_lab(join={"id": "   "})
        with pytest.raises(JoinConfigError):
            parse_join_config(lab)


class TestIsSecretLab:
    def test_lab_without_join_section_is_public(self):
        assert is_secret_lab({"github-prefix": "os-task1"}) is False

    def test_explicit_public_link(self):
        assert is_secret_lab({"join": {"link": "public"}}) is False

    def test_secret_link(self):
        assert is_secret_lab(secret_lab()) is True

    def test_case_and_spacing_are_tolerated(self):
        assert is_secret_lab({"join": {"link": " Secret "}}) is True

    def test_malformed_section_does_not_raise(self):
        assert is_secret_lab({"join": "secret"}) is False

    def test_none_config(self):
        assert is_secret_lab(None) is False


class TestParseJoinConfig:
    def test_lab_without_section_gets_defaults(self):
        settings = parse_join_config({"github-prefix": "os-task1"})
        assert settings.link == "public"
        assert settings.revision == 1
        assert settings.opens_at is None
        assert settings.closes_at is None
        assert settings.secret is False

    def test_unknown_link_value_is_a_config_error(self):
        with pytest.raises(JoinConfigError) as e:
            parse_join_config({"join": {"link": "sekret"}})
        assert "join.link" in str(e.value)

    def test_section_must_be_a_mapping(self):
        with pytest.raises(JoinConfigError):
            parse_join_config({"join": ["secret"]})

    @pytest.mark.parametrize("revision", [0, -1, "1", 1.5, True])
    def test_revision_must_be_a_positive_int(self, revision):
        with pytest.raises(JoinConfigError) as e:
            parse_join_config({"join": {"revision": revision}})
        assert "join.revision" in str(e.value)

    def test_unparseable_date_is_a_config_error(self):
        with pytest.raises(JoinConfigError) as e:
            parse_join_config({"join": {"opens-at": "завтра"}})
        assert "join.opens-at" in str(e.value)

    def test_closes_before_opens_is_a_config_error(self):
        lab = secret_lab(join={"opens-at": "2026-10-15 11:30", "closes-at": "2026-10-15 10:00"})
        with pytest.raises(JoinConfigError) as e:
            parse_join_config(lab)
        assert "closes-at" in str(e.value)

    def test_team_lab_with_secret_link_is_rejected(self):
        """§12 плана: командные лабы с секретной ссылкой пока не поддерживаются."""
        lab = secret_lab()
        lab["team"] = {"size-max": 4}
        with pytest.raises(JoinConfigError) as e:
            parse_join_config(lab)
        assert "team" in str(e.value)

    def test_team_lab_with_public_window_is_allowed(self):
        """Окно само по себе командной лабе не противоречит."""
        lab = {"team": {"size-max": 4}, "join": {"opens-at": "2026-10-15 10:00"}}
        settings = parse_join_config(lab)
        assert settings.secret is False
        assert settings.opens_at is not None

    def test_yaml_datetime_value_is_accepted(self):
        """PyYAML разбирает незакавыченную дату в datetime сам."""
        lab = {"join": {"link": "secret", "opens-at": datetime(2026, 10, 15, 10, 0)}}
        settings = parse_join_config(lab)
        assert settings.opens_at.year == 2026


class TestParseWindow:
    def test_naive_time_is_read_in_course_timezone(self):
        window = parse_window(secret_lab(), "UTC+3")
        assert window.opens_at == datetime(2026, 10, 15, 10, 0, tzinfo=MSK)
        assert window.closes_at == datetime(2026, 10, 15, 11, 30, tzinfo=MSK)

    def test_without_closes_at_the_link_never_expires(self):
        lab = secret_lab(join={"opens-at": "2026-10-15 10:00", "closes-at": None})
        window = parse_window(lab, "UTC+3")
        assert window.closes_at is None
        assert window.state(datetime(2030, 1, 1, tzinfo=MSK)) == STATE_OPEN

    def test_secret_lab_without_opens_at_is_closed(self):
        """Контрольная, закоммиченная заранее, не должна открыться при деплое."""
        lab = {"join": {"link": "secret"}}
        window = parse_window(lab, "UTC+3")
        assert window.state(datetime(2030, 1, 1, tzinfo=MSK)) == STATE_NOT_OPEN
        assert window.is_visible(datetime(2030, 1, 1, tzinfo=MSK)) is False

    def test_public_lab_without_opens_at_is_open(self):
        """Обратная совместимость: у обычной лабы отсутствие окна - «открыта»."""
        window = parse_window({"github-prefix": "os-task1"}, "UTC+3")
        assert window.state(datetime(2020, 1, 1, tzinfo=MSK)) == STATE_OPEN
        assert window.is_visible() is True
        assert window.accepts_new_repos() is True

    def test_before_opens_at(self):
        window = parse_window(secret_lab(), "UTC+3")
        now = datetime(2026, 10, 15, 9, 59, tzinfo=MSK)
        assert window.state(now) == STATE_NOT_OPEN
        assert window.is_visible(now) is False
        assert window.accepts_new_repos(now) is False

    def test_inside_the_window(self):
        window = parse_window(secret_lab(), "UTC+3")
        now = datetime(2026, 10, 15, 10, 30, tzinfo=MSK)
        assert window.state(now) == STATE_OPEN
        assert window.is_visible(now) is True
        assert window.accepts_new_repos(now) is True

    def test_after_closes_at(self):
        window = parse_window(secret_lab(), "UTC+3")
        now = datetime(2026, 10, 15, 11, 31, tzinfo=MSK)
        assert window.state(now) == STATE_CLOSED
        # Лаба остаётся в списке: работы сдают после окончания контрольной.
        assert window.is_visible(now) is True
        assert window.accepts_new_repos(now) is False

    def test_exactly_at_opens_at_is_open(self):
        window = parse_window(secret_lab(), "UTC+3")
        assert window.state(datetime(2026, 10, 15, 10, 0, tzinfo=MSK)) == STATE_OPEN

    def test_exactly_at_closes_at_is_still_open(self):
        window = parse_window(secret_lab(), "UTC+3")
        assert window.state(datetime(2026, 10, 15, 11, 30, tzinfo=MSK)) == STATE_OPEN

    def test_timezone_is_actually_applied(self):
        """Одно и то же наивное время в разных поясах - разные моменты."""
        msk = parse_window(secret_lab(), "UTC+3")
        utc = parse_window(secret_lab(), "UTC+0")
        assert msk.opens_at != utc.opens_at

    def test_naive_config_without_course_timezone_still_compares(self):
        """Без `timezone` курса время остаётся наивным - сравнение не должно падать."""
        window = parse_window(secret_lab(), None)
        assert window.state(datetime(2026, 10, 15, 10, 30, tzinfo=timezone.utc)) == STATE_OPEN

    def test_state_defaults_to_now(self):
        past = {"join": {"link": "secret", "opens-at": "2000-01-01 00:00"}}
        assert parse_window(past, "UTC+3").state() == STATE_OPEN


class TestResolveToken:
    def setup_method(self):
        self.labs = {
            "1": {"github-prefix": "os-task1", "short-name": "ЛР1"},
            "7": secret_lab(),
        }
        self.courses = [("os-2026", course_with(self.labs))]

    def test_finds_the_lab(self):
        token = lab_token(SECRET, "os-2026", "7", self.labs["7"])
        assert resolve_token(SECRET, token, self.courses) == ("os-2026", "7")

    def test_does_not_find_after_revision_bump(self):
        token = lab_token(SECRET, "os-2026", "7", self.labs["7"])
        self.labs["7"]["join"]["revision"] = 2
        assert resolve_token(SECRET, token, self.courses) is None
        # ...а новая ссылка работает
        new_token = lab_token(SECRET, "os-2026", "7", self.labs["7"])
        assert resolve_token(SECRET, new_token, self.courses) == ("os-2026", "7")

    def test_public_lab_is_never_resolved(self):
        """У обычной лабы секретной ссылки нет вовсе."""
        public = {"1": {"github-prefix": "os-task1"}}
        token = compute_token(SECRET, "os-2026\n1", 1)
        assert resolve_token(SECRET, token, [("os-2026", course_with(public))]) is None

    def test_other_secret_key_does_not_resolve(self):
        token = lab_token(OTHER_SECRET, "os-2026", "7", self.labs["7"])
        assert resolve_token(SECRET, token, self.courses) is None

    @pytest.mark.parametrize("junk", [
        "", None, "short", "TOOLONGTOKEN", "abcdefghi1", "abcdefgh01",
        "../../etc/passwd", "ABCDEFGHIJ", "abcdefghijk",
    ])
    def test_malformed_tokens_are_rejected_by_the_regex(self, junk):
        """Мусор отсеивается форматом до перебора курсов."""
        def exploding():
            raise AssertionError("перебор не должен был начаться")
            yield  # pragma: no cover

        assert resolve_token(SECRET, junk, exploding()) is None

    def test_lab_with_broken_join_section_is_skipped_not_fatal(self):
        labs = {
            "6": {"join": {"link": "secret", "revision": 0}},
            "7": secret_lab(),
        }
        courses = [("os-2026", course_with(labs))]
        token = lab_token(SECRET, "os-2026", "7", labs["7"])
        assert resolve_token(SECRET, token, courses) == ("os-2026", "7")

    def test_finds_the_lab_across_several_courses(self):
        other = {"7": secret_lab(join={"revision": 3})}
        courses = [("stats-2026", course_with(other)), ("os-2026", course_with(self.labs))]
        token = lab_token(SECRET, "os-2026", "7", self.labs["7"])
        assert resolve_token(SECRET, token, courses) == ("os-2026", "7")
        other_token = lab_token(SECRET, "stats-2026", "7", other["7"])
        assert resolve_token(SECRET, other_token, courses) == ("stats-2026", "7")


class TestCheckTokenCollisions:
    def test_no_collisions_for_distinct_labs(self):
        labs = {"7": secret_lab(), "8": secret_lab(join={"revision": 2})}
        assert check_token_collisions(SECRET, [("os-2026", course_with(labs))]) == []

    def test_same_join_id_in_two_labs_collides(self):
        labs = {
            "7": secret_lab(join={"id": "kr-2026"}),
            "8": secret_lab(join={"id": "kr-2026"}),
        }
        problems = check_token_collisions(SECRET, [("os-2026", course_with(labs))])
        assert len(problems) == 1
        assert "join.revision" in problems[0]

    def test_broken_section_is_reported_too(self):
        labs = {"7": {"join": {"link": "secret", "opens-at": "когда-нибудь"}}}
        problems = check_token_collisions(SECRET, [("os-2026", course_with(labs))])
        assert len(problems) == 1
        assert "opens-at" in problems[0]
