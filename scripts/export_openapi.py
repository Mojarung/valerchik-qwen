"""Regenerate the frontend contract without starting the application."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from app.main import app

(root / "frontend/openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n")
