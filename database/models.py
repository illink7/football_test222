"""
SQLAlchemy models for Survivor Football game.
"""
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    tg_id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)

    entries = relationship("Entry", back_populates="user", lazy="selectin")


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    rounds_total = Column(Integer, default=10)
    current_round = Column(Integer, default=1)
    status = Column(String(32), default="active")  # e.g. active, finished

    entries = relationship("Entry", back_populates="game", lazy="selectin")


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.tg_id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    status = Column(String(32), default="active")  # active | out

    user = relationship("User", back_populates="entries")
    game = relationship("Game", back_populates="entries")
    selections = relationship(
        "Selection",
        back_populates="entry",
        lazy="selectin",
        order_by="Selection.round",
    )


class Team(Base):
    """Pool of teams that can be selected in games."""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)


class Selection(Base):
    __tablename__ = "selections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)
    round = Column(Integer, nullable=False)
    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=False)

    entry = relationship("Entry", back_populates="selections")
    team1 = relationship("Team", foreign_keys=[team1_id])
    team2 = relationship("Team", foreign_keys=[team2_id])
