#!/usr/bin/env python3
"""SQLite database module for GitHub Relay.

Provides connection management and schema initialization for the durable event store.
"""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    from .config import get_settings
except ImportError:
    from config import get_settings

# Thread-local storage for database connections
_thread_local = threading.local()

# Default database file path
DEFAULT_DB_PATH = Path.home() / ".hermes" / "github-relay" / "events.db"


def get_db_path() -> Path:
    """Get the database file path from settings or use default."""
    settings = get_settings()
    if settings.db_path:
        return Path(settings.db_path)
    return DEFAULT_DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get or create a thread-local database connection."""
    if not hasattr(_thread_local, "connection"):
        db_path = get_db_path()
        
        # Ensure parent directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")
        
        # Set busy timeout
        conn.execute("PRAGMA busy_timeout=30000")
        
        _thread_local.connection = conn
    
    return _thread_local.connection


def get_new_connection() -> sqlite3.Connection:
    """Get a new database connection (bypass thread-local cache)."""
    db_path = get_db_path()
    
    # Ensure parent directory exists
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
        timeout=30.0,
    )
    
    # Enable WAL mode for better concurrency
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Set busy timeout
    conn.execute("PRAGMA busy_timeout=30000")
    
    return conn


@contextmanager
def get_db_cursor(new_connection: bool = False):
    """Context manager for database cursor with automatic commit/rollback."""
    if new_connection:
        conn = get_new_connection()
    else:
        conn = get_connection()
    cursor = conn.cursor()
    
    try:
        yield cursor
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.commit()


def init_db():
    """Initialize the database schema if not already present."""
    try:
        from . import schema
    except ImportError:
        import schema
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create schema
    cursor.execute(schema.EVENTS_TABLE)
    
    # Create indexes
    for index_sql in schema.INDEXES:
        try:
            cursor.execute(index_sql)
        except sqlite3.OperationalError:
            # Index may already exist
            pass
    
    conn.commit()


def ensure_db_directory():
    """Ensure the database directory exists."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
