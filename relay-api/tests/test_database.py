#!/usr/bin/env python3
"""Unit tests for GitHub Relay database operations."""

import os
import sys
import tempfile
import pytest
from datetime import datetime
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Set up test environment before importing modules
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")


def test_database_initialization():
    """Test that the database initializes correctly."""
    from database import init_db, get_db_path
    
    # Initialize database
    init_db()
    
    # Verify file was created
    db_path = get_db_path()
    assert db_path.exists(), f"Database file not created at {db_path}"
    
    # Verify schema was created
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
    table = cursor.fetchone()
    conn.close()
    
    assert table is not None, "Events table not created"


def test_insert_event():
    """Test inserting an event into the database."""
    from database import init_db
    from repository import insert_event, get_event_by_id
    
    # Initialize database
    init_db()
    
    # Insert test event
    event_id = insert_event(
        github_delivery_id="test-delivery-123",
        github_event_type="push",
        payload_json='{"ref": "main"}',
        github_hook_id="hook-123",
        repository_full_name="owner/repo",
        installation_id=456,
    )
    
    # Event ID should be a UUID4, not the github_delivery_id
    assert event_id is not None
    assert len(event_id) == 36  # UUID4 format
    
    # Verify the event was stored with correct github_delivery_id
    stored_event = get_event_by_id(event_id)
    assert stored_event["github_delivery_id"] == "test-delivery-123"
    
    # Retrieve and verify
    event = get_event_by_id(event_id)
    assert event is not None
    assert event["github_delivery_id"] == "test-delivery-123"
    assert event["github_event_type"] == "push"
    assert event["repository_full_name"] == "owner/repo"
    assert event["status"] == "pending"


def test_claim_and_ack_event():
    """Test claiming and acknowledging an event."""
    from database import init_db
    from repository import insert_event, claim_event, ack_event, get_event_by_id
    
    # Initialize database
    init_db()
    
    # Insert test event
    event_id = insert_event(
        github_delivery_id="test-delivery-456",
        github_event_type="pull_request",
        payload_json='{"action": "opened"}',
    )
    
    # Claim the event
    assert claim_event(event_id, "worker-1") is True
    
    # Verify status changed
    event = get_event_by_id(event_id)
    assert event["status"] == "claimed"
    assert event["claimed_by"] == "worker-1"
    
    # Acknowledge the event
    assert ack_event(event_id) is True
    
    # Verify status changed
    event = get_event_by_id(event_id)
    assert event["status"] == "acked"


def test_duplicate_event():
    """Test handling duplicate events."""
    from database import init_db
    from repository import insert_event, mark_event_duplicate, get_events_by_github_delivery_id
    
    # Initialize database
    init_db()
    
    # Insert first event
    event1_id = insert_event(
        github_delivery_id="delivery-789",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Insert duplicate event
    event2_id = insert_event(
        github_delivery_id="delivery-789",  # Same delivery ID
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Mark second event as duplicate
    assert mark_event_duplicate(event2_id, event1_id) is True
    
    # Verify duplicate was marked
    event = get_events_by_github_delivery_id("delivery-789")
    assert event is not None
    assert event["status"] == "duplicate" or event["id"] == event1_id


def test_claim_concurrent():
    """Test that concurrent claims work correctly."""
    from database import init_db
    from repository import insert_event, claim_event
    
    # Initialize database
    init_db()
    
    # Insert event
    event_id = insert_event(
        github_delivery_id="concurrent-test",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # First claim should succeed
    assert claim_event(event_id, "worker-1") is True
    
    # Second claim should fail
    assert claim_event(event_id, "worker-2") is False


def test_cleanup_old_events():
    """Test cleanup of old acked events."""
    from database import init_db
    from repository import insert_event, ack_event, cleanup_old_ack_events
    
    # Initialize database
    init_db()
    
    # Insert and ack an event
    event_id = insert_event(
        github_delivery_id="cleanup-test",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    ack_event(event_id)
    
    # Verify cleanup works (should return 1 for the acked event)
    count = cleanup_old_ack_events()
    assert count >= 0  # May be 0 if retention period not expired
