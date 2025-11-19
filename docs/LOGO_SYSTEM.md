# Course Logo System Architecture

## Overview

Course logos are served by the FastAPI backend using StaticFiles middleware, not by the Nginx frontend.

## How It Works

### Backend (main.py)

```python
from fastapi.staticfiles import StaticFiles

# Mount logos directory as static files
LOGOS_DIR = os.path.join(COURSES_DIR, "logos")
app.mount("/courses/logos", StaticFiles(directory=LOGOS_DIR), name="course_logos")
```

This makes all files in `courses/logos/` accessible via HTTP at `/courses/logos/`.

### Frontend

The frontend receives logo URLs from the API response:

```json
{
  "id": "os-2025-spring",
  "name": "Operating Systems",
  "logo": "/courses/logos/os-2025-spring.png",
  ...
}
```

The frontend uses this URL directly in `<img src={logo}>` tags.

### File Storage

**Physical location:** `courses/logos/` (in git repository)
**HTTP endpoint:** `/courses/logos/` (served by FastAPI backend)

```
Repository:
courses/
├── logos/
│   ├── os-2025-spring.png  ← Physical file
│   └── ml-2025-spring.png
└── index.yaml

HTTP Access:
GET /courses/logos/os-2025-spring.png  → Returns the PNG file
GET /courses/logos/ml-2025-spring.png  → Returns the PNG file
```

## Path Format

**Always use HTTP paths in index.yaml:**

✅ **Correct:**
```yaml
logo: "/courses/logos/os-2025-spring.png"
```

❌ **Incorrect:**
```yaml
logo: "courses/logos/os-2025-spring.png"  # Missing leading slash
logo: "/courses/logos/os-2025-spring"     # Missing file extension
logo: "os-2025-spring.png"                # Missing path prefix
```

## Why Backend Instead of Frontend?

1. **Separation of concerns**: Logos are course data, not UI assets
2. **No frontend rebuild**: Can change logos without rebuilding React app
3. **Future API**: Easy to add upload/management endpoints later
4. **Logical grouping**: Logos stay with course YAML files in `courses/`

## Deployment Considerations

### Docker

When deploying with Docker, ensure `courses/logos/` is accessible:

**Option 1: Include in image (recommended for git-tracked logos)**
```dockerfile
# In backend.Dockerfile
COPY courses/ /app/courses/
```

**Option 2: Mount as volume (for frequently changing logos)**
```yaml
# In docker-compose.yaml
volumes:
  - ./courses:/app/courses
```

### Reverse Proxy Configuration

**IMPORTANT**: When using a reverse proxy (Nginx, Caddy, etc.), you must proxy `/courses/logos/*` requests to the backend!

#### Caddy Docker Proxy Example

```yaml
backend:
  labels:
    caddy: labgrader.markpolyak.ru
    # API endpoints
    caddy.handle_path: /api/v1*
    caddy.handle_path.0_reverse_proxy: "{{upstreams 8000}}"
    # Course logos (REQUIRED!)
    caddy.handle_path_1: /courses/logos*
    caddy.handle_path_1.0_reverse_proxy: "{{upstreams 8000}}"
```

#### Nginx Example

```nginx
location /api/v1/ {
    proxy_pass http://backend:8000;
}

# REQUIRED: Proxy logos to backend
location /courses/logos/ {
    proxy_pass http://backend:8000;
}

location / {
    proxy_pass http://frontend:8080;
}
```

#### Common Issue: Logos Not Loading

**Symptoms:**
- `/courses/logos/file.png` returns HTML page or 404
- Browser shows broken image icons

**Cause:**
Reverse proxy not configured to route `/courses/logos/*` to backend.

**Fix:**
Add proxy rule for `/courses/logos/*` → backend (see examples above).

### File Permissions

Ensure the backend process can read the logos directory:
```bash
chmod 755 courses/logos
chmod 644 courses/logos/*.png
```

## Default Fallback

If no logo is specified in index.yaml, the API returns `/assets/default.png`, which is served by the frontend Nginx.

This keeps backward compatibility with existing default logo behavior.

## Testing

### Check if logos are served:

```bash
# Start backend
uvicorn main:app --reload

# Test logo endpoint
curl http://localhost:8000/courses/logos/test.png

# Should return the image or 404 if not found
```

### Verify in API response:

```bash
curl http://localhost:8000/courses

# Should see logo paths like:
# "logo": "/courses/logos/os-2025-spring.png"
```

## Troubleshooting

**Logo not loading:**
1. Check file exists: `ls courses/logos/`
2. Check path in index.yaml has leading `/`
3. Check FastAPI logs for 404 errors
4. Verify file permissions

**StaticFiles not mounted:**
1. Check backend startup logs for: `✅ Course logos available at /courses/logos`
2. If warning shown, directory doesn't exist - create it:
   ```bash
   mkdir -p courses/logos
   ```
