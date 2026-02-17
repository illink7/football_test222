"""
FastAPI app for Telegram Web App: team selection and APIs.
Bot is started separately in main.py via asyncio.gather (same process).
"""
import json
import random
import urllib.request
import urllib.parse
import uuid
import base64
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from pydantic import BaseModel

from config import (
    BOT_TOKEN, ADMIN_ID, FOOTBALL_DATA_API_KEY,
    TON_CENTER_API_KEY, TON_CENTER_BASE_URL, TON_NETWORK,
    TON_RECEIVE_WALLET, TON_TEST_MODE, WEBAPP_BASE_URL,
    USDT_JETTON_MASTER, TON_CENTER_V3_URL,
)
from database import get_db
from database.models import Entry, Game, Match, EntryMatchSelection, Selection, Team, Ticket, TonTransaction, User, UserAchievement
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


class TicketResultItem(BaseModel):
    ticket_index: int
    passed: bool
    team1_name: str
    team2_name: str
    team1_scored: bool
    team2_scored: bool
    stake_after: float
    team_not_scored: str | None = None  # назва команди, яка не забила (якщо вибув)


class RoundResultResponse(BaseModel):
    passed: bool  # хоч один білет пройшов
    round: int
    matches: list[MatchScoreItem]
    your_team1_scored: bool
    your_team2_scored: bool
    message: str
    stake_after_round: float | None = None  # сума по всіх активних білетах
    tickets: list[TicketResultItem] = []  # результат по кожному білету


class SubmitTwoTeams(BaseModel):
    team1_id: int
    team2_id: int
    ticket_index: int = 1


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
    stake: float | None = None  # сума ставки на один білет (USDT)
    num_tickets: int | None = None  # кількість білетів (ставок); за замовч. 1


class ConnectWalletBody(BaseModel):
    wallet_address: str


class DepositBody(BaseModel):
    amount: float


class WithdrawBody(BaseModel):
    amount: float


class CreateBundesligaGameBody(BaseModel):
    start_matchday: int | None = None  # з якого туру BL1 починається гра (напр. 24); якщо None — використовується поточний тур
    rounds_count: int    # скільки турів грає (напр. 5 → тури 24–28)


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


def _grant_achievement(db, user_id: int, key: str) -> bool:
    """Розблокувати досягнення, якщо ще не має. Повертає True якщо надано."""
    exists = db.execute(
        select(UserAchievement).where(UserAchievement.user_id == user_id, UserAchievement.achievement_key == key)
    ).scalars().first()
    if exists:
        return False
    db.add(UserAchievement(user_id=user_id, achievement_key=key))
    return True


def _balance(user) -> float:
    """Баланс користувача в USDT."""
    if user is None:
        return 0.0
    v = getattr(user, "balance_usdt", None)
    return float(v) if v is not None else 0.0


def _set_balance(user, value: float):
    """Встановити баланс в USDT."""
    user.balance_usdt = round(value, 6)


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
    """Повертає user_id, is_admin, balance (USDT), entries."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.commit()
        db.refresh(user)
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
        db.commit()
        db.refresh(user)
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
    result = {
        "user_id": uid,
        "is_admin": is_admin,
        "balance": _balance(user),
        "ton_wallet_address": user.ton_wallet_address,
        "entries": entries_list
    }
    games = db.execute(select(Game).where(Game.status == "active").order_by(Game.id.desc())).scalars().all()
    result["games"] = [
        {"id": g.id, "title": g.title, "current_round": g.current_round, "rounds_total": g.rounds_total, "status": g.status}
        for g in games
    ]
    achievements = (
        db.execute(select(UserAchievement.achievement_key, UserAchievement.unlocked_at).where(UserAchievement.user_id == uid).order_by(UserAchievement.unlocked_at))
        .all()
    )
    result["achievements"] = [{"key": a[0], "unlocked_at": a[1].isoformat() if getattr(a[1], "isoformat", None) else str(a[1])} for a in achievements]
    return result


@app.post("/api/join_game")
async def join_game(body: JoinGameBody, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Приєднатися до гри. Ставка в USDT (0.1, 0.2, 0.5, 1, 2, 5)."""
    game = db.get(Game, body.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Гру не знайдено")
    if game.status != "active":
        raise HTTPException(status_code=400, detail="Гра не активна")
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
    bal = _balance(user)
    if bal <= 0:
        raise HTTPException(status_code=400, detail="Поповніть баланс (USDT) у розділі Профіль")
    existing = db.execute(
        select(Entry).where(Entry.user_id == uid, Entry.game_id == body.game_id)
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Ви вже приєднані до цієї гри")
    num_tickets = max(1, min(10, int(body.num_tickets or 1)))
    initial_stake = float(body.stake) if body.stake is not None else 0.1
    if initial_stake < 0.1:
        raise HTTPException(status_code=400, detail="Мінімальна ставка — 0.1 USDT")
    stake_float = round(initial_stake, 2)
    total_cost = round(stake_float * num_tickets, 2)
    if bal < total_cost:
        raise HTTPException(status_code=400, detail=f"Недостатньо USDT. Потрібно: {total_cost}, ваш баланс: {bal}")
    _set_balance(user, bal - total_cost)
    entry = Entry(user_id=uid, game_id=body.game_id, status="active", stake=body.stake, stake_amount=stake_float)
    db.add(entry)
    db.flush()
    for i in range(1, num_tickets + 1):
        db.add(Ticket(entry_id=entry.id, ticket_index=i, stake_amount=stake_float, status="active"))
    _grant_achievement(db, uid, "first_bet")
    db.commit()
    db.refresh(entry)
    db.refresh(user)
    return {"ok": True, "entry_id": entry.id, "game_id": game.id, "stake": stake_float, "num_tickets": num_tickets, "balance": _balance(user)}


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


def _round_deadline_utc(db, game_id: int, round_num: int):
    """Дедлайн ставок для туру = мін. utc_date серед матчів туру (початок першого матчу)."""
    rows = (
        db.execute(
            select(Match.utc_date).where(
                Match.game_id == game_id,
                Match.round == round_num,
                Match.utc_date.isnot(None),
            )
        )
        .scalars().all()
    )
    dates = [r[0] for r in rows if r[0]]
    return min(dates) if dates else None


def _can_bet_round(db, game_id: int, round_num: int) -> bool:
    deadline = _round_deadline_utc(db, game_id, round_num)
    if deadline is None:
        return True
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now < deadline


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


@app.get("/api/entry/{entry_id}/round_info")
async def get_round_info(entry_id: int, db=Depends(get_db)):
    """Інформація про поточний тур: дедлайн ставок та чи можна ще ставити."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    game = db.get(Game, entry.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    rnd = game.current_round
    deadline = _round_deadline_utc(db, game.id, rnd)
    can_bet = _can_bet_round(db, game.id, rnd)
    return {
        "entry_id": entry_id,
        "game_id": game.id,
        "current_round": rnd,
        "rounds_total": game.rounds_total,
        "deadline_utc": deadline.isoformat() if deadline else None,
        "can_bet": can_bet,
    }


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
    if not _can_bet_round(db, game.id, rnd):
        raise HTTPException(status_code=400, detail="Дедлайн ставок минув. Ставки по цьому туру більше не приймаються.")

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
    if not _can_bet_round(db, game.id, rnd):
        raise HTTPException(status_code=400, detail="Дедлайн ставок минув. Ставки по цьому туру більше не приймаються.")
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
    """Поточний тур: матчі + по кожному білету — використані команди та поточний вибір (якщо є)."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    tickets_raw = (
        db.execute(select(Ticket).where(Ticket.entry_id == entry_id).order_by(Ticket.ticket_index))
        .scalars().all()
    )
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game.id, Match.round == rnd)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    match_rows = [
        {"home_id": m.home_team_id, "home_name": m.home_team.name, "away_id": m.away_team_id, "away_name": m.away_team.name}
        for m in matches
    ]
    teams_list: list[Team] = []
    seen: set[int] = set()
    for m in matches:
        for tid in (m.home_team_id, m.away_team_id):
            if tid not in seen:
                seen.add(tid)
                t = db.get(Team, tid)
                if t:
                    teams_list.append(t)
    tickets_out: list[dict] = []
    for ticket in tickets_raw:
        used_team_ids: set[int] = set()
        prev = (
            db.execute(
                select(Selection.team1_id, Selection.team2_id).where(
                    Selection.entry_id == entry_id,
                    Selection.ticket_index == ticket.ticket_index,
                    Selection.round < rnd,
                )
            )
            .all()
        )
        for row in prev:
            used_team_ids.add(row[0])
            used_team_ids.add(row[1])
        sel_cur = (
            db.execute(
                select(Selection).where(
                    Selection.entry_id == entry_id,
                    Selection.ticket_index == ticket.ticket_index,
                    Selection.round == rnd,
                )
            )
            .scalars().first()
        )
        selection_info = None
        if sel_cur:
            s = sel_cur
            selection_info = {
                "team1_id": s.team1_id,
                "team2_id": s.team2_id,
                "team1_name": s.team1.name,
                "team2_name": s.team2.name,
            }
        tickets_out.append({
            "ticket_index": ticket.ticket_index,
            "status": ticket.status,
            "stake_amount": float(ticket.stake_amount),
            "used_team_ids": list(used_team_ids),
            "selection": selection_info,
        })
    stake_legacy = entry.stake_amount if entry.stake_amount is not None else entry.stake
    return {
        "entry_id": entry_id,
        "round": rnd,
        "teams": [TeamItem(id=t.id, name=t.name) for t in teams_list],
        "matches": match_rows,
        "tickets": tickets_out,
        "used_team_ids": list(tickets_out[0]["used_team_ids"]) if tickets_out else [],
        "stake": float(stake_legacy) if stake_legacy is not None else None,
    }


@app.post("/api/entry/{entry_id}/submit_teams")
async def submit_two_teams(entry_id: int, body: SubmitTwoTeams, db=Depends(get_db)):
    """Зберегти вибір 2 команд для одного білета в цьому турі. Замінює попередній вибір для цього білета."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    if not _can_bet_round(db, game.id, rnd):
        raise HTTPException(status_code=400, detail="Дедлайн ставок минув. Ставки по цьому туру більше не приймаються.")
    ticket_index = max(1, body.ticket_index or 1)
    ticket = db.execute(
        select(Ticket).where(Ticket.entry_id == entry_id, Ticket.ticket_index == ticket_index)
    ).scalars().first()
    if not ticket or ticket.status != "active":
        raise HTTPException(status_code=400, detail="Білет не знайдено або вже вибув")
    matches = (
        db.execute(select(Match).where(Match.game_id == game.id, Match.round == rnd))
        .scalars().all()
    )
    allowed_ids = {m.home_team_id for m in matches} | {m.away_team_id for m in matches}
    used_team_ids = set()
    prev = (
        db.execute(
            select(Selection.team1_id, Selection.team2_id).where(
                Selection.entry_id == entry_id,
                Selection.ticket_index == ticket_index,
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
        raise HTTPException(status_code=400, detail="Не можна вибирати команди, які вже використовувались у цьому білеті")
    if body.team1_id == body.team2_id:
        raise HTTPException(status_code=400, detail="Обери дві різні команди")
    existing = (
        db.execute(
            select(Selection).where(
                Selection.entry_id == entry_id,
                Selection.ticket_index == ticket_index,
                Selection.round == rnd,
            )
        )
        .scalars().all()
    )
    for sel in existing:
        db.delete(sel)
    db.add(Selection(entry_id=entry_id, ticket_index=ticket_index, round=rnd, team1_id=body.team1_id, team2_id=body.team2_id))
    db.commit()
    return {"ok": True, "round": rnd, "ticket_index": ticket_index}


MIN_GOALS, MAX_GOALS = 0, 3


def _apply_round_results(db, game, rnd: int) -> None:
    """Застосувати результати туру: оновити білети/записи, збільшити game.current_round. У матчах мають бути вже заповнені home_goals/away_goals."""
    matches = (
        db.execute(
            select(Match)
            .where(Match.game_id == game.id, Match.round == rnd)
            .order_by(Match.id)
        )
        .scalars().all()
    )
    if not matches or matches[0].home_goals is None:
        return
    scored_team_ids: set[int] = set()
    for m in matches:
        if (m.home_goals or 0) >= 1:
            scored_team_ids.add(m.home_team_id)
        if (m.away_goals or 0) >= 1:
            scored_team_ids.add(m.away_team_id)
    rows = (
        db.execute(
            select(Selection.entry_id, Selection.ticket_index, Selection.team1_id, Selection.team2_id).where(
                Selection.round == rnd,
            )
        )
        .all()
    )
    for row in rows:
        eid, tidx, t1, t2 = row[0], row[1], row[2], row[3]
        ticket = db.execute(
            select(Ticket).where(Ticket.entry_id == eid, Ticket.ticket_index == tidx)
        ).scalars().first()
        if not ticket or ticket.status != "active":
            continue
        if t1 in scored_team_ids and t2 in scored_team_ids:
            ticket.stake_amount = ticket.stake_amount * 1.5
            e = db.get(Entry, eid)
            if e and game.current_round >= 5:
                _grant_achievement(db, e.user_id, "survived_5_rounds")
        else:
            ticket.status = "out"
            e = db.get(Entry, eid)
            if e:
                _grant_achievement(db, e.user_id, "first_loss")
                still_active = (
                    db.execute(select(Ticket).where(Ticket.entry_id == eid, Ticket.status == "active"))
                    .scalars().first()
                )
                if not still_active:
                    e.status = "out"
    game.current_round += 1
    if game.current_round > game.rounds_total:
        game.status = "finished"
    db.commit()
    db.refresh(game)


@app.post("/api/entry/{entry_id}/run_round")
async def run_round(entry_id: int, db=Depends(get_db)):
    """Симуляція туру: рахуємо білети всіх записів, повертаємо результат для цього entry."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Entry is not active")
    game = db.get(Game, entry.game_id)
    if not game or game.status != "active":
        raise HTTPException(status_code=400, detail="Game not active")
    rnd = game.current_round
    any_sel = (
        db.execute(
            select(Selection).where(
                Selection.entry_id == entry_id,
                Selection.round == rnd,
            )
        )
        .scalars().first()
    )
    if not any_sel:
        raise HTTPException(status_code=400, detail="Спочатку обери команди хоча б для одного білета")
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
        _apply_round_results(db, game, rnd)
        db.refresh(game)
        for m in matches:
            db.refresh(m)
    scored_team_ids = set()
    for m in matches:
        if (m.home_goals or 0) >= 1:
            scored_team_ids.add(m.home_team_id)
        if (m.away_goals or 0) >= 1:
            scored_team_ids.add(m.away_team_id)
    selections_this_entry = (
        db.execute(
            select(Selection).where(
                Selection.entry_id == entry_id,
                Selection.round == rnd,
            )
        )
        .scalars().all()
    )
    ticket_results: list[TicketResultItem] = []
    for sel in selections_this_entry:
        ticket = db.execute(
            select(Ticket).where(Ticket.entry_id == entry_id, Ticket.ticket_index == sel.ticket_index)
        ).scalars().first()
        if not ticket:
            continue
        t1_ok = sel.team1_id in scored_team_ids
        t2_ok = sel.team2_id in scored_team_ids
        passed = t1_ok and t2_ok
        team_not = None
        if not passed:
            if not t1_ok and not t2_ok:
                team_not = sel.team1.name + ", " + sel.team2.name
            elif not t1_ok:
                team_not = sel.team1.name
            else:
                team_not = sel.team2.name
        ticket_results.append(
            TicketResultItem(
                ticket_index=sel.ticket_index,
                passed=passed,
                team1_name=sel.team1.name,
                team2_name=sel.team2.name,
                team1_scored=t1_ok,
                team2_scored=t2_ok,
                stake_after=float(ticket.stake_amount),
                team_not_scored=team_not,
            )
        )
    any_passed = any(t.passed for t in ticket_results)
    active_tickets = (
        db.execute(select(Ticket).where(Ticket.entry_id == entry_id, Ticket.status == "active"))
        .scalars().all()
    )
    stake_total = sum(float(t.stake_amount) for t in active_tickets) if active_tickets else None
    msg = "Хоч один білет пройшов — продовжуйте!" if any_passed else "Усі ваші білети вибули."
    if not ticket_results:
        msg = "Немає вибору для цього туру."
    return RoundResultResponse(
        passed=any_passed,
        round=rnd,
        matches=[MatchScoreItem(home_name=m.home_team.name, away_name=m.away_team.name, home_goals=m.home_goals or 0, away_goals=m.away_goals or 0) for m in matches],
        your_team1_scored=any(t.team1_scored for t in ticket_results),
        your_team2_scored=any(t.team2_scored for t in ticket_results),
        message=msg,
        stake_after_round=stake_total,
        tickets=ticket_results,
    )


@app.post("/api/entry/{entry_id}/cash_out")
async def cash_out(entry_id: int, db=Depends(get_db)):
    """Забрати виграш: сума всіх активних білетів додається до балансу."""
    entry = db.get(Entry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status != "active":
        raise HTTPException(status_code=400, detail="Запис не активний або виграш вже забрано")
    active = (
        db.execute(select(Ticket).where(Ticket.entry_id == entry_id, Ticket.status == "active"))
        .scalars().all()
    )
    amount_float = sum(float(t.stake_amount) for t in active) if active else 0.0
    if amount_float <= 0:
        amount_float = float(entry.stake_amount or entry.stake or 0)
    user = db.get(User, entry.user_id)
    if user:
        _set_balance(user, _balance(user) + amount_float)
    entry.status = "cashed_out"
    if user:
        if amount_float >= 500:
            _grant_achievement(db, user.tg_id, "cashed_out_500")
        if amount_float >= 100:
            _grant_achievement(db, user.tg_id, "cashed_out_100")
    db.commit()
    new_balance = _balance(user) if user else None
    return {"ok": True, "amount": amount_float, "balance": new_balance}


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


# ----- TON Connect & Payments -----


@app.get("/tonconnect-manifest.json")
async def tonconnect_manifest():
    """TON Connect manifest file."""
    from fastapi.responses import JSONResponse
    manifest = {
        "url": WEBAPP_BASE_URL,
        "name": "Survivor Football",
        "iconUrl": WEBAPP_BASE_URL + "/icon.png",
    }
    return JSONResponse(content=manifest)


@app.post("/api/connect_wallet")
async def connect_wallet(body: ConnectWalletBody, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Зберегти адресу TON гаманця користувача."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
    if not body.wallet_address or len(body.wallet_address) < 20:
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    user.ton_wallet_address = body.wallet_address
    db.commit()
    return {"ok": True, "wallet_address": body.wallet_address}


@app.post("/api/disconnect_wallet")
async def disconnect_wallet(uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Відключити TON гаманець."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
    user.ton_wallet_address = None
    db.commit()
    return {"ok": True}


@app.post("/api/deposit")
async def deposit(body: DepositBody, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Поповнення балансу в USDT (мережа TON)."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
    try:
        amount_float = round(float(body.amount), 2)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Невірний формат суми")
    if amount_float < 0.1:
        raise HTTPException(status_code=400, detail="Мінімум 0.1 USDT")
    tx_id = str(uuid.uuid4())
    comment = f"deposit_{uid}_{tx_id[:8]}"
    
    # Тестовий режим
    if TON_TEST_MODE:
        _set_balance(user, _balance(user) + amount_float)
        db.add(TonTransaction(tx_hash=f"test_{tx_id}", user_id=uid, amount=amount_float, comment=comment))
        db.commit()
        return {
            "ok": True,
            "transaction_id": tx_id,
            "payment_link": None,
            "amount": amount_float,
            "wallet": None,
            "comment": comment,
            "test_mode": True,
            "balance": _balance(user),
        }
    
    # Реальний режим: поповнення в USDT на TON
    if not TON_RECEIVE_WALLET:
        raise HTTPException(
            status_code=400,
            detail="TON_RECEIVE_WALLET не налаштовано.",
        )
    comment_enc = urllib.parse.quote(comment)
    # Tonkeeper: відкриває переказ, користувач обирає USDT
    payment_link = f"https://app.tonkeeper.com/transfer/{TON_RECEIVE_WALLET}?text={comment_enc}&jetton={USDT_JETTON_MASTER}"
    # Універсальний ton:// — відкривається в Telegram Wallet та інших TON-гаманцях
    payment_link_telegram = f"ton://transfer/{TON_RECEIVE_WALLET}?text={comment_enc}&jetton={USDT_JETTON_MASTER}"
    return {
        "ok": True,
        "transaction_id": tx_id,
        "payment_link": payment_link,
        "payment_link_telegram": payment_link_telegram,
        "amount": amount_float,
        "wallet": TON_RECEIVE_WALLET,
        "comment": comment,
        "test_mode": False,
    }


@app.post("/api/withdraw")
async def withdraw(body: WithdrawBody, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Запит на виведення USDT на TON гаманець (обробляється вручну або автоматично)."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0
    if not user.ton_wallet_address:
        raise HTTPException(status_code=400, detail="Підключіть TON гаманець спочатку")
    amount = round(float(body.amount), 2)
    if amount < 0.1:
        raise HTTPException(status_code=400, detail="Мінімум 0.1 USDT")
    bal = _balance(user)
    if bal < amount:
        raise HTTPException(status_code=400, detail=f"Недостатньо USDT. Баланс: {bal}")
    _set_balance(user, bal - amount)
    db.commit()
    return {"ok": True, "amount": amount, "balance": _balance(user)}


@app.post("/api/admin/confirm_deposit")
async def admin_confirm_deposit(
    tx_id: str = Query(...),
    user_id: int = Query(...),
    amount: float = Query(...),
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    """Адмін: ручно підтвердити депозит USDT."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    existing = db.execute(select(TonTransaction).where(TonTransaction.tx_hash == f"test_{tx_id}")).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="Транзакція вже підтверджена")
    _set_balance(user, _balance(user) + amount)
    db.add(TonTransaction(tx_hash=f"test_{tx_id}", user_id=user_id, amount=amount, comment=f"admin_confirm_{tx_id}"))
    db.commit()
    return {"ok": True, "amount": amount, "balance": _balance(user)}


def _decode_jetton_comment(forward_payload_b64: str) -> str:
    """Декодувати текст коментаря з forward_payload (base64)."""
    if not forward_payload_b64:
        return ""
    try:
        raw = base64.b64decode(forward_payload_b64)
        # перші 4 байти — op (0x00000000 для text), далі utf-8 текст
        if len(raw) > 4:
            return raw[4:].decode("utf-8", errors="ignore").strip()
        return raw.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


@app.get("/api/check_transaction/{tx_id}")
async def check_transaction(tx_id: str, uid: int = Depends(get_current_user), db=Depends(get_db)):
    """Перевірити вхідний USDT (jetton) переказ через TON Center API v3."""
    user = db.get(User, uid)
    if not user:
        user = User(tg_id=uid, balance=0, balance_usdt=0.0)
        db.add(user)
        db.flush()
    if getattr(user, "balance_usdt", None) is None:
        user.balance_usdt = 0.0

    test_tx = db.execute(select(TonTransaction).where(TonTransaction.tx_hash == f"test_{tx_id}")).scalars().first()
    if test_tx:
        return {"confirmed": True, "amount": test_tx.amount, "balance": _balance(user)}
    
    if not TON_RECEIVE_WALLET:
        return {"confirmed": False, "message": "TON_RECEIVE_WALLET not configured"}
    try:
        # TON Center API v3: вхідні USDT-трансфери на наш гаманець
        url = (
            f"{TON_CENTER_V3_URL}jetton/transfers?"
            f"owner_address={urllib.parse.quote(TON_RECEIVE_WALLET)}&direction=in&jetton_master={urllib.parse.quote(USDT_JETTON_MASTER)}&limit=50"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        transfers = data.get("jetton_transfers") or []
        prefix = f"deposit_{uid}_{tx_id[:8]}"
        for t in transfers:
            comment = _decode_jetton_comment(t.get("forward_payload") or "")
            if prefix not in comment:
                continue
            tx_hash = t.get("transaction_hash") or ""
            if not tx_hash:
                continue
            existing = db.execute(select(TonTransaction).where(TonTransaction.tx_hash == tx_hash)).scalars().first()
            if existing:
                continue
            # USDT на TON: 6 decimals
            amount_raw = int(t.get("amount") or "0")
            amount_usdt = round(amount_raw / 1_000_000, 2)
            if amount_usdt <= 0:
                continue
            _set_balance(user, _balance(user) + amount_usdt)
            db.add(TonTransaction(tx_hash=tx_hash, user_id=uid, amount=amount_usdt, comment=comment))
            db.commit()
            return {"confirmed": True, "amount": amount_usdt, "balance": _balance(user)}
        return {"confirmed": False}
    except Exception as e:
        return {"confirmed": False, "error": str(e)}


def _fetch_bl1_matches_for_matchday(matchday: int):
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY, "Accept": "application/json"}
    req = urllib.request.Request(
        f"https://api.football-data.org/v4/competitions/BL1/matches?matchday={matchday}",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    return data.get("matches") or []


def _parse_utc_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _get_current_bundesliga_matchday():
    """Визначити поточний тур Бундесліги: якщо поточний тур завершений (всі матчі FINISHED), повертає наступний тур."""
    if not FOOTBALL_DATA_API_KEY:
        return None
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY, "Accept": "application/json"}
    try:
        req = urllib.request.Request(
            "https://api.football-data.org/v4/competitions/BL1",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            comp = json.loads(resp.read().decode())
        current = comp.get("currentSeason") or {}
        matchday = current.get("currentMatchday")
        if matchday is None or not isinstance(matchday, int) or matchday < 1:
            matchday = 1
        # Перевіряємо, чи всі матчі поточного туру завершені
        try:
            matches_data = _fetch_bl1_matches_for_matchday(matchday)
            all_finished = True
            if matches_data:
                for m in matches_data:
                    status = m.get("status")
                    if status != "FINISHED":
                        all_finished = False
                        break
                # Якщо всі матчі завершені, переходимо до наступного туру
                if all_finished and matchday < 34:
                    matchday += 1
        except Exception:
            pass  # Якщо не вдалося отримати матчі, повертаємо поточний тур
        return matchday
    except Exception:
        return None


@app.get("/api/admin/bundesliga_info")
async def bundesliga_info(
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    """Отримати інформацію про поточний тур Бундесліги та максимальну кількість турів для створення гри."""
    if not FOOTBALL_DATA_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Додайте FOOTBALL_DATA_API_KEY у змінні середовища.",
        )
    current_matchday = _get_current_bundesliga_matchday()
    if current_matchday is None:
        raise HTTPException(
            status_code=502,
            detail="Не вдалося визначити поточний тур Бундесліги.",
        )
    max_rounds = 34 - current_matchday + 1
    return {
        "ok": True,
        "current_matchday": current_matchday,
        "max_rounds": max_rounds,
        "message": f"Поточний тур: {current_matchday}. Можна створити гру на максимум {max_rounds} турів (тури {current_matchday}–34).",
    }


@app.post("/api/admin/create_bundesliga_game")
async def create_bundesliga_game(
    body: CreateBundesligaGameBody,
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    """Створити гру Бундесліги: якщо start_matchday не вказано, використовується поточний тур. Менеджер обирає кількість турів."""
    if not FOOTBALL_DATA_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Додайте FOOTBALL_DATA_API_KEY у змінні середовища.",
        )
    # Якщо start_matchday не вказано, використовуємо поточний тур
    if body.start_matchday is None:
        start = _get_current_bundesliga_matchday()
        if start is None:
            raise HTTPException(
                status_code=502,
                detail="Не вдалося визначити поточний тур Бундесліги.",
            )
    else:
        start = max(1, min(34, body.start_matchday))
    count = max(1, min(34, body.rounds_count))
    if start + count - 1 > 34:
        raise HTTPException(
            status_code=400,
            detail=f"start_matchday ({start}) + rounds_count ({count}) не може перевищувати 34. Максимум турів: {34 - start + 1}.",
        )
    existing = db.execute(select(Game).where(Game.title == "Bundesliga", Game.start_matchday.isnot(None))).scalars().first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="Гра Бундесліги з start_matchday вже існує. Створіть нову з іншою назвою або видаліть існуючу.",
        )
    game = Game(
        title="Bundesliga",
        rounds_total=count,
        current_round=1,
        status="active",
        start_matchday=start,
    )
    db.add(game)
    db.flush()
    existing_teams = {t.name: t for t in db.execute(select(Team)).scalars().all()}

    def get_or_create_team(name: str):
        if not name:
            return None
        if name in existing_teams:
            return existing_teams[name]
        team = Team(name=name)
        db.add(team)
        db.flush()
        existing_teams[name] = team
        return team

    total_matches = 0
    for round_num in range(1, count + 1):
        matchday = start + round_num - 1
        try:
            matches_data = _fetch_bl1_matches_for_matchday(matchday)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Не вдалося отримати матчі туру {matchday}: {e}")
        for m in matches_data:
            home_name = (m.get("homeTeam") or {}).get("name") or (m.get("homeTeam") or {}).get("shortName") or ""
            away_name = (m.get("awayTeam") or {}).get("name") or (m.get("awayTeam") or {}).get("shortName") or ""
            if not home_name or not away_name:
                continue
            home_team = get_or_create_team(home_name)
            away_team = get_or_create_team(away_name)
            if not home_team or not away_team:
                continue
            score = (m.get("score") or {}).get("fullTime") or {}
            hg, ag = score.get("home"), score.get("away")
            db.add(
                Match(
                    game_id=game.id,
                    round=round_num,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    home_goals=int(hg) if hg is not None else None,
                    away_goals=int(ag) if ag is not None else None,
                    utc_date=_parse_utc_date(m.get("utcDate")),
                    external_id=str(m["id"]) if m.get("id") is not None else None,
                    status=m.get("status"),
                )
            )
            total_matches += 1
    db.commit()
    db.refresh(game)
    return {
        "ok": True,
        "game_id": game.id,
        "title": game.title,
        "start_matchday": start,
        "rounds_total": count,
        "matches_count": total_matches,
        "message": f"Гра створена: тури {start}–{start + count - 1} Бундесліги. Дедлайн ставок — початок першого матчу кожного туру.",
    }


@app.post("/api/admin/sync_bundesliga_round")
async def sync_bundesliga_round(
    game_id: int | None = Query(None),
    db=Depends(get_db),
    _admin: int = Depends(require_admin),
):
    """Синхронізувати поточний тур з Football-Data.org: оновити рахунки; якщо всі матчі завершені — застосувати результати туру і перейти до наступного."""
    if not FOOTBALL_DATA_API_KEY:
        raise HTTPException(status_code=400, detail="FOOTBALL_DATA_API_KEY не налаштовано.")
    game = None
    if game_id:
        game = db.get(Game, game_id)
    if not game:
        game = db.execute(
            select(Game).where(Game.title == "Bundesliga", Game.start_matchday.isnot(None))
        ).scalars().first()
    if not game or game.status != "active":
        raise HTTPException(status_code=404, detail="Активну гру Бундесліги (зі start_matchday) не знайдено.")
    rnd = game.current_round
    if rnd > game.rounds_total:
        return {"ok": True, "message": "Гра вже завершена.", "current_round": rnd}
    start_matchday = game.start_matchday or rnd
    matchday = start_matchday + rnd - 1
    try:
        matches_data = _fetch_bl1_matches_for_matchday(matchday)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не вдалося отримати матчі: {e}")
    our_matches = (
        db.execute(select(Match).where(Match.game_id == game.id, Match.round == rnd))
        .scalars().all()
    )
    ext_id_to_match = {m.external_id: m for m in our_matches if m.external_id}
    all_finished = True
    for m in matches_data:
        ext_id = str(m.get("id")) if m.get("id") is not None else None
        our = ext_id_to_match.get(ext_id) if ext_id else None
        if not our:
            continue
        score = (m.get("score") or {}).get("fullTime") or {}
        hg, ag = score.get("home"), score.get("away")
        status_val = m.get("status")
        if status_val != "FINISHED":
            all_finished = False
        our.home_goals = int(hg) if hg is not None else our.home_goals
        our.away_goals = int(ag) if ag is not None else our.away_goals
        our.status = status_val
    db.commit()
    if all_finished and our_matches:
        _apply_round_results(db, game, rnd)
        return {
            "ok": True,
            "message": f"Тур {rnd} завершено. Результати застосовано. Поточний тур: {game.current_round}.",
            "current_round": game.current_round,
            "round_finished": True,
        }
    return {
        "ok": True,
        "message": "Рахунки оновлено. Очікуйте завершення всіх матчів туру.",
        "current_round": rnd,
        "round_finished": False,
    }


@app.post("/api/admin/fetch_bundesliga_round")
async def fetch_bundesliga_round(db=Depends(get_db), _admin: int = Depends(require_admin)):
    """Підтягнути поточний тур Бундесліги з Football-Data.org: створити/оновити гру Bundesliga та матчі поточного туру (без start_matchday — один тур)."""
    if not FOOTBALL_DATA_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Додайте FOOTBALL_DATA_API_KEY у змінні середовища (реєстрація на football-data.org).",
        )
    headers = {"X-Auth-Token": FOOTBALL_DATA_API_KEY, "Accept": "application/json"}
    try:
        req = urllib.request.Request(
            "https://api.football-data.org/v4/competitions/BL1",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            comp = json.loads(resp.read().decode())
    except Exception as e:
        raise HTTPException(status_code=502, detail="Не вдалося отримати BL1: " + str(e))
    current = comp.get("currentSeason") or {}
    matchday = current.get("currentMatchday")
    if matchday is None:
        matchday = current.get("currentMatchday", 1)
    if not isinstance(matchday, int) or matchday < 1:
        matchday = 1
    try:
        req2 = urllib.request.Request(
            f"https://api.football-data.org/v4/competitions/BL1/matches?matchday={matchday}",
            headers=headers,
        )
        with urllib.request.urlopen(req2, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        raise HTTPException(status_code=502, detail="Не вдалося отримати матчі: " + str(e))
    matches_data = data.get("matches") or data if isinstance(data, list) else []
    if not matches_data and isinstance(data, dict):
        matches_data = data.get("matches") or []
    game = db.execute(select(Game).where(Game.title == "Bundesliga")).scalars().first()
    if not game:
        game = Game(title="Bundesliga", rounds_total=34, current_round=matchday, status="active")
        db.add(game)
        db.flush()
    game.current_round = matchday
    game.status = "active"
    existing_teams = {t.name: t for t in db.execute(select(Team)).scalars().all()}
    def get_or_create_team(name: str):
        if not name:
            return None
        if name in existing_teams:
            return existing_teams[name]
        team = Team(name=name)
        db.add(team)
        db.flush()
        existing_teams[name] = team
        return team
    old_matches = (
        db.execute(select(Match).where(Match.game_id == game.id, Match.round == matchday))
        .scalars().all()
    )
    def parse_utc(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    for m in old_matches:
        db.delete(m)
    for m in matches_data:
        home_name = (m.get("homeTeam") or {}).get("name") or (m.get("homeTeam") or {}).get("shortName") or ""
        away_name = (m.get("awayTeam") or {}).get("name") or (m.get("awayTeam") or {}).get("shortName") or ""
        if not home_name or not away_name:
            continue
        home_team = get_or_create_team(home_name)
        away_team = get_or_create_team(away_name)
        if not home_team or not away_team:
            continue
        score = (m.get("score") or {}).get("fullTime") or {}
        home_goals = score.get("home")
        away_goals = score.get("away")
        ext_id = str(m.get("id")) if m.get("id") is not None else None
        utc_d = parse_utc(m.get("utcDate"))
        status_val = m.get("status")
        db.add(
            Match(
                game_id=game.id,
                round=matchday,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                home_goals=int(home_goals) if home_goals is not None else None,
                away_goals=int(away_goals) if away_goals is not None else None,
                utc_date=utc_d,
                external_id=ext_id,
                status=status_val,
            )
        )
    db.commit()
    db.refresh(game)
    return {
        "ok": True,
        "game_id": game.id,
        "title": game.title,
        "matchday": matchday,
        "matches_count": len(matches_data),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
