#!/usr/bin/env python3
"""Configuration management for GitHub Relay."""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=str(env_path))


class Settings:
    """Application settings."""
    
    def __init__(self):
        # Database path
        self.db_path: Optional[str] = os.getenv("DB_PATH")
        
        # Webhook settings
        self.webhook_secret: Optional[str] = os.getenv("GITHUB_WEBHOOK_SECRET")
        
        # Drainer authentication
        self.bearer_token: Optional[str] = os.getenv("DRAINER_BEARER_TOKEN")
        
        # Server settings
        self.host: str = os.getenv("HOST", "0.0.0.0")
        self.port: int = int(os.getenv("PORT", "8000"))
        
        # Relay settings
        self.lease_seconds: int = int(os.getenv("LEASE_SECONDS", "300"))
        self.max_batch_size: int = int(os.getenv("MAX_BATCH_SIZE", "100"))
        self.max_retries: int = int(os.getenv("MAX_RETRIES", "10"))
        self.ack_retention_days: int = int(os.getenv("ACK_RETENTION_DAYS", "14"))


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
