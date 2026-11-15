"""Queue load test — simulates N concurrent users and reports gspread call
counts + latency statistics.

Usage:
    python test_queue_load.py <path/to/main.py> [concurrent_users]

Default: 10 concurrent users (the "10 человек" scenario from the plan).

Run it against the OLD main.py (before optimization) and the NEW main.py
(after optimization) and compare the numbers:

    python test_queue_load.py upload/main.py 10        # BEFORE
    python test_queue_load.py download/main.py 10      # AFTER

Scenarios:
  1. COLD GET /queue     — N simultaneous reads, cache cold
  2. WARM GET /queue     — N simultaneous reads, cache warm
  3. GET /queue/status   — N simultaneous status checks (authenticated)
  4. POST /register      — N simultaneous registrations (different students)
  5. MIXED               — realistic: 60% view, 30% status, 10% register

For each scenario reports:
  - Total gspread API calls + per-method breakdown
  - Calls per request (total / N)
  - Latency: min, p50, p95, max (ms)
  - HTTP status code distribution
  - For register: position correctness (1..N, no gaps/dupes)
  - PASS/FAIL verdict vs targets from PLAN_sheets_optimization.md

The fake spreadsheet is thread-safe (counts under a Lock) and stateful
(append_row/update_cell actually modify rows) so concurrent mutations
produce realistic results.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import threading
import time
import datetime as _dt
import re
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Thread-safe counting fakes (mimic gspread surface used by the queue path)
# ---------------------------------------------------------------------------


class CountingWorksheet:
    """Fake gspread Worksheet — counts every call AND maintains state.

    Stateful: append_row/update_cell/batch_update actually modify _rows, so
    concurrent mutations (register, set_status) produce realistic results.
    Thread-safe: all state access under a Lock.
    """

    def __init__(self, spreadsheet, title: str, rows: list[list]):
        self._ss = spreadsheet
        self.title = title
        self._rows = [
            [str(c) if c is not None else "" for c in r] for r in rows
        ]
        self.id = (hash(title) & 0xFFFF) or 1
        self._lock = threading.Lock()

    @staticmethod
    def _stringify(row):
        return ["" if c is None else str(c) for c in row]

    def get_all_values(self):
        self._ss._count("get_all_values", self.title)
        with self._lock:
            return [list(r) for r in self._rows]

    def row_values(self, n):
        self._ss._count("row_values", self.title)
        with self._lock:
            if 1 <= n <= len(self._rows):
                return list(self._rows[n - 1])
            return []

    def col_values(self, n):
        self._ss._count("col_values", self.title)
        with self._lock:
            return [
                row[n - 1] if n - 1 < len(row) else ""
                for row in self._rows
            ]

    def cell(self, r, c):
        self._ss._count("cell", self.title)
        with self._lock:
            if 1 <= r <= len(self._rows) and 1 <= c <= len(self._rows[r - 1]):
                val = self._rows[r - 1][c - 1]
            else:
                val = ""
            return types.SimpleNamespace(row=r, col=c, value=val)

    def update_cell(self, r, c, v):
        self._ss._count("update_cell", self.title)
        with self._lock:
            while len(self._rows) < r:
                self._rows.append([""] * 8)
            row = self._rows[r - 1]
            while len(row) < c:
                row.append("")
            row[c - 1] = str(v) if v is not None else ""

    def append_row(self, values, **kw):
        self._ss._count("append_row", self.title)
        with self._lock:
            self._rows.append(self._stringify(values))

    def append_rows(self, values, **kw):
        self._ss._count("append_rows", self.title)
        with self._lock:
            for row in values:
                self._rows.append(self._stringify(row))

    def batch_update(self, updates, **kw):
        self._ss._count("batch_update", self.title)
        with self._lock:
            for upd in updates:
                rng = upd.get("range", "")
                vals = upd.get("values", [[]])
                m = re.match(r"([A-Z]+)(\d+)", rng)
                if not m:
                    continue
                col = ord(m.group(1)) - ord("A") + 1
                row_num = int(m.group(2))
                while len(self._rows) < row_num:
                    self._rows.append([""] * 8)
                target = self._rows[row_num - 1]
                for i, row_vals in enumerate(vals):
                    if i > 0:
                        while len(self._rows) < row_num + i:
                            self._rows.append([""] * 8)
                        target = self._rows[row_num + i - 1]
                    for j, v in enumerate(row_vals):
                        while len(target) < col + j:
                            target.append("")
                        target[col + j - 1] = str(v) if v is not None else ""


class CountingSpreadsheet:
    def __init__(self, sheets: dict[str, CountingWorksheet]):
        self._sheets = sheets
        self.id = "fake-ss-id"
        self.counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def _count(self, method: str, title: str):
        with self._lock:
            self.counts[(method, title)] = self.counts.get((method, title), 0) + 1
            self.counts[("__total__", "")] = self.counts.get(("__total__", ""), 0) + 1

    def worksheet(self, title):
        self._count("worksheet", title)
        ws = self._sheets.get(title)
        if ws is None:
            from gspread.exceptions import WorksheetNotFound
            raise WorksheetNotFound(title)
        return ws

    def worksheets(self):
        self._count("worksheets", "")
        return list(self._sheets.values())

    def add_worksheet(self, title, rows, cols):
        self._count("add_worksheet", title)
        ws = CountingWorksheet(self, title, [[]])
        self._sheets[title] = ws
        return ws

    def batch_update(self, body):
        self._count("batch_update_ss", "")


class CountingClient:
    def __init__(self, spreadsheet: CountingSpreadsheet):
        self._ss = spreadsheet

    def open_by_key(self, key):
        self._ss._count("open_by_key", "")
        return self._ss


# ---------------------------------------------------------------------------
# Test fixture: N students pre-populated in group sheet
# ---------------------------------------------------------------------------

QUEUE_HEADERS = ["ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "GitHub", "Время", "Статус"]
HISTORY_HEADERS = ["Дата", "ПОЗИЦИЯ", "ГРУППА", "ФИО", "ЛР", "Пар№", "GitHub", "Время", "Статус"]

GROUP_ID = "4432"
COURSE_ID = "dev-course"
LAB_SHORT = "ЛР1"

DEFAULT_USERS = 10


def build_students(n: int) -> list[tuple[str, str]]:
    """(github, fio) pairs for n students."""
    return [(f"student{i}", f"Студент{i} Тестов{i}") for i in range(n)]


def build_spreadsheet(today_str: str, students: list[tuple[str, str]]) -> CountingSpreadsheet:
    queue_ws = CountingWorksheet(
        None,
        f"Очередь-{today_str}",
        [QUEUE_HEADERS],
    )
    history_ws = CountingWorksheet(None, "История_очереди", [HISTORY_HEADERS])

    group_rows = [
        ["ФИО", "Группа", "Вариант", "Комментарий", "Дата", "GitHub", LAB_SHORT],
        ["", "", "", "", "", "", LAB_SHORT],
    ]
    for github, fio in students:
        group_rows.append([fio, GROUP_ID, "", "", "", github, ""])
    group_ws = CountingWorksheet(None, GROUP_ID, group_rows)

    sheets = {
        f"Очередь-{today_str}": queue_ws,
        "История_очереди": history_ws,
        GROUP_ID: group_ws,
    }
    ss = CountingSpreadsheet(sheets)
    for ws in sheets.values():
        ws._ss = ss
    return ss


# ---------------------------------------------------------------------------
# Patch main with fakes + canned config
# ---------------------------------------------------------------------------


def patch_main(main, spreadsheet: CountingSpreadsheet, today_str: str):
    course_info = {
        "google": {"spreadsheet": "fake-ss-id", "student-name-column": 1},
        "github": {"organization": "fake-org"},
        "timezone": "UTC",
    }
    lab_config = {"short-name": LAB_SHORT}
    session = types.SimpleNamespace(
        slot_id="slot-1", starts_at=None, ends_at=None, room="101", subject="ЛР"
    )

    main.sheets_client = lambda: CountingClient(spreadsheet)
    main._resolve_queue_lab = lambda cid, lid: (course_info, LAB_SHORT, lab_config)
    main._queue_today_str = lambda ci: today_str
    main._queue_now_str = lambda ci: "12:00"
    main._queue_active_session = lambda cid, gid, lk: session
    main._queue_next_session = lambda cid, gid, lk: None
    main._schedule_group_ids = lambda cid: [GROUP_ID]
    main.is_team_lab = lambda lc: False
    main.session_no_from_slot = lambda slot: 1

    # CI check: mock to always pass (no GitHub calls in load test)
    main.ensure_ci_passed = lambda **kw: None
    main.LabGrader = lambda client: types.SimpleNamespace()
    main.GitHubClient = lambda token: types.SimpleNamespace()

    # Reset caches so each run starts clean
    if hasattr(main, "_queue_ready_done"):
        main._queue_ready_done.clear()
    if hasattr(main, "_aux_cache"):
        main._aux_cache.clear()
    try:
        from grading.queue.cache import reset_queue_cache
        reset_queue_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Counting helpers
# ---------------------------------------------------------------------------


def reset_counts(spreadsheet: CountingSpreadsheet):
    with spreadsheet._lock:
        spreadsheet.counts.clear()


def total(spreadsheet: CountingSpreadsheet) -> int:
    with spreadsheet._lock:
        return spreadsheet.counts.get(("__total__", ""), 0)


def breakdown(spreadsheet: CountingSpreadsheet) -> dict[str, int]:
    agg: dict[str, int] = {}
    with spreadsheet._lock:
        for (method, _title), n in spreadsheet.counts.items():
            if method == "__total__":
                continue
            agg[method] = agg.get(method, 0) + n
    return dict(sorted(agg.items()))


# ---------------------------------------------------------------------------
# Thread-local TestClient (one per thread, sharing the app)
# ---------------------------------------------------------------------------


_thread_local = threading.local()


def get_thread_client(app) -> TestClient:
    if not hasattr(_thread_local, "client"):
        _thread_local.client = TestClient(app)
    return _thread_local.client


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass
class RequestStat:
    label: str
    latency_ms: float
    status_code: int
    error: str = ""


@dataclass
class ScenarioResult:
    name: str
    desc: str
    n_users: int
    total_gspread: int
    per_method: dict[str, int]
    latencies: list[float] = field(default_factory=list)
    status_codes: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = k - f
    if f + 1 < len(s):
        return s[f] + c * (s[f + 1] - s[f])
    return s[f]


def run_concurrent(fn, n: int) -> list[RequestStat]:
    """Run fn(i) n times concurrently, one per thread."""
    stats: list[RequestStat] = []
    stats_lock = threading.Lock()

    def wrapper(i: int) -> RequestStat:
        start = time.perf_counter()
        try:
            label, status_code = fn(i)
            elapsed = (time.perf_counter() - start) * 1000
            return RequestStat(label=label, latency_ms=elapsed, status_code=status_code)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return RequestStat(
                label="error", latency_ms=elapsed, status_code=0,
                error=f"{type(e).__name__}: {e}",
            )

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(wrapper, i) for i in range(n)]
        for fut in as_completed(futures):
            with stats_lock:
                stats.append(fut.result())
    return stats


def collect_stats(result: ScenarioResult, stats: list[RequestStat]):
    for s in stats:
        result.latencies.append(s.latency_ms)
        result.status_codes[s.status_code] = result.status_codes.get(s.status_code, 0) + 1
        if s.error:
            result.errors.append(s.error)


# ---------------------------------------------------------------------------
# Request functions (each returns (label, status_code))
# ---------------------------------------------------------------------------


def make_queue_get(app):
    path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue"
    def fn(i):
        r = get_thread_client(app).get(path)
        return ("GET /queue", r.status_code)
    return fn


def make_status_get(app, students, cookies, cookie_name):
    path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue/status"
    def fn(i):
        github = students[i % len(students)][0]
        r = get_thread_client(app).get(
            path, cookies={cookie_name: cookies[github]}
        )
        return ("GET /queue/status", r.status_code)
    return fn


def make_register(app, students, cookies, cookie_name):
    path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue/register"
    def fn(i):
        github = students[i % len(students)][0]
        r = get_thread_client(app).post(
            path, cookies={cookie_name: cookies[github]}
        )
        return ("POST /register", r.status_code)
    return fn


# ---------------------------------------------------------------------------
# Mixed workload
# ---------------------------------------------------------------------------


def run_mixed(app, spreadsheet, students, cookies, cookie_name, n: int) -> list[RequestStat]:
    """~60% view + 30% status + 10% register, all concurrent."""
    queue_path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue"
    status_path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue/status"
    register_path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue/register"

    n_view = max(1, int(n * 0.6))
    n_status = max(1, int(n * 0.3))
    n_register = max(1, n - n_view - n_status)

    tasks: list[tuple[str, int]] = []
    tasks.extend(("view", i) for i in range(n_view))
    tasks.extend(("status", i) for i in range(n_status))
    tasks.extend(("register", i) for i in range(n_register))

    def wrapper(task):
        kind, idx = task
        client = get_thread_client(app)
        start = time.perf_counter()
        try:
            if kind == "view":
                r = client.get(queue_path)
                label = "GET /queue"
            elif kind == "status":
                github = students[idx % len(students)][0]
                r = client.get(status_path, cookies={cookie_name: cookies[github]})
                label = "GET /queue/status"
            else:
                github = students[idx % len(students)][0]
                r = client.post(register_path, cookies={cookie_name: cookies[github]})
                label = "POST /register"
            elapsed = (time.perf_counter() - start) * 1000
            return RequestStat(label=label, latency_ms=elapsed, status_code=r.status_code)
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return RequestStat(
                label="error", latency_ms=elapsed, status_code=0,
                error=f"{type(e).__name__}: {e}",
            )

    stats: list[RequestStat] = []
    stats_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(wrapper, t) for t in tasks]
        for fut in as_completed(futures):
            with stats_lock:
                stats.append(fut.result())
    return stats


# ---------------------------------------------------------------------------
# Pre-registration (warm-up for status scenario)
# ---------------------------------------------------------------------------


def pre_register(main, students, cookies, cookie_name, count: int):
    """Register `count` students synchronously (warm-up)."""
    register_path = f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue/register"
    client = TestClient(main.app)
    registered = []
    for i in range(min(count, len(students))):
        github, _ = students[i]
        try:
            r = client.post(register_path, cookies={cookie_name: cookies[github]})
            if r.status_code == 200:
                registered.append(github)
        except Exception:
            pass
    return registered


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def format_scenario(result: ScenarioResult, target: int) -> str:
    lines = []
    lines.append("")
    lines.append("-" * 64)
    lines.append(f"  {result.name}")
    lines.append(f"  {result.desc}")
    lines.append("-" * 64)

    n = result.n_users
    total_calls = result.total_gspread
    per_req = total_calls / n if n else 0

    if result.latencies:
        lat = result.latencies
        lat_str = (
            f"min={min(lat):.0f}  p50={percentile(lat, 50):.0f}  "
            f"p95={percentile(lat, 95):.0f}  max={max(lat):.0f}"
        )
    else:
        lat_str = "n/a"

    lines.append(f"  gspread calls:      {total_calls} total ({per_req:.1f}/request)")
    lines.append(f"  Latency (ms):       {lat_str}")

    status_str = ", ".join(
        f"{code} x{cnt}" for code, cnt in sorted(result.status_codes.items())
    )
    lines.append(f"  HTTP status:        {status_str}")

    if result.per_method:
        lines.append("  Breakdown:")
        for method, cnt in result.per_method.items():
            lines.append(f"    {method:20s} x{cnt}")

    if result.errors:
        lines.append(f"  Errors ({len(result.errors)}):")
        for e in result.errors[:3]:
            lines.append(f"    {e}")

    if "positions" in result.extra:
        positions = result.extra["positions"]
        if positions:
            lines.append(f"  Positions assigned: {sorted(positions)}")
            expected = list(range(1, n + 1))
            if sorted(positions) == expected:
                lines.append("  Position check:     OK (1..N, no gaps/dupes)")
            else:
                lines.append(f"  Position check:     FAIL (expected {expected})")

    verdict = "PASS" if total_calls <= target else "FAIL"
    lines.append(f"  Target:             <= {target} calls -> [{verdict}]")
    return "\n".join(lines)


def format_summary(results: list[tuple[str, ScenarioResult, int]]) -> str:
    lines = []
    lines.append("")
    lines.append("=" * 64)
    lines.append("  Summary")
    lines.append("=" * 64)
    lines.append(
        f"  {'Scenario':22s} {'Calls':>7s} {'/Req':>7s} "
        f"{'p95(ms)':>9s} {'Target':>7s}  Verdict"
    )
    lines.append("  " + "-" * 58)
    for _name, result, target in results:
        n = result.n_users
        per_req = result.total_gspread / n if n else 0
        p95 = percentile(result.latencies, 95) if result.latencies else 0
        verdict = "OK" if result.total_gspread <= target else "X"
        lines.append(
            f"  {result.name:22s} {result.total_gspread:>7d} {per_req:>7.1f} "
            f"{p95:>9.0f} {target:>7d}  [{verdict}]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def load_main(path: str):
    spec = importlib.util.spec_from_file_location("main_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["main_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def reset_queue_cache():
    try:
        from grading.queue.cache import reset_queue_cache as _reset
        _reset()
    except Exception:
        pass


def run(path: str, n_users: int) -> int:
    print()
    print("=" * 64)
    print(f"  Queue Load Test")
    print(f"  {n_users} concurrent users")
    print(f"  Target: {path}")
    print("=" * 64)

    main = load_main(path)
    today_str = _dt.date.today().isoformat()
    students = build_students(n_users)

    # --- Build spreadsheet + patch main ---
    spreadsheet = build_spreadsheet(today_str, students)
    patch_main(main, spreadsheet, today_str)

    app = main.app
    cookie_name = main.STUDENT_SESSION_COOKIE
    cookies = {}
    for github, fio in students:
        cookies[github] = main.build_student_session(
            main.signer, github, fio, COURSE_ID, GROUP_ID
        )

    # --- Pre-register half the students for status scenario ---
    print("\n  Pre-registering students for status scenario...")
    pre_registered = pre_register(main, students, cookies, cookie_name, n_users)
    print(f"  Pre-registered: {len(pre_registered)}/{n_users}")

    all_results: list[tuple[str, ScenarioResult, int]] = []

    # === Scenario 1: COLD GET /queue ===
    reset_counts(spreadsheet)
    reset_queue_cache()
    fn = make_queue_get(app)
    stats = run_concurrent(fn, n_users)
    result = ScenarioResult(
        name="COLD GET /queue",
        desc=f"{n_users} simultaneous reads, cache cold",
        n_users=n_users,
        total_gspread=total(spreadsheet),
        per_method=breakdown(spreadsheet),
    )
    collect_stats(result, stats)
    target = 2  # after optimization: 1 read + cache fill
    print(format_scenario(result, target))
    all_results.append(("COLD", result, target))

    # === Scenario 2: WARM GET /queue ===
    # Warm the cache with one synchronous request
    TestClient(app).get(
        f"/courses/{COURSE_ID}/groups/{GROUP_ID}/labs/{quote(LAB_SHORT)}/queue"
    )
    reset_counts(spreadsheet)
    stats = run_concurrent(fn, n_users)
    result = ScenarioResult(
        name="WARM GET /queue",
        desc=f"{n_users} simultaneous reads, cache warm",
        n_users=n_users,
        total_gspread=total(spreadsheet),
        per_method=breakdown(spreadsheet),
    )
    collect_stats(result, stats)
    target = 0  # after optimization: all cache hits
    print(format_scenario(result, target))
    all_results.append(("WARM", result, target))

    # === Scenario 3: GET /queue/status ===
    reset_counts(spreadsheet)
    fn_status = make_status_get(app, students, cookies, cookie_name)
    stats = run_concurrent(fn_status, n_users)
    result = ScenarioResult(
        name="GET /queue/status",
        desc=f"{n_users} simultaneous status checks (authenticated)",
        n_users=n_users,
        total_gspread=total(spreadsheet),
        per_method=breakdown(spreadsheet),
    )
    collect_stats(result, stats)
    target = 1  # after optimization: <= 1 per request
    print(format_scenario(result, target))
    all_results.append(("STATUS", result, target))

    # === Scenario 4: POST /register ===
    # Fresh spreadsheet: empty queue so all N students can register
    spreadsheet_fresh = build_spreadsheet(today_str, students)
    patch_main(main, spreadsheet_fresh, today_str)

    reset_counts(spreadsheet_fresh)
    fn_register = make_register(app, students, cookies, cookie_name)
    stats = run_concurrent(fn_register, n_users)

    # Extract positions from the queue sheet after all registrations
    positions = []
    try:
        queue_ws = spreadsheet_fresh._sheets[f"Очередь-{today_str}"]
        rows = queue_ws.get_all_values()[1:]  # skip header
        positions = [
            int(r[0]) for r in rows if r and r[0] and str(r[0]).isdigit()
        ]
    except Exception:
        pass

    result = ScenarioResult(
        name="POST /register",
        desc=f"{n_users} simultaneous registrations (different students)",
        n_users=n_users,
        total_gspread=total(spreadsheet_fresh),
        per_method=breakdown(spreadsheet_fresh),
        extra={"positions": positions},
    )
    collect_stats(result, stats)
    target = n_users * 3  # ~3 calls per register (1 read + 1 write + 1 ctx)
    print(format_scenario(result, target))
    all_results.append(("REGISTER", result, target))

    # === Scenario 5: MIXED ===
    # Use the fresh spreadsheet: some students registered, some not
    reset_counts(spreadsheet_fresh)
    stats = run_mixed(
        app, spreadsheet_fresh, students, cookies, cookie_name, n_users
    )
    result = ScenarioResult(
        name="MIXED",
        desc=f"60% view + 30% status + 10% register ({n_users} users)",
        n_users=n_users,
        total_gspread=total(spreadsheet_fresh),
        per_method=breakdown(spreadsheet_fresh),
    )
    collect_stats(result, stats)
    target = 5  # after optimization
    print(format_scenario(result, target))
    all_results.append(("MIXED", result, target))

    # --- Summary ---
    print(format_summary(all_results))

    print("\n  Targets (after optimization):")
    print("    COLD GET /queue:   <= 2 calls (1 read + cache fill)")
    print("    WARM GET /queue:   = 0 (all cache hits)")
    print(f"    GET /queue/status: <= 1 call")
    print(f"    POST /register:    <= {n_users * 3} ({n_users} writes + reads)")
    print("    MIXED:             <= 5 calls")
    print()

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python test_queue_load.py <path/to/main.py> [concurrent_users]")
        print("       default users: 10")
        sys.exit(2)

    main_path = sys.argv[1]
    users = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_USERS
    if users < 1:
        print(f"concurrent_users must be >= 1, got {users}")
        sys.exit(2)

    sys.exit(run(main_path, users))
