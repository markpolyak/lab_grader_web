# CLAUDE.md

See `docs/PROJECT_DESCRIPTION.md` for full project documentation.

## Quick Reference

- **Backend**: Single file `main.py` (FastAPI monolith, ~750 LOC)
- **Frontend**: `frontend/courses-front/` (React 19 + Vite)
- **Courses**: `courses/index.yaml` + individual YAML files
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
| Add React component | `frontend/courses-front/src/components/` |
| Add/edit course | `courses/` directory + `index.yaml` |
| Add translation | `frontend/courses-front/src/locales/{en,ru,zh}/` |
| Add tests | `tests/` |

## Grading System

- **Success**: `v` written to Google Sheets
- **Failure**: `x` written to Google Sheets
- **With penalty**: `v-{n}` where n = penalty points
- **Protection**: Can only overwrite empty cells, `x`, or cells starting with `?`

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
```

## CI/CD

- **Tests**: Run on every push via `.github/workflows/tests.yml`
- **Docker images**: Built on push to `main` and `claude/**` branches
- Images published to `ghcr.io/markpolyak/lab_grader_web-{frontend,backend}`

## Notes

- Course IDs in `index.yaml` are stable identifiers; filenames can change
- Backend validates `index.yaml` on startup - check logs for errors
- Logs persist in `logs/` directory (mounted as Docker volume)
