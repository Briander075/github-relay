# GitHub Relay - Deployment Guide

This guide covers deploying the GitHub Relay for production use on Synology NAS with Cloudflare Tunnel.

## Prerequisites

- Synology NAS with Docker support
- GitHub repository with webhook access
- Cloudflare account (for tunneling)

## Environment Variables

Create a `.env` file with the following required variables:

```bash
# Required for API authentication
BEARER_TOKEN=your-secure-bearer-token-here

# Database path (default: /data/db.sqlite3)
# DB_PATH=/data/db.sqlite3

# Log level (default: info)
# LOG_LEVEL=info
```

## Docker Compose Deployment

### Step 1: Clone the repository

```bash
git clone https://github.com/Briander075/github-relay.git
cd github-relay
```

### Step 2: Configure environment

Create a `.env` file in the root directory:

```bash
BEARER_TOKEN=$(openssl rand -hex 32)
```

### Step 3: Build and start

```bash
docker-compose up -d
```

### Step 4: Verify deployment

```bash
docker-compose ps
docker-compose logs -f
```

### Step 5: Test the health endpoint

```bash
curl -H "Authorization: Bearer your-bearer-token" http://localhost:8000/healthz
```

Expected response:
```json
{"ok": true}
```

## GitHub Webhook Setup

1. Go to your GitHub repository
2. Navigate to Settings → Webhooks → Add webhook
3. Set Payload URL to your Cloudflare Tunnel URL
4. Set Content type to `application/json`
5. Set Secret to your GitHub webhook secret
6. Select events to subscribe to (e.g., `push`, `pull_request`)
7. Click Add webhook

## Cloudflare Tunnel Configuration

1. Install cloudflared:
   ```bash
   docker run cloudflare/cloudflared:latest tunnel version
   ```

2. Create tunnel configuration:
   ```bash
   docker run -v $(pwd)/tunnel-config:/etc/cloudflared cloudflare/cloudflared:latest tunnel create github-relay
   ```

3. Update `docker-compose.yml` with your tunnel credentials

## Database Persistence

The SQLite database is stored in the `relay-data` volume, ensuring events persist across container restarts.

```bash
# View database size
docker run --rm -v github-relay-relay-data:/data alpine ls -lh /data

# Backup database
docker run --rm -v github-relay-relay-data:/data -v $(pwd):/backup alpine tar -czf /backup/backup.tar.gz /data/db.sqlite3
```

## Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs relay-api

# Check database permissions
docker exec -it github-relay-api ls -la /data
```

### Health check failing

```bash
# Test health endpoint directly
docker exec -it github-relay-api curl http://localhost:8000/healthz
```

### Webhook not delivering

1. Check Cloudflare Tunnel is running
2. Verify webhook secret matches
3. Check relay logs for signature validation errors

## Monitoring

### Check event counts

```bash
docker exec -it github-relay-api python3 -c "
from src.repository import get_event_count
counts = get_event_count()
print(f'Pending: {counts[\"pending\"]}, Claimed: {counts[\"claimed\"]}, Acked: {counts[\"acked\"]}, Dead: {counts[\"dead\"]}')
"
```

### View pending events

```bash
docker exec -it github-relay-api python3 -c "
from src.repository import query_events
events = query_events(status='pending', limit=10)
for e in events:
    print(f'{e[\"github_delivery_id\"]} - {e[\"github_event_type\"]}')
"
```

## Maintenance

### Backup database

```bash
docker run --rm -v github-relay-relay-data:/data -v $(pwd):/backup alpine tar -czf /backup/$(date +%Y%m%d)-backup.tar.gz /data/db.sqlite3
```

### Restore database

```bash
docker run --rm -v github-relay-relay-data:/data -v $(pwd):/backup alpine tar -xzf /backup/backup.tar.gz -C /data
```

### Update deployment

```bash
docker-compose pull
docker-compose up -d
```

## Production Checklist

- [ ] Bearer token is strong (32+ random bytes)
- [ ] Database volume is backed up regularly
- [ ] Cloudflare Tunnel has stable connectivity
- [ ] GitHub webhook secret is stored securely
- [ ] Monitoring alerts are configured
- [ ] Log aggregation is in place
