# CLAUDE.md

See `docs/PROJECT_DESCRIPTION.md` for full project documentation.

## Quick Reference

- **Backend**: Single file `main.py` (FastAPI monolith)
- **Frontend**: `frontend/courses-front/` (React 19 + Vite)
- **Courses**: `courses/index.yaml` + individual YAML files

### Development Commands

```bash
# Docker (recommended)
docker-compose up
# Frontend: http://localhost:8080, Backend: http://localhost:8000

# Without Docker
cd frontend/courses-front && npm install --legacy-peer-deps && npm run dev
pip install -r requirements.txt && uvicorn main:app --reload --port 8000
```

### Required Environment Variables

```bash
GITHUB_TOKEN=ghp_...      # GitHub API token
ADMIN_LOGIN=admin         # Admin panel login
ADMIN_PASSWORD=...        # Admin panel password
```

## Key Conventions

- All backend endpoints in `main.py` - no separate modules
- Course operations use `course_id` from `index.yaml`, not filename
- Error messages and comments are in Russian
- Frontend components: `componentName/index.jsx` + `styled.js`
- Use `--legacy-peer-deps` for npm install

## Common Tasks

| Task | Location |
|------|----------|
| Add API endpoint | `main.py` |
| Add React component | `frontend/courses-front/src/components/` |
| Add/edit course | `courses/` directory + `index.yaml` |
| Add translation | `frontend/courses-front/src/locales/{en,ru,zh}/` |

## Notes

- No automated tests in CI - test manually before pushing
- Course IDs in `index.yaml` are stable identifiers; filenames can change
- Backend validates `index.yaml` on startup - check logs for errors
