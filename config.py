"""
Configuration for Survivor Football Telegram Mini App.
Load from environment or .env; keep secrets out of version control.
"""
import os
from pathlib import Path

# Bot
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))  # Telegram user ID of admin

# Paths
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "survivor.db"

# Database
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Web App (FastAPI) - for Mini App URL
WEBAPP_BASE_URL: str = os.getenv("WEBAPP_BASE_URL", "https://your-domain.com")
