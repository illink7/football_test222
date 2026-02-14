"""
Single entry point for Railway: FastAPI + aiogram bot in one process.
Creates SQLite DB and tables on startup if they don't exist.
"""
import os

from database import init_db
from webapp.main import app

if __name__ == "__main__":
    # Ensure DB and tables exist before starting the server (and bot in lifespan)
    init_db()

    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
