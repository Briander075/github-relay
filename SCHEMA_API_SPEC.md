# GitHub Relay - Schema and API Specification

## Purpose
This document defines the v1 storage model and HTTP contract for the GitHub relay.

The goal is to remove ambiguity before implementation starts, especially around durability, claiming, acknowledgement, retries, and duplicate delivery.

This spec assumes the architecture in `ARCHITECTURE.md`:
- FastAPI relay service
- SQLite durable event store
- Cloudflare Tunnel for ingress
- local drainer polling from Pan's machine

## Design goals
- durable receipt of validated GitHub webhooks
- safe draining by an intermittent consumer
- no silent loss if the drainer dies mid-batch
- understandable operational model
- simple enough to implement on a Synology-hosted container

## Non-goals for v1
- multiple competing consumers with complex balancing
- exactly-once delivery guarantees
- arbitrary workflow orchestration
- long-running job execution inside the relay

V1 should target at-least-once delivery with idempotent consumers.

## Delivery model
The relay follows this lifecycle:
1. GitHub sends a webhook to the relay
2. relay validates signature and basic request metadata
3. relay stores the event durably
4. drainer polls for work
5. relay atomically claims a bounded batch
6. drainer processes locally
7. drainer acknowledges success
8. unacked claims eventually expire and become retryable

## Data model

### Table: `events`
This table is the durable event log plus queue state.

Suggested schema:

```sql
CREATE TABLE events (
  id TEXT PRIMARY KEY,
  github_delivery_id TEXT,
  github_event_type TEXT NOT NULL,
  github_hook_id TEXT,
  repository_full_name TEXT,
  repository_id INTEGER,
  installation_id INTEGER,
  action TEXT,
  status TEXT NOT NULL,
  received_at TEXT NOT NULL,
  claimed_at TEXT,
  claim_expires_at TEXT,
  claimed_by TEXT,
  acked_at TEXT,
  dead_at TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  payload_json TEXT NOT NULL,
  headers_json TEXT,
  signature_valid INTEGER NOT NULL,
  duplicate_of_event_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (status IN ('pending', 'claimed', 'acked', 'dead', 'duplicate'))
);
```

Notes:
- `id`: relay-generated event id, UUIDv7 preferred
- `github_delivery_id`: from `X-GitHub-Delivery`, nullable only if the source truly omitted it
- `github_event_type`: from `X-GitHub-Event`
- `status`: current queue state
- `payload_json`: raw JSON payload as received after validation
- `headers_json`: optional sanitized subset of useful headers for debugging
- `signature_valid`: should always be `1` for persisted accepted events in v1, but storing it makes audit/debug easier
- `duplicate_of_event_id`: if duplicates are stored rather than rejected

### Indexes
Recommended indexes:

```sql
CREATE UNIQUE INDEX idx_events_github_delivery_id
ON events(github_delivery_id)
WHERE github_delivery_id IS NOT NULL;

CREATE INDEX idx_events_status_claim_expires_at
ON events(status, claim_expires_at, received_at);

CREATE INDEX idx_events_repository_status
ON events(repository_full_name, status, received_at);
```

If you choose to keep duplicate rows instead of rejecting them, do not make `github_delivery_id` globally unique. In that case, use a non-unique index and explicit duplicate marking logic. For v1, rejecting duplicate insertion or treating it as an idempotent accept is cleaner.

## State machine

### `pending`
Event is durably stored and eligible to be claimed.

### `claimed`
Event has been handed to a drainer but not yet acknowledged.

Required fields:
- `claimed_at`
- `claim_expires_at`
- `claimed_by`

### `acked`
Event was successfully processed by the drainer.

Required field:
- `acked_at`

### `dead`
Event exceeded retry limits or was manually marked unrecoverable.

Required field:
- `dead_at`

### `duplicate`
Optional state if duplicate delivery rows are retained for audit.
For v1, simpler behavior is to avoid inserting a second row and return an idempotent success instead.

## Claiming semantics
Claiming must be atomic.
Two drainers must not successfully claim the same `pending` event in the same lease window.

The claim operation should:
- select only `pending` events and expired `claimed` events
- claim up to `limit` events in oldest-first order
- set `status='claimed'`
- set `claimed_at=now`
- set `claim_expires_at=now + lease_seconds`
- set `claimed_by=<consumer_id>`
- increment `retry_count` only when reclaiming an expired claim or after a reported failure, not on the first successful claim

A lease timeout is required so interrupted consumers do not cause permanent loss.

## Ack semantics
Ack should succeed only when the event is currently claimed by the same consumer, unless v1 intentionally allows looser semantics.

Recommended rules:
- ack requires `event_id`
- ack requires `consumer_id`
- ack changes `claimed -> acked`
- ack sets `acked_at=now`
- ack is idempotent if the event is already acked
- ack should fail with a clear response if the event is claimed by a different consumer

## Retry semantics
Retry policy should be explicit.

Recommended v1 behavior:
- if a claim expires without ack, the event becomes eligible for reclaim
- when reclaiming an expired claim, increment `retry_count`
- if `retry_count` exceeds a configured threshold, move the event to `dead`
- if the drainer reports a processing failure directly, optionally update `last_error` and either:
  - return event to `pending`, or
  - keep it claimed until lease expiry

Suggested defaults:
- `lease_seconds = 300`
- `batch_limit = 10`
- `max_retries = 10`

## Idempotency and duplicates
GitHub may redeliver webhooks.
The local drainer may also repeat work after crashes or network issues.

Therefore:
- the relay must treat `github_delivery_id` as an idempotency key when available
- repeated delivery of the same GitHub delivery id should not create unbounded duplicates
- the drainer must assume local actions may be replayed

Recommended v1 inbound behavior:
- if a webhook arrives with a `github_delivery_id` already stored, return HTTP 200 or 202 with a response body indicating `duplicate=true`
- do not enqueue a second actionable row for the same delivery id

## Security model

### Inbound webhook security
Required:
- validate `X-Hub-Signature-256`
- compare using constant-time comparison
- reject invalid signatures with HTTP 401
- optionally reject missing required GitHub headers with HTTP 400

### Drainer authentication
The drainer endpoints must not be publicly callable without auth.

Recommended v1:
- shared bearer token between drainer and relay
- separate from the GitHub webhook secret
- sent in `Authorization: Bearer <token>`

## API specification

Base path examples:
- `/github/webhook`
- `/api/v1/drain/claim`
- `/api/v1/drain/ack`
- `/healthz`

Exact paths can vary, but behavior should remain stable.

---

## 1. Receive GitHub webhook

### `POST /github/webhook`
Receive and persist a GitHub webhook.

#### Request headers
Required:
- `X-GitHub-Event`
- `X-GitHub-Delivery`
- `X-Hub-Signature-256`
- `Content-Type: application/json`

#### Request body
Raw GitHub webhook JSON payload.

#### Behavior
- validate signature before parsing trust-sensitive data
- parse payload
- extract useful metadata
- insert event if new
- return idempotent success if duplicate delivery id already exists

#### Success response
**HTTP 202 Accepted**

```json
{
  "accepted": true,
  "event_id": "evt_01J...",
  "duplicate": false,
  "status": "pending"
}
```

#### Duplicate response
**HTTP 200 OK** or **202 Accepted**

```json
{
  "accepted": true,
  "event_id": "evt_01J...",
  "duplicate": true,
  "status": "pending"
}
```

#### Error responses
- `400` malformed request or missing required headers
- `401` invalid signature
- `500` persistence failure after validation

---

## 2. Claim pending events

### `POST /api/v1/drain/claim`
Atomically claim a bounded batch of work for one consumer.

#### Authentication
Required bearer token.

#### Request body
```json
{
  "consumer_id": "pan-laptop",
  "limit": 10,
  "lease_seconds": 300,
  "repository": null,
  "event_types": null
}
```

Fields:
- `consumer_id`: required stable identifier for the drainer instance
- `limit`: optional, max events to claim
- `lease_seconds`: optional, bounded by server-side max
- `repository`: optional filter
- `event_types`: optional filter list

#### Behavior
- identify pending items and expired claimed items eligible for reclaim
- claim up to `limit`
- return claimed events plus lease metadata

#### Success response
**HTTP 200 OK**

```json
{
  "consumer_id": "pan-laptop",
  "claimed_count": 2,
  "lease_seconds": 300,
  "events": [
    {
      "event_id": "evt_01JABC...",
      "github_delivery_id": "d3d6...",
      "github_event_type": "push",
      "repository_full_name": "Briander075/github-relay",
      "action": null,
      "received_at": "2026-04-20T20:35:12Z",
      "claim_expires_at": "2026-04-20T20:40:12Z",
      "payload": {}
    },
    {
      "event_id": "evt_01JABD...",
      "github_delivery_id": "e4a1...",
      "github_event_type": "pull_request",
      "repository_full_name": "Briander075/github-relay",
      "action": "opened",
      "received_at": "2026-04-20T20:36:02Z",
      "claim_expires_at": "2026-04-20T20:41:02Z",
      "payload": {}
    }
  ]
}
```

If there is no work:

```json
{
  "consumer_id": "pan-laptop",
  "claimed_count": 0,
  "lease_seconds": 300,
  "events": []
}
```

---

## 3. Acknowledge processed events

### `POST /api/v1/drain/ack`
Acknowledge one or more successfully processed events.

#### Authentication
Required bearer token.

#### Request body
```json
{
  "consumer_id": "pan-laptop",
  "event_ids": [
    "evt_01JABC...",
    "evt_01JABD..."
  ]
}
```

#### Behavior
- mark matching claimed events as `acked`
- require `claimed_by == consumer_id` unless already `acked`
- return per-event outcomes if partial success is possible

#### Success response
**HTTP 200 OK**

```json
{
  "consumer_id": "pan-laptop",
  "acked": [
    "evt_01JABC...",
    "evt_01JABD..."
  ],
  "already_acked": [],
  "not_found": [],
  "not_owned": []
}
```

---

## 4. Report processing failure (optional but recommended)

### `POST /api/v1/drain/fail`
Allow the drainer to report an explicit processing failure.

This endpoint is optional for v1, but useful.
If omitted, failed items can simply be left unacked until lease expiry.

#### Request body
```json
{
  "consumer_id": "pan-laptop",
  "event_id": "evt_01JABC...",
  "error": "git pull failed: merge conflict",
  "requeue": true
}
```

#### Suggested behavior
- verify ownership of the claim
- write `last_error`
- if `requeue=true`, move back to `pending`
- otherwise leave as claimed until expiry, or move directly to `dead` depending on policy

---

## 5. Health check

### `GET /healthz`
Basic service health.

#### Success response
```json
{
  "ok": true
}
```

Optional deeper variant may include DB connectivity.

---

## 6. Debug list endpoint (recommended)

### `GET /api/v1/events`
List recent events for inspection.
This is useful for manual debugging and should likely require auth.

Suggested query parameters:
- `status`
- `limit`
- `repository`
- `github_event_type`

## Example drainer loop
1. call `/api/v1/drain/claim`
2. if zero events, sleep/poll interval
3. process claimed events one by one
4. ack successes in batch via `/api/v1/drain/ack`
5. optionally report failures
6. repeat

## Operational defaults
Recommended configuration values for v1:
- webhook secret from environment
- drainer bearer token from environment
- SQLite file on persistent mounted volume
- WAL mode enabled for SQLite
- lease timeout: 300 seconds
- default batch size: 10
- max batch size: 100
- max retries: 10
- acked event retention: at least 7 to 30 days

## SQLite notes
To keep SQLite sane in a containerized home-server deployment:
- store the DB on a mounted persistent volume
- enable WAL mode
- set a reasonable busy timeout
- avoid unnecessary write amplification
- keep transactions short and explicit

## Open implementation questions
These should be decided before coding starts if possible:
- Should duplicate deliveries be rejected, or accepted idempotently with the original event id returned?
- Should `/ack` allow batch partial success, or fail all if any event is invalid?
- Should failed processing increment `retry_count` immediately, or only on reclaim after lease expiry?
- How long should acked events be retained?
- Should the drainer support repository filters in v1, or can that wait?

## Recommended v1 answers
To keep implementation boring and reliable:
- duplicate deliveries: accept idempotently, do not create a second actionable row
- `/ack`: allow partial success and return per-category results
- `retry_count`: increment on reclaim after lease expiry, not on first claim
- retention: keep acked events for at least 14 days
- repository filters: optional, not required for first pass

## Summary
The relay contract for v1 is:
- persist first
- claim atomically
- ack explicitly
- retry after lease expiry
- tolerate duplicates
- require idempotent consumers

If Pan builds to this contract, the system should survive the exact boring failures that kill naive home-lab webhook designs.
