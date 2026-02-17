"""
Database package: engine, session, and model registration.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DATABASE_URL

# Ensure data directory exists for SQLite
if DATABASE_URL.startswith("sqlite"):
    from pathlib import Path
    path = Path(DATABASE_URL.replace("sqlite:///", ""))
    path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency for FastAPI; yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_entries_stake_column():
    """Add entries.stake column if missing (migration for existing DBs)."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('entries') WHERE name='stake'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE entries ADD COLUMN stake INTEGER"))
            conn.commit()


def _ensure_matches_goals_columns():
    """Add matches.home_goals and away_goals if missing."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('matches') WHERE name='home_goals'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE matches ADD COLUMN home_goals INTEGER"))
            conn.execute(text("ALTER TABLE matches ADD COLUMN away_goals INTEGER"))
            conn.commit()


def _ensure_entries_stake_amount_column():
    """Add entries.stake_amount (REAL) if missing; backfill from stake."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('entries') WHERE name='stake_amount'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE entries ADD COLUMN stake_amount REAL"))
            conn.execute(text("UPDATE entries SET stake_amount = CAST(stake AS REAL) WHERE stake IS NOT NULL AND stake_amount IS NULL"))
            conn.execute(text("UPDATE entries SET stake_amount = 10.0 WHERE stake_amount IS NULL"))
            conn.commit()


def _ensure_users_balance_column():
    """Add users.balance if missing; new users get 0 поінтів (потрібно поповнити)."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('users') WHERE name='balance'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 0"))
            conn.execute(text("UPDATE users SET balance = 0 WHERE balance IS NULL"))
            conn.commit()


def _ensure_users_ton_wallet_column():
    """Add users.ton_wallet_address for TON Connect integration."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('users') WHERE name='ton_wallet_address'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN ton_wallet_address VARCHAR(64)"))
            conn.commit()


def _ensure_users_balance_usdt_column():
    """Add users.balance_usdt (REAL) for USDT balance."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('users') WHERE name='balance_usdt'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN balance_usdt REAL DEFAULT 0"))
            conn.execute(text("UPDATE users SET balance_usdt = 0 WHERE balance_usdt IS NULL"))
            conn.commit()


def _ensure_selections_ticket_index():
    """Add selections.ticket_index for підтримка кількох білетів."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('selections') WHERE name='ticket_index'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE selections ADD COLUMN ticket_index INTEGER NOT NULL DEFAULT 1"))
            conn.commit()


def _ensure_game_start_matchday():
    """Add games.start_matchday for Bundesliga (start BL1 matchday)."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('games') WHERE name='start_matchday'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE games ADD COLUMN start_matchday INTEGER"))
            conn.commit()


def _ensure_match_utc_date_and_external():
    """Add matches.utc_date, external_id, status for Football-Data.org."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('matches') WHERE name='utc_date'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE matches ADD COLUMN utc_date DATETIME"))
            conn.execute(text("ALTER TABLE matches ADD COLUMN external_id VARCHAR(32)"))
            conn.execute(text("ALTER TABLE matches ADD COLUMN status VARCHAR(32)"))
            conn.commit()


def _ensure_tickets_backfill():
    """Для записів без білетів створити один білет (зворотна сумісність)."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT 1 FROM tickets LIMIT 1"))
        except Exception:
            return  # таблиця tickets ще не існує
        try:
            entries = conn.execute(text("SELECT id, stake_amount, status FROM entries")).fetchall()
        except Exception:
            return
        for row in entries:
            eid, stake, status = row[0], row[1], row[2]
            has = conn.execute(text("SELECT 1 FROM tickets WHERE entry_id = :eid"), {"eid": eid}).fetchone()
            if has:
                continue
            stake_val = float(stake) if stake is not None else 10.0
            ticket_status = "out" if status == "out" else "active"
            conn.execute(
                text("INSERT INTO tickets (entry_id, ticket_index, stake_amount, status) VALUES (:eid, 1, :stake, :status)"),
                {"eid": eid, "stake": stake_val, "status": ticket_status},
            )
        conn.commit()


def init_db():
    """Create all tables. Call on app startup."""
    from database import models  # noqa: F401 - register models
    Base.metadata.create_all(bind=engine)
    _ensure_entries_stake_column()
    _ensure_matches_goals_columns()
    _ensure_entries_stake_amount_column()
    _ensure_users_balance_column()
    _ensure_users_ton_wallet_column()
    _ensure_users_balance_usdt_column()
    _ensure_selections_ticket_index()
    _ensure_game_start_matchday()
    _ensure_match_utc_date_and_external()
    _ensure_tickets_backfill()


def seed_teams():
    """Insert default football teams (EPL + La Liga, add if not exist by name)."""
    from sqlalchemy import select
    from database.models import Team
    teams = [
        # Англійська Прем'єр-ліга
        "Арсенал", "Манчестер Сити", "Астон Вилла", "Манчестер Юнайтед", "Челси", "Ливерпуль",
        "Брентфорд", "Эвертон", "Борнмут", "Ньюкасл", "Сандерленд", "Фулхэм", "Кристал Пэлас",
        "Брайтон", "Лидс Юнайтед", "Тоттенхэм", "Ноттингем Форест", "Вест Хэм", "Бернли", "Вулверхэмптон",
        # Ла Ліга
        "Реал Мадрид", "Барселона", "Вильярреал", "Атлетико Мадрид", "Бетис", "Эспаньол", "Сельта",
        "Реал Сосьедад", "Атлетик", "Осасуна", "Хетафе", "Севилья", "Алавес", "Валенсия", "Жирона",
        "Эльче", "Райо Вальекано", "Мальорка", "Леванте", "Реал Овьедо",
    ]
    with SessionLocal() as db:
        existing = set(db.execute(select(Team.name)).scalars().all())
        for name in teams:
            if name not in existing:
                db.add(Team(name=name))
                existing.add(name)
        db.commit()
