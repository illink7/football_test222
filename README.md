# Survivor Football — Telegram Mini App

Users join games by purchasing **Entries**. Each round (1–10), they pick **2 teams**. If both teams score > 0, the entry survives. Teams already used for that entry are blocked in future rounds.

## Tech stack

- **Bot:** aiogram 3.x  
- **Web App API:** FastAPI  
- **DB:** SQLite + SQLAlchemy  
- **Frontend:** HTML/JS (Telegram Web App)

## File structure

```
Football/
├── config.py              # BOT_TOKEN, ADMIN_ID, DATABASE_URL, WEBAPP_BASE_URL
├── requirements.txt
├── database/
│   ├── __init__.py        # engine, SessionLocal, get_db, init_db
│   └── models.py          # User, Game, Entry, Team, Selection
├── bot/
│   ├── main.py            # Bot entry, polling
│   └── handlers/
│       ├── admin.py       # /create_game, /add_teams, /add_entry, /result
│       └── user.py        # /start (My Entries + Web App button)
└── webapp/
    ├── main.py            # FastAPI: serve select_teams, API for teams & selection
    └── select_teams.html  # Mini App UI: fetch teams, submit selection, close
```

## Setup

1. **Env (optional)**  
   Create `.env` or set:
   - `BOT_TOKEN` — Telegram Bot Token  
   - `ADMIN_ID` — Your Telegram user ID (for admin commands)  
   - `WEBAPP_BASE_URL` — Base URL of the FastAPI app (e.g. `https://your-domain.com`)  
   - `DATABASE_URL` — Defaults to `sqlite:///data/survivor.db`

2. **Install**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run bot**
   ```bash
   python -m bot.main
   ```

4. **Run Web App (FastAPI)**
   ```bash
   python -m webapp.main
   # or: uvicorn webapp.main:app --host 0.0.0.0 --port 8000
   ```

## Admin commands

- **/create_game \<title>** — Create a new game (10 rounds).
- **/add_teams Team1, Team2, ...** — Add teams to the pool (e.g. `Arsenal, Chelsea, ManCity`).
- **/add_entry \<game_id> [tg_id]** — Add an entry for a user (default: you).
- **/result \<game_id> \<results_string>** — Submit round results and advance game.  
  Example: `/result 1 Arsenal:1, Chelsea:0, ManCity:2`  
  Entries that picked a team with 0 goals get status `out`. Game moves to next round (or `finished`).

## User flow

1. User sends **/start** → sees "My Entries" and, for active entries, a **Pick teams** Web App button.
2. Web App opens `select_teams?entry_id=...` → loads available teams (excluding already used for that entry), shows round.
3. User picks 2 teams and submits → selection is saved, Web App closes.

## Database models

- **User** — `tg_id`, `username`, `is_admin`  
- **Game** — `id`, `title`, `rounds_total`, `current_round`, `status`  
- **Entry** — `id`, `user_id`, `game_id`, `status` (active/out)  
- **Team** — `id`, `name` (pool of selectable teams)  
- **Selection** — `id`, `entry_id`, `round`, `team1_id`, `team2_id`
