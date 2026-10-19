"""
Sanity-checks vnl.db against the 2026 men's VNL format and against results
published by the official sources listed below.

Run from the project root:
    python ingestion/verify_data.py

Format (18 teams, 12 preliminary matches each, then 8 knockout matches):
18 x 12 / 2 = 108 preliminary + 4 quarter-finals + 2 semi-finals + bronze + final = 116.

Results confirmed on official pages (checked 2026-10-19):
- Final: Poland 3-2 USA, 2 Aug 2026 (Volleyball World match report)
  https://en.volleyballworld.com/volleyball/competitions/volleyball-nations-league/news/poland-defend-vnl-title-outlasting-usa-in-five-set-ningbo-final
- Quarter-final: USA 3-0 Italy, 30 Jul 2026 (USA Volleyball, same page as the semi-final)
- Semi-final: USA 3-2 Japan, 1 Aug 2026 (USA Volleyball)
  https://usavolleyball.org/story/u-s-men-to-play-for-2026-vnl-gold-after-dramatic-win-over-previously-unbeaten-japan/
- Bronze: Slovenia won (Volleyball World match report, same link as the final)

The other knockout results in the database were not confirmed on an official
page, only checked for consistency with the above.
"""

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "vnl.db"

CONFIRMED = [
    # (round, home, away, home_sets, away_sets)
    ("Final", "USA", "Poland", 2, 3),
    ("Quarter-finals", "Italy", "USA", 0, 3),
    ("Semi-finals", "Japan", "USA", 2, 3),
]


def main():
    c = sqlite3.connect(DB)
    failures = []

    def check(label, ok, detail=""):
        print(("PASS  " if ok else "FAIL  ") + label + (f"  ({detail})" if detail else ""))
        if not ok:
            failures.append(label)

    total = c.execute("SELECT COUNT(*) FROM matches WHERE season = '2026'").fetchone()[0]
    teams = c.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    prelim = c.execute("SELECT COUNT(*) FROM matches WHERE season = '2026' AND round IS NULL").fetchone()[0]
    knockout = dict(c.execute(
        "SELECT round, COUNT(*) FROM matches WHERE season = '2026' AND round IS NOT NULL GROUP BY round"
    ).fetchall())

    check("18 teams", teams == 18, f"found {teams}")
    check("116 matches in total", total == 116, f"found {total}")
    check("108 preliminary matches (18 x 12 / 2)", prelim == 108, f"found {prelim}")
    check("knockout: 4 quarter-finals, 2 semi-finals, 1 bronze, 1 final",
          knockout == {"Quarter-finals": 4, "Semi-finals": 2, "3rd place": 1, "Final": 1}, str(knockout))

    per_team = c.execute(
        """SELECT MIN(n), MAX(n) FROM (SELECT team, COUNT(*) n FROM (
             SELECT home_team_id team FROM matches WHERE round IS NULL
             UNION ALL SELECT away_team_id FROM matches WHERE round IS NULL) GROUP BY team)"""
    ).fetchone()
    check("every team has exactly 12 preliminary matches", per_team == (12, 12), f"min/max {per_team}")

    for rnd, home, away, hs, as_ in CONFIRMED:
        row = c.execute(
            """SELECT 1 FROM matches m JOIN teams th ON th.id = m.home_team_id
               JOIN teams ta ON ta.id = m.away_team_id
               WHERE m.round = ? AND th.name = ? AND ta.name = ? AND m.home_score = ? AND m.away_score = ?""",
            (rnd, home, away, hs, as_),
        ).fetchone()
        check(f"{rnd}: {home} {hs}-{as_} {away} matches the official result", row is not None)

    bronze = c.execute(
        """SELECT th.name, ta.name, m.home_score, m.away_score FROM matches m
           JOIN teams th ON th.id = m.home_team_id JOIN teams ta ON ta.id = m.away_team_id
           WHERE m.round = '3rd place'"""
    ).fetchone()
    winner = bronze[0] if bronze and bronze[2] > bronze[3] else (bronze[1] if bronze else None)
    check("Bronze match won by Slovenia", winner == "Slovenia", f"database says {winner}")

    print()
    print("All checks passed." if not failures else f"{len(failures)} check(s) failed.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
