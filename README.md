# github-relay

A durable webhook relay for delivering GitHub events to an intermittent local machine.

This project exists because the consumer machine is not a reliable webhook target. The relay receives GitHub events on an always-on host, stores them durably, and lets the local machine drain them later.

## What this repo is for
V1 is intentionally narrow:
- receive GitHub webhooks
- validate signatures
- persist accepted events durably
- let a local drainer claim pending work
- require explicit ack after successful processing
- retry work when the drainer disappears mid-flight

This is not meant to be a general workflow engine.

## Document consumption order
If you are implementing this project, read the docs in this order:

### 1. `ARCHITECTURE.md`
Read this first.
It explains the system shape, why the relay exists, and why v1 prefers SQLite over Redis.

Use it to understand the problem and the high-level design.

### 2. `SCHEMA_API_SPEC.md`
Read this second.
It defines the actual contract:
- storage model
- event states
- claim and ack semantics
- retry rules
- duplicate handling
- API endpoints and request/response shapes

Use it as the implementation source of truth.
Do not improvise different queue semantics unless the doc is updated first.

### 3. `PAN_TASKS.md`
Read this third.
It breaks the work into implementation slices that are small enough to ship without thrashing.

Use it as the execution plan.
Do one slice at a time.

### 4. `DEPLOYMENT.md` (for production deployment)
Read this when you're ready to deploy.
It covers:
- Docker Compose setup
- Environment variable configuration
- Cloudflare Tunnel integration
- GitHub webhook setup
- Database persistence and backup

Use it as your production deployment checklist.

## Intended v1 stack
- FastAPI
- SQLite
- Cloudflare Tunnel
- local drainer process on Pan's machine

## Core implementation rules
- durability beats cleverness
- the laptop being offline is normal
- at-least-once delivery is acceptable
- idempotent consumer behavior is required
- no silent loss on relay restart or drainer interruption

## What not to do
- do not replace SQLite with Redis for v1 without a real reason
- do not treat fetch as implicit success
- do not skip explicit ack semantics
- do not build the whole system in one giant pass
- do not invent extra abstractions before the core path works

## Minimum success criteria for v1
The project is doing its job when:
- valid GitHub webhooks are accepted and stored durably
- duplicate GitHub deliveries do not create duplicate actionable work
- the drainer can claim, process, and ack events
- interrupted processing does not lose events
- expired claims become retryable
- queue state is inspectable

## Running the Drainer

The drainer script can be activated in two ways:

### Primary Trigger: Automatic Background Polling
The drainer runs automatically via `launchd` (macOS) or `systemd` (Linux) for continuous, hands-off operation.

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
python scripts/dev-server.py
```

## Suggested way to work
1. read `ARCHITECTURE.md`
2. read `SCHEMA_API_SPEC.md`
3. implement from `PAN_TASKS.md` in order
4. write tests for each slice
5. avoid expanding scope until the boring path is solid

That is the path that gets this built without turning it into an overengineered little shrine.
