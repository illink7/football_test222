"""
Admin-only handlers: create game, submit results.
"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import ADMIN_ID
from database import SessionLocal
from database.models import Game, Entry, Selection, Team, User

router = Router(name="admin")


def is_admin(tg_id: int) -> bool:
    return tg_id == ADMIN_ID or tg_id == 0  # 0 for dev when ADMIN_ID not set


@router.message(Command("create_game"), F.from_user.id == ADMIN_ID)
async def cmd_create_game(message: Message):
    """Create a new game. Usage: /create_game <title>"""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Usage: /create_game <title>\nExample: /create_game Premier League Survivor")
        return
    title = args[1].strip()
    db: Session = SessionLocal()
    try:
        game = Game(title=title, rounds_total=10, current_round=1, status="active")
        db.add(game)
        db.commit()
        db.refresh(game)
        await message.answer(f"Game created: «{game.title}» (ID: {game.id}, 10 rounds).")
    finally:
        db.close()


@router.message(Command("add_entry"), F.from_user.id == ADMIN_ID)
async def cmd_add_entry(message: Message):
    """Add an entry for a user to a game. Usage: /add_entry <game_id> [tg_id] (tg_id default: you)."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Usage: /add_entry <game_id> [tg_id]")
        return
    try:
        game_id = int(parts[1])
    except ValueError:
        await message.answer("game_id must be a number.")
        return
    tg_id = message.from_user.id if message.from_user else 0
    if len(parts) >= 3:
        try:
            tg_id = int(parts[2])
        except ValueError:
            await message.answer("tg_id must be a number.")
            return
    db: Session = SessionLocal()
    try:
        game = db.get(Game, game_id)
        if not game:
            await message.answer("Game not found.")
            return
        user = db.get(User, tg_id)
        if not user:
            user = User(tg_id=tg_id)
            db.add(user)
            db.flush()
        entry = Entry(user_id=tg_id, game_id=game_id, status="active")
        db.add(entry)
        db.commit()
        db.refresh(entry)
        await message.answer(f"Entry #{entry.id} added for user {tg_id} in game «{game.title}».")
    finally:
        db.close()


@router.message(Command("add_teams"), F.from_user.id == ADMIN_ID)
async def cmd_add_teams(message: Message):
    """Add teams to the pool. Usage: /add_teams Arsenal, Chelsea, ManCity, Liverpool, ..."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "Usage: /add_teams Team1, Team2, Team3, ...\n"
            "Example: /add_teams Arsenal, Chelsea, ManCity, Liverpool"
        )
        return
    names = [n.strip() for n in args[1].split(",") if n.strip()]
    if not names:
        await message.answer("No team names provided.")
        return
    db: Session = SessionLocal()
    added = 0
    try:
        for name in names:
            existing = db.execute(select(Team).where(Team.name == name)).scalars().first()
            if not existing:
                db.add(Team(name=name))
                added += 1
        db.commit()
        await message.answer(f"Added {added} new team(s). Total requested: {len(names)}.")
    finally:
        db.close()


@router.message(Command("result"), F.from_user.id == ADMIN_ID)
async def cmd_result(message: Message):
    """
    Submit round results and advance game.
    Usage: /result <game_id> <results_string>
    Example: /result 1 Arsenal:1, Chelsea:0, ManCity:2
    """
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Usage: /result <game_id> <results_string>\n"
            "Example: /result 1 Arsenal:1, Chelsea:0, ManCity:2"
        )
        return
    try:
        game_id = int(parts[1])
    except ValueError:
        await message.answer("game_id must be a number.")
        return
    results_string = parts[2].strip()

    # Parse "Arsenal:1, Chelsea:0, ManCity:2" -> { "Arsenal": 1, "Chelsea": 0, "ManCity": 2 }
    team_scores = {}
    for pair in results_string.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        name, score_str = pair.split(":", 1)
        name = name.strip()
        try:
            team_scores[name] = int(score_str.strip())
        except ValueError:
            continue

    if not team_scores:
        await message.answer("Could not parse any team:score pairs.")
        return

    db: Session = SessionLocal()
    try:
        game = db.get(Game, game_id)
        if not game:
            await message.answer(f"Game with ID {game_id} not found.")
            return
        if game.status != "active":
            await message.answer(f"Game is not active (status: {game.status}).")
            return

        # Resolve team names to IDs (by name match)
        teams_list = db.execute(select(Team)).scalars().all()
        teams_by_name = {t.name: t for t in teams_list}
        team_id_to_score = {}
        for name, score in team_scores.items():
            if name in teams_by_name:
                team_id_to_score[teams_by_name[name].id] = score

        # Current round for this game
        rnd = game.current_round

        # Entries that have a selection for this round
        entries_with_selection = (
            db.execute(
                select(Entry).join(Selection, Selection.entry_id == Entry.id).where(
                    Entry.game_id == game_id,
                    Entry.status == "active",
                    Selection.round == rnd,
                )
            )
            .unique()
            .scalars().all()
        )

        out_count = 0
        for entry in entries_with_selection:
            sel = next((s for s in entry.selections if s.round == rnd), None)
            if not sel:
                continue
            s1 = team_id_to_score.get(sel.team1_id)
            s2 = team_id_to_score.get(sel.team2_id)
            # If either team scored 0 (and we have a score for them), entry is out
            if (s1 is not None and s1 == 0) or (s2 is not None and s2 == 0):
                entry.status = "out"
                out_count += 1

        # Move game to next round
        if game.current_round >= game.rounds_total:
            game.status = "finished"
            game.current_round = game.rounds_total
        else:
            game.current_round += 1

        db.commit()
        status = "finished" if game.status == "finished" else f"round {game.current_round}"
        await message.answer(
            f"Results applied for game «{game.title}». "
            f"Entries marked out this round: {out_count}. Game status: {status}."
        )
    finally:
        db.close()
