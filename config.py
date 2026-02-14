"""
Configuration for Survivor Football Telegram Mini App.
Load from environment or .env; keep secrets out of version control.
"""
import os
from pathlib import Path

# Bot (for production set BOT_TOKEN and ADMIN_ID in environment variables)
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "7995725678:AAFNNQFMHnG5GT3ix-bl8lyUoDGoKpYvpUM")
# Користувач з цим Telegram ID бачить адмін-панель; решта — юзер-панель.
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "8386941234"))

# Paths
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "survivor.db"

# Database
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Web App (FastAPI) - for Mini App URL
WEBAPP_BASE_URL: str = os.getenv("WEBAPP_BASE_URL", "https://your-domain.com")
