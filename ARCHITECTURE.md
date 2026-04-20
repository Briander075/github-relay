# GitHub Relay - Architecture

## Objective
Bridge GitHub webhooks to an intermittent local development machine, while preserving events when that machine is offline, asleep, or unreachable.

The system should treat the laptop being unavailable as normal, not as a failure case.

## Recommended v1 Architecture
Use an asynchronous relay with a durable event store:

`[GitHub Webhook] -> [Cloudflare Tunnel] -> [Relay API (FastAPI)] -> [Durable Event Store (SQLite)] <- [Local Drainer on Pan's machine]`

Core idea:
- GitHub delivers once to an always-on relay
- the relay validates and stores events durably
- Pan's machine polls later and drains pending work when it is available

This is the right shape because Pan is not a reliable webhook target.

## Why this architecture
Direct GitHub -> laptop delivery is the wrong fit here because the laptop is:
- not always on
- not always reachable
- not a stable public endpoint

The relay pattern fixes the real problem:
- inbound delivery happens once, to an always-on service
- events are buffered centrally
- the local machine can recover later without losing the queue

## Important design correction
The earlier draft described Redis as a durable buffer.
That is only conditionally true.

### Blunt warning
Plain Redis is not durable enough by default for this use case.
If the container restarts or persistence is configured poorly, queued webhook events can disappear. That would defeat the entire point of the relay.

Redis can be acceptable if AOF persistence is explicitly enabled and tested, but for this project that is more infrastructure than v1 needs.

## Recommendation
For v1, prefer:
- FastAPI
- SQLite-backed durable event store / queue
- Cloudflare Tunnel
- Local drainer

Why SQLite is the better default here:
- fewer moving parts
- easier manual inspection
- stronger and more obvious persistence story
- simpler deployment on a Synology box
- easier for Pan to implement in small, reliable slices

Redis remains a valid later option if there is a specific need for Redis semantics or existing NAS infrastructure built around it.

## Components

### 1. Relay API (Synology container)
**Technology:** Python / FastAPI

**Role:**
- expose the webhook endpoint
- validate GitHub webhook signatures
- normalize incoming events
- persist validated events to the durable store
- expose endpoints for draining and acknowledgement

This service should do minimal work in the request path. The request path should validate, persist, and return quickly.

### 2. Durable Event Store (SQLite)
**Technology:** SQLite

**Role:**
- store all accepted webhook events durably
- track delivery state
- support retries and acknowledgement
- provide auditability and manual inspection

This is better thought of as an event log plus queue metadata, not just a FIFO bucket.

Suggested stored fields:
- internal relay event id
- GitHub delivery id
- event type
- repository / installation identifiers as needed
- received timestamp
- raw payload or normalized payload
- signature validation result metadata
- status (`pending`, `claimed`, `acked`, `dead` or similar)
- claim timestamp / claimant id
- ack timestamp
- retry count
- last error

### 3. Cloudflare Tunnel (Synology container)
**Technology:** `cloudflared`

**Role:**
- provide a secure inbound path without opening router ports
- keep the relay reachable even though it lives on a home network device

### 4. Local Drainer (Pan's machine)
**Technology:** Python script or small service

**Role:**
- poll the relay for pending events
- claim a bounded batch of events
- perform local automation
- acknowledge successful processing
- leave failed items for retry according to policy

The drainer should be idempotent and assume duplicates are possible.

## Required delivery semantics
This part matters. Without it, the system will look fine until restarts or double polling turn it into soup.

### Event identity
Each stored event should have:
- a relay-generated internal id
- GitHub's delivery id when available

Use these for dedupe and traceability.

### Dedupe / idempotency
The system should tolerate duplicate delivery from GitHub and duplicate polling by the drainer.

At minimum:
- store GitHub delivery id when present
- prevent accidental insertion of the same delivery twice, or mark duplicates explicitly
- make drainer actions safe to replay

### Claim vs ack
Do not jump straight from `pending` to `done` on fetch.

Recommended flow:
1. drainer requests work
2. relay returns a bounded batch and marks items `claimed`
3. drainer processes them locally
4. drainer calls ack endpoint for successful items
5. unacked claims expire and become eligible for retry

This prevents silent loss when the laptop dies mid-batch.

### Retry
Define retry behavior explicitly:
- claimed items that are not acked within a lease timeout return to `pending`
- retry count increments on requeue or failure
- after some threshold, items move to `dead` or a dead-letter state for manual review

### Stale events
Decide what happens to old events:
- keep indefinitely for audit, or
- expire after a retention period, or
- archive old acked items separately

For v1, retaining acked events for debugging is usually worth it.

## API shape for v1
The exact route names can vary, but v1 should include these behaviors:

### Inbound webhook endpoint
- accepts GitHub webhook POSTs
- validates signature
- stores event durably
- returns success quickly

### Fetch / claim endpoint
- returns a bounded batch of pending events
- atomically marks them claimed by a consumer
- includes lease / claim expiration metadata

### Ack endpoint
- marks claimed events as successfully processed
- should reject or safely ignore invalid acknowledgements

### Health endpoint
- basic readiness / liveness for deployment checks

Optional but useful:
- list events endpoint for debugging
- replay endpoint for manual recovery

## Suggested v1 operating model
The whole relay stack on the Synology NAS can still be deployed with Docker Compose, but the stack becomes simpler:
- relay API container
- cloudflared container
- SQLite database on a mounted volume

This is simpler than adding Redis unless Redis is already wanted for other reasons.

## Why this is a better fit for Pan
Pan can build this, but only if the work is sliced small.
Do not hand Pan a vague task like:
- build the full relay system

Do hand Pan concrete slices like:
1. define the event schema and storage model
2. implement GitHub webhook signature validation
3. persist validated events durably
4. implement fetch/claim endpoint for pending events
5. implement ack endpoint
6. build the local drainer poller
7. add dedupe and idempotency handling
8. write deployment and recovery docs

That is a sane task grain. Anything fuzzier invites thrash.

## Alternative if Redis is kept
If there is a strong reason to keep Redis, then the document should explicitly require:
- AOF persistence enabled
- persistence settings tested under restart conditions
- clear acknowledgement semantics outside of a naive list pop
- recovery behavior documented

Without that, calling Redis a durable buffer is hand-wavy at best.

## Final recommendation
The architecture direction is correct: relay centrally, drain locally later.

The main change is to prefer a SQLite durable queue / event log for v1 instead of a Redis queue.
That makes the system simpler, more inspectable, and more robust for an intermittent laptop consumer.

---
*Last Updated: 2026-04-20*
