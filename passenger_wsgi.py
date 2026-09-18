"""Portable WSGI entry point; configure the Python environment in the host."""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("DASHBOARD_PROJECT_ROOT", Path(__file__).resolve().parent))
sys.path.insert(0, str(PROJECT_ROOT))
from dashboard.app import create_app

application = create_app(str(PROJECT_ROOT / ".env"))
