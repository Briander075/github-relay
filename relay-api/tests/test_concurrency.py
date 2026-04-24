#!/usr/bin/env python3
"""Concurrency tests for GitHub Relay database operations."""

import sqlite3
import threading
from pathlib import Path
import tempfile
import pytest

# Add the src directory to the path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Set up test environment
DB_PATH = Path(tempfile.mktemp(suffix=".db"))


@pytest.fixture(scope="function", autouse=True)
def setup_db():
    """Set up database path and ensure cleanup."""
    import os
    os.environ["DB_PATH"] = str(DB_PATH)
    
    from database import init_db, get_connection, get_db_path
    from repository import insert_event, get_events_by_github_delivery_id, query_events
    
    # Initialize database
    init_db()
    
    # Clear any existing thread-local connections
    from database import invalidate_thread_local_connection
    invalidate_thread_local_connection()
    
    yield
    
    # Cleanup
    if DB_PATH.exists():
        DB_PATH.unlink()
        wal_path = DB_PATH.with_suffix('.db-wal')
        shm_path = DB_PATH.with_suffix('.db-shm')
        if wal_path.exists():
            wal_path.unlink()
        if shm_path.exists():
            shm_path.unlink()
    
    # Clear thread-local connections
    from database import invalidate_thread_local_connection
    invalidate_thread_local_connection()


def test_concurrent_duplicate_delivery():
    """Test concurrent duplicate delivery insertions (race condition test).
    
    This test verifies that when two threads try to insert the same
    github_delivery_id simultaneously, the database handles it correctly
    and only creates one event (idempotent behavior).
    """
    from database import get_connection, get_db_path
    from repository import insert_event, get_events_by_github_delivery_id, query_events
    
    # Clear any existing thread-local connections
    from database import invalidate_thread_local_connection
    invalidate_thread_local_connection()
    
    # Shared results
    results = {}
    errors = []
    
    def insert_for_thread(thread_id):
        try:
            # Get a fresh connection for this thread
            conn = sqlite3.connect(str(get_db_path()), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            
            # Both threads use the SAME github_delivery_id
            event_id = str(__import__('uuid').uuid4())
            now = __import__('datetime').datetime.utcnow().isoformat() + "Z"
            
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO events (
                        id, github_delivery_id, github_event_type, github_hook_id,
                        repository_full_name, repository_id, installation_id, action,
                        status, received_at, claimed_at, claim_expires_at, claimed_by,
                        acked_at, dead_at, retry_count, last_error, payload_json,
                        headers_json, signature_valid, duplicate_of_event_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event_id, "concurrent-delivery-same", "push", None, None, None, None, None,
                     "pending", now, None, None, None, None, None, 0, None,
                     '{"ref": "main"}', None, 1, None, now, now)
                )
                conn.commit()
                results[thread_id] = event_id
            except sqlite3.IntegrityError as e:
                # Handle race condition - another thread inserted first
                conn.rollback()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM events WHERE github_delivery_id = ?",
                    ("concurrent-delivery-same",)
                )
                row = cursor.fetchone()
                if row:
                    results[thread_id] = row[0]
                else:
                    raise Exception(f"IntegrityError but no event found: {e}")
            finally:
                conn.close()
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")
    
    # Run two threads with the SAME delivery ID
    t1 = threading.Thread(target=insert_for_thread, args=(1,))
    t2 = threading.Thread(target=insert_for_thread, args=(2,))
    
    # Start both threads simultaneously
    t1.start()
    t2.start()
    
    # Wait for both to complete
    t1.join(timeout=10)
    t2.join(timeout=10)
    
    # Check for errors
    if errors:
        raise Exception(f"Thread errors: {errors}")
    
    # Both threads should have the same event_id
    assert len(results) == 2, "Both threads should have inserted successfully"
    assert results[1] == results[2], "Both threads should have the same event_id"
    
    # Verify only one event exists in the database
    event = get_events_by_github_delivery_id("concurrent-delivery-same")
    assert event is not None, "Event should exist"
    assert event["id"] == results[1], "Event ID should match"
    
    # Should only have one event for this delivery ID
    events_list = query_events()
    assert len(events_list) == 1, f"Should only have one event, got {len(events_list)}"
