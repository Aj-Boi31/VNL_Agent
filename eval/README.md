# Agent evaluation

A script that asks the agent 49 questions and checks the answers against the database.

| Run | Result | Notes |
|---|---|---|
| 1 | **48 / 49** (98%) | One miss: a player lookup with no team name |
| 2 | **49 / 49** (100%) | Same 49 questions, after I added a `find_player` tool to fix that miss |

The second run is **not an independent test**. I fixed the failure the first run showed and then re-ran the same questions, so a pass was expected. The honest read is "found one gap, fixed it". A fresh set of questions would be the proper next test.

Per-question output is in [`results.csv`](results.csv) (run 2) and [`results_run1_before_find_player.csv`](results_run1_before_find_player.csv) (run 1). The summary is in [`RESULTS.md`](RESULTS.md).

## How it works

- `run_eval.py` builds the questions from `vnl.db` with a fixed random seed, so you get the same 49 every time.
- The expected answers come from plain SQL, not from the agent's own tool functions. The agent is checked against the data, not against itself.
- Each question goes to the real agent (`agent.gemini_agent.ask`, live Gemini calls, normal model fallback).
- Grading is string / regex matching. For head-to-head I ask for the winner's name first and check it comes before the loser's.

```bash
python eval/run_eval.py            # asks all 49 questions, needs GEMINI_API_KEY
python eval/run_eval.py --rescore  # re-grades the saved answers without calling the API
```

| Category | Questions |
|---|---|
| Standings | 7 |
| Preliminary-round wins | 6 |
| Head-to-head winner | 8 |
| Recent form (last 5) | 6 |
| Player position | 8 |
| Player height | 6 |
| Stats the data doesn't have (should decline) | 5 |
| Unknown team (should say so) | 3 |

## The miss in run 1

"How tall is Cory Schoenherr in cm?" The agent only had `get_team_roster`, which needs a team name. With no team given it guessed USA, didn't find him, and said the data wasn't available. He's on Canada. I added `find_player(name)` to search by name, and run 2 answered it correctly (203 cm).

## Limits

- 49 questions, one run each, all single-fact lookups. It doesn't test multi-step questions, follow-ups or oddly phrased ones.
- I wrote the questions, the scorer and the agent, so it isn't independent.
- String matching can reject a right answer that's worded differently, and can accept a vague one that happens to contain the right word.
- Run 1 also had a scorer bug: it rejected a number followed by a full stop ("they won 3."). That was my bug, not the agent's. I fixed it and re-graded the saved answers with `--rescore` instead of asking again.
- LLM answers vary between runs, and results depend on which free-tier Gemini models were available that day.
