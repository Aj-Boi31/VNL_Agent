"""
The four data functions the Gemini agent can call. Each function talks
directly to vnl.db via sqlite3 (no Django ORM dependency, so these can be
unit tested or run from a plain script without spinning up Django).

Data is scoped to the 2026 VNL men's season only -- see ingestion/fetch_vnl_data.py
for why 2024/2025 were excluded (incomplete match data on the free API tier).

Scores in `matches` are SETS WON (volleyball is best-of-5), e.g. home_score=3,
away_score=1 means the home team won 3 sets to 1.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "vnl.db"
SEASON = "2026"  # single-season scope, see module docstring


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _find_team_id(conn, team_name):
    """Case-insensitive partial match so 'poland' or 'Poland' or 'Pol'
    all resolve to the right team. Returns (id, canonical_name) or (None, None)."""
    row = conn.execute(
        "SELECT id, name FROM teams WHERE LOWER(name) LIKE LOWER(?) LIMIT 1",
        (f"%{team_name}%",),
    ).fetchone()
    if row:
        return row["id"], row["name"]
    return None, None


def get_team_results(team: str) -> dict:
    """All 2026 season matches for a given team, with W/L record.

    Args:
        team: team name, e.g. "Poland" or "Japan". Partial names work.

    Returns:
        dict with team name, record, and a list of match results.
    """
    conn = _connect()
    try:
        team_id, canonical_name = _find_team_id(conn, team)
        if not team_id:
            return {"error": f"No team found matching '{team}'."}

        rows = conn.execute(
            """
            SELECT m.date, m.round, m.status,
                   th.name AS home_name, ta.name AS away_name,
                   m.home_score, m.away_score, m.home_team_id
            FROM matches m
            JOIN teams th ON m.home_team_id = th.id
            JOIN teams ta ON m.away_team_id = ta.id
            WHERE m.season = ? AND (m.home_team_id = ? OR m.away_team_id = ?)
            ORDER BY m.date ASC
            """,
            (SEASON, team_id, team_id),
        ).fetchall()

        results = []
        wins, losses = 0, 0
        for r in rows:
            is_home = r["home_team_id"] == team_id
            team_score = r["home_score"] if is_home else r["away_score"]
            opp_score = r["away_score"] if is_home else r["home_score"]
            opponent = r["away_name"] if is_home else r["home_name"]

            outcome = None
            if team_score is not None and opp_score is not None:
                outcome = "W" if team_score > opp_score else "L"
                if outcome == "W":
                    wins += 1
                else:
                    losses += 1

            results.append({
                "date": r["date"],
                "round": r["round"],
                "opponent": opponent,
                "venue": "home" if is_home else "away",
                "sets_won": team_score,
                "sets_lost": opp_score,
                "outcome": outcome,
                "status": r["status"],
            })

        return {
            "team": canonical_name,
            "season": SEASON,
            "record": f"{wins}-{losses}",
            "matches": results,
        }
    finally:
        conn.close()


def get_head_to_head(team1: str, team2: str) -> dict:
    """All 2026 season meetings between two teams.

    Args:
        team1: first team name.
        team2: second team name.

    Returns:
        dict with each match's result and a summary of who leads the series.
    """
    conn = _connect()
    try:
        id1, name1 = _find_team_id(conn, team1)
        id2, name2 = _find_team_id(conn, team2)

        if not id1:
            return {"error": f"No team found matching '{team1}'."}
        if not id2:
            return {"error": f"No team found matching '{team2}'."}

        rows = conn.execute(
            """
            SELECT m.date, m.round, m.status,
                   th.name AS home_name, ta.name AS away_name,
                   m.home_score, m.away_score, m.home_team_id
            FROM matches m
            JOIN teams th ON m.home_team_id = th.id
            JOIN teams ta ON m.away_team_id = ta.id
            WHERE m.season = ?
              AND ((m.home_team_id = ? AND m.away_team_id = ?)
                OR (m.home_team_id = ? AND m.away_team_id = ?))
            ORDER BY m.date ASC
            """,
            (SEASON, id1, id2, id2, id1),
        ).fetchall()

        meetings = []
        wins1, wins2 = 0, 0
        for r in rows:
            is_home_team1 = r["home_team_id"] == id1
            t1_score = r["home_score"] if is_home_team1 else r["away_score"]
            t2_score = r["away_score"] if is_home_team1 else r["home_score"]

            winner = None
            if t1_score is not None and t2_score is not None:
                if t1_score > t2_score:
                    winner = name1
                    wins1 += 1
                else:
                    winner = name2
                    wins2 += 1

            meetings.append({
                "date": r["date"],
                "round": r["round"],
                "home_team": r["home_name"],
                "away_team": r["away_name"],
                "score": f"{r['home_score']}-{r['away_score']}",
                "winner": winner,
            })

        return {
            "team1": name1,
            "team2": name2,
            "season": SEASON,
            "series_record": f"{name1} {wins1} - {wins2} {name2}",
            "meetings": meetings,
        }
    finally:
        conn.close()


def get_recent_form(team: str, last_n: int = 5) -> dict:
    """A team's most recent finished matches, most recent first.

    Args:
        team: team name.
        last_n: how many recent matches to return (default 5).

    Returns:
        dict with a form string (e.g. "W-W-L-W-L") and match details.
    """
    conn = _connect()
    try:
        team_id, canonical_name = _find_team_id(conn, team)
        if not team_id:
            return {"error": f"No team found matching '{team}'."}

        rows = conn.execute(
            """
            SELECT m.date, m.round, m.status,
                   th.name AS home_name, ta.name AS away_name,
                   m.home_score, m.away_score, m.home_team_id
            FROM matches m
            JOIN teams th ON m.home_team_id = th.id
            JOIN teams ta ON m.away_team_id = ta.id
            WHERE m.season = ? AND (m.home_team_id = ? OR m.away_team_id = ?)
              AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
            ORDER BY m.date DESC
            LIMIT ?
            """,
            (SEASON, team_id, team_id, last_n),
        ).fetchall()

        matches = []
        form = []
        for r in rows:
            is_home = r["home_team_id"] == team_id
            team_score = r["home_score"] if is_home else r["away_score"]
            opp_score = r["away_score"] if is_home else r["home_score"]
            opponent = r["away_name"] if is_home else r["home_name"]
            outcome = "W" if team_score > opp_score else "L"
            form.append(outcome)

            matches.append({
                "date": r["date"],
                "opponent": opponent,
                "sets_won": team_score,
                "sets_lost": opp_score,
                "outcome": outcome,
            })

        return {
            "team": canonical_name,
            "season": SEASON,
            "form": "-".join(form),  # most recent first
            "matches": matches,
        }
    finally:
        conn.close()


def get_standings() -> dict:
    """The full 2026 VNL standings table, ordered by position.

    Returns:
        dict with the ranked list of teams and their records.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT s.position, t.name, s.wins, s.loses, s.points,
                   s.games_played, s.scored_points, s.received_points
            FROM standings s
            JOIN teams t ON s.team_id = t.id
            WHERE s.season = ?
            ORDER BY s.position ASC
            """,
            (SEASON,),
        ).fetchall()

        if not rows:
            return {"error": f"No standings data found for season {SEASON}."}

        table = [
            {
                "position": r["position"],
                "team": r["name"],
                "wins": r["wins"],
                "losses": r["loses"],
                "points": r["points"],
                "games_played": r["games_played"],
                "scored_points": r["scored_points"],
                "received_points": r["received_points"],
            }
            for r in rows
        ]

        return {"season": SEASON, "standings": table}
    finally:
        conn.close()


if __name__ == "__main__":
    # Quick manual smoke test -- run directly with `python agent/queries.py`
    import json

    print("=== get_standings ===")
    print(json.dumps(get_standings(), indent=2)[:1000])

    print("\n=== get_team_results('Poland') ===")
    print(json.dumps(get_team_results("Poland"), indent=2)[:1000])

    print("\n=== get_head_to_head('Poland', 'Japan') ===")
    print(json.dumps(get_head_to_head("Poland", "Japan"), indent=2)[:1000])

    print("\n=== get_recent_form('Poland') ===")
    print(json.dumps(get_recent_form("Poland"), indent=2)[:1000])
