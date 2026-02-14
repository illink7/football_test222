"""
FastAPI app for Telegram Web App: team selection and APIs.
Bot runs in background via lifespan (single process for Railway).
"""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from pydantic import BaseModel

from database import get_db, init_db
from database.models import Entry, Game, Selection, Team

BASE_DIR = Path(__file__).resolve().parent
_bot_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB, start bot in background; on shutdown stop bot."""
    global _bot_task
    init_db()
    from bot.main import run_bot
    _bot_task = asyncio.create_task(run_bot())
    yield
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Survivor Football Web App", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- API models -----


class TeamItem(BaseModel):
    id: int
    name: str


class SelectionSubmit(BaseModel):
    entry_id: int
    team1_id: int
    team2_id: int


# ----- Endpoints -----


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the team selection page (or redirect to select_teams)."""
    return FileResponse(BASE_DIR / "select_teams.html")


@app.get("/select_teams", response_class=HTMLResponse)
async def select_teams_page():
    """Serve select_teams.html for the Mini App."""
    return FileResponse(BASE_DIR / "select_teams.html")


@app.get("/api/teams/available")
async def get_available_teams(
    entry_id: int = Query(..., description="Entry ID"),
    db=Depends(get_db),
):
    """
    Return teams that can still be picked for this entry
    (all teams minus those already used in any round for this entry).
    """
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")

    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")

    used_ids = set()
    for sel in entry.selections:
        used_ids.add(sel.team1_id)
        used_ids.add(sel.team2_id)

    if used_ids:
        teams = db.execute(select(Team).where(Team.id.notin_(used_ids))).scalars().all()
    else:
        teams = db.execute(select(Team)).scalars().all()
    return [TeamItem(id=t.id, name=t.name) for t in teams]


@app.get("/api/entry/{entry_id}/round")
async def get_current_round(entry_id: int, db=Depends(get_db)):
    """Return current round number for this entry's game (for display and submit)."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    game = db.get(Game, entry.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"entry_id": entry_id, "game_id": game.id, "current_round": game.current_round}


@app.post("/api/selection")
async def submit_selection(body: SelectionSubmit, db=Depends(get_db)):
    """Save selection for the entry's current round and return success."""
    entry = db.get(Entry, body.entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")

    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")

    rnd = game.current_round
    if rnd > game.rounds_total:
        raise HTTPException(status_code=400, detail="Game has no more rounds")

    # Check team IDs exist and are not already used for this entry
    used_ids = {s.team1_id for s in entry.selections} | {s.team2_id for s in entry.selections}
    if body.team1_id in used_ids or body.team2_id in used_ids:
        raise HTTPException(status_code=400, detail="One or both teams already used for this entry")
    if body.team1_id == body.team2_id:
        raise HTTPException(status_code=400, detail="Pick two different teams")

    team1 = db.get(Team, body.team1_id)
    team2 = db.get(Team, body.team2_id)
    if not team1 or not team2:
        raise HTTPException(status_code=400, detail="Invalid team ID")

    sel = Selection(entry_id=body.entry_id, round=rnd, team1_id=body.team1_id, team2_id=body.team2_id)
    db.add(sel)
    db.commit()
    return {"ok": True, "selection_id": sel.id, "round": rnd}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
