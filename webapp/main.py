"""
FastAPI app for Telegram Web App: team selection and APIs.
Bot is started separately in main.py via asyncio.gather (same process).
"""
import random
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import HTMLResponse, FileResponse, Response
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


class MatchScoreItem(BaseModel):
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int


class RoundResultResponse(BaseModel):
    passed: bool
    round: int
    matches: list[MatchScoreItem]
    your_team1_scored: bool
    your_team2_scored: bool
    message: str
    stake_after_round: float | None = None  # поточна сума після туру (×1.5 якщо пройшов)


class SubmitTwoTeams(BaseModel):
    team1_id: int
    team2_id: int


class GameCreate(BaseModel):
    title: str


class MatchesAdd(BaseModel):
    round: int
    matches: list[str]  # each "Home — Away"


class SetTeamsBody(BaseModel):
    """Один список команд (назви по рядку). З нього генеруються 10 турів по 10 матчів (тасування пар)."""
    team_names: list[str]  # 20 команд, по одній на рядок


class EntryAdd(BaseModel):
    game_id: int
    user_id: int | None = None  # default = current user


class JoinGameBody(BaseModel):
    game_id: int
    stake: int | None = None  # сума ставки (грн)


# ----- Auth: require valid Telegram initData -----


def get_current_user(
    init_data: str | None = Query(None, alias="init_data"),
    x_telegram_init_data: str | None = Header(None),
):
    init = init_data or x_telegram_init_data
    if not init:
        raise HTTPException(
            status_code=401,
            detail="Відкрийте додаток з чату бота @PriceCalculatorNL_bot (кнопка меню або /start), а не в браузері.",
        )
    uid = get_user_id_from_init_data(init, BOT_TOKEN)
    if uid is None:
        raise HTTPException(
            status_code=401,
            detail="Помилка перевірки: відкрийте додаток саме з бота @PriceCalculatorNL_bot. Якщо ви зайшли з іншого бота — авторизація не працюватиме.",
        )
    return uid


def require_admin(uid: int = Depends(get_current_user)):
    """Тільки uid == ADMIN_ID (8386941234) має доступ до адмін-ендпоінтів; решта — юзери."""
    if uid != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Admin only")
    return uid


# ----- Endpoints -----


@app.get("/favicon.ico")
async def favicon():
    """Avoid 404 in logs when browser requests favicon."""
    return Response(status_code=204)


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
    """Повертає user_id, is_admin (True лише для 8386941234), entries. Решта юзерів — юзер-панель."""
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
            "stake": (e.stake_amount if e.stake_amount is not None else e.stake),
        }
        for (e, g) in entries
    ]
    result = {"user_id": uid, "is_admin": is_admin, "entries": entries_list}
    games = db.execute(select(Game).where(Game.status == "active").order_by(Game.id.desc())).scalars().all()
    result["games"] = [
        {"id": g.id, "title": g.title, "current_round": g.current_round, "rounds_total": g.rounds_total, "status": g.status}
        for g in games
    ]
    return result


@app.post("/api/join_game")
async def join_game(body: JoinGameBody, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Будь-який гравець може приєднатися до гри (створити собі запис). Опційно вказати суму ставки."""
    game = db.get(Game, body.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Гру не знайдено")
    if game.status != "active":
        raise HTTPException(status_code=400, detail="Гра не активна")
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid)
        db.add(user)
        db.flush()
    existing = db.execute(
        select(Entry).where(Entry.user_id == uid, Entry.game_id == body.game_id)
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Ви вже приєднані до цієї гри")
    initial_stake = float(body.stake) if body.stake is not None else 10.0
    entry = Entry(user_id=uid, game_id=body.game_id, status="active", stake=body.stake, stake_amount=initial_stake)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"ok": True, "entry_id": entry.id, "game_id": game.id, "stake": entry.stake_amount}


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


# ----- Pick 2 teams that will score (from round's team list) -----


@app.get("/api/entry/{entry_id}/teams_for_round")
async def get_teams_for_round(entry_id: int, db=Depends(get_db)):
    """Команди поточного туру, без тих, що гравець вже вибирав у попередніх турах. + поточна ставка."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    used_team_ids: set[int] = set()
    prev = (
        db.execute(
            select(Selection.team1_id, Selection.team2_id).where(
                Selection.entry_id == entry_id,
                Selection.round < rnd,
            )
        )
        .all()
    )
    for row in prev:
        used_team_ids.add(row[0])
        used_team_ids.add(row[1])
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game.id, Match.round == rnd)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    seen: set[int] = set()
    teams_list: list[Team] = []
    for m in matches:
        for tid in (m.home_team_id, m.away_team_id):
            if tid not in seen:
                seen.add(tid)
                t = db.get(Team, tid)
                if t:
                    teams_list.append(t)
    stake = entry.stake_amount if entry.stake_amount is not None else entry.stake
    return {
        "entry_id": entry_id,
        "round": rnd,
        "teams": [TeamItem(id=t.id, name=t.name) for t in teams_list],
        "used_team_ids": list(used_team_ids),
        "stake": float(stake) if stake is not None else None,
    }


@app.post("/api/entry/{entry_id}/submit_teams")
async def submit_two_teams(entry_id: int, body: SubmitTwoTeams, db=Depends(get_db)):
    """Save user's choice of 2 teams that will score this round. Replaces any previous selection for this round."""
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
            select(Match).where(Match.game_id == game.id, Match.round == rnd)
        )
        .scalars().all()
    )
    allowed_ids: set[int] = set()
    for m in matches:
        allowed_ids.add(m.home_team_id)
        allowed_ids.add(m.away_team_id)
    used_team_ids: set[int] = set()
    prev = (
        db.execute(
            select(Selection.team1_id, Selection.team2_id).where(
                Selection.entry_id == entry_id,
                Selection.round < rnd,
            )
        )
        .all()
    )
    for row in prev:
        used_team_ids.add(row[0])
        used_team_ids.add(row[1])
    if body.team1_id not in allowed_ids or body.team2_id not in allowed_ids:
        raise HTTPException(status_code=400, detail="Обери дві команди зі списку цього туру")
    if body.team1_id in used_team_ids or body.team2_id in used_team_ids:
        raise HTTPException(status_code=400, detail="Не можна вибирати команди з минулих турів")
    if body.team1_id == body.team2_id:
        raise HTTPException(status_code=400, detail="Обери дві різні команди")
    existing = (
        db.execute(
            select(Selection).where(
                Selection.entry_id == entry_id,
                Selection.round == rnd,
            )
        )
        .scalars().all()
    )
    for sel in existing:
        db.delete(sel)
    db.add(Selection(entry_id=entry_id, round=rnd, team1_id=body.team1_id, team2_id=body.team2_id))
    db.commit()
    return {"ok": True, "round": rnd}


MIN_GOALS, MAX_GOALS = 0, 3


@app.post("/api/entry/{entry_id}/run_round")
async def run_round(entry_id: int, db=Depends(get_db)):
    """Simulate round: generate goals if not yet done, check all entries, advance round. Returns result for this entry."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    sel_row = (
        db.execute(
            select(Selection).where(
                Selection.entry_id == entry_id,
                Selection.round == rnd,
            )
        )
        .scalars().first()
    )
    if not sel_row:
        raise HTTPException(status_code=400, detail="Спочатку обери дві команди для цього туру")
    selection = sel_row
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game.id, Match.round == rnd)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    if not matches:
        raise HTTPException(status_code=400, detail="Немає матчів у цьому турі")
    round_just_simulated = False
    if matches[0].home_goals is None:
        round_just_simulated = True
        for m in matches:
            m.home_goals = random.randint(MIN_GOALS, MAX_GOALS)
            m.away_goals = random.randint(MIN_GOALS, MAX_GOALS)
        scored_team_ids: set[int] = set()
        for m in matches:
            if m.home_goals and m.home_goals >= 1:
                scored_team_ids.add(m.home_team_id)
            if m.away_goals and m.away_goals >= 1:
                scored_team_ids.add(m.away_team_id)
        entries_with_selection = (
            db.execute(
                select(Selection.entry_id, Selection.team1_id, Selection.team2_id).where(
                    Selection.round == rnd,
                )
            )
            .all()
        )
        for row in entries_with_selection:
            eid, t1, t2 = row[0], row[1], row[2]
            e = db.get(Entry, eid)
            if not e:
                continue
            if t1 not in scored_team_ids or t2 not in scored_team_ids:
                e.status = "out"
            else:
                # Пройшов тур — множимо ставку на 1.5 (10 → 15 → 22.5 → …)
                e.stake_amount = (e.stake_amount if e.stake_amount is not None else e.stake or 10.0) * 1.5
        game.current_round += 1
        db.commit()
        db.refresh(game)
        if round_just_simulated:
            for m in matches:
                db.refresh(m)
    scored_team_ids = set()
    for m in matches:
        if m.home_goals and m.home_goals >= 1:
            scored_team_ids.add(m.home_team_id)
        if m.away_goals and m.away_goals >= 1:
            scored_team_ids.add(m.away_team_id)
    your_t1 = selection.team1_id in scored_team_ids
    your_t2 = selection.team2_id in scored_team_ids
    passed = your_t1 and your_t2
    match_items = [
        MatchScoreItem(
            home_name=m.home_team.name,
            away_name=m.away_team.name,
            home_goals=m.home_goals or 0,
            away_goals=m.away_goals or 0,
        )
        for m in matches
    ]
    if passed:
        msg = "Обидві ваші команди забили — ви проходите далі!"
    else:
        msg = "Одна або обидві команди не забили. Ви вибуваєте."
    db.refresh(entry)
    stake_after = float(entry.stake_amount) if entry.stake_amount is not None else None
    return RoundResultResponse(
        passed=passed,
        round=rnd,
        matches=match_items,
        your_team1_scored=your_t1,
        your_team2_scored=your_t2,
        message=msg,
        stake_after_round=stake_after,
    )


@app.post("/api/entry/{entry_id}/cash_out")
async def cash_out(entry_id: int, db=Depends(get_db)):
    """Забрати виграш: запис більше не активний, гравець отримує поточну суму ставки."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Запис не активний або виграш вже забрано")
    amount = entry.stake_amount if entry.stake_amount is not None else entry.stake
    if amount is None:
        amount = 0.0
    entry.status = "cashed_out"
    db.commit()
    return {"ok": True, "amount": float(amount)}


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


@app.post("/api/games/{game_id}/set_teams")
async def api_set_teams(
    game_id: int,
    body: SetTeamsBody,
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    """Задати список команд і автоматично згенерувати 10 турів (тасування пар). Видаляє старі матчі гри."""
    game = db.get(Game, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    names = [n.strip() for n in body.team_names if n.strip()]
    if len(names) != 20:
        raise HTTPException(
            status_code=400,
            detail=f"Потрібно рівно 20 команд (зараз {len(names)}). По одній назві на рядок.",
        )
    teams_list = db.execute(select(Team)).scalars().all()

    def norm(s: str) -> str:
        return s.replace("э", "е").replace("ё", "е").replace("і", "и").strip()

    teams_by_name: dict[str, Team] = {}
    for t in teams_list:
        teams_by_name[t.name] = t
        teams_by_name[norm(t.name)] = t
    team_ids: list[int] = []
    not_found: list[str] = []
    for name in names:
        t = teams_by_name.get(name) or teams_by_name.get(norm(name))
        if t:
            team_ids.append(t.id)
        else:
            not_found.append(name)
    if not_found:
        raise HTTPException(
            status_code=400,
            detail="Команди не знайдено (додайте їх у базу або перевірте написання): " + ", ".join(not_found[:5])
            + (" …" if len(not_found) > 5 else ""),
        )
    existing = (
        db.execute(select(Match).where(Match.game_id == game_id))
        .scalars().all()
    )
    for m in existing:
        db.delete(m)
    for rnd in range(1, 11):
        ids = list(team_ids)
        random.shuffle(ids)
        for i in range(0, 20, 2):
            db.add(
                Match(
                    game_id=game_id,
                    round=rnd,
                    home_team_id=ids[i],
                    away_team_id=ids[i + 1],
                )
            )
    db.commit()
    return {"ok": True, "rounds_created": 10, "matches_per_round": 10}


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

    def norm(s: str) -> str:
        return s.replace("э", "е").replace("ё", "е").replace("і", "и").strip()

    teams_by_name: dict[str, Team] = {}
    for t in teams_list:
        teams_by_name[t.name] = t
        teams_by_name[norm(t.name)] = t
    added = 0
    not_found: list[str] = []
    for line in body.matches:
        line = line.strip()
        if not line:
            continue
        for sep in (" — ", " - ", "–", "-"):
            if sep in line:
                parts = line.split(sep, 1)
                home_name, away_name = parts[0].strip(), parts[1].strip()
                home = teams_by_name.get(home_name) or teams_by_name.get(norm(home_name))
                away = teams_by_name.get(away_name) or teams_by_name.get(norm(away_name))
                if home and away:
                    db.add(Match(game_id=game_id, round=body.round, home_team_id=home.id, away_team_id=away.id))
                    added += 1
                else:
                    if not home:
                        not_found.append(home_name)
                    if not away:
                        not_found.append(away_name)
                break
    db.commit()
    return {"added": added, "not_found": list(dict.fromkeys(not_found))}


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
