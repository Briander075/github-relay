# GitHub Relay - Synology NAS Deployment Quick Start

## Prerequisites

- Synology NAS with Docker installed and running
- Internet access from Synology NAS
- GitHub repository with webhook access
- Cloudflare account (for tunneling)

## Deployment Steps

### 1. SSH into your Synology NAS

```bash
ssh admin@your-synology-ip
```

### 2. Clone the repository

```bash
cd /volume1/docker  # or your preferred docker directory
git clone https://github.com/Briander075/github-relay.git
cd github-relay
```

### 3. Create environment file

```bash
cat > .env << 'EOF'
DRAINER_BEARER_TOKEN=$(openssl rand -hex 32)
EOF
```

**Important:** Save this `DRAINER_BEARER_TOKEN` value - you'll need it for GitHub webhook configuration.

### 4. Build and start the stack

```bash
docker-compose up -d
```

### 5. Verify deployment

```bash
# Check container status
docker-compose ps

# Check logs
docker-compose logs relay-api

# Test health endpoint
curl -H "Authorization: Bearer $(grep DRAINER_BEARER_TOKEN .env | cut -d= -f2)" http://localhost:8000/healthz
```

Expected response:
```json
{"ok": true}
```

### 6. Configure GitHub Webhook

1. Go to your GitHub repository
2. Settings → Webhooks → Add webhook
3. **Payload URL**: Use your Cloudflare Tunnel URL (see step 7)
4. **Content type**: `application/json`
5. **Secret**: Create a new secret and save it securely
6. **Select events**: `push`, `pull_request`, etc.
7. Click "Add webhook"

### 7. Set up Cloudflare Tunnel (Optional but Recommended)

If you want to receive webhooks without exposing your local network:

1. Install cloudflared on your local machine (not Synology):
   ```bash
   # macOS with Homebrew
   brew install cloudflare-tunnel
   
   # Or download from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/installation/
   ```

2. Create tunnel:
   ```bash
   cloudflared tunnel create github-relay
   ```

3. Configure tunnel:
   ```bash
   # Follow the prompt to create config file
   # The config file will be at ~/.cloudflared/config.yml
   ```

4. Start tunnel:
   ```bash
   cloudflared tunnel --config ~/.cloudflared/config.yml run
   ```

5. Update the `cloudflared` section in `docker-compose.yml` with your tunnel configuration

### 8. Test Webhook Delivery

Trigger a webhook by making a change in your GitHub repository:
- Push a commit
- Create a pull request
- Open an issue

Check the relay logs:
```bash
docker-compose logs relay-api | tail -n 50
```

You should see log entries like:
```
Received push event for <delivery_id>
```

### 9. Deploy the Drainer

The drainer runs on your local machine (Pan's laptop). Follow the instructions in `OPERATIONS.md` to set up the drainer.

### 10. Verify End-to-End Flow

1. Make a change in GitHub
2. Verify webhook was delivered to relay (check logs)
3. Verify event is in pending state (check with `/api/v1/events?status=pending`)
4. Trigger drainer to process events
5. Verify event moves to acked state

## Troubleshooting

### Container won't start

```bash
# Check logs
docker-compose logs relay-api

# Check environment variables
docker exec -it github-relay-api env | grep DRAINER
```

### Webhook not delivering

1. Check Cloudflare Tunnel is running
2. Verify webhook secret matches in GitHub
3. Check relay logs for signature validation errors

### Drainer can't claim events

1. Verify drainer is using the correct `DRAINER_BEARER_TOKEN`
2. Check relay is running: `docker-compose ps`
3. Check relay logs for authentication errors

## Maintenance

### Backup database

```bash
docker run --rm -v github-relay-relay-data:/data -v $(pwd):/backup alpine tar -czf /backup/backup.tar.gz /data/db.sqlite3
```

### Update deployment

```bash
docker-compose pull
docker-compose up -d
```

### Rotate bearer token

1. Generate new token:
   ```bash
   openssl rand -hex 32
   ```

2. Update `.env` file with new token

3. Restart relay:
   ```bash
   docker-compose restart relay-api
   ```

4. Update drainer configuration with new token

## Next Steps

- Set up monitoring and alerting
- Configure automated backups
- Review `OPERATIONS.md` for detailed operational procedures
