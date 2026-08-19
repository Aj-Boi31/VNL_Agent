# VNL Volleyball Data Agent

A Django app for asking natural-language questions about FIVB Volleyball
Men's Nations League (VNL) data, answered by a Gemini function-calling
agent backed by real match data in SQLite.

## Setup (run these on your own machine, in this project folder)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up your API keys
cp .env.example .env
# then open .env and fill in HIGHLIGHTLY_API_KEY and GEMINI_API_KEY

# 3. Pull VNL data into vnl.db (creates the database, 2026 season only)
python ingestion/fetch_vnl_data.py

# 4. Run Django's own migrations (auth, sessions -- separate from teams/matches)
python manage.py migrate

# 5. Start the dev server
python manage.py runserver
```

Then visit http://127.0.0.1:8000/

### Getting API keys (both free, no credit card)
- **Highlightly** (match data): https://highlightly.net/login
- **Gemini** (the agent): https://aistudio.google.com/apikey

Keys are loaded automatically from `.env` via `python-dotenv` -- no need to
`export` them manually each session.

## Project structure

```
vnl_agent/          Django project settings
tracker/            Django app: models mapped to teams/matches tables
ingestion/           fetch_vnl_data.py -- pulls VNL data from TheSportsDB
vnl.db               SQLite database (created by ingestion script, gitignored)
```

## Status

- [x] Project scaffolded
- [x] Ingestion script (Highlightly API, 2026 season, verified complete: 116/116 matches)
- [x] Query functions (agent/queries.py) -- tested against real data
- [x] Gemini function-calling agent (agent/gemini_agent.py) -- tested, multi-turn working
- [x] Django views + templates (dashboard + ask endpoint)
- [x] .env-based API key handling
- [ ] Deployment
