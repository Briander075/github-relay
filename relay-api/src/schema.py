#!/usr/bin/env python3
"""Database schema definitions for GitHub Relay.

This module contains the SQL statements for creating the events table and indexes.
"""

# Events table schema
EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
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
)
"""

# Recommended indexes
INDEXES = [
    """CREATE INDEX IF NOT EXISTS idx_events_github_delivery_id
    ON events(github_delivery_id)""",
    
    """CREATE INDEX IF NOT EXISTS idx_events_status_claim_expires_at
    ON events(status, claim_expires_at, received_at)""",
    
    """CREATE INDEX IF NOT EXISTS idx_events_repository_status
    ON events(repository_full_name, status, received_at)""",
]
