#!/bin/bash
#
# Branch Switcher for Lab Grader
# Usage: ./switch-branch.sh [branch-name]
#
# This script helps switch between different branches in production.
# It updates docker-compose.yaml tags, pulls new images, and restarts services.
#

set -e

COMPOSE_FILE="${COMPOSE_FILE:-/opt/labgrader/docker-compose.yaml}"
BRANCH="${1:-main}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if docker-compose.yaml exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo -e "${RED}❌ Error: docker-compose.yaml not found at $COMPOSE_FILE${NC}"
    echo "Set COMPOSE_FILE environment variable to the correct path"
    exit 1
fi

echo -e "${YELLOW}🔄 Switching to branch: ${BRANCH}${NC}"
echo ""

# Create backup
BACKUP_FILE="${COMPOSE_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
cp "$COMPOSE_FILE" "$BACKUP_FILE"
echo -e "${GREEN}✅ Backup created: $BACKUP_FILE${NC}"

# Update image tags in docker-compose.yaml
# This replaces everything after the last colon with the branch name
sed -i "s|\(lab_grader_[^:]*\):.*|\1:${BRANCH}|g" "$COMPOSE_FILE"
echo -e "${GREEN}✅ Updated docker-compose.yaml with branch: ${BRANCH}${NC}"

# Show the changes
echo ""
echo -e "${YELLOW}📝 New image tags:${NC}"
grep "image:" "$COMPOSE_FILE" | sed 's/^/  /'
echo ""

# Change to the directory containing docker-compose.yaml
cd "$(dirname "$COMPOSE_FILE")"

# Pull new images
echo -e "${YELLOW}📥 Pulling Docker images...${NC}"
if docker compose pull; then
    echo -e "${GREEN}✅ Images pulled successfully${NC}"
else
    echo -e "${RED}❌ Failed to pull images${NC}"
    echo "Restoring backup..."
    cp "$BACKUP_FILE" "$COMPOSE_FILE"
    exit 1
fi

# Restart services
echo ""
echo -e "${YELLOW}🔄 Restarting services...${NC}"
if docker compose up -d --force-recreate; then
    echo -e "${GREEN}✅ Services restarted successfully${NC}"
else
    echo -e "${RED}❌ Failed to restart services${NC}"
    echo "Restoring backup..."
    cp "$BACKUP_FILE" "$COMPOSE_FILE"
    exit 1
fi

echo ""
echo -e "${GREEN}🎉 Successfully switched to branch: ${BRANCH}${NC}"
echo ""
echo "To view logs:"
echo "  docker compose logs -f"
echo ""
echo "To switch back to main:"
echo "  $0 main"
echo ""
echo "Backup saved at: $BACKUP_FILE"
