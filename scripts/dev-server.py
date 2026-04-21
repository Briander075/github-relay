#!/usr/bin/env python3
"""Development server for GitHub Relay API

This script can be used in two ways:
- Primary: Background polling via launchd/systemd (automatic, continuous)
- Secondary: Manual run for testing and troubleshooting

Usage (manual mode):
    python scripts/dev-server.py
"""

import subprocess
import sys

def start_dev_server():
    """Start the FastAPI development server with auto-reload"""
    print("Starting GitHub Relay API development server...")
    subprocess.run([
        sys.executable, "-m", "uvicorn",
        "src.main:app",
        "--reload",
        "--port", "8000",
        "--host", "0.0.0.0"
    ])

if __name__ == "__main__":
    start_dev_server()
