"""
Secret join links and availability windows for labs.

Continuation of the /join student repo creation flow (see
docs/REPO_GENERATION_PLAN.md); the full design lives in
docs/SECRET_JOIN_LINKS_PLAN.md.

A lab gains two independent properties here:

  * `join.link: secret` - the lab is reachable only through an unguessable
    `/j/{token}` address, and `/join/{course_id}/{lab_id}` stops working for
    it entirely;
  * `join.opens-at` / `join.closes-at` - the window during which the link
    hands out repositories.

The token is computed, never stored: `HMAC-SHA256(SECRET_KEY, join_id \n
revision)` truncated to 10 base32 characters (§3.1 of the plan). Nothing
secret therefore reaches git, no new storage appears, and the server can
always rebuild the link from the config - which is what the admin page and
the post-OAuth redirect need.

Like grading/teams.py, this module knows nothing about FastAPI and never
touches Google Sheets. Course enumeration is not done here either: callers
pass an iterable of (course_id, course_info) pairs, which in the server is
main.iter_course_configs() - the single enumeration point the config storage
migration will have to change (§13 of the plan).
"""
import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .sheets_client import parse_deadline
from .teams import is_team_lab

logger = logging.getLogger(__name__)

# Token length and alphabet (§3.1). 10 characters of base32 (RFC 4648, lower
# case) are 50 bits - as unguessable as a GitHub Classroom link, while the
# alphabet has no 0/1/8/9, so O/0 and l/1 cannot be confused when the link is
# dictated or typed by hand.
TOKEN_LENGTH = 10
TOKEN_RE = re.compile(rf"^[a-z2-7]{{{TOKEN_LENGTH}}}$")

# Values of `join.link`.
LINK_PUBLIC = "public"
LINK_SECRET = "secret"
LINK_VALUES = (LINK_PUBLIC, LINK_SECRET)

DEFAULT_REVISION = 1

# States of the availability window, as reported to the admin page and to the
# student's landing page.
STATE_NOT_OPEN = "not_open"
STATE_OPEN = "open"
STATE_CLOSED = "closed"


class JoinConfigError(Exception):
    """A lab's `join` section is present but malformed."""


@dataclass(frozen=True)
class JoinSettings:
    """Parsed `join` section of a lab config."""
    link: str = LINK_PUBLIC
    revision: int = DEFAULT_REVISION
    join_id: str | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None

    @property
    def secret(self) -> bool:
        return self.link == LINK_SECRET


@dataclass(frozen=True)
class JoinWindow:
    """
    The availability window of a lab, answering the three questions the rest
    of the code asks: may the link be used now, may a new repository still be
    created, and should the lab appear in the student's lab list.

    `secret` matters because the default differs: a lab with no `opens-at`
    is open when its link is public (that is how every lab behaves today) and
    NOT open when the link is secret - otherwise a test committed to the
    config in advance would go live the moment it is deployed (§3.3).
    """
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    secret: bool = False

    def state(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        now = _as_aware(now)

        if self.opens_at is None:
            if self.secret:
                return STATE_NOT_OPEN
        elif now < _as_aware(self.opens_at):
            return STATE_NOT_OPEN

        # Exactly at closes-at the lab is still open: the boundary belongs to
        # the window, the same way it does at opens-at above.
        if self.closes_at is not None and now > _as_aware(self.closes_at):
            return STATE_CLOSED

        return STATE_OPEN

    def is_visible(self, now: datetime | None = None) -> bool:
        """Whether the lab may be shown and graded publicly (§5 of the plan)."""
        return self.state(now) != STATE_NOT_OPEN

    def accepts_new_repos(self, now: datetime | None = None) -> bool:
        """Whether a repository may still be created for a student."""
        return self.state(now) == STATE_OPEN


def _as_aware(value: datetime) -> datetime:
    """
    Treat a naive datetime as UTC.

    parse_deadline() leaves a value naive when the course has no `timezone`
    (or an unparseable one); comparing it with an aware "now" would raise.
    The course config is the place to fix that, so this only keeps the
    comparison working and is never a substitute for `timezone`.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _join_section(lab_config: dict | None) -> dict:
    """
    The lab's `join` section as a mapping.

    Raises:
        JoinConfigError: the section is present but is not a mapping
    """
    if not isinstance(lab_config, dict):
        return {}
    raw = lab_config.get("join")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise JoinConfigError("Секция join лабораторной работы должна быть словарём")
    return raw


def _revision_of(raw: dict) -> int:
    """
    `join.revision`, validated.

    Kept apart from the rest of the parsing because the token depends on this
    field and on `join.id` alone: a typo in `opens-at` must surface as a
    configuration error, never as a silently revoked link in the middle of a
    test (see lab_token).

    Raises:
        JoinConfigError: not an integer, or smaller than 1
    """
    revision = raw.get("revision", DEFAULT_REVISION)
    # bool is an int subclass, and `revision: yes` in YAML is a bool.
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise JoinConfigError(
            f"Некорректное значение join.revision: {revision!r} (ожидается целое число не меньше 1)"
        )
    return revision


def is_secret_lab(lab_config: dict | None) -> bool:
    """
    Whether the lab is reachable only through a secret link.

    Deliberately total: a malformed section answers False here and is
    reported by parse_join_config() instead. Public paths never rely on this
    function alone - they go through a full parse and hide anything that does
    not parse (see main.find_public_lab_config).
    """
    try:
        raw = _join_section(lab_config)
    except JoinConfigError:
        return False
    value = raw.get("link")
    return isinstance(value, str) and value.strip().lower() == LINK_SECRET


def parse_join_config(lab_config: dict | None, timezone_str: str | None = None) -> JoinSettings:
    """
    Parse and validate the `join` section of a lab config (§4 of the plan).

    Args:
        lab_config: the lab's config mapping
        timezone_str: the course's `timezone` ("UTC+3"), applied to naive
            `opens-at` / `closes-at` values

    Returns:
        JoinSettings - the defaults (public, revision 1, no window) for a lab
        with no `join` section at all, so existing labs are unaffected

    Raises:
        JoinConfigError: unknown `link`, non-positive or non-integer
        `revision`, unparseable dates, `closes-at` before `opens-at`, or a
        team lab asking for a secret link (not supported in this iteration,
        §12)
    """
    raw = _join_section(lab_config)
    if not raw:
        return JoinSettings()

    link = raw.get("link", LINK_PUBLIC)
    if not isinstance(link, str) or link.strip().lower() not in LINK_VALUES:
        raise JoinConfigError(
            f"Некорректное значение join.link: {link!r} (допустимо: {', '.join(LINK_VALUES)})"
        )
    link = link.strip().lower()

    revision = _revision_of(raw)

    join_id = raw.get("id")
    if join_id is not None:
        if not isinstance(join_id, str) or not join_id.strip():
            raise JoinConfigError("Некорректное значение join.id: ожидается непустая строка")
        join_id = join_id.strip()

    opens_at = _parse_moment(raw, "opens-at", timezone_str)
    closes_at = _parse_moment(raw, "closes-at", timezone_str)

    if opens_at is not None and closes_at is not None and closes_at < opens_at:
        raise JoinConfigError("join.closes-at раньше join.opens-at")

    if link == LINK_SECRET and is_team_lab(lab_config):
        # The team endpoints address a lab by course/lab pair and check the
        # join_session cookie against the path; moving them onto a token is a
        # separate task (§12 of the plan).
        raise JoinConfigError(
            "Командная лабораторная работа пока не может иметь секретную ссылку "
            "(join.link: secret вместе с секцией team)"
        )

    return JoinSettings(
        link=link,
        revision=revision,
        join_id=join_id,
        opens_at=opens_at,
        closes_at=closes_at,
    )


def _parse_moment(raw: dict, key: str, timezone_str: str | None) -> datetime | None:
    """
    One `opens-at` / `closes-at` value, in the course timezone.

    Parsing is done by the same grading.sheets_client.parse_deadline that
    reads deadlines out of the spreadsheet, so the accepted formats are the
    ones a teacher already knows. A value without a time component therefore
    means the END of that day (23:59:59), exactly as a deadline does - which
    is why docs/COURSE_CONFIG.md tells teachers to spell the time out.
    """
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None

    if isinstance(value, datetime):
        # PyYAML parses an unquoted `2026-10-15 10:00:00` into a datetime.
        return _as_aware(value)

    if not isinstance(value, str):
        raise JoinConfigError(f"Некорректное значение join.{key}: {value!r} (ожидается дата и время)")

    parsed = parse_deadline(value, timezone_str)
    if parsed is None:
        raise JoinConfigError(
            f"Не удалось разобрать join.{key}: {value!r} "
            "(ожидается, например, \"2026-10-15 10:00\")"
        )
    return _as_aware(parsed)


def parse_window(lab_config: dict | None, timezone_str: str | None = None) -> JoinWindow:
    """
    The lab's availability window (§3.3 of the plan).

    Raises:
        JoinConfigError: the `join` section does not parse
    """
    settings = parse_join_config(lab_config, timezone_str)
    return JoinWindow(
        opens_at=settings.opens_at,
        closes_at=settings.closes_at,
        secret=settings.secret,
    )


def join_identity(course_id: str, lab_key: str, lab_config: dict | None) -> str:
    """
    The stable identity of a lab's link - what the token is computed from.

    By default it is the course id plus the lab's key in the YAML, which is
    enough while configs are edited through git. An explicit `join.id` frees
    the link from both names, so that renaming a course or re-keying a lab in
    a future admin editor does not silently break an already distributed link
    (§3.1, §13).
    """
    raw = _join_section(lab_config)
    explicit = raw.get("id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return f"{course_id}\n{lab_key}"


def compute_token(secret_key: str, join_id: str, revision: int = DEFAULT_REVISION) -> str:
    """
    The link token: base32(HMAC-SHA256(secret_key, "{join_id}\\n{revision}"))
    truncated to TOKEN_LENGTH characters, lower case.

    Truncating an HMAC is the standard construction (HOTP does the same) and
    costs nothing beyond the bits dropped: without SECRET_KEY a guess can
    only be tested against the server, so there is no offline search.
    """
    digest = hmac.new(
        secret_key.encode("utf-8"),
        f"{join_id}\n{revision}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b32encode(digest).decode("ascii").lower()[:TOKEN_LENGTH]


def lab_token(secret_key: str, course_id: str, lab_key: str, lab_config: dict | None) -> str:
    """
    The token of one lab, straight from its config.

    Only the two fields the token is built from are validated here. A lab
    whose window does not parse keeps its link working and answers
    LAB_MISCONFIGURED - a typo in `closes-at` must not read as "this link no
    longer exists", which is what a failure here would look like from the
    outside.

    Raises:
        JoinConfigError: `join` is not a mapping, or `revision` is invalid
    """
    return compute_token(
        secret_key,
        join_identity(course_id, lab_key, lab_config),
        _revision_of(_join_section(lab_config)),
    )


def iter_secret_labs(
    course_configs: Iterable[tuple[str, dict]],
) -> list[tuple[str, str, dict]]:
    """
    Every lab with a secret link, as (course_id, lab_key, lab_config).

    A lab whose `join` section is malformed is skipped with a warning rather
    than taking the whole enumeration down - the request that names it gets
    LAB_MISCONFIGURED of its own.
    """
    found = []
    for course_id, course_info in course_configs:
        labs = (course_info or {}).get("labs")
        if not isinstance(labs, dict):
            continue
        for lab_key, lab_config in labs.items():
            if not is_secret_lab(lab_config):
                continue
            found.append((course_id, str(lab_key), lab_config))
    return found


def resolve_token(
    secret_key: str,
    token: str | None,
    course_configs: Iterable[tuple[str, dict]],
) -> tuple[str, str] | None:
    """
    Find the lab a link token belongs to.

    Args:
        secret_key: the server's SECRET_KEY
        token: the token from the URL - untrusted input
        course_configs: (course_id, course_info) pairs, from
            main.iter_course_configs()

    Returns:
        (course_id, lab_key), or None if the token is malformed, revoked
        (`revision` was raised) or simply belongs to nothing

    The format is checked by TOKEN_RE before anything is enumerated, so junk
    costs nothing, and the comparison itself is always
    `secrets.compare_digest`. The token is never logged - a log line names
    the course and the lab key instead.
    """
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None

    for course_id, lab_key, lab_config in iter_secret_labs(course_configs):
        try:
            candidate = lab_token(secret_key, course_id, lab_key, lab_config)
        except JoinConfigError as e:
            logger.warning("Секретная ссылка курса '%s', лаба '%s': %s", course_id, lab_key, e)
            continue
        if secrets.compare_digest(candidate, token):
            return course_id, lab_key

    return None


def check_token_collisions(
    secret_key: str,
    course_configs: Iterable[tuple[str, dict]],
) -> list[str]:
    """
    Labs whose secret links collide, as ready-to-log messages.

    At 50 bits a collision is vanishingly unlikely, but it would resolve a
    link to the wrong lab silently; three lines turn that into a startup
    error a teacher can fix by raising `join.revision`.
    """
    seen: dict[str, tuple[str, str]] = {}
    problems: list[str] = []

    for course_id, lab_key, lab_config in iter_secret_labs(course_configs):
        try:
            token = lab_token(secret_key, course_id, lab_key, lab_config)
        except JoinConfigError as e:
            problems.append(f"курс '{course_id}', лаба '{lab_key}': {e}")
            continue
        if token in seen:
            other_course, other_lab = seen[token]
            problems.append(
                f"курс '{course_id}', лаба '{lab_key}': секретная ссылка совпадает со ссылкой "
                f"курса '{other_course}', лаба '{other_lab}' - увеличьте join.revision"
            )
            continue
        seen[token] = (course_id, lab_key)

    return problems
