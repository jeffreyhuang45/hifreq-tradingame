"""
Playwright configuration for E2E tests.
"""
import os
from pathlib import Path

# Base URL for the application
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

# Test settings
TIMEOUT = 30000  # 30 seconds default timeout
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# Paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
SCREENSHOTS_DIR = PROJECT_ROOT / "tests" / "e2e" / "screenshots"
VIDEOS_DIR = PROJECT_ROOT / "tests" / "e2e" / "videos"

# Create directories if they don't exist
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# Browser settings
BROWSERS = ["chromium"]  # Can add "firefox", "webkit" for cross-browser testing

# Viewport size
VIEWPORT = {"width": 1280, "height": 720}
