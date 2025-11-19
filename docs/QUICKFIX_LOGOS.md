# Quick Fix: Course Logos Not Loading

## Problem

Course logos return HTML pages instead of images.

**Example:** `https://labgrader.markpolyak.ru/courses/logos/os-2025_logo.png` returns HTML.

## Root Cause

Your reverse proxy (Caddy) is not routing `/courses/logos/*` requests to the backend. All requests go to frontend (Nginx), which returns `index.html` for unknown routes.

## Solution

Update your `docker-compose.yaml` on the server to proxy `/courses/logos/*` to backend.

### Step 1: SSH to Server

```bash
ssh your-server
cd /opt/labgrader
```

### Step 2: Edit docker-compose.yaml

```bash
nano docker-compose.yaml
```

### Step 3: Add Logo Proxy Rule

Find the `backend` service `labels` section and add the highlighted lines:

```yaml
  backend:
    image: ghcr.io/markpolyak/lab_grader_backend:main
    volumes:
      - ./google-credentials:/app/google-credentials/
    environment:
      CREDENTIALS_FILE: /app/google-credentials/credentials.json
      GITHUB_TOKEN: your-token
      ADMIN_LOGIN: your-login
      ADMIN_PASSWORD: your-password
      SECRET_KEY: your-secret
    labels:
      caddy: labgrader.markpolyak.ru
      # API endpoints
      caddy.handle_path: /api/v1*
      caddy.handle_path.0_reverse_proxy: "{{upstreams 8000}}"
      # 👇 ADD THESE TWO LINES 👇
      caddy.handle_path_1: /courses/logos*
      caddy.handle_path_1.0_reverse_proxy: "{{upstreams 8000}}"
    networks:
      - caddy-proxy
```

**Key points:**
- Use `caddy.handle_path_1` (note the `_1` suffix - different from `_0` for API)
- Pattern is `/courses/logos*` (with asterisk)
- Proxy to `{{upstreams 8000}}` (same as API)

### Step 4: Restart Services

```bash
docker compose down
docker compose up -d
```

### Step 5: Verify

```bash
# Test logo endpoint directly
curl https://labgrader.markpolyak.ru/courses/logos/os-2025_logo.png -I

# Should return:
# HTTP/2 200
# content-type: image/png
```

Or open in browser: https://labgrader.markpolyak.ru/courses/logos/os-2025_logo.png

## Why This Happens

```
Before fix:
/courses/logos/file.png → Frontend (Nginx) → index.html ❌

After fix:
/courses/logos/file.png → Backend (FastAPI) → image file ✅
/api/v1/courses → Backend (FastAPI) → JSON ✅
/ → Frontend (Nginx) → React app ✅
```

## Full Example docker-compose.yaml

See `docker-compose.example.yaml` in the repository for complete reference.

## Additional Notes

- This change is safe and won't affect existing functionality
- Only adds a new route for logos
- No need to rebuild images, just restart containers
- Logs are available with: `docker compose logs -f backend`
