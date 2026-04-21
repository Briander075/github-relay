# GitHub Relay - Relay API

Slice 1: Project Skeleton with Basic API and Health Endpoint

## Overview

This is the reference implementation of the Asynchronous Relay Pattern for bridging GitHub webhooks to intermittent local machines.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   GitHub        │     │  Cloudflare     │     │   Local Drainer │
│   Webhook       │────>│  Tunnel         │────>│   (Polls)       │
│   (Public URL)  │     │  (Inbound)      │     │   (Intermittent)│
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                        │                      │
         │                        │                      │
         ▼                        ▼                      ▼
  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
  │   SQLite Queue  │     │   Relay API     │     │   Local Machine │
  │   (Durable)     │     │   (FastAPI)     │     │   (Drainer)     │
  └─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Components

- **Relay API**: FastAPI endpoint that validates webhooks and stores them in SQLite
- **SQLite Queue**: Durable event storage with claim/ack semantics
- **Cloudflare Tunnel**: Secure inbound path without opening router ports
- **Local Drainer**: Polls the relay and processes work when the local machine is available

## Running the Relay API

The script can be activated in two ways:

### Primary Trigger: Automatic Background Polling

The local drainer runs automatically via `launchd` (macOS) or `systemd` (Linux) for continuous, hands-off operation:

**macOS (launchd):**
```bash
# Load the agent to start automatically at login
launchctl load ~/Library/LaunchAgents/com.github.relay.drainer.plist

# Unload to stop
launchctl unload ~/Library/LaunchAgents/com.github.relay.drainer.plist
```

**Linux (systemd):**
```bash
# Enable to start at boot
sudo systemctl enable github-relay-drainer

# Start immediately
sudo systemctl start github-relay-drainer

# Stop
sudo systemctl stop github-relay-drainer
```

### Secondary Trigger: Manual Run for Testing/Troubleshooting

For development, debugging, or manual intervention:

```bash
cd relay-api
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
```

Or using the development script:

```bash
python scripts/dev-server.py
```

The API will be available at `http://localhost:8000`

### Docker Development

```bash
docker-compose up --build
```

### API Endpoints

#### Health Check

```
GET /health
```

Response:
```json
{
  "status": "healthy",
  "service": "github-relay-api",
  "version": "0.1.0",
  "timestamp": "2026-04-20T00:00:00Z"
}
```

#### Swagger UI

```
http://localhost:8000/docs
```

## Structure

```
relay-api/
├── src/
│   └── main.py         # FastAPI application
├── tests/              # Unit and integration tests
├── scripts/            # Development scripts
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

## Next Steps

- Slice 2: SQLite storage layer
- Slice 3: GitHub webhook validation
- Slice 4: Idempotent duplicate handling
- Slice 5: Claim endpoint
- Slice 6: Ack endpoint
- Slice 7: Lease expiry and retry behavior
- Slice 8: Local drainer
- Slice 9: Failure reporting endpoint
- Slice 10: Debug/inspection tools
- Slice 11: Deployment packaging
- Slice 12: Recovery and ops docs

## License

MIT
