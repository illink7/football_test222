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
    """Add users.balance if missing; new users get 1000 поінтів."""
    if "sqlite" not in DATABASE_URL:
        return
    with engine.connect() as conn:
        r = conn.execute(text("SELECT 1 FROM pragma_table_info('users') WHERE name='balance'"))
        if r.scalar() is None:
            conn.execute(text("ALTER TABLE users ADD COLUMN balance INTEGER DEFAULT 1000"))
            conn.execute(text("UPDATE users SET balance = 1000 WHERE balance IS NULL"))
            conn.commit()


def init_db():
    """Create all tables. Call on app startup."""
    from database import models  # noqa: F401 - register models
    Base.metadata.create_all(bind=engine)
    _ensure_entries_stake_column()
    _ensure_matches_goals_columns()
    _ensure_entries_stake_amount_column()
    _ensure_users_balance_column()


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
