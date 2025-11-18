# Deployment Guide

## Overview

This guide explains how to deploy and manage Lab Grader Web in production using Docker and GitHub Container Registry (GHCR).

## Architecture

- **CI/CD**: GitHub Actions builds Docker images on workflow dispatch
- **Registry**: Images are pushed to `ghcr.io/markpolyak/lab_grader_*`
- **Deployment**: Docker Compose on production server
- **Auto-updates**: Watchtower monitors `main` branch for updates

## Image Tagging Strategy

When you trigger the CI workflow from any branch, the following tags are created:

| Tag | Example | Description |
|-----|---------|-------------|
| Branch name | `main`, `feature-xyz` | Current branch name |
| `latest` | `latest` | Only for main branch |
| SHA with prefix | `main-abc1234` | Branch name + commit SHA |

**Examples:**

```bash
# Building from 'main' branch creates:
ghcr.io/markpolyak/lab_grader_frontend:main
ghcr.io/markpolyak/lab_grader_frontend:latest
ghcr.io/markpolyak/lab_grader_frontend:main-abc1234

# Building from 'feature-auth' branch creates:
ghcr.io/markpolyak/lab_grader_frontend:feature-auth
ghcr.io/markpolyak/lab_grader_frontend:feature-auth-xyz5678
```

## Server Setup

### Initial Setup

1. **Copy deployment script to server:**

```bash
# On your local machine
scp scripts/switch-branch.sh your-server:/opt/labgrader/
ssh your-server "chmod +x /opt/labgrader/switch-branch.sh"
```

2. **Create docker-compose.yaml on server:**

```yaml
# /opt/labgrader/docker-compose.yaml
services:
  frontend:
    image: ghcr.io/markpolyak/lab_grader_frontend:main
    networks:
      - caddy-proxy
    labels:
      caddy: labgrader.markpolyak.ru
      caddy.reverse_proxy: "{{upstreams 8080}}"

  backend:
    image: ghcr.io/markpolyak/lab_grader_backend:main
    volumes:
      - ./google-credentials:/app/google-credentials/
    environment:
      CREDENTIALS_FILE: /app/google-credentials/credentials.json
      GITHUB_TOKEN: ${GITHUB_TOKEN}
      ADMIN_LOGIN: ${ADMIN_LOGIN}
      ADMIN_PASSWORD: ${ADMIN_PASSWORD}
      SECRET_KEY: ${SECRET_KEY}
    labels:
      caddy: labgrader.markpolyak.ru
      caddy.handle_path: /api/v1*
      caddy.handle_path.0_reverse_proxy: "{{upstreams 8000}}"
    networks:
      - caddy-proxy

networks:
  caddy-proxy:
    external: true
```

3. **Create .env file with secrets:**

```bash
# /opt/labgrader/.env
GITHUB_TOKEN=your_github_token
ADMIN_LOGIN=your_admin_login
ADMIN_PASSWORD=your_secure_password
SECRET_KEY=your_secret_key
```

## Switching Between Branches

### Method 1: Using the Script (Recommended)

```bash
cd /opt/labgrader

# Switch to a feature branch for testing
./switch-branch.sh feature-new-ui

# Check logs
docker compose logs -f

# Switch back to main
./switch-branch.sh main
```

The script will:
- ✅ Create automatic backup
- ✅ Update docker-compose.yaml
- ✅ Pull new images
- ✅ Restart services
- ✅ Show clear status messages

### Method 2: Manual Switching

```bash
cd /opt/labgrader

# Edit docker-compose.yaml and change :main to :your-branch
nano docker-compose.yaml

# Pull and restart
docker compose pull
docker compose up -d --force-recreate
```

### Method 3: Using Environment Variable

Update your docker-compose.yaml to use variables:

```yaml
services:
  frontend:
    image: ghcr.io/markpolyak/lab_grader_frontend:${BRANCH:-main}
  backend:
    image: ghcr.io/markpolyak/lab_grader_backend:${BRANCH:-main}
```

Then switch with:

```bash
BRANCH=feature-xyz docker compose up -d --pull=always --force-recreate
```

## Watchtower Configuration

Watchtower automatically updates containers when new images are pushed with the same tag.

**Current behavior:**
- Watchtower monitors `main` tag
- When you push new code to `main` and CI builds it, Watchtower auto-updates production

**To prevent auto-updates during testing:**

When testing a feature branch, the tag is different (e.g., `feature-xyz`), so Watchtower won't auto-update. This is intentional and safe.

## Deployment Workflows

### Production Update (Main Branch)

```bash
# On your machine
git push origin main

# Trigger GitHub Actions manually or via workflow_dispatch

# Wait for CI to complete (~5 min)

# Watchtower will auto-update production (if configured)
# Or manually:
ssh your-server
cd /opt/labgrader
docker compose pull
docker compose up -d
```

### Testing a Feature Branch

```bash
# On your machine
git push origin feature-new-auth

# Trigger GitHub Actions for this branch

# On server
ssh your-server
cd /opt/labgrader
./switch-branch.sh feature-new-auth

# Test the changes
# Check logs: docker compose logs -f

# When done testing, switch back
./switch-branch.sh main
```

### Rolling Back to Previous Version

```bash
# Option 1: Use the automatic backup
cd /opt/labgrader
ls -la docker-compose.yaml.backup.*
cp docker-compose.yaml.backup.20250118_143022 docker-compose.yaml
docker compose up -d --force-recreate

# Option 2: Use commit SHA tag
./switch-branch.sh main-abc1234
```

## Monitoring

### View Logs

```bash
# All services
docker compose logs -f

# Just backend
docker compose logs -f backend

# Last 100 lines
docker compose logs --tail=100 backend
```

### Check Running Containers

```bash
docker compose ps
```

### Check Image Versions

```bash
docker compose images
```

## Troubleshooting

### Images Not Pulling

```bash
# Check if you're logged in to GHCR
docker login ghcr.io

# Manually pull to see detailed error
docker pull ghcr.io/markpolyak/lab_grader_backend:main
```

### Services Not Starting

```bash
# Check logs for errors
docker compose logs backend
docker compose logs frontend

# Verify environment variables
docker compose config
```

### Wrong Branch Deployed

```bash
# Check current image tags
grep "image:" docker-compose.yaml

# Restore from backup
ls -la docker-compose.yaml.backup.*
cp docker-compose.yaml.backup.LATEST docker-compose.yaml
docker compose up -d --force-recreate
```

## Best Practices

1. **Always test feature branches** before merging to main
2. **Use the switch script** - it creates automatic backups
3. **Monitor logs** after switching branches
4. **Keep main stable** - only merge tested code
5. **Document changes** in commit messages for easier rollbacks

## Security Notes

- Never commit `docker-compose.yaml` with secrets to git
- Use `.env` file for sensitive data (also not in git)
- Restrict SSH access to deployment server
- Regularly rotate secrets (ADMIN_PASSWORD, SECRET_KEY, etc.)
- Keep Docker and Watchtower updated

## Reference Commands

```bash
# Quick reference for common tasks

# Switch to feature branch
./switch-branch.sh feature-name

# Back to production
./switch-branch.sh main

# View logs
docker compose logs -f

# Restart without pulling
docker compose restart

# Stop all services
docker compose down

# Start services
docker compose up -d

# Check status
docker compose ps
```
