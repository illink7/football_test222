"""
Database package: engine, session, and model registration.
"""
from sqlalchemy import create_engine
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


def init_db():
    """Create all tables. Call on app startup."""
    from database import models  # noqa: F401 - register models
    Base.metadata.create_all(bind=engine)


def seed_teams():
    """Insert 20 default football teams if the table is empty."""
    from sqlalchemy import select
    from database.models import Team
    with SessionLocal() as db:
        if db.execute(select(Team)).scalars().first() is not None:
            return
        teams = [
            "Арсенал", "Манчестер Сіті", "Ліверпуль", "Челсі", "Манчестер Юнайтед",
            "Тоттенхем", "Ньюкасл", "Астон Вілла", "Брайтон", "Вест Хем",
            "Кристал Пелес", "Вулвергемптон", "Борнмут", "Фулхем", "Брентфорд",
            "Евертон", "Ноттінгем Форест", "Лідс", "Лестер Сіті", "Саутгемптон",
        ]
        for name in teams:
            db.add(Team(name=name))
        db.commit()
