"""
User handlers: /start, My Entries, open Web App to pick teams.
"""
from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command
from sqlalchemy import select

from config import WEBAPP_BASE_URL
from database import SessionLocal
from database.models import User, Entry, Game

router = Router(name="user")


@router.message(Command("start"))
async def cmd_start(message: Message):
    """Register user if needed and show My Entries."""
    tg_id = message.from_user.id if message.from_user else 0
    username = message.from_user.username if message.from_user else None

    db = SessionLocal()
    try:
        user = db.get(User, tg_id)
        if not user:
            user = User(tg_id=tg_id, username=username)
            db.add(user)
            db.commit()
            db.refresh(user)

        entries = (
            db.execute(
                select(Entry, Game)
                .join(Game, Entry.game_id == Game.id)
                .where(Entry.user_id == tg_id)
                .order_by(Entry.id.desc())
            )
            .all()
        )

        if not entries:
            await message.answer(
                "Вітаю в Survivor Football.\n\n"
                "У тебе ще немає записів. Адмін має створити гру та додати тобі entry (/add_entry). "
                "Потім тут з’явиться кнопка «Обрати команди» — відкриється веб-додаток.\n\n"
                "Щоб веб-додаток був у меню бота (кнопка внизу чату): у @BotFather → твій бот → "
                "Bot Settings → Menu Button → Configure → вкажи URL свого додатку (наприклад Railway).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Відкрити веб-додаток", web_app=WebAppInfo(url=WEBAPP_BASE_URL.rstrip("/")))],
                    [InlineKeyboardButton(text="Вывод средств", callback_data="withdraw_again")],
                ]),
            )
            return

        lines = ["My Entries:\n"]
        active_entries = []
        for row in entries:
            entry, game = row[0], row[1]
            status_emoji = "✅" if entry.status == "active" else "❌"
            lines.append(f"{status_emoji} Entry #{entry.id} — {game.title} (round {game.current_round}/{game.rounds_total}) — {entry.status}")
            if entry.status == "active" and game.status == "active":
                active_entries.append((entry, game))

        reply_markup = None
        if active_entries:
            buttons = [
                [InlineKeyboardButton(
                    text=f"Pick teams — Entry #{e.id} ({g.title})",
                    web_app=WebAppInfo(url=f"{WEBAPP_BASE_URL.rstrip('/')}/select_teams?entry_id={e.id}"),
                )]
                for e, g in active_entries
            ]
            reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer("\n".join(lines), reply_markup=reply_markup)
    finally:
        db.close()
