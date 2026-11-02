"""
Admin identity and per-course access (docs/MULTI_TEACHER_PLAN.md §5, §6).

Pure logic, no FastAPI: the dependencies built on top of it (`require_admin`,
`require_course_access`) live in main.py next to the routes.

- Identity comes from GitHub OAuth (or the emergency ADMIN_LOGIN/ADMIN_PASSWORD
  sign-in) and is carried in the signed `admin_session` cookie.
- Rights are NOT carried in the cookie beyond the role: a teacher's access to a
  course is recomputed from `course.github.teachers` on every request, so an
  edit of that list takes effect without signing in again.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLE_SUPERADMIN = "superadmin"
ROLE_TEACHER = "teacher"
ROLES = (ROLE_SUPERADMIN, ROLE_TEACHER)

VIA_GITHUB = "github"
VIA_PASSWORD = "password"
VIAS = (VIA_GITHUB, VIA_PASSWORD)


@dataclass(frozen=True)
class AdminIdentity:
    """Who is signed in to the admin panel, and how."""

    login: str
    role: str
    via: str

    @property
    def is_superadmin(self) -> bool:
        return self.role == ROLE_SUPERADMIN

    def to_dict(self) -> dict:
        return {"login": self.login, "role": self.role, "via": self.via}

    @classmethod
    def from_dict(cls, data) -> "AdminIdentity | None":
        """
        Build an identity from a decoded cookie payload.

        Returns:
            None for anything that is not a well-formed payload - including the
            old cookie format (a bare login string), which simply means
            "sign in again" (§5 of the plan).
        """
        if not isinstance(data, dict):
            return None
        login, role, via = data.get("login"), data.get("role"), data.get("via")
        if not isinstance(login, str) or not login:
            return None
        if role not in ROLES or via not in VIAS:
            return None
        return cls(login=login, role=role, via=via)


def parse_login_list(value: str | None) -> frozenset[str]:
    """`SUPERADMIN_GITHUB_LOGINS`: comma-separated, case-insensitive (casefolded)."""
    if not value:
        return frozenset()
    return frozenset(part.strip().casefold() for part in value.split(",") if part.strip())


def course_teachers(course_info) -> list[str]:
    """`course.github.teachers` - a mixed list of names and GitHub logins."""
    if not isinstance(course_info, dict):
        return []
    github = course_info.get("github")
    if not isinstance(github, dict):
        return []
    teachers = github.get("teachers") or []
    if not isinstance(teachers, list):
        return []
    return [str(entry) for entry in teachers if entry]


def is_course_teacher(course_info, login: str | None) -> bool:
    """
    Whether `login` is listed in the course's `teachers`.

    Case-insensitive, like GitHub logins themselves. Name entries ("Mark
    Polyak") never equal a login, so they grant nothing.
    """
    if not login:
        return False
    wanted = login.casefold()
    return any(entry.strip().casefold() == wanted for entry in course_teachers(course_info))


def is_teacher_in_upload(course_data, login: str | None) -> bool:
    """
    Same check against a raw course YAML (`{"course": {...}}`), as uploaded or
    saved through the editor - before it ever reaches the index.
    """
    course = course_data.get("course") if isinstance(course_data, dict) else None
    return is_course_teacher(course, login)
