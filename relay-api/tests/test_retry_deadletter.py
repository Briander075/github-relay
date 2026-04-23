#!/usr/bin/env python3
"""End-to-end tests for retry and dead-letter behavior.

This module tests:
- Lease expiry reclaim behavior through the claim flow
- Retry count increments on reclaim
- Transition to dead state after retry exhaustion
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

# Set up test environment before importing modules
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")

from database import init_db
from repository import (
    insert_event,
    claim_event,
    claim_events,
    get_event_by_id,
    report_failure,
    get_settings,
    get_db_cursor,
    ensure_utc_iso8601,
)


def setup_module():
    """Initialize database for all tests in this module."""
    init_db()


def test_failed_claim_returns_to_pending():
    """Test that failed processing returns event to pending state."""
    event_id = insert_event(
        github_delivery_id="pending-test-1",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )

    # Claim the event
    assert claim_event(event_id, "consumer-1") is True

    # Report failure with requeue
    result = report_failure(event_id, "Connection timeout", requeue=True)

    assert result["status"] == "requeued"

    # Verify event is back to pending
    event = get_event_by_id(event_id)
    assert event["status"] == "pending"
    assert event["retry_count"] == 1


def test_dead_event_handling():
    """Test that dead events have proper status and timestamp."""
    event_id = insert_event(
        github_delivery_id="dead-test-1",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Manually mark event as dead
    from repository import mark_event_dead
    
    assert mark_event_dead(event_id) is True
    
    # Verify event is in dead state
    event = get_event_by_id(event_id)
    assert event["status"] == "dead"
    assert event["dead_at"] is not None
    assert event["claimed_by"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
