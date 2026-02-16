"""
Configuration for Survivor Football Telegram Mini App.
Load from environment or .env; keep secrets out of version control.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

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

# Football-Data.org API (optional; for "Підтягнути тур Бундесліги"). Get key at https://www.football-data.org/
FOOTBALL_DATA_API_KEY: str | None = os.getenv("FOOTBALL_DATA_API_KEY") or None

# TON Center API (for transaction verification). Get key from @tonapibot (mainnet) or @tontestnetapibot (testnet)
TON_CENTER_API_KEY: str | None = os.getenv("TON_CENTER_API_KEY") or None
TON_CENTER_BASE_URL: str = os.getenv("TON_CENTER_BASE_URL", "https://toncenter.com/api/v2/")
TON_NETWORK: str = os.getenv("TON_NETWORK", "mainnet")  # mainnet or testnet

# TON wallet for receiving payments (admin wallet)
TON_RECEIVE_WALLET: str | None = os.getenv("TON_RECEIVE_WALLET") or None

# Test mode: if True, deposits are auto-confirmed without checking blockchain (for testing)
TON_TEST_MODE: bool = os.getenv("TON_TEST_MODE", "false").lower() == "true"
