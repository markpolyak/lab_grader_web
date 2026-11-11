"""
SQLite storage of the defense queue (issue #62).

The queue is the only part of the service that keeps state of its own: who
stands in which queue, who was called, how each defense ended. That state
must survive a backend restart in the middle of a lesson and stay consistent
when two requests (or two processes) touch the same queue at once, so it
lives in one SQLite file on a docker volume - an embedded database, not a
separately deployed service (see
docs/hackathon/shaverma-driven-development/01-queue-storage.md).

This module opens connections, creates the schema and runs transactions;
the SQL of the queue logic lives in grading/defense_queue.py. The store also
keeps the last good copy of a network schedule (it implements
grading.schedule.base.SnapshotStore), with the SQL of that table here.

Like grading/teams.py, this module knows nothing about FastAPI and never
touches Google Sheets.
"""
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Сколько соединение ждёт, пока другая транзакция отпустит запись. Транзакции
# очереди - миллисекунды, так что 10 с означают «база действительно занята».
DEFAULT_BUSY_TIMEOUT_SECONDS = 10

# Переключение в WAL требует монопольного доступа к файлу: если в этот момент
# стартует второй процесс, пробуем ещё раз.
WAL_ATTEMPTS = 5
WAL_RETRY_PAUSE_SECONDS = 0.2

# DDL выполняется отдельными execute внутри BEGIN IMMEDIATE: executescript
# сам делает COMMIT и сломал бы транзакцию миграции.
_SCHEMA_V1 = (
    """
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        course_id TEXT NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('schedule', 'manual', 'all_day')),
        lesson_key TEXT,
        lesson_type TEXT NOT NULL DEFAULT '',
        subject TEXT NOT NULL DEFAULT '',
        groups TEXT NOT NULL,
        starts_at TEXT NOT NULL,
        ends_at TEXT NOT NULL,
        signup_opens_at TEXT NOT NULL,
        signup_closes_at TEXT NOT NULL,
        day_ends_at TEXT NOT NULL,
        closed_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (course_id, day, lesson_key)
    )
    """,
    """
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES sessions(id),
        course_id TEXT NOT NULL,
        login TEXT NOT NULL,
        github TEXT NOT NULL,
        student_name TEXT NOT NULL,
        group_name TEXT NOT NULL,
        lab_key TEXT NOT NULL,
        lab_name TEXT NOT NULL,
        seq INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'waiting', 'called', 'defended', 'not_defended', 'no_show', 'left', 'expired'
        )),
        created_at TEXT NOT NULL,
        called_at TEXT,
        finished_at TEXT
    )
    """,
    # Одна активная запись студента на курс - на уровне базы, а не проверкой
    # в коде: так правило держится и при двух процессах.
    """
    CREATE UNIQUE INDEX entries_one_active ON entries(course_id, login)
        WHERE status IN ('waiting', 'called')
    """,
    "CREATE INDEX entries_by_session ON entries(session_id, status, seq)",
    "CREATE INDEX entries_by_course_time ON entries(course_id, created_at)",
    # Запросы по одному студенту (последний исход дня, «уже защитил») -
    # без него они перебирали бы всю бессрочную историю курса.
    "CREATE INDEX entries_by_login ON entries(course_id, login, status)",
    "CREATE INDEX sessions_by_course_day ON sessions(course_id, day)",
    """
    CREATE TABLE schedule_snapshots (
        source_key TEXT PRIMARY KEY,
        fetched_at TEXT NOT NULL,
        payload TEXT NOT NULL
    )
    """,
)

# Пути, для которых схема уже проверена в этом процессе.
_init_lock = threading.Lock()
_initialized_paths: set[str] = set()


class QueueError(Exception):
    """
    A queue operation was refused.

    `code` is a stable UPPER_SNAKE code (ALREADY_IN_QUEUE, QUEUE_EMPTY,
    STORAGE_UNAVAILABLE, ...): main.py maps it to an HTTP status and the
    frontend translates it, so the server never sends ready-made text.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _is_storage_failure(exc: sqlite3.Error) -> bool:
    """
    Whether an sqlite3 error means "the database cannot be used right now".

    OperationalError covers a locked database, a full disk, a missing
    directory and an I/O error; a bare DatabaseError is a damaged file.
    IntegrityError and the like are logic errors and are left to the caller.
    """
    return isinstance(exc, sqlite3.OperationalError) or type(exc) is sqlite3.DatabaseError


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    # После некоторых ошибок (SQLITE_FULL, SQLITE_IOERR) SQLite уже откатил
    # транзакцию сам, и ROLLBACK ответил бы ещё одной ошибкой.
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _snapshot_time(value: datetime) -> str:
    """UTC ISO-8601 with a fixed number of digits, so that strings compare like times."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"fetched_at must be timezone-aware: {value!r}")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class QueueStore:
    """
    One SQLite file holding the queues of every course.

    A connection is opened for each operation and closed right after it, so
    the object is safe to share between the threads of FastAPI's pool, and
    two QueueStore objects on one file behave like two processes would.
    Every change goes through transaction(), i.e. BEGIN IMMEDIATE: writers
    are serialized by SQLite itself, not by a lock of this process. Pure
    reads go through read() and take no write lock.
    """

    def __init__(self, path, busy_timeout: float = DEFAULT_BUSY_TIMEOUT_SECONDS):
        self.path = os.path.abspath(os.fspath(path))
        self.busy_timeout = busy_timeout

    # -----------------------------------------------------------------------
    # Connections
    # -----------------------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        # isolation_level=None: модуль sqlite3 не открывает транзакции сам,
        # BEGIN IMMEDIATE / COMMIT пишутся явно в transaction().
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout * 1000)}")
        except BaseException:
            conn.close()
            raise
        return conn

    def _unavailable(self, exc: Exception) -> QueueError:
        logger.error(f"Хранилище очереди {self.path} недоступно: {exc}")
        # Следующий запрос проверит схему заново: файл могли удалить или
        # подменить, пока процесс работал.
        _initialized_paths.discard(self.path)
        return QueueError("STORAGE_UNAVAILABLE", str(exc))

    @contextmanager
    def connect(self):
        """
        A fresh connection in autocommit mode, closed on exit.

        Raises:
            QueueError: STORAGE_UNAVAILABLE when the file cannot be opened or
            used (locked for longer than busy_timeout, no disk space, damaged)
        """
        try:
            self._ensure_initialized()
            conn = self._open()
        except QueueError:
            raise
        except (OSError, sqlite3.Error) as e:
            if isinstance(e, sqlite3.Error) and not _is_storage_failure(e):
                raise
            raise self._unavailable(e) from e

        try:
            yield conn
        except sqlite3.Error as e:
            if not _is_storage_failure(e):
                raise
            raise self._unavailable(e) from e
        finally:
            conn.close()

    @contextmanager
    def _begin(self, statement: str):
        with self.connect() as conn:
            conn.execute(statement)
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                _rollback_quietly(conn)
                raise

    def transaction(self):
        """
        BEGIN IMMEDIATE ... COMMIT, rolled back on any exception.

        IMMEDIATE takes the write lock at the start, so a transaction never
        has to upgrade a read snapshot into a write - the case where SQLite
        answers "database is locked" without waiting.

        Raises:
            QueueError: STORAGE_UNAVAILABLE, see connect()
        """
        return self._begin("BEGIN IMMEDIATE")

    def read(self):
        """
        BEGIN DEFERRED ... COMMIT: several SELECTs see one state of the file.

        No write lock is taken, so in WAL a reader neither waits for a
        writer nor holds one up. Writing inside it is a mistake: the upgrade
        to a write lock may fail at once with "database is locked".

        Raises:
            QueueError: STORAGE_UNAVAILABLE, see connect()
        """
        return self._begin("BEGIN DEFERRED")

    # -----------------------------------------------------------------------
    # Schema
    # -----------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        if self.path in _initialized_paths:
            return
        with _init_lock:
            if self.path in _initialized_paths:
                return
            self._initialize()
            _initialized_paths.add(self.path)

    def _initialize(self) -> None:
        """
        Create the directory, switch the file to WAL and migrate the schema.

        Safe to run from several processes at once: the migration re-reads
        user_version under BEGIN IMMEDIATE, so only the first one creates the
        tables.
        """
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        conn = self._open()
        try:
            self._enable_wal(conn)
            self._migrate(conn)
        finally:
            conn.close()

    def _enable_wal(self, conn: sqlite3.Connection) -> None:
        # WAL: чтения (опрос очереди идёт через read()) не ждут писателей,
        # а писатели - читателей. Режим хранится в самом файле, поэтому для
        # уже переключённой базы это мгновенно. PRAGMA journal_mode нельзя
        # выполнять внутри транзакции.
        mode = None
        for attempt in range(WAL_ATTEMPTS):
            try:
                mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                break
            except sqlite3.OperationalError as e:
                if attempt == WAL_ATTEMPTS - 1:
                    logger.warning(f"{self.path}: не удалось включить WAL: {e}")
                    return
                time.sleep(WAL_RETRY_PAUSE_SECONDS)
        if str(mode).lower() != "wal":
            logger.warning(
                f"{self.path}: журнал в режиме {mode!r}, а не WAL - "
                "опрос и запись очереди будут ждать друг друга (файловая система без общей памяти?)"
            )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        if conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
            return

        conn.execute("BEGIN IMMEDIATE")
        try:
            # Перечитываем под блокировкой: другой процесс мог создать схему,
            # пока мы ждали BEGIN IMMEDIATE.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise QueueError(
                    "STORAGE_UNAVAILABLE",
                    f"schema version {version} of {self.path} is newer than this code ({SCHEMA_VERSION})",
                )
            if version < 1:
                for statement in _SCHEMA_V1:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except QueueError as e:
            _rollback_quietly(conn)
            logger.error(f"Хранилище очереди: {e}")
            raise
        except BaseException:
            _rollback_quietly(conn)
            raise
        logger.info(f"Хранилище очереди {self.path}: схема версии {SCHEMA_VERSION}")

    # -----------------------------------------------------------------------
    # Schedule snapshots (grading.schedule.base.SnapshotStore)
    # -----------------------------------------------------------------------

    # Копию сохраняет опрос, который только что скачал страницу: он не должен
    # ждать писателя очереди 10 с. Не дождался - копия обновится при
    # следующей загрузке, а опрос ответит как обычно.
    SNAPSHOT_BUSY_TIMEOUT_SECONDS = 1

    def load_snapshot(self, key: str) -> tuple[datetime, str] | None:
        """The last saved copy for a source key: (fetched_at, payload), or None."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM schedule_snapshots WHERE source_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["fetched_at"]), row["payload"]

    def save_snapshot(self, key: str, fetched_at: datetime, payload: str) -> None:
        """
        Save a copy, unless the stored one is newer.

        Two processes may fetch the same page; whichever finishes last must
        not put an older copy over a newer one. A short write that waits for
        the lock at most SNAPSHOT_BUSY_TIMEOUT_SECONDS.

        Raises:
            QueueError: STORAGE_UNAVAILABLE, see connect()
        """
        hurried = QueueStore(self.path, min(self.busy_timeout, self.SNAPSHOT_BUSY_TIMEOUT_SECONDS))
        with hurried.transaction() as conn:
            conn.execute(
                """
                INSERT INTO schedule_snapshots (source_key, fetched_at, payload) VALUES (?, ?, ?)
                ON CONFLICT (source_key) DO UPDATE
                    SET fetched_at = excluded.fetched_at, payload = excluded.payload
                    WHERE excluded.fetched_at >= schedule_snapshots.fetched_at
                """,
                (key, _snapshot_time(fetched_at), payload),
            )

    def snapshot_fetched_at(self, key: str) -> datetime | None:
        """When the saved copy was fetched - a cheap check without the payload."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT fetched_at FROM schedule_snapshots WHERE source_key = ?",
                (key,),
            ).fetchone()
        return None if row is None else datetime.fromisoformat(row["fetched_at"])
