"""
Pulls FIVB Volleyball Men's Nations League (VNL) match data from
TheSportsDB's free API into a local SQLite database (vnl.db).

League ID 5083 = FIVB Volleyball Nations League (men's).
Free tier endpoint, no auth key required for eventsseason.php.

Run:
    python ingestion/fetch_vnl_data.py

Creates two tables:
    teams(id, name, country, badge_url)
    matches(id, season, round, date, home_team_id, away_team_id,
            home_score, away_score, status)
"""

import sqlite3
import time
import requests

LEAGUE_ID = "5083"
SEASONS = ["2024", "2025", "2026"]
DB_PATH = "vnl.db"
BASE_URL = "https://www.thesportsdb.com/api/v1/json/123"  # "123" = current free/test key ("3" is retired)

HEADERS = {
    # TheSportsDB (behind Cloudflare) 403s requests with no browser-like
    # User-Agent -- the default requests UA gets blocked.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            country TEXT,
            badge_url TEXT
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            season TEXT NOT NULL,
            round TEXT,
            date TEXT,
            home_team_id INTEGER,
            away_team_id INTEGER,
            home_score INTEGER,
            away_score INTEGER,
            status TEXT,
            FOREIGN KEY (home_team_id) REFERENCES teams(id),
            FOREIGN KEY (away_team_id) REFERENCES teams(id)
        );
        """
    )
    conn.commit()


def get_or_create_team(conn, name):
    if not name:
        return None
    cur = conn.execute("SELECT id FROM teams WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO teams (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def fetch_season(season):
    url = f"{BASE_URL}/eventsseason.php"
    params = {"id": LEAGUE_ID, "s": season}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json().get("events") or []


def parse_score(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def ingest_season(conn, season):
    events = fetch_season(season)
    print(f"Season {season}: {len(events)} events fetched")

    inserted = 0
    for ev in events:
        home_name = ev.get("strHomeTeam")
        away_name = ev.get("strAwayTeam")
        if not home_name or not away_name:
            continue

        home_id = get_or_create_team(conn, home_name)
        away_id = get_or_create_team(conn, away_name)

        conn.execute(
            """
            INSERT INTO matches (id, season, round, date, home_team_id,
                                  away_team_id, home_score, away_score, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                ev.get("idEvent"), season, ev.get("intRound"), ev.get("dateEvent"),
                home_id, away_id, parse_score(ev.get("intHomeScore")),
                parse_score(ev.get("intAwayScore")), ev.get("strStatus"),
            ),
        )
        inserted += 1

    conn.commit()
    print(f"Season {season}: {inserted} matches upserted")


def main():
    conn = get_connection()
    create_tables(conn)
    for season in SEASONS:
        ingest_season(conn, season)
        time.sleep(1)
    conn.close()


if __name__ == "__main__":
    main()
