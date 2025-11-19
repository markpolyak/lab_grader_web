# Deployment Scripts

This directory contains scripts for managing production deployments.

## switch-branch.sh

Utility script for switching between Git branches in production.

### Installation

Copy the script to your production server:

```bash
scp switch-branch.sh your-server:/opt/labgrader/
ssh your-server "chmod +x /opt/labgrader/switch-branch.sh"
```

### Usage

```bash
# Switch to a feature branch
./switch-branch.sh feature-new-auth

# Switch back to main (production)
./switch-branch.sh main

# With custom docker-compose.yaml path
COMPOSE_FILE=/path/to/docker-compose.yaml ./switch-branch.sh my-branch
```

### What it does

1. Creates an automatic backup of `docker-compose.yaml`
2. Updates image tags to the specified branch
3. Pulls the new Docker images
4. Restarts services with `--force-recreate`
5. Shows clear status messages

### Environment Variables

- `COMPOSE_FILE` - Path to docker-compose.yaml (default: `/opt/labgrader/docker-compose.yaml`)

### Backups

The script automatically creates timestamped backups:
- Format: `docker-compose.yaml.backup.YYYYMMDD_HHMMSS`
- Location: Same directory as docker-compose.yaml
- Useful for quick rollbacks

### Troubleshooting

**Permission denied:**
```bash
chmod +x switch-branch.sh
```

**Docker compose not found:**
```bash
# Ensure docker-compose.yaml exists
ls -la /opt/labgrader/docker-compose.yaml

# Or set COMPOSE_FILE
export COMPOSE_FILE=/path/to/your/docker-compose.yaml
```

**Failed to pull images:**
```bash
# Login to GitHub Container Registry
docker login ghcr.io

# Check if the branch was built in CI
# Visit: https://github.com/markpolyak/lab_grader_web/actions
```

## See Also

- [Deployment Guide](../docs/DEPLOYMENT.md) - Full deployment documentation
- [docker-compose.example.yaml](../docker-compose.example.yaml) - Example configuration
