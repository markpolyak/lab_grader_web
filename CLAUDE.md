# CLAUDE.md

See `docs/PROJECT_DESCRIPTION.md` for full project documentation and `docs/DEVELOPMENT.md`
for local setup (own Google Sheet, service account, GitHub org, OAuth App).

## Quick Reference

- **Backend**: `main.py` (FastAPI app, all HTTP endpoints, ~3800 LOC) + `grading/` (grading,
  Sheets/GitHub clients, repo provisioning, teams, bulk jobs)
- **Frontend**: `frontend/courses-front/` (React 19 + Vite)
- **Courses**: `courses/index.yaml` + individual YAML files (see `docs/COURSE_CONFIG.md` for all options)
- **Tests**: `tests/` (pytest)

### Development Commands

```bash
# Docker (recommended)
docker-compose up
# Frontend: http://localhost:8080, Backend: http://localhost:8000

# Without Docker
# (FRONTEND_URL=http://localhost:5173 in .env, or the queue API is 403 BAD_ORIGIN)
cd frontend/courses-front && npm install --legacy-peer-deps && npm run dev
pip install -r requirements-dev.txt && uvicorn main:app --reload --port 8000

# Run tests (main.py refuses to import without these variables; dummy values are enough)
export ADMIN_LOGIN=test ADMIN_PASSWORD=test SECRET_KEY=test GITHUB_TOKEN=dummy
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing
cd frontend/courses-front && npm test
```

### Required Environment Variables

```bash
GITHUB_TOKEN=ghp_...      # GitHub API token (scopes: repo, read:org, workflow)
ADMIN_LOGIN=admin         # Admin panel login
ADMIN_PASSWORD=...        # Admin panel password
SECRET_KEY=...            # Cookie signing key AND secret /j/{token} links (changing it revokes every link)
LOG_DIR=/app/logs         # Log directory (optional)
LOG_LEVEL=INFO            # Logging level (optional)

# Optional: only needed for /join/... (student repo auto-creation, see docs/REPO_GENERATION_PLAN.md)
# and for the defense queue sign-in
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...

# Frontend address. Not only the redirect after GitHub sign-in: the queue API accepts an
# Origin only from FRONTEND_URL, PUBLIC_BASE_URL or the request's own host, anything else
# (GETs included) is 403 BAD_ORIGIN. Must match where the frontend runs:
# http://localhost:8080 with docker compose, http://localhost:5173 with npm run dev
FRONTEND_URL=http://localhost:8080

# Optional: public address used to build secret /j/{token} links, and the
# reverse proxies allowed to set X-Forwarded-For (empty = trust nobody)
PUBLIC_BASE_URL=https://labgrader.example.ru
FORWARDED_ALLOW_IPS=

# Optional: SQLite file of the defense queue (default data/queue.sqlite3, relative to the
# working directory). In Docker it must be on the queue-data volume (docs/DEPLOYMENT.md)
QUEUE_DB_PATH=/app/data/queue.sqlite3
```

## Key Conventions

- All HTTP endpoints live in `main.py`; reusable logic goes to `grading/` (see Common Tasks)
- Course operations use `course_id` from `index.yaml`, not filename
- Error messages and UI text are in Russian
- Frontend components: `componentName/index.jsx` + `styled.js`
- Use `--legacy-peer-deps` for npm install
- Column indexing: 0-based in config, auto-converted to 1-based for gspread

## Common Tasks

| Task | Location |
|------|----------|
| Add API endpoint | `main.py` |
| Change grading logic | `grading/bulk.py` (`evaluate_student`, shared by single and bulk grading) |
| Team lab operations | `grading/teams.py` (`TeamRegistry`) |
| Add React component | `frontend/courses-front/src/components/` |
| Add/edit course | `courses/` directory + `index.yaml` |
| Secret link / availability window | `grading/join_links.py` |
| Defense queue (sign-up rules, windows, statuses) | `grading/defense_queue.py` (`DefenseQueue`); storage `grading/queue_store.py` |
| Schedule source (another university) | new `grading/schedule/<name>.py` + `queue.schedule.source: <name>` |
| Add translation | `frontend/courses-front/src/locales/{en,ru,zh}/` |
| Add tests | `tests/` |

## Grading System

- **Success**: `v` written to Google Sheets
- **Failure**: `x` written to Google Sheets
- **With penalty**: `v-{n}` where n = penalty points
- **With score**: `v@{score}` where score = points earned (e.g., `v@10.5` or `v@10,5`)
- **With score and penalty**: `v@{score}-{n}` (e.g., `v@10.5-3` or `v@10,5-3`)
- **Protection**: Can only overwrite empty cells, `x`, or cells starting with `?`
- **Decimal separator**: Automatically detected from Google Sheets locale settings

### Lab Config Structure (course YAML)

```yaml
labs:
  "2":
    github-prefix: os-task2       # Repo name prefix
    short-name: ЛР2               # Column header in spreadsheet
    taskid-max: 20                # Max variant number
    taskid-shift: 4               # Offset for variant calculation
    penalty-max: 9                # Max penalty points
    ignore-task-id: False         # Skip variant check (default: False)
    ci:
      workflows:                  # Specific jobs to check
        - run-autograding-tests
        - cpplint
    files:                        # Required files in repo
      - lab2.cpp
    score:                        # Optional: Extract score from logs
      patterns:                   # List of regex patterns (tried in order)
        - '##\[notice\]Points\s+(\d+(?:[.,]\d+)?)/\d+'  # e.g., "##[notice]Points 10/10" -> 10
        - 'Score\s+is\s+(\d+(?:[.,]\d+)?)'              # e.g., "Score is 10.5" -> 10.5
        - 'Total:\s+(\d+(?:[.,]\d+)?)'                  # e.g., "Total: 10" -> 10
```

**Score extraction notes:**
- Patterns are regex with first capturing group = score value
- Accepts both `.` and `,` as decimal separator in logs
- Output format matches Google Sheets locale (e.g., `10.5` for en_US, `10,5` for ru_RU)
- If score patterns configured but not found in logs → error
- If score patterns not configured → no score extraction (backward compatible)

## Student Repo Auto-Creation (`/join/...`)

Replaces GitHub Classroom's "assignment link" flow (see `docs/REPO_GENERATION_PLAN.md` for full design):
`GET /join/{course_id}/{lab_id}` lands the student on a page with a "Sign in with GitHub" button;
`/join/{course_id}/{lab_id}/start` and `/join/callback` drive a GitHub OAuth Web Application Flow
(username is only ever taken from the verified `GET /user` response, never from the frontend) and then
create the student's repo from the lab's `template-repo` and fix up collaborator access, using the
server's `GITHUB_TOKEN` - not the student's OAuth token. Orchestration lives in
`grading/repo_provisioning.py` (`RepoProvisioner`), mirroring `grading/grader.py`'s `LabGrader`.
Requires a GitHub OAuth App registered by the teacher (not the student) - see `.env.example`.

Labs with `repo-provisioning: fork` (see `docs/COURSE_CONFIG.md`) can later have template updates propagated
to every student fork as pull requests, from the admin lab list page (`/admin/courses/{course_id}/labs`).
Orchestration lives in `grading/propagate.py` (in-memory job state, single-worker backend required - see
`docs/PROJECT_DESCRIPTION.md`). All `/admin/...` and course-management routes require the `require_admin`
FastAPI dependency in `main.py`, not just the frontend's `ProtectedRoute`.

## Team (group) Lab Assignments

Labs with a `team` section in their config are done by teams: one repository per team, shared by
its members (see `docs/TEAM_ASSIGNMENTS_PLAN.md` for the full design and
`docs/PROJECT_DESCRIPTION.md` for the teacher-facing instructions).

- **A team is a repository.** `{github-prefix}-team-{N}` in the course organization; the roster is
  its direct collaborators plus pending invitations, and the title/description live in the repo's
  `description` field as `Название — описание`. No new storage: `grading/teams.py:TeamRegistry`
  reads GitHub, caches the result for 30 s and mutates under a per-lab lock (single-worker backend
  required, like `propagate.py`/`bulk.py`). Mutations re-read with `fresh=True` inside the lock.
- **Username only from the cookie.** The team endpoints take the student's GitHub login from the
  signed `join_session` cookie (`require_join_session` in `main.py`) and never from the body, query
  or path - anything else hands out access to a private repo under someone else's login. The
  callback issues that cookie for a team lab instead of creating a repository.
- **`provision(access_username=...)`** separates the repo suffix (a team slug) from the student who
  gets access; omitting it keeps the individual-lab behaviour untouched.
- **Grading**: `evaluate_student(..., repo_name=...)` grades the team's repository. It is called
  exactly ONCE per team with a synthetic `SheetContext` (`current_cell_value=""`,
  `student_order=None`); `can_overwrite_cell` is then applied per member against their own cell.
  Calling it per member would triple the GitHub work. TASKID is off for team labs
  (`taskid_column` returns None), and bulk `by_file` mode is refused.

## Secret Join Links and Availability Windows

A lab can be reached through an unguessable `/j/{token}` link instead of
`/join/{course_id}/{lab_id}`, and can have an availability window. A test
("контрольная") is a lab with both (see `docs/SECRET_JOIN_LINKS_PLAN.md`; the
teacher-facing instructions are in `docs/PROJECT_DESCRIPTION.md`).

- **The token is computed, not stored.** `grading/join_links.py`:
  `base32(HMAC-SHA256(SECRET_KEY, "{join.id or course+lab key}\n{revision}"))[:10]`,
  alphabet `a-z2-7`. Nothing secret reaches git, the link is revoked by raising
  `join.revision`, and the server can always rebuild it. Comparison is always
  `secrets.compare_digest`, the format is checked by `TOKEN_RE` before any
  search, and the token is never logged - log the course id and lab key.
- **Indistinguishable from a missing lab.** For a secret lab, and for any lab
  that has not reached `join.opens-at`, `/join/{c}/{l}`, `/start`, the lab list
  and public grading must answer exactly what a nonexistent lab answers - same
  code, same body. Public paths resolve through `find_public_lab_config`
  (window + secrecy) or `find_visible_lab_config` (window only, so an open
  secret lab is still gradable); `find_lab_config` stays for admin paths,
  `/j/{token}` and bulk grading.
- **Two different defaults.** A lab with no `join` section behaves exactly as
  before. Missing `opens-at` means "open" for a public lab and "not open yet"
  for a secret one.
- **After `closes-at`** no new repository is created, but a student who
  already has one repairs access through the same link - that is
  `RepoProvisioner.provision(create=False)`, whose default must keep the
  behaviour of individual and team labs unchanged.
- **`team` + `link: secret` is a configuration error**, not a silent fallback
  (team labs are addressed by course/lab pair - out of scope, §12 of the plan).
- **Course enumeration goes through `main.iter_course_configs()`** (reading
  itself lives in `grading/course_index.py`, shared with
  `scripts/join-link.py`) - the single point the config storage migration will
  have to change.
- The link is shown by the admin lab list page and by `scripts/join-link.py`,
  built from `PUBLIC_BASE_URL` (then `FRONTEND_URL`, then `request.base_url`).

## Defense Queue

`course.queue` turns on a queue for lab defenses: a student signs up for a lesson of their group,
the teacher calls the next one and records the outcome (see `docs/COURSE_CONFIG.md`, the
teacher-facing instructions in `docs/PROJECT_DESCRIPTION.md`, and the ADRs in
`docs/hackathon/shaverma-driven-development/`).

- **Never writes to Google Sheets.** The queue only orders defenses, grades stay the teacher's.
  `grading/queue_roster.py` only reads the sheet (3 reads per refresh, 60 s cache); polling
  `GET /courses/{id}/queue` touches neither Sheets nor GitHub.
- **Login only from the cookie.** Student endpoints take the GitHub login from the signed
  `queue_session_{course_id}` cookie (`require_queue_session` in `main.py`), issued by the shared
  `/join/callback`, and never from the body, query or path. The entry's group comes from the sheet.
  Per-course cookie names come from `_course_cookie_name`: an id outside `[A-Za-z0-9_.-]` becomes
  `queue_session~<base64url of the id>`, so two Cyrillic ids never share a cookie. The OAuth nonce
  cookie is per course too (`queue_oauth_{course_id}`), so two sign-ins in two tabs do not clash.
- **SQLite, one transaction per change.** `grading/queue_store.py` (`QUEUE_DB_PATH`) runs every
  change as `BEGIN IMMEDIATE` (`transaction()`) and every pure read as `BEGIN DEFERRED`
  (`read()`), so in WAL polling neither waits for writers nor holds them up. The one write of a
  poll besides the lazy ones below: with `source: guap`, the request that has just downloaded the
  page (once per 30 min per process) saves the copy in a short `BEGIN IMMEDIATE` that waits for
  the lock at most 1 s (`QueueStore.SNAPSHOT_BUSY_TIMEOUT_SECONDS`); a refused save is only
  logged, the poll answers as usual. "One active entry per student per course" is checked
  inside the transaction and backed by the partial UNIQUE index `entries_one_active`: double
  sign-up is impossible even with several processes.
  `call-next` with `expected_entry_id` (the head the teacher sees) never calls an extra student
  on a repeat or from a second tab; without it every request calls the next one - parallel
  defenses, by design. SQL of the queue logic lives in `grading/defense_queue.py`; the schema,
  migrations and the schedule snapshot table are in `grading/queue_store.py`.
- **Lazy windows, no background threads.** Today's sessions are materialized by the first view
  or `sync_day` that misses them. Every change starts with `_expire_past`; a read takes a short
  write only when `_has_overdue` finds waiting/called entries of a day that is over (see
  `DefenseQueue._reading`). `defense_queue.py` and `queue_store.py` never read the clock: time is
  always passed as `now`. History is limited to a period of at most 400 days and 2000 entries
  (`truncated`), anything else is `INVALID_PERIOD`.
- **"Passed the check" is `lab_cell_status`**: `v...` may sign up, `defended-prefix` (`vv...`) is
  already defended, anything else - including manual marks like `X` or `н/я` - is not passed.
  Stricter than `not can_overwrite_cell` on purpose.
- **A new university is a module.** `grading/schedule/<name>.py` with `SOURCE_NAME` and
  `build(cfg, ctx)` (no network in `build`: it is also the config check), named by
  `queue.schedule.source`; neither the registry nor the queue changes. The registry glues lessons
  of the same groups, type and subject that overlap or follow with a break of at most 40 min
  (`merge_adjacent`), after applying `queue.schedule.changes` (cancelled and extra lessons,
  `grading/schedule/changes.py`), which it takes out of the section: sources never see it.
  `guap` caches the page for 30 min, downloads it within a 5 s budget, headers included (a
  short-lived worker thread; while one that missed the budget still hangs, new downloads fail at
  once instead of piling up), refuses a body over 2 MB as `BAD_PAGE` (other requests answer from
  the copy at once, or wait at most 1 s without one), keeps the last good copy in SQLite for up to
  14 days and accepts only a course timezone of UTC-6..UTC+5.
- **Requests only from the service's own pages.** Every queue API endpoint, GETs included (all
  but the `/queue/login` navigation), calls `_check_queue_origin`: a foreign `Origin` is
  `403 BAD_ORIGIN` (CORS allows any origin with credentials, so names and history would
  otherwise be readable cross-site).
- Errors are stable `UPPER_SNAKE` codes in `detail`, translated by the frontend (ru/en/zh). The
  teacher's day view, history and actions on existing sessions and entries work from the database
  whatever the course status or config errors, with signup shown as closed while the queue does
  not work; opening signup by hand needs a working queue (active course, valid enabled config).
  Opening it again for the same groups on the same day changes the window of that manual session
  (extends, shortens or reopens it) instead of starting a second queue. A late outcome or a fix
  keeps `finished_at` (the end of that day, the time of the first outcome), and "already
  defended" holds for the teacher's actions too: return answers `LAB_ALREADY_DEFENDED`, defended
  while the student is queued again for the same lab answers `ALREADY_IN_QUEUE`. The teacher page
  renews `admin_session` only during the first 11 h after the password sign-in
  (`admin_session_start` cookie), so a session never outlives 12 h.

## Bulk Grading (admin)

Grades a whole group for one lab in a single run, started from the admin lab list page
(`/admin/courses/{course_id}/labs`) - see `docs/PROJECT_DESCRIPTION.md` for the full behaviour.

- `grading/bulk.py:evaluate_student` holds the grading decision and is shared by `grade_lab` and the
  bulk run, so the two cannot drift apart. It never touches Sheets: the spreadsheet context arrives
  through a lazily-invoked provider, which is what lets `grade_lab` still answer repository and CI
  errors without opening a Sheets connection.
- Endpoints: `POST /admin/courses/{id}/groups/{g}/labs/{l}/bulk-grade` (202 + `job_id`),
  `GET /admin/bulk-grade-jobs/{job_id}`, `POST /admin/bulk-grade-jobs/{job_id}/cancel`.
- Job state mirrors `grading/propagate.py` (in-memory, single-worker backend required, 409 on a
  second run for the same course/group/lab).
- With `name_file` set, repos are discovered by the lab's prefix and matched to sheet rows by the
  first line of that file; without it, only students who already have a username in the sheet are
  graded. Name matching is exact after normalization - no fuzzy matching, by design.
- Reads the worksheet once via `get_all_values()` and writes grades in batches of 10: the per-cell
  helpers spend ~6 Sheets requests per student, over the 60 reads/minute quota for a group of 30.

## CI/CD

- **Tests**: Run via `.github/workflows/tests.yml` on push and on pull requests into `main`
  and `hackathon/**`
- **Docker images**: Built on push to `main`, `claude/**`, `feature/**` and `fix/**` branches
- Images published to `ghcr.io/markpolyak/lab_grader_web-{frontend,backend}`

## Notes

- Course IDs in `index.yaml` are stable identifiers; filenames can change
- Backend validates `index.yaml` on startup - check logs for errors
- Logs persist in `logs/` directory (mounted as Docker volume)
