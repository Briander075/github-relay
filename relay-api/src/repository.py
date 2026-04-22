#!/usr/bin/env python3
"""Repository module for GitHub Relay event storage.

Provides CRUD operations for events using SQLite.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

try:
    from .config import get_settings
    from .database import get_connection, get_db_cursor
except ImportError:
    from config import get_settings
    from database import get_connection, get_db_cursor


def ensure_utc_iso8601(dt: Optional[datetime]) -> str:
    """Convert datetime to UTC ISO8601 string."""
    if dt is None:
        return datetime.utcnow().isoformat() + "Z"
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    # Convert to UTC
    utc_dt = dt.utctimetuple()
    return datetime(*utc_dt[:6]).isoformat() + "Z"


def insert_event(
    github_delivery_id: str,
    github_event_type: str,
    payload_json: str,
    headers_json: Optional[str] = None,
    signature_valid: int = 1,
    github_hook_id: Optional[str] = None,
    repository_full_name: Optional[str] = None,
    repository_id: Optional[int] = None,
    installation_id: Optional[int] = None,
    action: Optional[str] = None,
    duplicate_of_event_id: Optional[str] = None,
) -> str:
    """Insert a new event into the database.
    
    Returns the event ID.
    """
    # Generate a unique event ID (UUID4)
    event_id = str(uuid.uuid4())
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO events (
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                github_delivery_id,
                github_event_type,
                github_hook_id,
                repository_full_name,
                repository_id,
                installation_id,
                action,
                "pending",
                now,
                None,
                None,
                None,
                None,
                None,
                0,
                None,
                payload_json,
                headers_json,
                signature_valid,
                duplicate_of_event_id,
                now,
                now,
            ),
        )
    
    return event_id


def get_pending_event() -> Optional[Dict[str, Any]]:
    """Get a pending event for claiming.
    
    Returns the event data dictionary or None if no pending events.
    """
    with get_db_cursor() as cursor:
        settings = get_settings()
        
        # Find oldest pending event
        cursor.execute(
            """
            SELECT 
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            FROM events
            WHERE status = 'pending'
            ORDER BY received_at ASC
            LIMIT 1
            """
        )
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # Get column names
        columns = [description[0] for description in cursor.description]
        
        # Convert to dictionary
        result = dict(zip(columns, row))
        
        # Convert datetime strings back to datetime objects
        for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
            if result.get(field):
                result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
        
        return result


def claim_events(limit: int, consumer_id: str, lease_seconds: int) -> List[Dict[str, Any]]:
    """Atomically claim a batch of pending events for processing.
    
    Returns a list of claimed event dictionaries.
    Includes both pending and expired claimed events for reclaim.
    Only returns events claimed in the current operation.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    claim_expires = ensure_utc_iso8601(
        datetime.utcnow() + timedelta(seconds=lease_seconds)
    )
    
    with get_db_cursor() as cursor:
        # First, get the list of event IDs that will be claimed
        cursor.execute(
            """
            SELECT id FROM events
            WHERE status IN ('pending', 'claimed')
            AND (
                status = 'pending' 
                OR (status = 'claimed' AND claim_expires_at < ?)
            )
            ORDER BY received_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        event_ids_to_claim = [row[0] for row in cursor.fetchall()]
        
        if not event_ids_to_claim:
            return []
        
        # Claim eligible events (pending or expired claimed)
        cursor.execute(
            """
            UPDATE events
            SET 
                status = 'claimed',
                claimed_at = ?,
                claim_expires_at = ?,
                claimed_by = ?,
                updated_at = ?
            WHERE id IN (
                SELECT id FROM events
                WHERE status IN ('pending', 'claimed')
                AND (
                    status = 'pending' 
                    OR (status = 'claimed' AND claim_expires_at < ?)
                )
                ORDER BY received_at ASC
                LIMIT ?
            )
            """,
            (now, claim_expires, consumer_id, now, now, limit),
        )
        
        # Get only the events claimed in this operation (by their IDs)
        cursor.execute(
            """
            SELECT 
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            FROM events
            WHERE id IN (""" + ",".join("?" * len(event_ids_to_claim)) + """)
            ORDER BY received_at ASC
            """,
            event_ids_to_claim,
        )
        
        rows = cursor.fetchall()
        results = []
        for row in rows:
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row))
            # Convert datetime strings back to datetime objects
            for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
                if result.get(field):
                    result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
            results.append(result)
        
        return results


def reclaim_expired_events(consumer_id: str, lease_seconds: int) -> List[Dict[str, Any]]:
    """Reclaim events that expired before the current lease.
    
    Increments retry_count for reclaimed events. Returns the events that were reclaimed.
    """
    settings = get_settings()
    max_retries = settings.max_retries
    now = ensure_utc_iso8601(datetime.utcnow())
    claim_expires = ensure_utc_iso8601(
        datetime.utcnow() + timedelta(seconds=lease_seconds)
    )
    
    reclaimed = []
    expired_to_reclaim = []
    dead_events = []
    
    with get_db_cursor() as cursor:
        # Find expired claimed events that haven't exceeded max retries
        cursor.execute(
            """
            SELECT id, retry_count FROM events
            WHERE status = 'claimed'
            AND claimed_by IS NOT NULL
            AND claim_expires_at < ?
            AND retry_count < ?
            """,
            (now, max_retries),
        )
        expired_events = cursor.fetchall()
        
        for row in expired_events:
            event_id = row[0]
            retry_count = row[1]
            expired_to_reclaim.append(event_id)
            
            # Increment retry count
            cursor.execute(
                """
                UPDATE events
                SET 
                    retry_count = retry_count + 1,
                    claimed_at = ?,
                    claim_expires_at = ?,
                    claimed_by = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, claim_expires, consumer_id, now, event_id),
            )
            
            # Check if now exceeds max retries
            new_retry_count = retry_count + 1
            if new_retry_count >= max_retries:
                # Mark as dead
                cursor.execute(
                    """
                    UPDATE events
                    SET 
                        status = 'dead',
                        dead_at = ?,
                        updated_at = ?,
                        last_error = ?,
                        claimed_by = NULL,
                        claim_expires_at = NULL
                    WHERE id = ?
                    """,
                    (now, now, f"Exceeded max retries ({max_retries})", event_id),
                )
                dead_events.append(event_id)
    
    # Fetch the reclaimed events
    if expired_to_reclaim:
        placeholders = ",".join("?" * len(expired_to_reclaim))
        cursor.execute(
            f"""
            SELECT 
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            FROM events
            WHERE id IN ({placeholders})
            ORDER BY received_at ASC
            """,
            expired_to_reclaim,
        )
        rows = cursor.fetchall()
        for row in rows:
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row))
            for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
                if result.get(field):
                    result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
            reclaimed.append(result)
    
    return reclaimed


def claim_event(event_id: str, claim_by: str) -> bool:
    """Claim an event for processing.
    
    Returns True if claim was successful, False if event was already claimed.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    claim_expires = ensure_utc_iso8601(
        datetime.utcnow() + timedelta(seconds=get_settings().lease_seconds)
    )
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET 
                status = 'claimed',
                claimed_at = ?,
                claim_expires_at = ?,
                claimed_by = ?,
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, claim_expires, claim_by, now, event_id),
        )
        
        return cursor.rowcount > 0


def ack_event(event_id: str) -> bool:
    """Acknowledge successful processing of an event.
    
    Returns True if ack was successful, False if event not found.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET 
                status = 'acked',
                acked_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'claimed'
            """,
            (now, now, event_id),
        )
        
        return cursor.rowcount > 0


def report_failure(event_id: str, error_message: str, requeue: bool = False) -> Dict[str, Any]:
    """Report a processing failure for an event.
    
    Updates last_error and optionally requeues the event to 'pending' state.
    Returns a dict with 'success' and 'status' fields.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        # First, verify the event exists and get current state
        cursor.execute(
            """
            SELECT id, status, claimed_by, retry_count FROM events WHERE id = ?
            """,
            (event_id,),
        )
        row = cursor.fetchone()
        
        if not row:
            return {"success": False, "error": "Event not found"}
        
        event_id, status, claimed_by, retry_count = row
        
        if status not in ("pending", "claimed"):
            return {"success": False, "error": f"Event is in {status} status, not processable"}
        
        # Update last_error
        cursor.execute(
            """
            UPDATE events
            SET 
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (error_message, now, event_id),
        )
        
        if requeue and status == "claimed":
            # Return to pending state
            cursor.execute(
                """
                UPDATE events
                SET 
                    status = 'pending',
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    retry_count = retry_count + 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, event_id),
            )
            return {"success": True, "status": "requeued"}
        
        return {"success": True, "status": status}


def ack_events(event_ids: List[str], consumer_id: str) -> Dict[str, List[str]]:
    """Acknowledge multiple successfully processed events.
    
    Returns dict with 'acked', 'already_acked', 'not_found', 'not_owned' lists.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    results = {
        "acked": [],
        "already_acked": [],
        "not_found": [],
        "not_owned": []
    }
    
    with get_db_cursor() as cursor:
        for event_id in event_ids:
            # Check current state
            cursor.execute(
                """
                SELECT id, status, claimed_by FROM events WHERE id = ?
                """,
                (event_id,),
            )
            row = cursor.fetchone()
            
            if not row:
                results["not_found"].append(event_id)
            elif row[1] == "acked":
                results["already_acked"].append(event_id)
            elif row[1] == "claimed" and row[2] == consumer_id:
                # Update to acked
                cursor.execute(
                    """
                    UPDATE events
                    SET 
                        status = 'acked',
                        acked_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, event_id),
                )
                results["acked"].append(event_id)
            else:
                # Event not claimed by this consumer or already acked
                results["not_owned"].append(event_id)
    
    return results


def update_event_error(event_id: str, error_message: str) -> bool:
    """Update event with error information for retry.
    
    Returns True if update was successful, False if event not found.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET 
                retry_count = retry_count + 1,
                last_error = ?,
                updated_at = ?
            WHERE id = ? AND status IN ('pending', 'claimed')
            """,
            (error_message, now, event_id),
        )
        
        return cursor.rowcount > 0


def mark_event_dead(event_id: str) -> bool:
    """Mark an event as dead after too many retries.
    
    Returns True if update was successful, False if event not found.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET 
                status = 'dead',
                dead_at = ?,
                updated_at = ?
            WHERE id = ? AND status IN ('pending', 'claimed')
            """,
            (now, now, event_id),
        )
        
        return cursor.rowcount > 0


def query_events(
    status: Optional[str] = None,
    repository: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Query events with filtering options for debugging and inspection.
    
    Args:
        status: Filter by event status (pending, claimed, acked, dead, duplicate)
        repository: Filter by repository full name (e.g., 'owner/repo')
        event_type: Filter by GitHub event type (e.g., 'push', 'pull_request')
        limit: Maximum number of results (default 50)
        offset: Offset for pagination (default 0)
    
    Returns:
        List of event dictionaries matching the filters.
    """
    # Build the query dynamically based on provided filters
    query = """
        SELECT 
            id, github_delivery_id, github_event_type, github_hook_id,
            repository_full_name, repository_id, installation_id, action,
            status, received_at, claimed_at, claim_expires_at, claimed_by,
            acked_at, dead_at, retry_count, last_error, payload_json,
            headers_json, signature_valid, duplicate_of_event_id,
            created_at, updated_at
        FROM events
    """
    
    conditions = []
    params = []
    
    if status:
        conditions.append("status = ?")
        params.append(status)
    
    if repository:
        conditions.append("repository_full_name = ?")
        params.append(repository)
    
    if event_type:
        conditions.append("github_event_type = ?")
        params.append(event_type)
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY received_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    with get_db_cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row))
            # Convert datetime strings back to datetime objects
            for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
                if result.get(field):
                    result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
            results.append(result)
        
        return results


def mark_event_duplicate(event_id: str, duplicate_of_event_id: str) -> bool:
    """Mark an event as a duplicate of another event.
    
    Returns True if update was successful, False if event not found.
    """
    now = ensure_utc_iso8601(datetime.utcnow())
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE events
            SET 
                status = 'duplicate',
                duplicate_of_event_id = ?,
                updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (duplicate_of_event_id, now, event_id),
        )
        
        return cursor.rowcount > 0


def get_event_by_id(event_id: str) -> Optional[Dict[str, Any]]:
    """Get an event by its ID.
    
    Returns the event data dictionary or None if not found.
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT 
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        )
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # Get column names
        columns = [description[0] for description in cursor.description]
        
        # Convert to dictionary
        result = dict(zip(columns, row))
        
        # Convert datetime strings back to datetime objects
        for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
            if result.get(field):
                result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
        
        return result


def get_events_by_github_delivery_id(github_delivery_id: str) -> Optional[Dict[str, Any]]:
    """Get an event by its GitHub delivery ID.
    
    Returns the event data dictionary or None if not found.
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT 
                id, github_delivery_id, github_event_type, github_hook_id,
                repository_full_name, repository_id, installation_id, action,
                status, received_at, claimed_at, claim_expires_at, claimed_by,
                acked_at, dead_at, retry_count, last_error, payload_json,
                headers_json, signature_valid, duplicate_of_event_id,
                created_at, updated_at
            FROM events
            WHERE github_delivery_id = ?
            """,
            (github_delivery_id,),
        )
        
        row = cursor.fetchone()
        if not row:
            return None
        
        # Get column names
        columns = [description[0] for description in cursor.description]
        
        # Convert to dictionary
        result = dict(zip(columns, row))
        
        # Convert datetime strings back to datetime objects
        for field in ["received_at", "claimed_at", "claim_expires_at", "acked_at", "dead_at", "created_at", "updated_at"]:
            if result.get(field):
                result[field] = datetime.fromisoformat(result[field].replace("Z", "+00:00"))
        
        return result


def cleanup_old_ack_events() -> int:
    """Remove events that were acked more than ack_retention_days ago.
    
    Returns the number of events deleted.
    """
    settings = get_settings()
    retention_days = settings.ack_retention_days
    
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM events
            WHERE status = 'acked'
            AND datetime(acked_at) < datetime('now', ?)
            """,
            (f"-{retention_days} days",),
        )
        
        return cursor.rowcount


def get_event_count() -> Dict[str, int]:
    """Get event counts by status.
    
    Returns a dictionary with counts for each status.
    """
    with get_db_cursor() as cursor:
        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM events
            GROUP BY status
            """
        )
        
        result = {}
        for row in cursor.fetchall():
            result[row[0]] = row[1]
        
        # Ensure all statuses are present
        for status in ["pending", "claimed", "acked", "dead", "duplicate"]:
            if status not in result:
                result[status] = 0
        
        return result
