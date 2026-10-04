# Agent evaluation

An automated check of how often the agent answers correctly, graded against the database itself.

**Result (2026-10-04): 48 / 49 correct (98%).** Full per-question output is in [`results.csv`](results.csv) and the summary is in [`RESULTS.md`](RESULTS.md).

| Category | Correct | Total |
|---|---|---|
| Standings (who finished where) | 7 | 7 |
| Preliminary-round wins | 6 | 6 |
| Head-to-head winner | 8 | 8 |
| Recent form (last 5 matches) | 6 | 6 |
| Roster: player position | 8 | 8 |
| Roster: player height | 5 | 6 |
| Unavailable stats (should decline) | 5 | 5 |
| Unknown team (should say so) | 3 | 3 |

## How it works

- `run_eval.py` generates the questions from `vnl.db` with a fixed random seed, so the same 49 questions are produced every run.
- Each expected answer is computed with plain SQL, independently of the agent's own tool functions, so the agent is checked against the data and not against itself.
- Each question is sent to the real agent (`agent.gemini_agent.ask`, live Gemini calls with the normal model fallback chain).
- Grading is deterministic string/regex matching. Head-to-head questions ask for the winner's name first, and the grader checks the winner appears before the loser.

```bash
python eval/run_eval.py            # asks all questions, needs GEMINI_API_KEY
python eval/run_eval.py --rescore  # re-grades the saved answers without calling the API
```

## The one failure

"How tall is Cory Schoenherr in cm?" The agent looked in the USA roster, did not find him, and said the data was unavailable. He is in the database, on a different team. `get_team_roster` only searches by team, so the agent has no way to find a player without knowing the team. This is a real gap in the tool set, not a grading artefact. A `find_player(name)` tool would fix it.

## Limitations

- One run, 49 questions, all single-fact lookups. It does not test multi-step reasoning, follow-up questions or ambiguous phrasing.
- Exact-match grading can reject a correct answer worded unexpectedly and can accept a vague one that happens to contain the expected token.
- The questions were written by the same person who built the agent, not by independent users.
- The first run flagged one answer as wrong because the scorer rejected a number followed by a full stop ("they won 3."). That was a bug in the scorer, not the agent. It was fixed and the saved answers were re-graded with `--rescore`, not re-asked.
- Results depend on the Gemini models available on the free tier on the run date.
