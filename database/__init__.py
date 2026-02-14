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


def init_db():
    """Create all tables. Call on app startup."""
    from database import models  # noqa: F401 - register models
    Base.metadata.create_all(bind=engine)
    _ensure_entries_stake_column()
    _ensure_matches_goals_columns()


def seed_teams():
    """Insert default football teams (add if not exist by name)."""
    from sqlalchemy import select
    from database.models import Team
    teams = [
        "Вулверхэмптон", "Арсенал", "Борнмут", "Ман Юнайтед", "Брайтон",
        "Ливерпуль", "Фулхэм", "Бернли", "Ман Сити", "Кристал Пэлас",
        "Эвертон", "Челси", "Лидс", "Брентфорд", "Ньюкасл",
        "Сандерленд", "Астон Вилла", "Вест Хэм", "Тоттенхэм", "Ноттингем Форест",
    ]
    with SessionLocal() as db:
        existing = set(db.execute(select(Team.name)).scalars().all())
        for name in teams:
            if name not in existing:
                db.add(Team(name=name))
                existing.add(name)
        db.commit()
