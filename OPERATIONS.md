# GitHub Relay - Operations Guide

This guide covers operational procedures for managing the GitHub Relay in production.

## Table of Contents

- [Monitoring Health](#monitoring-health)
- [Inspecting Queue State](#inspecting-queue-state)
- [Rotating Secrets](#rotating-secrets)
- [Recovering Stuck Events](#recovering-stuck-events)
- [Manual Replay](#manual-replay)
- [Troubleshooting](#troubleshooting)

---

## Monitoring Health

### Check Relay Status

```bash
# Basic health check
curl http://localhost:8000/healthz

# Detailed health check
curl http://localhost:8000/health
```

Expected response for basic health:
```json
{"ok": true}
```

### Check Docker Container Health

```bash
docker-compose ps
docker-compose logs relay-api | tail -n 100
```

### Monitor Event Counts

```bash
docker exec -it github-relay-api python3 -c "
from src.repository import get_event_count
counts = get_event_count()
print(f'Event counts:')
print(f'  Pending: {counts[\"pending\"]}')
print(f'  Claimed: {counts[\"claimed\"]}')
print(f'  Acked: {counts[\"acked\"]}')
print(f'  Dead: {counts[\"dead\"]}')
print(f'  Duplicate: {counts[\"duplicate\"]}')
"
```

---

## Inspecting Queue State

### View Pending Events

```bash
# View last 10 pending events
docker exec -it github-relay-api python3 -c "
from src.repository import query_events
events = query_events(status='pending', limit=10)
print('Pending events:')
for e in events:
    print(f'  {e[\"github_delivery_id\"]}: {e[\"github_event_type\"]} @ {e[\"repository_full_name\"]}')
"
```

### View Dead Letter Events

```bash
# View dead events with error details
docker exec -it github-relay-api python3 -c "
from src.repository import query_events
events = query_events(status='dead', limit=20)
print('Dead events:')
for e in events:
    print(f'  {e[\"github_delivery_id\"]}: {e[\"last_error\"]}')
"
```

### Filter by Repository

```bash
# View events for specific repository
docker exec -it github-relay-api python3 -c "
from src.repository import query_events
events = query_events(repo='owner/repo', limit=20)
print(f'Events for owner/repo: {len(events)}')
for e in events[:5]:
    print(f'  {e[\"github_delivery_id\"]}: {e[\"status\"]} - {e[\"github_event_type\"]}')
"
```

---

## Rotating Secrets

### Rotating Bearer Token

1. Generate a new token:
   ```bash
   openssl rand -hex 32
   ```

2. Update the `BEARER_TOKEN` in your `.env` file

3. Restart the relay:
   ```bash
   docker-compose restart relay-api
   ```

4. Update the token in your GitHub webhook settings

### Rotating GitHub Webhook Secret

1. Go to your GitHub repository Settings → Webhooks
2. Find your webhook and click "Edit"
3. Generate a new secret
4. Update the secret in your GitHub webhook
5. Update the secret in your drainer configuration

---

## Recovering Stuck Events

### Reclaim Expired Events

Events that were claimed but not acked will automatically reappear after the lease expires (default 300 seconds).

To check for expired events:

```bash
docker exec -it github-relay-api python3 -c "
import sqlite3
from pathlib import Path

db_path = Path('/data/db.sqlite3')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Find events with expired claims
cursor.execute('''
    SELECT id, github_delivery_id, claimed_at, claim_expires_at, claimed_by
    FROM events
    WHERE status = 'claimed'
    AND claim_expires_at < datetime('now')
''')

expired = cursor.fetchall()
print(f'Expired events: {len(expired)}')
for e in expired:
    print(f'  {e[0]}: {e[1]} - expired at {e[3]} by {e[4]}')

conn.close()
"
```

### Manually Reset Event Status

If an event is stuck in `claimed` status:

```bash
docker exec -it github-relay-api python3 << 'EOF'
import sqlite3
from pathlib import Path

db_path = Path('/data/db.sqlite3')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Find and reset stuck events
cursor.execute('''
    UPDATE events
    SET status = 'pending',
        claimed_at = NULL,
        claim_expires_at = NULL,
        claimed_by = NULL,
        retry_count = retry_count + 1
    WHERE status = 'claimed'
    AND claim_expires_at < datetime('now')
    AND id = 'EVENT_ID_HERE'
''')

conn.commit()
print('Event reset successfully')
conn.close()
EOF
```

Replace `EVENT_ID_HERE` with the actual event ID.

---

## Manual Replay

### Replay a Single Event

To manually trigger processing of an event:

```bash
docker exec -it github-relay-api python3 << 'EOF'
import sqlite3
from pathlib import Path
import json

db_path = Path('/data/db.sqlite3')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Get the event
cursor.execute('SELECT id, payload_json, github_event_type FROM events WHERE github_delivery_id = ?', ('DELIVERY_ID',))
event = cursor.fetchone()

if event:
    event_id, payload_json, event_type = event
    payload = json.loads(payload_json)
    print(f'Replaying: {event_id}')
    print(f'Event type: {event_type}')
    print(f'Repository: {payload.get(\"repository\", {}).get(\"full_name\", \"N/A\")}')
else:
    print('Event not found')

conn.close()
EOF
```

### Replay All Dead Events

To attempt processing of all dead events:

```bash
docker exec -it github-relay-api python3 << 'EOF'
import sqlite3
from pathlib import Path

db_path = Path('/data/db.sqlite3')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Find dead events
cursor.execute('SELECT id, github_delivery_id FROM events WHERE status = ?', ('dead',))
events = cursor.fetchall()

print(f'Found {len(events)} dead events')
for event_id, delivery_id in events:
    print(f'  {delivery_id}: {event_id}')
    # Add logic here to manually retry processing
    # This would typically involve re-inserting into pending queue

conn.close()
EOF
```

---

## Troubleshooting

### Relay Not Starting

```bash
# Check logs
docker-compose logs relay-api

# Check database exists and is writable
docker exec -it github-relay-api ls -la /data
docker exec -it github-relay-api sqlite3 /data/db.sqlite3 "SELECT 1;"

# Check environment variables
docker exec -it github-relay-api env | grep -E 'BEARER|DB_PATH'
```

### Webhook Not Delivering

1. Verify Cloudflare Tunnel is running:
   ```bash
   docker-compose ps cloudflared
   ```

2. Check webhook signature validation:
   ```bash
   docker-compose logs relay-api | grep -i signature
   ```

3. Verify webhook secret matches in GitHub and drainer

### High Error Rate

```bash
# Check error distribution
docker exec -it github-relay-api python3 -c "
from src.repository import query_events
events = query_events(status='dead', limit=100)
errors = {}
for e in events:
    err = e['last_error'] or 'unknown'
    errors[err] = errors.get(err, 0) + 1

print('Error distribution:')
for err, count in sorted(errors.items(), key=lambda x: x[1], reverse=True)[:10]:
    print(f'  {count}: {err}')
"
```

### Database Corruption

If database becomes corrupted:

1. Stop the relay:
   ```bash
   docker-compose down
   ```

2. Backup the database:
   ```bash
   docker run --rm -v github-relay-relay-data:/data alpine tar -czf /tmp/db-backup.tar.gz /data/db.sqlite3
   ```

3. Create new database:
   ```bash
   docker-compose run --rm relay-api python3 -c "from src.database import init_db; init_db()"
   ```

4. Restore from backup if needed (advanced recovery procedure)

### Memory Pressure

If the system is under memory pressure:

```bash
# Check memory usage
docker stats github-relay-api

# Increase SQLite cache size
docker exec -it github-relay-api sqlite3 /data/db.sqlite3 "PRAGMA cache_size = -20000;"
```

---

## Emergency Procedures

### Complete System Reset

If everything fails:

```bash
# Stop everything
docker-compose down

# Remove all data (WARNING: This deletes all queued events!)
docker-compose down -v

# Start fresh
docker-compose up -d
```

### Data Recovery

If events are lost:

1. Check if backup exists
2. Restore from backup
3. Reconfigure GitHub webhook to re-deliver missed events

### Emergency Contact

For production issues:
1. Check logs immediately
2. Identify affected events
3. Assess scope of impact
4. Implement recovery procedure
5. Document incident post-mortem
