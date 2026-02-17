"""
Вывод средств: пошаговый сценарий в чате (кошелёк → сумма).
Сообщения на русском, как в интерфейсе вывода.
"""
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.orm import Session

from database import SessionLocal
from database.models import User

router = Router(name="withdraw")


def _balance(user: User) -> float:
    v = getattr(user, "balance_usdt", None)
    if v is None:
        return 0.0
    return float(v)


def _set_balance(user: User, value: float) -> None:
    user.balance_usdt = round(value, 2)


def is_ton_address(text: str) -> bool:
    s = (text or "").strip()
    return bool(s and re.match(r"^(EQ|UQ)[a-zA-Z0-9_-]{46,48}$", s))


class WithdrawStates(StatesGroup):
    waiting_wallet = State()
    waiting_amount = State()


@router.message(Command("withdraw"))
@router.message(F.text.casefold() == "вывод средств")
async def cmd_withdraw(message: Message, state: FSMContext):
    tg_id = message.from_user.id if message.from_user else 0
    db: Session = SessionLocal()
    try:
        user = db.get(User, tg_id)
        if not user:
            user = User(tg_id=tg_id, balance=0, balance_usdt=0.0)
            db.add(user)
            db.flush()
        if getattr(user, "balance_usdt", None) is None:
            user.balance_usdt = 0.0

        wallet = (user.ton_wallet_address or "").strip()
        if not wallet:
            await state.set_state(WithdrawStates.waiting_wallet)
            await message.answer(
                "<b>Кошелёк для вывода</b>\n\n"
                "Отправьте ваш TON-кошелёк одним сообщением (формат EQ... или UQ...). "
                "Его можно будет позже изменить в личном кабинете.",
            )
            return

        bal = _balance(user)
        await state.set_state(WithdrawStates.waiting_amount)
        await message.answer(
            "<b>Вывод средств</b>\n\n"
            f"Ваш текущий баланс: <b>{bal:.2f} USDT</b>\n"
            f"Кошелёк для вывода: <code>{wallet[:8]}...{wallet[-6:]}</code>\n\n"
            "Введите сумму вывода в USDT (например: 10.5):",
        )
    finally:
        db.close()


@router.message(WithdrawStates.waiting_wallet, F.text)
async def process_wallet(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not is_ton_address(text):
        await message.answer(
            "Не похоже на адрес TON-кошелька. Отправьте адрес в формате EQ... или UQ... (одним сообщением)."
        )
        return

    tg_id = message.from_user.id if message.from_user else 0
    db: Session = SessionLocal()
    try:
        user = db.get(User, tg_id)
        if not user:
            user = User(tg_id=tg_id, balance=0, balance_usdt=0.0)
            db.add(user)
            db.flush()
        user.ton_wallet_address = text
        db.commit()
        await state.clear()
        await message.answer(
            "<b>Кошелёк сохранён!</b>\n\n"
            f"Адрес: <code>{text}</code>\n\n"
            "Теперь нажмите ещё раз «Вывод средств» и введите сумму.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Вывод средств", callback_data="withdraw_again")]
            ]),
        )
    finally:
        db.close()


@router.message(WithdrawStates.waiting_amount, F.text)
async def process_amount(message: Message, state: FSMContext):
    text = (message.text or "").strip().replace(",", ".")
    try:
        amount = round(float(text), 2)
    except ValueError:
        await message.answer("Введите число — сумму в USDT (например: 10 или 10.5).")
        return

    if amount < 0.1:
        await message.answer("Минимальная сумма вывода: 0.1 USDT.")
        return

    tg_id = message.from_user.id if message.from_user else 0
    db: Session = SessionLocal()
    try:
        user = db.get(User, tg_id)
        if not user:
            await state.clear()
            await message.answer("Ошибка: пользователь не найден.")
            return
        if getattr(user, "balance_usdt", None) is None:
            user.balance_usdt = 0.0

        bal = _balance(user)
        if bal < amount:
            await message.answer(
                "❌ <b>Недостаточно средств на балансе.</b>\n\n"
                f"Ваш баланс: <b>{bal:.2f} USDT</b>",
            )
            return

        _set_balance(user, bal - amount)
        db.commit()
        new_bal = _balance(user)
        await state.clear()
        await message.answer(
            f"✅ Заявка на вывод <b>{amount:.2f} USDT</b> создана.\n\n"
            f"Остаток на балансе: <b>{new_bal:.2f} USDT</b>\n\n"
            "Перевод будет выполнен на указанный кошелёк. Ожидайте зачисления.",
        )
    finally:
        db.close()


@router.callback_query(F.data == "withdraw_again")
async def cb_withdraw_again(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    tg_id = callback.from_user.id if callback.from_user else 0
    db: Session = SessionLocal()
    try:
        user = db.get(User, tg_id)
        if not user:
            user = User(tg_id=tg_id, balance=0, balance_usdt=0.0)
            db.add(user)
            db.flush()
        if getattr(user, "balance_usdt", None) is None:
            user.balance_usdt = 0.0
        wallet = (user.ton_wallet_address or "").strip()
        if not wallet:
            await state.set_state(WithdrawStates.waiting_wallet)
            await callback.message.answer(
                "<b>Кошелёк для вывода</b>\n\n"
                "Отправьте ваш TON-кошелёк одним сообщением (формат EQ... или UQ...). "
                "Его можно будет позже изменить в личном кабинете.",
            )
            return
        bal = _balance(user)
        await state.set_state(WithdrawStates.waiting_amount)
        await callback.message.answer(
            "<b>Вывод средств</b>\n\n"
            f"Ваш текущий баланс: <b>{bal:.2f} USDT</b>\n"
            f"Кошелёк для вывода: <code>{wallet[:8]}...{wallet[-6:]}</code>\n\n"
            "Введите сумму вывода в USDT (например: 10.5):",
        )
    finally:
        db.close()
