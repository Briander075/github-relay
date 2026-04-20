# Pan Task Backlog - GitHub Relay v1

This backlog assumes the target architecture and contract are defined by:
- `ARCHITECTURE.md`
- `SCHEMA_API_SPEC.md`

The goal is to keep the work sliced small enough that implementation stays real and does not dissolve into vibes.

## Ground rules
- build v1 against SQLite, not Redis
- do not invent different delivery semantics mid-implementation
- optimize for durability and clarity, not cleverness
- treat Pan's laptop being offline as normal
- target at-least-once delivery with idempotent consumer behavior

## Suggested implementation order

### Slice 1: Project skeleton
**Goal:** Create the repo structure and app skeleton without business logic.

**Deliverables:**
- FastAPI app scaffold
- config loading from environment
- dependency setup
- basic Docker support
- `/healthz` route returning `{ "ok": true }`

**Done when:**
- app starts locally
- health endpoint works
- config errors fail clearly

---

### Slice 2: SQLite storage layer
**Goal:** Create the durable storage model.

**Deliverables:**
- SQLite connection management
- schema creation / migrations for `events`
- indexes from spec
- WAL mode enabled
- basic repository functions for insert, claim, ack, update error

**Done when:**
- DB file is created on a mounted path
- schema matches `SCHEMA_API_SPEC.md`
- unit tests cover insert and fetch basics

---

### Slice 3: GitHub webhook validation
**Goal:** Safely accept real GitHub webhook traffic.

**Deliverables:**
- `POST /github/webhook`
- `X-Hub-Signature-256` validation
- required header validation
- payload parsing and metadata extraction
- persistence of validated events

**Done when:**
- valid signed webhook is accepted and stored
- invalid signature returns 401
- malformed request returns 400

---

### Slice 4: Idempotent duplicate handling
**Goal:** Prevent duplicate GitHub deliveries from creating queue soup.

**Deliverables:**
- `github_delivery_id` uniqueness or equivalent idempotency logic
- duplicate delivery behavior per spec
- response includes `duplicate=true` when appropriate

**Done when:**
- same GitHub delivery can be posted twice without creating two actionable rows
- tests prove duplicate handling

---

### Slice 5: Claim endpoint
**Goal:** Let the drainer safely fetch work.

**Deliverables:**
- authenticated `POST /api/v1/drain/claim`
- atomic claim logic
- lease timeout handling
- bounded batch selection
- oldest-first claim order

**Done when:**
- claim returns pending events
- claimed rows are marked with `claimed_at`, `claim_expires_at`, `claimed_by`
- concurrent claims do not hand out the same event twice

---

### Slice 6: Ack endpoint
**Goal:** Mark successful processing explicitly.

**Deliverables:**
- authenticated `POST /api/v1/drain/ack`
- batch ack support
- idempotent ack behavior
- ownership checks against `consumer_id`

**Done when:**
- claimed events move to `acked`
- already acked events are harmless
- not-owned events are reported cleanly

---

### Slice 7: Lease expiry and retry behavior
**Goal:** Make interrupted processing recoverable.

**Deliverables:**
- expired claimed events become eligible for reclaim
- `retry_count` increments on reclaim after lease expiry
- retry threshold moves events to `dead`

**Done when:**
- unacked claimed events reappear after lease timeout
- excessive retries land in `dead`
- tests cover reclaim behavior

---

### Slice 8: Local drainer
**Goal:** Build the consumer that runs on Pan's machine.

**Deliverables:**
- configurable poll loop
- calls claim endpoint
- processes events locally
- batches successful ack calls
- logs failures clearly

**Done when:**
- drainer can fetch and ack test events end-to-end
- drainer survives empty polls and relay restarts
- drainer can be stopped mid-batch without silent event loss

---

### Slice 9: Failure reporting endpoint (optional but recommended)
**Goal:** Improve observability for local processing failures.

**Deliverables:**
- authenticated `POST /api/v1/drain/fail`
- `last_error` recording
- optional requeue behavior

**Done when:**
- drainer can report a local failure reason
- operator can inspect the failure later

---

### Slice 10: Debug/inspection tools
**Goal:** Make the system inspectable when something goes sideways.

**Deliverables:**
- authenticated `GET /api/v1/events`
- filters by status / repo / event type
- clear event serialization for debugging

**Done when:**
- recent queue state can be inspected without opening SQLite manually

---

### Slice 11: Deployment packaging
**Goal:** Make Synology deployment boring.

**Deliverables:**
- `Dockerfile`
- `docker-compose.yml`
- mounted persistent volume for SQLite
- `cloudflared` wiring
- environment variable documentation

**Done when:**
- the stack can be launched on Synology with a persistent DB
- restart does not lose queued events

---

### Slice 12: Recovery and ops docs
**Goal:** Prevent future confusion at 11 PM.

**Deliverables:**
- README deployment steps
- webhook setup instructions for GitHub
- how to rotate secrets
- how to inspect pending/dead events
- how to replay or manually recover stuck work

**Done when:**
- a future human can operate the system without reverse-engineering the code

## Nice-to-have after v1
Do not front-load these.

- replay endpoint
- metrics endpoint
- repository-level filtering for consumers
- multiple consumers with partitioning
- dead-letter replay tooling
- admin UI
- Redis or Postgres backend abstraction

## Acceptance checklist for the whole project
Before calling v1 done, verify:
- valid GitHub webhooks are durably stored
- duplicate deliveries do not create duplicate actionable work
- drainer can claim, process, and ack events
- drainer interruption does not silently lose work
- expired claims become retryable
- retry exhaustion is visible via `dead` state
- queue contents are inspectable
- restart of relay container does not wipe the event store

## Strong suggestion to Pan
Do not try to finish the whole system in one pass.
Ship one slice at a time, with tests.
A relay that only accepts and stores webhooks correctly is useful progress.
A magical all-in-one relay that mostly works is how home-lab projects become folklore.
