# VNL Volleyball Data Agent

Ask questions about the 2026 Volleyball Men's Nations League in plain English and get answers from real match data. A Gemini agent picks the right database query for each question, so it doesn't guess scores or standings.

![Ask the agent a question and get a grounded answer from real match data](docs/dashboard-demo.png)

## What you'll see

A dashboard with the standings table and an "Ask the agent" box. Try:

- "What's Poland's record this season?"
- "Who's leading the standings?"
- "How has Japan played in their last 5 games?"
- "Who won when Brazil played France?"
- "How tall is Cory Schoenherr?"

It declines what it can't answer, like a team that isn't in the tournament or per-player stats (kills, aces), instead of making something up.

## At a glance

| | |
|---|---|
| Stack | Python, Django, SQLite, Gemini (function calling) |
| Data | 2026 men's VNL: 116 matches, 18 teams, 411 players |
| Agent | 6 query tools, with a fallback across 4 free Gemini models |
| Checked by | 6 Django tests + a 49-question evaluation ([results](eval/README.md)) |
| Hosted | No, runs locally |

## Run it

You need three free API keys (no credit card): [Highlightly](https://highlightly.net/login) for match data, [Gemini](https://aistudio.google.com/apikey) for the agent, and [SportsAPI Pro](https://sportsapipro.com) for rosters. The database isn't in the repo, so the first run pulls the data.

```bash
pip install -r requirements.txt

cp .env.example .env
# open .env and fill in HIGHLIGHTLY_API_KEY, GEMINI_API_KEY, SPORTSAPIPRO_API_KEY

python ingestion/fetch_vnl_data.py   # builds vnl.db (2026 season)
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/.

Want to skip the browser? `python agent/gemini_agent.py` starts a chat in the terminal with the same agent.

The SportsAPI Pro free tier is capped at 100 requests a day and ingestion uses up to 36 per run, so a couple of re-runs can hit `429` errors.

## How it works

```
question -> Gemini picks a tool -> tool queries vnl.db -> Gemini answers from the result
```

```
agent/gemini_agent.py     the agent: tools, system prompt, model fallback
agent/queries.py          6 plain-Python query functions (no Django needed)
tracker/                  Django app: dashboard + the ask endpoint
ingestion/                pulls data from the APIs into vnl.db
eval/                     the 49-question evaluation
```

The 6 tools: `get_standings`, `get_team_results`, `get_head_to_head`, `get_recent_form`, `get_team_roster`, `find_player`. Gemini reads each function's type hints and docstring to decide which one to call.

## Does it work?

```bash
python manage.py test          # 6 tests on the ask endpoint
python eval/run_eval.py        # 49 questions graded against the database
```

- **Tests** cover CSRF enforcement, input validation and error handling. Gemini is mocked, so they don't use API quota.
- **Evaluation:** 48/49 on the first run. The miss was a player lookup with no team name, so I added `find_player` and re-ran to 49/49. That second run used the same questions, so it isn't an independent test. The method and limits are in [`eval/README.md`](eval/README.md).

## Problems I ran into

- **The first data source cut results off without saying.** TheSportsDB's free tier returned about 15 matches for a season that has 116. I only caught it by checking against Wikipedia.
- **The second source was incomplete for 2024 and 2025** (80/104 and 89/116 matches). I scoped the project to 2026, where all 116 matches are there, instead of shipping partial data.
- **Gemini free-tier quota is per model.** The agent tries a chain of models in order and only moves on when it gets a quota error.
- **The setup shifted under me.** A model I'd planned on was retired, the recommended calling pattern changed, and an SDK bug closed the HTTP client early. I fixed each by reading the current docs and GitHub issues.
- **Two providers, no shared IDs.** Matching teams by name broke for Turkey, which one API lists as "Türkiye". A small alias table fixes it.
- **No player stats exist for free.** I checked six sources (TheSportsDB, Highlightly, SportsAPI Pro, Sportradar's data dictionary, FIVB's stats page and SportDevs) and none had kills, blocks or aces. Rosters (name, position, height, weight, age, country) were available, so those are in.

## Limits

- 2026 season only.
- `get_team_results` includes the finals, while `get_standings` is the 12-game preliminary round only. A team's record can differ between them, and the agent is told to explain that.
- Round labels aren't filled in by the data source.
- No player performance stats.

## Next

- [ ] Deploy it
- [ ] A demo video at the top of this README
- [ ] A fresh set of evaluation questions I haven't tuned to
