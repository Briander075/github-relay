# GitHub Relay - Architecture

## Objective
To bridge GitHub webhooks to a local development machine, ensuring that all event notifications (e.g., `push`, `pull_request`) are captured and processed even if the local machine is offline, powered down, or disconnected from the internet.

## Architecture Overview
The system uses an **Asynchronous Relay** pattern. A persistent "Relay" hosted on an always-on local server (Synology NAS) receives webhooks and buffers them in a queue. A "Drainer" running on the local coding machine polls this queue to execute tasks.

### Data Flow
`[GitHub Webhook]` $\rightarrow$ `[Cloudflare Tunnel]` $ightarrow$ `[Relay API (FastAPI)]` $\rightarrow$ `[Relay Queue (Redis)]` $\leftarrow$ `[Local Agent Drainer]`

## Components

### 1. Relay API (Synology Container)
*   **Technology:** Python / FastAPI.
*   **Role:** The public-facing endpoint. It validates incoming GitHub webhooks (signature verification) and persists the payload into the Redis queue.

### 2. Relay Queue (Synology Container)
*   **Technology:** Redis.
*   **Role:** The durable buffer. It holds the JSON payloads of all pending events, providing the "memory" for the system when the local machine is offline.

### 3. Cloudflare Tunnel (Synology Container)
*   **Technology:** `cloudflared`.
*   **Role:** Provides a secure, outbound-only connection from the Synology NAS to the internet. This allows GitHub to reach the `Relay API` without any open ports or complex firewall/NAT configurations on the local network.

### 4. Local Drainer (Local Machine Process)
*   **Technology:** Python Script.
*   **Role:** The event consumer. It runs as a lightweight background process on the primary coding machine. It periodically polls the `Relay API` for new items. Upon detection, it triggers the local automation (e.g., `git pull`, `hermes-agent` task execution).

## Dependencies
*   **Infrastructure:** Synology NAS (Docker-enabled).
*   **Network:** Cloudflare (for Tunneling).
*   **Platform:** GitHub (Source of events).
*   **Software:** Docker, Docker Compose, Redis, Python 3.x.

## Deployment Strategy
The entire Relay stack (API, Redis, Cloudflared) will be deployed using a single `docker-compose.yml` file on the Synology NAS.

---
*Last Updated: 2026-04-07*
