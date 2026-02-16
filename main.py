"""
Single entry point for Railway: FastAPI + aiogram bot in one process.
Runs both simultaneously via asyncio.gather. Creates DB and seeds teams on startup.
"""
import asyncio
import os

from database import init_db, seed_teams
from webapp.main import app

if __name__ == "__main__":
    init_db()
    seed_teams()

    from uvicorn import Config, Server
    from bot.main import run_bot

    port = int(os.environ.get("PORT", 8000))
    server = Server(Config(app, host="0.0.0.0", port=port))

    async def main():
        tasks = [server.serve()]
        # Вимкнути бота якщо RUN_BOT=false (щоб уникнути конфліктів)
        if os.environ.get("RUN_BOT", "true").lower() != "false":
            tasks.append(run_bot())
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass  # Ctrl+C or shutdown — exit cleanly

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
