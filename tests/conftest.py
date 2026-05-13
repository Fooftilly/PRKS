"""Pytest: set test storage before any test module imports backend.server (import-time mkdir)."""
import os

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PRKS_TESTING", "1")
os.environ.setdefault("PRKS_STORAGE", os.path.join(_PROJECT_DIR, "data_testing"))
