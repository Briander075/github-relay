# GitHub Relay - Relay API

The durable webhook relay service that bridges GitHub webhooks to intermittent local machines.

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

The relay can be run in development or production environments.

### Development

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

## API Endpoints

### Health Check

```
GET /healthz
```

Response:
```json
{"ok": true}
```

### GitHub Webhook

```
POST /github/webhook
```

Accepts GitHub webhook deliveries with signature validation.

### Claim Events

```
POST /api/v1/drain/claim
```

Claims pending events for processing by a drainer.

### Ack Events

```
POST /api/v1/drain/ack
```

Marks events as successfully processed.

### Fail Events

```
POST /api/v1/drain/fail
```

Reports processing failures and optionally requeues events.

### List Events

```
GET /api/v1/events
```

Lists events with filters for debugging and inspection.

### Swagger UI

```
http://localhost:8000/docs
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DRAINER_BEARER_TOKEN` | Authentication token for drainer API | Yes |
| `DB_PATH` | Path to SQLite database | No, defaults to `db.sqlite3` |
| `LOG_LEVEL` | Logging level (debug/info/warning/error) | No, defaults to `info` |
| `HOST` | Server host binding | No, defaults to `0.0.0.0` |
| `PORT` | Server port | No, defaults to `8000` |
| `LEASE_SECONDS` | Claim lease duration | No, defaults to `300` |
| `MAX_BATCH_SIZE` | Maximum events per claim | No, defaults to `100` |
| `MAX_RETRIES` | Maximum retry attempts | No, defaults to `10` |
| `ACK_RETENTION_DAYS` | Days to retain acked events | No, defaults to `14` |

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

## Production Deployment

For production deployment instructions, see the main [DEPLOYMENT.md](../DEPLOYMENT.md) in the project root.

## Operations

For operational procedures including monitoring, troubleshooting, and recovery, see [OPERATIONS.md](../OPERATIONS.md).

## License

MIT
