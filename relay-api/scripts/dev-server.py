#!/usr/bin/env python3
"""Development server script for GitHub Relay Relay API"""

import subprocess
import sys
import os

def main():
    """Start the development server"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    relay_api_dir = os.path.dirname(script_dir)
    
    # Change to relay-api directory
    os.chdir(relay_api_dir)
    
    # Install dependencies if not already installed
    try:
        import fastapi
        import uvicorn
    except ImportError:
        print("Installing dependencies...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    
    # Start the development server
    print("Starting development server...")
    subprocess.run(
        [sys.executable, "-m", "uvicorn", "src.main:app", "--reload", "--port", "8000"],
        check=True
    )

if __name__ == "__main__":
    main()
