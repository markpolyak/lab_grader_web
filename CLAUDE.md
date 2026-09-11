# CLAUDE.md

See `docs/PROJECT_DESCRIPTION.md` for full project documentation.

## Quick Reference

- **Backend**: Single file `main.py` (FastAPI monolith, ~750 LOC)
- **Frontend**: `frontend/courses-front/` (React 19 + Vite)
- **Courses**: `courses/index.yaml` + individual YAML files (see `docs/COURSE_CONFIG.md` for all options)
- **Tests**: `tests/` (pytest)

### Development Commands

```bash
# Docker (recommended)
docker-compose up
# Frontend: http://localhost:8080, Backend: http://localhost:8000

# Without Docker
cd frontend/courses-front && npm install --legacy-peer-deps && npm run dev
pip install -r requirements.txt && uvicorn main:app --reload --port 8000

# Run tests
pytest tests/ -v
pytest tests/ --cov=. --cov-report=term-missing
```

### Required Environment Variables

```bash
GITHUB_TOKEN=ghp_...      # GitHub API token
ADMIN_LOGIN=admin         # Admin panel login
ADMIN_PASSWORD=...        # Admin panel password
SECRET_KEY=...            # Cookie signing key
LOG_DIR=/app/logs         # Log directory (optional)
LOG_LEVEL=INFO            # Logging level (optional)

# Optional: only needed for /join/... (student repo auto-creation, see docs/REPO_GENERATION_PLAN.md)
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
FRONTEND_URL=http://localhost:8080
```

## Key Conventions

- All backend endpoints in `main.py` - no separate modules
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

- **Tests**: Run on every push via `.github/workflows/tests.yml`
- **Docker images**: Built on push to `main` and `claude/**` branches
- Images published to `ghcr.io/markpolyak/lab_grader_web-{frontend,backend}`

## Notes

- Course IDs in `index.yaml` are stable identifiers; filenames can change
- Backend validates `index.yaml` on startup - check logs for errors
- Logs persist in `logs/` directory (mounted as Docker volume)
