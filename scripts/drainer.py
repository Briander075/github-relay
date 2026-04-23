#!/usr/bin/env python3
"""GitHub Relay Drainer - Local consumer for processing queued webhooks

This script runs as a background worker that:
- Polls the relay API for pending events
- Claims events for processing
- Processes events locally (configurable via callback)
- Batches successful ack calls
- Handles failures gracefully

Usage:
    python scripts/drainer.py

Environment variables:
    RELAY_URL: URL of the relay API (default: http://localhost:8000)
    DRAINER_BEARER_TOKEN: Bearer token for authentication
    CONSUMER_ID: Unique identifier for this drainer instance
    POLL_INTERVAL: Seconds between polls (default: 5)
    BATCH_SIZE: Max events per claim (default: 10)
    LEASE_SECONDS: Lease timeout in seconds (default: 300)
"""

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests


class Drainer:
    """Local drainer that processes GitHub webhook events from the relay."""

    def __init__(self, config=None):
        self.config = config or {}
        self.relay_url = self.config.get("relay_url", "http://localhost:8000")
        self.bearer_token = self.config.get("bearer_token")
        self.consumer_id = self.config.get("consumer_id", str(uuid.uuid4()))
        self.poll_interval = int(self.config.get("poll_interval", 5))
        self.batch_size = int(self.config.get("batch_size", 10))
        self.lease_seconds = int(self.config.get("lease_seconds", 300))
        self.log_dir = Path(self.config.get("log_dir", "~/.hermes/drainer/logs")).expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Track claimed events for batch acking
        self.claimed_events = []
        self.last_batch_time = time.time()

    def _build_headers(self):
        """Build request headers with authentication."""
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    def claim_events(self):
        """Claim a batch of pending events from the relay."""
        max_retries = 3
        retry_delay = 5  # seconds
        
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.post(
                    f"{self.relay_url}/api/v1/drain/claim",
                    headers={
                        **self._build_headers(),
                        "X-Consumer-Id": self.consumer_id,
                    },
                    params={
                        "limit": self.batch_size,
                        "lease_seconds": self.lease_seconds,
                    },
                    timeout=30,
                )

                if response.status_code == 401:
                    self._log("ERROR", "Authentication failed - check DRAINER_BEARER_TOKEN")
                    return []
                elif response.status_code != 200:
                    self._log("WARN", f"Claim attempt {attempt}/{max_retries} failed: {response.status_code} {response.text}")
                    if attempt < max_retries:
                        self._log("INFO", f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        continue
                    return []

                result = response.json()
                events = result.get("events", [])
                claimed_count = result.get("claimed_count", 0)

                if events:
                    self._log("INFO", f"Claimed {claimed_count} events for consumer {self.consumer_id}")

                return events

            except requests.exceptions.RequestException as e:
                self._log("WARN", f"Request attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    self._log("INFO", f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    self._log("ERROR", f"All {max_retries} claim attempts failed - drainer will continue polling")
                return []

    def process_event(self, event):
        """Process a single event. Override this in subclasses for custom logic."""
        # Default: just log the event
        self._log("INFO", f"Processing event: {event['event_id']} ({event['github_event_type']})")
        return True

    def ack_events(self):
        """Acknowledge successfully processed events in batches."""
        if not self.claimed_events:
            return

        event_ids = [e["event_id"] for e in self.claimed_events]
        try:
            response = requests.post(
                f"{self.relay_url}/api/v1/drain/ack",
                headers=self._build_headers(),
                json={
                    "consumer_id": self.consumer_id,
                    "event_ids": event_ids,
                },
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                acked = result.get("acked", [])
                not_owned = result.get("not_owned", [])
                already_acked = result.get("already_acked", [])
                not_found = result.get("not_found", [])

                self._log("INFO", f"Acked {len(acked)} events")
                if not_owned:
                    self._log("WARN", f"Not owned: {not_owned}")
                if already_acked:
                    self._log("WARN", f"Already acked: {already_acked}")
                if not_found:
                    self._log("ERROR", f"Not found: {not_found}")

                # Clear acknowledged events
                self.claimed_events = [e for e in self.claimed_events if e["event_id"] not in acked]
            else:
                self._log("ERROR", f"Ack failed: {response.status_code} {response.text}")

        except requests.exceptions.RequestException as e:
            self._log("ERROR", f"Ack request failed: {e}")

    def _log(self, level, message):
        """Log a message to file and console."""
        timestamp = datetime.now().isoformat()
        log_entry = f"{timestamp} [{level}] {message}"
        print(log_entry)

        log_file = self.log_dir / f"{self.consumer_id}.log"
        with open(log_file, "a") as f:
            f.write(log_entry + "\n")

    def run(self):
        """Main drainer loop."""
        self._log("INFO", f"Starting drainer for consumer {self.consumer_id}")
        self._log("INFO", f"Relay URL: {self.relay_url}")
        self._log("INFO", f"Batch size: {self.batch_size}")
        self._log("INFO", f"Poll interval: {self.poll_interval}s")
        self._log("INFO", f"Lease timeout: {self.lease_seconds}s")

        while True:
            # Claim events
            events = self.claim_events()

            if events:
                for event in events:
                    # Process the event
                    success = self.process_event(event)

                    if success:
                        self.claimed_events.append(event)
                    else:
                        self._log("ERROR", f"Failed to process event: {event['event_id']}")

                # Batch ack after processing
                self.ack_events()

                # Update last batch time
                self.last_batch_time = time.time()

            # Poll interval
            time.sleep(self.poll_interval)


def main():
    """Main entry point."""
    config = {
        "relay_url": os.getenv("RELAY_URL", "http://localhost:8000"),
        "bearer_token": os.getenv("DRAINER_BEARER_TOKEN"),
        "consumer_id": os.getenv("CONSUMER_ID", str(uuid.uuid4())),
        "poll_interval": os.getenv("POLL_INTERVAL", "5"),
        "batch_size": os.getenv("BATCH_SIZE", "10"),
        "lease_seconds": os.getenv("LEASE_SECONDS", "300"),
        "log_dir": os.getenv("LOG_DIR", "~/.hermes/drainer/logs"),
    }

    drainer = Drainer(config)
    drainer.run()


if __name__ == "__main__":
    main()
