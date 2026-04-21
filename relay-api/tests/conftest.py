#!/usr/bin/env python3
"""Pytest configuration for GitHub Relay tests."""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime


def cleanup_database(db_path: Path):
    """Clean up the test database file."""
    if db_path.exists():
        db_path.unlink()
    
    # Also remove WAL files
    wal_path = db_path.with_suffix('.db-wal')
    shm_path = db_path.with_suffix('.db-shm')
    
    if wal_path.exists():
        wal_path.unlink()
    if shm_path.exists():
        shm_path.unlink()


@pytest.fixture(scope="function", autouse=True)
def cleanup_test_db():
    """Cleanup the test database file between tests."""
    # Get the test database path from environment or use default
    db_path_str = os.getenv("DB_PATH")
    if db_path_str:
        db_path = Path(db_path_str)
    else:
        # Import config after adding to path
        from config import get_settings
        db_path = get_settings().db_path
        if not db_path:
            db_path = Path.home() / ".hermes" / "github-relay" / "events.db"
    
    # Clean up before each test
    if db_path.exists():
        cleanup_database(db_path)
    
    yield
    
    # Clean up after each test
    if db_path.exists():
        cleanup_database(db_path)


@pytest.fixture(scope="function")
def temp_db_path():
    """Create a temporary database path for each test."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test_events.db"
    
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    yield db_path
    
    # Clean up after test
    if db_path.parent.exists():
        shutil.rmtree(db_path.parent)


@pytest.fixture(scope="function")
def patch_db_path(temp_db_path):
    """Patch the database path for the duration of the test and initialize it."""
    with patch.dict(os.environ, {"DB_PATH": str(temp_db_path)}, clear=True):
        # Re-initialize settings to pick up the new DB_PATH
        if "relay-api.src.config" in sys.modules:
            del sys.modules["relay-api.src.config"]
        if "relay-api.src.database" in sys.modules:
            del sys.modules["relay-api.src.database"]
        
        # Initialize the database fresh for each test
        init_db()
        yield temp_db_path


@pytest.fixture(scope="function")
def cleanup_thread_local():
    """Clean up thread-local database connections after each test."""
    yield
    cleanup_thread_local_connections()


def cleanup_thread_local_connections():
    """Clean up thread-local database connections."""
    if "relay-api.src.database" in sys.modules:
        database_module = sys.modules["relay-api.src.database"]
        if hasattr(database_module, "_thread_local"):
            if hasattr(database_module._thread_local, "connection"):
                conn = database_module._thread_local.connection
                if conn:
                    conn.close()
                delattr(database_module._thread_local, "connection")


def test_insert_event(patch_db_path, cleanup_thread_local):
    """Test inserting a new event."""
    event_id = insert_event(
        github_delivery_id="test_delivery_123",
        github_event_type="push",
        payload_json='{"ref": "main", "commits": []}',
    )
    
    assert event_id is not None
    assert isinstance(event_id, str)


def test_get_pending_event(patch_db_path, cleanup_thread_local):
    """Test retrieving a pending event."""
    # Insert an event
    event_id = insert_event(
        github_delivery_id="test_delivery_456",
        github_event_type="pull_request",
        payload_json='{"action": "opened"}',
    )
    
    # Retrieve the pending event
    event = get_pending_event()
    
    assert event is not None
    assert event["id"] == event_id
    assert event["status"] == "pending"
    assert event["github_event_type"] == "pull_request"


def test_get_pending_event_empty(patch_db_path, cleanup_thread_local):
    """Test getting pending event when none exist."""
    # Should return None when no pending events
    event = get_pending_event()
    assert event is None


def test_claim_event(patch_db_path, cleanup_thread_local):
    """Test claiming an event for processing."""
    # Insert and claim an event
    event_id = insert_event(
        github_delivery_id="test_delivery_789",
        github_event_type="push",
        payload_json='{"ref": "develop"}',
    )
    
    claimed = claim_event(event_id, "drainer-worker-1")
    
    assert claimed is True
    
    # Verify the event status changed
    event = get_event_by_id(event_id)
    assert event["status"] == "claimed"
    assert event["claimed_by"] == "drainer-worker-1"
    assert event["claimed_at"] is not None


def test_claim_event_already_claimed(patch_db_path, cleanup_thread_local):
    """Test claiming an already claimed event returns False."""
    event_id = insert_event(
        github_delivery_id="test_delivery_abc",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # First claim
    assert claim_event(event_id, "worker-1") is True
    
    # Second claim should fail
    assert claim_event(event_id, "worker-2") is False


def test_ack_event(patch_db_path, cleanup_thread_local):
    """Test acknowledging a claimed event."""
    event_id = insert_event(
        github_delivery_id="test_delivery_def",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Claim then ack
    assert claim_event(event_id, "worker-1") is True
    assert ack_event(event_id) is True
    
    # Verify status changed
    event = get_event_by_id(event_id)
    assert event["status"] == "acked"
    assert event["acked_at"] is not None


def test_ack_event_not_claims(patch_db_path, cleanup_thread_local):
    """Test acking a non-claimed event returns False."""
    event_id = insert_event(
        github_delivery_id="test_delivery_ghi",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Can't ack a pending event
    assert ack_event(event_id) is False


def test_update_event_error(patch_db_path, cleanup_thread_local):
    """Test updating event with error for retry."""
    event_id = insert_event(
        github_delivery_id="test_delivery_jkl",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Update with error
    assert update_event_error(event_id, "Connection timeout") is True
    
    event = get_event_by_id(event_id)
    assert event["status"] == "pending"  # Should stay pending
    assert event["retry_count"] == 1
    assert event["last_error"] == "Connection timeout"


def test_mark_event_dead(patch_db_path, cleanup_thread_local):
    """Test marking an event as dead after retries."""
    event_id = insert_event(
        github_delivery_id="test_delivery_mno",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Mark as dead
    assert mark_event_dead(event_id) is True
    
    event = get_event_by_id(event_id)
    assert event["status"] == "dead"
    assert event["dead_at"] is not None


def test_mark_event_duplicate(patch_db_path, cleanup_thread_local):
    """Test marking an event as a duplicate."""
    original_event_id = insert_event(
        github_delivery_id="test_delivery_001",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    duplicate_event_id = insert_event(
        github_delivery_id="test_delivery_001",  # Same GitHub delivery ID
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Mark duplicate
    assert mark_event_duplicate(duplicate_event_id, original_event_id) is True
    
    event = get_event_by_id(duplicate_event_id)
    assert event["status"] == "duplicate"
    assert event["duplicate_of_event_id"] == original_event_id


def test_get_event_by_id(patch_db_path, cleanup_thread_local):
    """Test retrieving an event by ID."""
    event_id = insert_event(
        github_delivery_id="test_delivery_pqr",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    event = get_event_by_id(event_id)
    
    assert event is not None
    assert event["id"] == event_id
    assert event["github_event_type"] == "push"


def test_get_events_by_github_delivery_id(patch_db_path, cleanup_thread_local):
    """Test retrieving an event by GitHub delivery ID."""
    event_id = insert_event(
        github_delivery_id="test_delivery_stu",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    event = get_events_by_github_delivery_id("test_delivery_stu")
    
    assert event is not None
    assert event["id"] == event_id
    assert event["github_delivery_id"] == "test_delivery_stu"


def test_get_event_count(patch_db_path, cleanup_thread_local):
    """Test getting event counts by status."""
    # Insert some events
    event_id1 = insert_event(
        github_delivery_id="test_delivery_vwx",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    event_id2 = insert_event(
        github_delivery_id="test_delivery_yza",
        github_event_type="pull_request",
        payload_json='{"action": "opened"}',
    )
    
    # Claim one event
    claim_event(event_id1, "worker-1")
    
    # Get counts
    counts = get_event_count()
    
    assert "pending" in counts
    assert "claimed" in counts
    assert "acked" in counts
    assert "dead" in counts
    assert "duplicate" in counts
    
    # Verify counts
    assert counts["pending"] == 1  # event_id2
    assert counts["claimed"] == 1  # event_id1


def test_ensure_utc_iso8601():
    """Test the ensure_utc_iso8601 helper function."""
    # Test with None
    result = ensure_utc_iso8601(None)
    assert result.endswith("Z")
    
    # Test with UTC datetime
    dt = datetime.utcnow()
    result = ensure_utc_iso8601(dt)
    assert result.endswith("Z")
    
    # Test with timezone-aware datetime
    dt = datetime.fromisoformat("2024-01-01T12:00:00+00:00")
    result = ensure_utc_iso8601(dt)
    assert result.endswith("Z")


def test_cleanup_old_ack_events(patch_db_path, cleanup_thread_local):
    """Test cleaning up old acked events."""
    # Insert and ack an event
    event_id = insert_event(
        github_delivery_id="test_delivery_cleanup",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Claim and ack
    claim_event(event_id, "worker-1")
    ack_event(event_id)
    
    # Cleanup should work without error
    deleted = cleanup_old_ack_events()
    assert isinstance(deleted, int)


def test_duplicate_delivery_id_handling(patch_db_path, cleanup_thread_local):
    """Test handling of duplicate GitHub delivery IDs."""
    # Insert first event
    event_id1 = insert_event(
        github_delivery_id="same_delivery_id",
        github_event_type="push",
        payload_json='{"ref": "main"}',
    )
    
    # Insert second event with same delivery ID
    event_id2 = insert_event(
        github_delivery_id="same_delivery_id",
        github_event_type="push",
        payload_json='{"ref": "develop"}',
    )
    
    # Both should exist with different IDs
    event1 = get_event_by_id(event_id1)
    event2 = get_event_by_id(event_id2)
    
    assert event1["id"] != event2["id"]
    assert event1["github_delivery_id"] == event2["github_delivery_id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
