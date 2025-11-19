# Logging System

## Overview

The Lab Grader backend uses Python's `logging` module to provide detailed logging with timestamps for debugging and monitoring.

## Log Output

Logs are written to **two destinations simultaneously**:

1. **Console (stdout)** - visible via `docker logs`
2. **File** (`logs/labgrader.log`) - persists across container restarts

## Log Format

```
2025-11-19 12:34:56 - __main__ - INFO - Registration attempt - Course: os-2025, Group: P3315, Student: Иванов Иван, GitHub: ivanov
```

Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

**Note:** All logs now include timestamps, including uvicorn access logs (HTTP requests).

## Log Levels

The logging level can be controlled via the `LOG_LEVEL` environment variable:

- **INFO** (default): Normal operation, important events
- **DEBUG**: Detailed diagnostic information for troubleshooting
- **WARNING**: Warning messages only
- **ERROR**: Error messages only

### Enabling DEBUG Mode

For detailed troubleshooting (e.g., finding why a student is not found), enable DEBUG mode:

**In docker-compose.yaml:**
```yaml
backend:
  environment:
    LOG_LEVEL: DEBUG
```

**DEBUG mode adds:**
- Input field values (surname, name, patronymic)
- Student list from spreadsheet (first 5 entries)
- String length and representation for comparison
- Similar names when exact match fails

**Example DEBUG output:**
```
2025-11-19 12:34:56 - __main__ - INFO - Registration attempt - Course: os-2025, Group: 4332, Full name: 'Тестовый Тест Тестович', GitHub: markpolyak
2025-11-19 12:34:56 - __main__ - DEBUG - Input data - Surname: 'Тестовый', Name: 'Тест', Patronymic: 'Тестович'
2025-11-19 12:34:56 - __main__ - INFO - Searching for student 'Тестовый Тест Тестович' in column 2
2025-11-19 12:34:56 - __main__ - INFO - Found 15 students in spreadsheet
2025-11-19 12:34:56 - __main__ - DEBUG - Student list: ['Иванов Иван Иванович', 'Петров Петр', ...]
2025-11-19 12:34:56 - __main__ - WARNING - Student 'Тестовый Тест Тестович' not found in group 4332
2025-11-19 12:34:56 - __main__ - INFO - Found 1 students with matching surname: ['Тестовый Тест']
2025-11-19 12:34:56 - __main__ - DEBUG - Search string length: 23, repr: 'Тестовый Тест Тестович'
2025-11-19 12:34:56 - __main__ - DEBUG - First student in list - length: 21, repr: 'Иванов Иван Иванович'
```

This helps identify:
- Missing patronymic in spreadsheet
- Extra spaces
- Different encoding issues

## What Gets Logged

### Registration Endpoint (`/register`)
- **INFO**: Registration attempts with full name (consistent format)
- **INFO**: Number of students found in spreadsheet
- **INFO**: Students with matching surname (when not found)
- **INFO**: Successful registration or GitHub update
- **DEBUG**: Individual field values (surname, name, patronymic)
- **DEBUG**: Student list from spreadsheet
- **DEBUG**: String comparison details (length, repr)
- **WARNING**: Student not found, GitHub user not found
- **ERROR**: Missing configuration, spreadsheet errors

### Grading Endpoint (`/grade`)
- Grading requests (course, group, lab, GitHub username)
- Repository validation (test_main.py, workflows)
- Commit history checks
- Forbidden file modification detection
- CI check results for each workflow
- Google Sheets updates
- Errors with detailed context

### Startup
- Course index validation
- Logo directory mounting
- Configuration loading

## Persistent Logs in Docker

### Configuration

Logs are stored on the **host machine** via Docker volume mount:

**Host path:** `/opt/labgrader/logs/`
**Container path:** `/app/logs/`
**Log file:** `labgrader.log`

### docker-compose.yaml Setup

```yaml
backend:
  volumes:
    - ./logs:/app/logs  # Persistent logs
  environment:
    LOG_DIR: /app/logs
```

### Log Persistence

✅ **Logs survive:**
- `docker compose restart`
- `docker compose down && docker compose up`
- Container crashes
- Image updates

❌ **Logs are lost only if:**
- You manually delete the `logs/` directory on host
- You use `docker compose down -v` (removes volumes)

## Viewing Logs

### Via Docker (recent logs only)
```bash
docker compose logs -f backend
```

### Via log file (all logs, persistent)
```bash
# On server
tail -f /opt/labgrader/logs/labgrader.log

# View last 100 lines
tail -n 100 /opt/labgrader/logs/labgrader.log

# Search for errors
grep ERROR /opt/labgrader/logs/labgrader.log

# Search for specific GitHub user
grep "ivanov" /opt/labgrader/logs/labgrader.log
```

## Log Rotation

**Current:** No automatic rotation (file grows indefinitely)

**Recommended for production:**

Add logrotate configuration on the server:

```bash
# Create /etc/logrotate.d/labgrader
/opt/labgrader/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    notifempty
    missingok
    copytruncate
}
```

This will:
- Rotate logs daily
- Keep 30 days of logs
- Compress old logs
- Use `copytruncate` to avoid breaking open file handles

## Development

In development (without Docker), logs are written to `logs/labgrader.log` in the project root.

## Troubleshooting

**No log file created:**
- Check `LOG_DIR` environment variable
- Verify volume mount in docker-compose.yaml
- Check file permissions: `chmod 755 /opt/labgrader/logs`

**Logs not updating:**
- Verify container is running: `docker compose ps`
- Check for errors: `docker compose logs backend`
- Ensure volume mount is correct: `docker inspect <container_id>`

**Log file too large:**
- Manually archive: `gzip /opt/labgrader/logs/labgrader.log`
- Set up logrotate (see above)

## Examples

### Debug failed registration:
```bash
grep "Registration attempt" logs/labgrader.log | tail -n 20
```

### Find all errors today:
```bash
grep "$(date +%Y-%m-%d)" logs/labgrader.log | grep ERROR
```

### Track specific student's grading:
```bash
grep "ivanov" logs/labgrader.log | grep -E "(Grading|Successfully)"
```
