"""
SQLAlchemy models for Survivor Football game.
"""
from sqlalchemy import Column, Integer, String, Boolean, Float, ForeignKey, DateTime, Enum as SQLEnum
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class User(Base):
    __tablename__ = "users"

    tg_id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False)
    balance = Column(Integer, default=0)  # legacy (points)
    balance_usdt = Column(Float, default=0.0)  # баланс в USDT (основна валюта)
    ton_wallet_address = Column(String(64), nullable=True)  # TON wallet address (bounceable format)

    entries = relationship("Entry", back_populates="user", lazy="selectin")
    achievements = relationship("UserAchievement", back_populates="user", lazy="selectin")


class UserAchievement(Base):
    """Досягнення гравця: first_bet, first_loss, cashed_out_100, cashed_out_500, survived_5_rounds, etc."""
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.tg_id"), nullable=False)
    achievement_key = Column(String(64), nullable=False)  # first_bet, first_loss, cashed_out_100, ...
    unlocked_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="achievements")


class TonTransaction(Base):
    """Перевірені TON транзакції (щоб не підтверджувати двічі)."""
    __tablename__ = "ton_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_hash = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.tg_id"), nullable=False)
    amount = Column(Float, nullable=False)
    comment = Column(String(128), nullable=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow)


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    rounds_total = Column(Integer, default=10)
    current_round = Column(Integer, default=1)
    status = Column(String(32), default="active")  # e.g. active, finished
    # Для Бундесліги: тур BL1, з якого починається гра (game round 1 = start_matchday)
    start_matchday = Column(Integer, nullable=True)

    entries = relationship("Entry", back_populates="game", lazy="selectin")
    matches = relationship("Match", back_populates="game", lazy="selectin")


class Entry(Base):
    __tablename__ = "entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.tg_id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    status = Column(String(32), default="active")  # active | out | cashed_out
    stake = Column(Integer, nullable=True)  # legacy
    stake_amount = Column(Float, nullable=True)  # legacy; для кількох білетів використовуй Ticket.stake_amount

    user = relationship("User", back_populates="entries")
    game = relationship("Game", back_populates="entries")
    tickets = relationship("Ticket", back_populates="entry", lazy="selectin", order_by="Ticket.ticket_index")
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


class Ticket(Base):
    """Один білет (ставка) в межах запису. У кожному турі для білета обирають 2 команди; використані команди не повторюються в наступних турах для цього білета."""
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(Integer, ForeignKey("entries.id"), nullable=False)
    ticket_index = Column(Integer, nullable=False)  # 1, 2, 3, ...
    stake_amount = Column(Float, nullable=False)  # поточна сума (×1.5 після кожного пройденого туру)
    status = Column(String(32), default="active")  # active | out

    entry = relationship("Entry", back_populates="tickets")


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
    utc_date = Column(DateTime, nullable=True)   # kickoff UTC (deadline для ставок = мін по туру)
    external_id = Column(String(32), nullable=True)  # football-data.org match id
    status = Column(String(32), nullable=True)   # SCHEDULED, FINISHED, etc.

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
    ticket_index = Column(Integer, nullable=False, default=1)  # до якого білета належить вибір
    round = Column(Integer, nullable=False)
    team1_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    team2_id = Column(Integer, ForeignKey("teams.id"), nullable=False)

    entry = relationship("Entry", back_populates="selections")
    team1 = relationship("Team", foreign_keys=[team1_id])
    team2 = relationship("Team", foreign_keys=[team2_id])
