#!/usr/bin/env python3
"""GitHub Relay - Webhook Relay Service

This service provides a durable webhook relay for intermittent local machines.
It receives GitHub webhooks and stores them in SQLite for later draining.
"""

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse, PlainTextResponse
import hashlib
import hmac
import json
import logging
import os
import sys
import traceback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

try:
    from .config import get_settings
    from .database import init_db, ensure_db_directory
    from .repository import (
        insert_event,
        get_events_by_github_delivery_id,
        mark_event_duplicate
    )
except ImportError:
    from config import get_settings
    from database import init_db, ensure_db_directory
    from repository import (
        insert_event,
        get_events_by_github_delivery_id,
        mark_event_duplicate,
        claim_events,
        ack_events
    )

app = FastAPI(
    title="GitHub Relay API",
    description="Asynchronous webhook relay for intermittent local machines",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Initialize database on startup
@app.on_event("startup")
def startup_event():
    """Initialize database on startup."""
    ensure_db_directory()
    init_db()
    logger.info("Database initialized successfully")


@app.get("/healthz", tags=["System"])
def health_check_v1():
    """Basic health check endpoint per spec."""
    return {"ok": True}


@app.get("/health", tags=["System"])
def health_check():
    """Health check endpoint for container orchestration and monitoring."""
    try:
        # Test database connectivity
        try:
            from .repository import get_event_count
        except ImportError:
            from repository import get_event_count
        counts = get_event_count()
        
        return {
            "status": "healthy",
            "service": "github-relay-api",
            "version": "0.1.0",
            "timestamp": "2026-04-20T00:00:00Z",
            "database": {
                "pending": counts.get("pending", 0),
                "claimed": counts.get("claimed", 0),
                "acked": counts.get("acked", 0),
                "dead": counts.get("dead", 0),
                "duplicate": counts.get("duplicate", 0)
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "degraded",
            "service": "github-relay-api",
            "version": "0.1.0",
            "timestamp": "2026-04-20T00:00:00Z",
            "error": str(e)
        }, 503

@app.post("/github/webhook", tags=["Webhooks"])
async def receive_github_webhook(
    request: Request,
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_github_hook_id: str = Header(None, alias="X-GitHub-Hook-Id"),
    x_github_signature: str = Header(None, alias="X-GitHub-Signature"),
    x_github_signature_256: str = Header(None, alias="X-GitHub-Signature-256"),
):
    """Receive and store GitHub webhooks for later draining.
    
    This endpoint accepts GitHub webhooks and stores them in a durable SQLite
    database. The events can then be drained by an authorized client.
    """
    # Check required headers
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header")
    
    if not x_github_event:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")
    
    # Signature validation is optional for now - can be enabled via config
    settings = get_settings()
    
    # Verify webhook signature if secret is configured
    signature_valid = 0
    if settings.webhook_secret:
        if not x_github_signature and not x_github_signature_256:
            raise HTTPException(status_code=401, detail="Missing signature header (X-Hub-Signature or X-Hub-Signature-256)")
        
        # Get raw body for signature verification
        body_bytes = await request.body()
        
        # Verify signature using HMAC-SHA256 (GitHub standard)
        signature_to_verify = x_github_signature_256 or x_github_signature
        if signature_to_verify:
            # GitHub uses: HMAC_SHA256(webhook_secret, raw_body)
            expected_sig = "sha256=" + hmac.new(
                settings.webhook_secret.encode(),
                body_bytes,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature_to_verify, expected_sig):
                # Try SHA1 for older webhooks
                expected_sig_sha1 = "sha1=" + hmac.new(
                    settings.webhook_secret.encode(),
                    body_bytes,
                    hashlib.sha1
                ).hexdigest()
                
                if not hmac.compare_digest(signature_to_verify, expected_sig_sha1):
                    logger.warning(f"Invalid signature for delivery {x_github_delivery}")
                    raise HTTPException(status_code=401, detail="Invalid signature")
            signature_valid = 1
        else:
            signature_valid = 1
    else:
        signature_valid = 0
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    # Check for duplicate delivery ID
    existing_event = None
    try:
        existing_event = get_events_by_github_delivery_id(x_github_delivery)
    except Exception as e:
        logger.error(f"Failed to check for duplicate: {e}")
    
    # If duplicate, return the original event_id without inserting
    if existing_event:
        logger.info(f"Received duplicate {x_github_event} event for {x_github_delivery}")
        return {
            "accepted": True,
            "event_id": existing_event["id"],
            "duplicate": True,
            "status": "pending"
        }
    
    # Extract repository information if present
    repository_full_name = None
    repository_id = None
    if "repository" in payload:
        repository_full_name = payload["repository"].get("full_name")
        repository_id = payload["repository"].get("id")
    
    # Extract installation information if present
    installation_id = None
    if "installation" in payload:
        installation_id = payload["installation"].get("id")
    
    # Extract action if present
    action = payload.get("action")
    
    # Insert event into database
    event_id = insert_event(
        github_delivery_id=x_github_delivery,
        github_event_type=x_github_event,
        payload_json=json.dumps(payload),
        headers_json=json.dumps(dict(request.headers)),
        github_hook_id=x_github_hook_id,
        repository_full_name=repository_full_name,
        repository_id=repository_id,
        installation_id=installation_id,
        action=action,
        signature_valid=signature_valid,
        duplicate_of_event_id=None,  # Not a duplicate - new event
    )
    
    logger.info(f"Received {x_github_event} event for {x_github_delivery}")
    
    return {
        "accepted": True,
        "event_id": event_id,
        "duplicate": False,
        "status": "pending"
    }


@app.post("/api/v1/drain/claim", tags=["Drain"])
async def drain_claim(
    request: Request,
    consumer_id: str = Header(..., alias="X-Consumer-Id"),
    limit: int = 10,
    lease_seconds: int = 300,
):
    """Atomically claim a batch of pending events for processing.
    
    Authentication: Bearer token required in Authorization header.
    """
    # Validate bearer token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    settings = get_settings()
    
    if settings.bearer_token and token != settings.bearer_token:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    
    # Claim events
    try:
        claimed_events = claim_events(limit, consumer_id, lease_seconds)
    except Exception as e:
        logger.error(f"Failed to claim events: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to claim events: {str(e)}")
    
    logger.info(f"Claimed {len(claimed_events)} events for consumer {consumer_id}")
    
    # Build response
    events_list = []
    for event in claimed_events:
        events_list.append({
            "event_id": event["id"],
            "github_delivery_id": event["github_delivery_id"],
            "github_event_type": event["github_event_type"],
            "repository_full_name": event["repository_full_name"],
            "action": event["action"],
            "received_at": event["received_at"].isoformat().replace("+00:00", "Z") if event["received_at"] else None,
            "claim_expires_at": event["claim_expires_at"].isoformat().replace("+00:00", "Z") if event["claim_expires_at"] else None,
            "payload": json.loads(event["payload_json"])
        })
    
    return {
        "consumer_id": consumer_id,
        "claimed_count": len(claimed_events),
        "lease_seconds": lease_seconds,
        "events": events_list
    }


@app.get("/stats", tags=["System"])
def get_stats():
    """Get current event statistics."""
    try:
        try:
            from .repository import get_event_count
        except ImportError:
            from repository import get_event_count
        counts = get_event_count()
        
        return {
            "pending": counts.get("pending", 0),
            "claimed": counts.get("claimed", 0),
            "acked": counts.get("acked", 0),
            "dead": counts.get("dead", 0),
            "duplicate": counts.get("duplicate", 0)
        }
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/drain/ack", tags=["Drain"])
async def drain_ack(
    request: Request,
):
    """Acknowledge one or more successfully processed events.
    
    Authentication: Bearer token required in Authorization header.
    """
    from fastapi import Body
    
    settings = get_settings()
    
    # Simple bearer token validation (can be enhanced for production)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    if settings.bearer_token and token != settings.bearer_token:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    
    # Parse request body
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    
    consumer_id = body.get("consumer_id")
    event_ids = body.get("event_ids", [])
    
    if not consumer_id:
        raise HTTPException(status_code=400, detail="Missing consumer_id")
    
    if not event_ids:
        raise HTTPException(status_code=400, detail="Missing event_ids")
    
    # Acknowledge events
    try:
        results = ack_events(event_ids, consumer_id)
    except Exception as e:
        logger.error(f"Failed to ack events: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to ack events: {str(e)}")
    
    logger.info(f"Acknowledged {len(results['acked'])} events for consumer {consumer_id}")
    
    return results


@app.get("/events", tags=["Events"])
def list_events(status: str = None, limit: int = 100, offset: int = 0):
    """List events with optional status filter."""
    try:
        from .repository import get_event_count
    except ImportError:
        from repository import get_event_count
    
    counts = get_event_count()
    
    return {
        "summary": counts,
        "limit": limit,
        "offset": offset,
        # TODO: Implement actual event listing
        "events": []
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
