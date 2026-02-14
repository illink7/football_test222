"""
FastAPI app for Telegram Web App: team selection and APIs.
Bot is started separately in main.py via asyncio.gather (same process).
"""
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from pydantic import BaseModel

from config import BOT_TOKEN, ADMIN_ID
from database import get_db
from database.models import Entry, Game, Match, EntryMatchSelection, Selection, Team, User
from webapp.telegram_auth import get_user_id_from_init_data

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Survivor Football Web App")
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


class MatchItem(BaseModel):
    id: int
    home_name: str
    away_name: str
    selected: bool


class MatchSelectionSubmit(BaseModel):
    entry_id: int
    match_ids: list[int]


class GameCreate(BaseModel):
    title: str


class MatchesAdd(BaseModel):
    round: int
    matches: list[str]  # each "Home — Away"


class EntryAdd(BaseModel):
    game_id: int
    user_id: int | None = None  # default = current user


# ----- Auth: require valid Telegram initData -----


def get_current_user(
    init_data: str | None = Query(None, alias="init_data"),
    x_telegram_init_data: str | None = Header(None),
):
    init = init_data or x_telegram_init_data
    if not init:
        raise HTTPException(status_code=401, detail="Missing init_data (Telegram Web App)")
    uid = get_user_id_from_init_data(init, BOT_TOKEN)
    if uid is None:
        raise HTTPException(status_code=401, detail="Invalid init_data")
    return uid


def require_admin(uid: int = Depends(get_current_user)):
    if uid != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    return uid


# ----- Endpoints -----


@app.get("/", response_class=HTMLResponse)
async def index():
    """Main app: admin panel or user entries (opens from bot menu)."""
    return FileResponse(BASE_DIR / "app.html")


@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """Same as / - main web app."""
    return FileResponse(BASE_DIR / "app.html")


@app.get("/select_teams", response_class=HTMLResponse)
async def select_teams_page():
    """Serve select_teams.html for the Mini App (pick matches for entry)."""
    return FileResponse(BASE_DIR / "select_teams.html")


@app.get("/api/me")
async def api_me(uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Return current user info, is_admin, entries. Requires init_data in query or header."""
    is_admin = uid == ADMIN_ID
    entries = (
        db.execute(
            select(Entry, Game)
            .join(Game, Entry.game_id == Game.id)
            .where(Entry.user_id == uid)
            .order_by(Entry.id.desc())
        )
        .all()
    )
    entries_list = [
        {
            "entry_id": e.id,
            "game_id": g.id,
            "game_title": g.title,
            "current_round": g.current_round,
            "rounds_total": g.rounds_total,
            "status": e.status,
        }
        for (e, g) in entries
    ]
    result = {"user_id": uid, "is_admin": is_admin, "entries": entries_list}
    if is_admin:
        games = db.execute(select(Game).order_by(Game.id.desc())).scalars().all()
        result["games"] = [
            {"id": g.id, "title": g.title, "current_round": g.current_round, "rounds_total": g.rounds_total, "status": g.status}
            for g in games
        ]
    return result


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


# ----- Matches (pick which matches to bet on) -----


@app.get("/api/entry/{entry_id}/matches")
async def get_entry_matches(entry_id: int, db=Depends(get_db)):
    """List matches for the entry's game current round; include which are already selected by this entry."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game.id, Match.round == rnd)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    selected_ids = {s.match_id for s in entry.match_selections}
    result = []
    for m in matches:
        result.append(
            MatchItem(
                id=m.id,
                home_name=m.home_team.name,
                away_name=m.away_team.name,
                selected=m.id in selected_ids,
            )
        )
    return {
        "entry_id": entry_id,
        "round": rnd,
        "matches": result,
    }


@app.post("/api/entry/{entry_id}/matches")
async def submit_match_selections(entry_id: int, body: MatchSelectionSubmit, db=Depends(get_db)):
    """Save which matches the user selected for the current round (replaces previous selection for this round)."""
    if body.entry_id != entry_id:
        raise HTTPException(status_code=400, detail="entry_id mismatch")
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    matches_this_round = (
        db.execute(select(Match).where(Match.game_id == game.id, Match.round == rnd))
        .scalars().all()
    )
    match_ids_valid = {m.id for m in matches_this_round}
    for mid in body.match_ids:
        if mid not in match_ids_valid:
            raise HTTPException(status_code=400, detail=f"Invalid match_id: {mid}")
    existing = db.execute(
        select(EntryMatchSelection).where(EntryMatchSelection.entry_id == entry_id)
    ).scalars().all()
    for ems in existing:
        if ems.match_id in match_ids_valid:
            db.delete(ems)
    for mid in body.match_ids:
        db.add(EntryMatchSelection(entry_id=entry_id, match_id=mid))
    db.commit()
    return {"ok": True, "round": rnd, "selected_count": len(body.match_ids)}


# ----- Admin API (require_admin) -----


@app.post("/api/games")
async def api_create_game(body: GameCreate, db=Depends(get_db), _admin: int = Depends(require_admin)):
    g = Game(title=body.title, rounds_total=10, current_round=1, status="active")
    db.add(g)
    db.commit()
    db.refresh(g)
    return {"id": g.id, "title": g.title}


@app.get("/api/games/{game_id}/matches")
async def api_list_matches(
    game_id: int,
    round: int = Query(..., ge=1),
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game_id, Match.round == round)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    return [
        {"id": m.id, "home": m.home_team.name, "away": m.away_team.name}
        for m in matches
    ]


@app.post("/api/games/{game_id}/matches")
async def api_add_matches(
    game_id: int,
    body: MatchesAdd,
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    teams_list = db.execute(select(Team)).scalars().all()
    teams_by_name = {t.name: t for t in teams_list}
    added = 0
    for line in body.matches:
        line = line.strip()
        for sep in (" — ", " - ", "–", "-"):
            if sep in line:
                parts = line.split(sep, 1)
                home_name, away_name = parts[0].strip(), parts[1].strip()
                home = teams_by_name.get(home_name)
                away = teams_by_name.get(away_name)
                if home and away:
                    db.add(Match(game_id=game_id, round=body.round, home_team_id=home.id, away_team_id=away.id))
                    added += 1
                break
    db.commit()
    return {"added": added}


@app.post("/api/entries")
async def api_add_entry(body: EntryAdd, db=Depends(get_db), uid: int = Depends(require_admin)):
    game = db.get(Game, body.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    user_id = body.user_id if body.user_id is not None else uid
    user = db.get(User, user_id)
    if not user:
        user = User(tg_id=user_id)
        db.add(user)
        db.flush()
    entry = Entry(user_id=user_id, game_id=body.game_id, status="active")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"entry_id": entry.id, "game_id": game.id, "user_id": user_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
