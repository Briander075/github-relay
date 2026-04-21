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
