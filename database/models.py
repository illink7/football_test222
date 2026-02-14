"""
SQLAlchemy models for Survivor Football game.
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, ForeignKey, Enum as SQLEnum
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
    matches = relationship("Match", back_populates="game", lazy="selectin")


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.tg_id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    status = Column(String(32), default="active")  # active | out
    stake = Column(Integer, nullable=True)  # legacy
    stake_amount = Column(Float, nullable=True)  # поточна сума виграшу (×1.5 після кожного туру)

    user = relationship("User", back_populates="entries")
    game = relationship("Game", back_populates="entries")
    selections = relationship(
        "Selection",
        back_populates="entry",
        lazy="selectin",
        order_by="Selection.round",
    )
    match_selections = relationship(
        "EntryMatchSelection",
        back_populates="entry",
        lazy="selectin",
    )


class Team(Base):
    """Pool of teams that can be selected in games."""
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False)


class Match(Base):
    """Fixture for a round: home_team vs away_team. home_goals/away_goals set when round is simulated."""
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    round = Column(Integer, nullable=False)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    home_goals = Column(Integer, nullable=True)  # filled after round simulation
    away_goals = Column(Integer, nullable=True)

    game = relationship("Game", back_populates="matches")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])
    entry_selections = relationship("EntryMatchSelection", back_populates="match", lazy="selectin")


class EntryMatchSelection(Base):
    """User's selected matches for a round (which matches they 'bet' on)."""
    __tablename__ = "entry_match_selections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)

    entry = relationship("Entry", back_populates="match_selections")
    match = relationship("Match", back_populates="entry_selections")


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
