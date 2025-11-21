# CLAUDE.md - AI Assistant Guide

## Project Overview

**Lab Grader Web** is an automated web platform for grading student lab assignments with GitHub CI/CD and Google Sheets integration. The system automates student registration, lab submission verification through GitHub Actions, and grade recording to Google Sheets.

### Core Workflow
1. Student selects course/group/lab and enters their name + GitHub username
2. Backend validates student exists in Google Sheets and links their GitHub account
3. When submitting a lab, the system checks the student's GitHub repo for:
   - Existence of `test_main.py` (test file)
   - CI workflows in `.github/workflows/`
   - That test files weren't modified by the student
   - CI check run results
4. Results are recorded to Google Sheets (pass/fail)

## Tech Stack

### Backend (Python)
- **FastAPI** - REST API framework
- **uvicorn** - ASGI server
- **gspread** + **oauth2client** - Google Sheets API
- **PyYAML** - Course configuration parsing
- **requests** - GitHub API calls
- **itsdangerous** - Secure cookie signing for admin sessions

### Frontend (React)
- **React 19** with Vite 6
- **Material UI 7** + **Ant Design 5** - UI components
- **styled-components** - CSS-in-JS
- **React Router 6** - Client-side routing
- **react-i18next** - i18n (Russian, English, Chinese)
- **CodeMirror** - YAML editor for admin panel

### Infrastructure
- **Docker** - Multi-stage builds for frontend/backend
- **Nginx** - Serves frontend SPA, proxies API
- **GitHub Actions** - CI/CD pipeline (workflow_dispatch trigger)
- **GitHub Container Registry** - Docker image storage

## Project Structure

```
lab_grader_web/
├── main.py                          # FastAPI backend (all endpoints)
├── requirements.txt                 # Python dependencies
├── backend.Dockerfile               # Python container (multi-stage)
├── frontend.Dockerfile              # Node build + nginx (multi-stage)
├── docker-compose.yml               # Local development
├── nginx.conf                       # SPA routing config
│
├── courses/                         # Course configurations
│   ├── index.yaml                   # Course index (IDs, status, priority)
│   ├── operating-systems-2025.yaml  # Course config example
│   └── machine-learning-basics-2025.yaml
│
├── frontend/courses-front/          # React application
│   ├── src/
│   │   ├── App.jsx                  # Routes setup
│   │   ├── main.jsx                 # Entry point
│   │   ├── api/index.js             # API client functions
│   │   ├── components/
│   │   │   ├── course-list/         # Course selection
│   │   │   ├── group-list/          # Group selection
│   │   │   ├── lab-list/            # Lab selection
│   │   │   ├── registation-form/    # Student form & grading
│   │   │   └── admin/               # Admin login & protected routes
│   │   └── locales/{en,ru,zh}/      # Translation files
│   ├── package.json
│   └── vite.config.js
│
├── scripts/
│   └── switch-branch.sh             # Production branch switching
│
├── docs/
│   ├── PROJECT_DESCRIPTION.md       # Detailed project documentation
│   └── DEPLOYMENT.md                # Production deployment guide
│
└── .github/workflows/
    └── ci.yaml                      # Docker build & push workflow
```

## Development Setup

### Prerequisites
- Docker and Docker Compose
- Python 3.12+ (for local backend development)
- Node.js 22+ (for local frontend development)
- Google Service Account credentials (`credentials.json`)
- GitHub Personal Access Token

### Local Development

```bash
# Create .env file with required variables
cat > .env << 'EOF'
GITHUB_TOKEN=ghp_your_token
ADMIN_LOGIN=admin
ADMIN_PASSWORD=your_password
SECRET_KEY=your_secret_key
CREDENTIALS_FILE=credentials.json
EOF

# Start with Docker Compose
docker-compose up

# Frontend: http://localhost:8080
# Backend API: http://localhost:8000
```

### Frontend Development (without Docker)

```bash
cd frontend/courses-front
npm install --legacy-peer-deps
npm run dev          # Dev server at http://localhost:5173
npm run build        # Production build
npm run lint         # ESLint
```

### Backend Development (without Docker)

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Key Conventions

### Code Style
- **Python**: No explicit linter configured; follow PEP 8
- **JavaScript/JSX**: ESLint with React plugins configured
- **YAML**: 2-space indentation, use `---` document start markers

### File Naming
- React components: `PascalCase.jsx` or `camelCase/index.jsx`
- Styled components: `styled.js` alongside component
- Python: single `main.py` for all backend code (monolith)

### API Patterns
- All endpoints defined in `main.py`
- Course operations use `course_id` from `index.yaml`, not filename
- Pydantic models for request validation (`StudentRegistration`, `GradeRequest`, etc.)
- HTTPException for error responses with Russian messages

### Environment Variables
| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | Yes | GitHub API access token |
| `ADMIN_LOGIN` | Yes | Admin panel username |
| `ADMIN_PASSWORD` | Yes | Admin panel password |
| `SECRET_KEY` | No | Cookie signing key (default: "super-secret-key") |
| `CREDENTIALS_FILE` | No | Path to Google credentials (default: "credentials.json") |
| `VITE_API_BASE_URL` | No | Frontend API URL (default: "http://localhost:8000") |

## Course Configuration System

### Index File (`courses/index.yaml`)
Central registry for all courses with stable IDs:

```yaml
version: "1.0"
courses:
  - id: "os-2025-spring"           # Stable ID (never changes)
    file: "operating-systems-2025.yaml"
    status: "active"               # active | archived | hidden
    priority: 100                  # Higher = shown first
    featured: true                 # Optional highlight
```

### Course File Structure
```yaml
course:
  name: "Operating systems"
  semester: "Spring 2025"
  email: "instructor@example.com"

  github:
    organization: "org-name"       # GitHub org for student repos
    teachers: ["teacher1"]

  google:
    spreadsheet: "spreadsheet_id"  # Google Sheets ID
    info-sheet: "Info"             # Sheet to exclude from groups
    student-name-column: 1
    lab-column-offset: 1

  labs:
    "1":
      github-prefix: "task1"       # Repo: {prefix}-{username}
      short-name: "Lab1"
      ci: [workflows]
      moss:                        # Plagiarism detection config
        language: c
```

## API Endpoints

### Public
| Method | Path | Description |
|--------|------|-------------|
| GET | `/courses` | List courses (filter: `?status=active\|archived\|all`) |
| GET | `/courses/{id}` | Course details |
| GET | `/courses/{id}/groups` | List groups from Google Sheets |
| GET | `/courses/{id}/groups/{group}/labs` | Available labs |
| POST | `/courses/{id}/groups/{group}/register` | Register student GitHub |
| POST | `/courses/{id}/groups/{group}/labs/{lab}/grade` | Check and grade lab |

### Admin (cookie auth required)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/login` | Login (sets `admin_session` cookie) |
| GET | `/admin/check-auth` | Verify session |
| POST | `/admin/logout` | Clear session |
| GET | `/courses/{id}/edit` | Get course YAML |
| PUT | `/courses/{id}/edit` | Update course YAML |
| DELETE | `/courses/{id}` | Soft delete (sets status=hidden) |
| POST | `/courses/upload` | Upload new course file |

## Common Development Tasks

### Adding a New Course
1. Create `courses/new-course.yaml` with required structure
2. Add entry to `courses/index.yaml` with unique ID
3. Backend validates on startup - check logs for errors

### Modifying API
- All endpoints in `main.py`
- Add Pydantic models for request/response validation
- Use `get_course_by_id()` helper for course lookup

### Adding Frontend Components
1. Create component directory in `src/components/`
2. Add `index.jsx` for component, `styled.js` for styles
3. Create wrapper component (`*Wrapper.jsx`) if needed for routing
4. Add route in `App.jsx`

### Adding Translations
- Edit files in `src/locales/{en,ru,zh}/translation.json`
- Use `useTranslation()` hook and `t('key')` in components

## CI/CD Pipeline

The GitHub Actions workflow (`ci.yaml`) is triggered manually via `workflow_dispatch`:

1. Builds frontend and backend Docker images in parallel
2. Tags images with: branch name, `latest` (main only), `{branch}-{sha}`
3. Pushes to `ghcr.io/markpolyak/lab_grader_{frontend,backend}`

### Production Deployment
- Use `scripts/switch-branch.sh` to switch branches on server
- Watchtower auto-updates when `main` tag changes
- See `docs/DEPLOYMENT.md` for full guide

## Security Considerations

- Admin sessions: signed cookies with 1-hour expiry
- Required env vars validated on startup (fails fast)
- Unprivileged Docker containers (non-root user)
- Test file modification detection prevents cheating
- Never commit `.env` or `credentials.json`

## Troubleshooting

### Backend won't start
- Check `ADMIN_LOGIN`, `ADMIN_PASSWORD`, `GITHUB_TOKEN` are set
- Verify `courses/index.yaml` exists and is valid
- Check all referenced course files exist

### Google Sheets errors
- Verify `credentials.json` exists and has correct permissions
- Check spreadsheet ID in course config
- Ensure service account has access to the spreadsheet

### GitHub API errors
- Verify `GITHUB_TOKEN` has required scopes (repo access)
- Check organization name in course config
- Verify repo naming matches `{github-prefix}-{username}` pattern

## Notes for AI Assistants

- Backend is a monolith (`main.py`) - all changes go in one file
- Comments and error messages are in Russian
- The "registation" typo in folder name is intentional (legacy)
- Course IDs in `index.yaml` are stable; filenames can change
- Test before pushing - no automated tests in CI currently
