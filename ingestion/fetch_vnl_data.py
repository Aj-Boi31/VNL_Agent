"""
Pulls FIVB Volleyball Men's Nations League (VNL) match data from the
Highlightly Volleyball API into a local SQLite database (vnl.db).

Requires a free Highlightly API key (100 requests/day, no card needed):
    https://highlightly.net/login  -> Dashboard -> API key

Set it as an environment variable before running:
    export HIGHLIGHTLY_API_KEY="your-key-here"        (Mac/Linux)
    setx HIGHLIGHTLY_API_KEY "your-key-here"           (Windows, new shells)
    $env:HIGHLIGHTLY_API_KEY="your-key-here"           (Windows PowerShell, current shell)

Run:
    python ingestion/fetch_vnl_data.py

Creates two tables (schema matches tracker/models.py exactly):
    teams(id, name, country, badge_url)
    matches(id, season, round, date, home_team_id, away_team_id,
            home_score, away_score, status)

Note: home_score/away_score store SETS WON (volleyball is best-of-5), not
points. state.score.current from the API looks like "3 - 1".
"""

import os
import sqlite3
import time
import requests
from dotenv import load_dotenv

load_dotenv()  # picks up .env in the current working directory

SEASONS = [2026]  # Scoped to 2026 only -- confirmed complete (116/116 matches).
                  # 2024 (80/104) and 2025 (89/116) are missing matches on the
                  # Highlightly free tier and were dropped from scope.
DB_PATH = "vnl.db"
BASE_URL = "https://volleyball.highlightly.net"
API_KEY = os.environ.get("HIGHLIGHTLY_API_KEY")

HEADERS = {
    "x-rapidapi-key": API_KEY,
    # x-rapidapi-host is only required when calling through the RapidAPI
    # gateway (volleyball-highlights-api.p.rapidapi.com). Calling
    # volleyball.highlightly.net directly, it's not required.
}

PAGE_LIMIT = 100  # API max per page for /matches

# --- Roster data (separate provider: SportsAPI Pro) ---
# Highlightly doesn't expose player-level data at all. SportsAPI Pro does
# have real, verified team rosters (confirmed manually against Poland's
# actual 2026 squad) -- but it uses a completely different team ID system
# than Highlightly, so each team's SportsAPI Pro ID has to be resolved by
# name search once and cached in teams.sportsapipro_id.
#
# Individual PLAYER STATISTICS (kills, blocks, aces, etc.) were checked and
# confirmed NOT available through this or any other provider we tested
# (SportsAPI Pro, Sportradar, TheSportsDB, or FIVB's own site) -- see
# README "Data scope & known limitations". This only pulls roster/profile
# data: name, position, height, weight, age, nationality.
SPORTSAPIPRO_API_KEY = os.environ.get("SPORTSAPIPRO_API_KEY")
SPORTSAPIPRO_BASE_URL = "https://api.sportsapipro.com/v2/volleyball"
SPORTSAPIPRO_HEADERS = {"x-api-key": SPORTSAPIPRO_API_KEY}

# SportsAPI Pro's own search doesn't fuzzy-match these -- a query for
# Highlightly's name returns zero results, not just a low-ranked one, so the
# relevance-ranking fallback in resolve_sportsapipro_team_id never gets a
# candidate to work with. Retry with the known local/official name instead.
SPORTSAPIPRO_NAME_ALIASES = {
    "turkey": "Türkiye",
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
            badge_url TEXT,
            sportsapipro_id INTEGER
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

        CREATE INDEX IF NOT EXISTS idx_matches_season ON matches(season);
        CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id);

        CREATE TABLE IF NOT EXISTS standings (
            season TEXT NOT NULL,
            team_id INTEGER NOT NULL,
            position INTEGER,
            wins INTEGER,
            loses INTEGER,
            points INTEGER,
            games_played INTEGER,
            scored_points INTEGER,
            received_points INTEGER,
            PRIMARY KEY (season, team_id),
            FOREIGN KEY (team_id) REFERENCES teams(id)
        );

        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,
            team_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            position TEXT,
            height_cm INTEGER,
            weight_kg INTEGER,
            jersey_number TEXT,
            date_of_birth TEXT,
            country TEXT,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        );
        """
    )
    # Migration: add sportsapipro_id to teams if this vnl.db predates it
    # (CREATE TABLE IF NOT EXISTS above won't alter an existing table).
    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(teams)")]
    if "sportsapipro_id" not in existing_cols:
        conn.execute("ALTER TABLE teams ADD COLUMN sportsapipro_id INTEGER")
    conn.commit()


def api_get(path, params=None):
    if not API_KEY:
        raise RuntimeError(
            "HIGHLIGHTLY_API_KEY environment variable is not set. "
            "Get a free key at https://highlightly.net/login and set it before running this script."
        )
    resp = requests.get(f"{BASE_URL}{path}", params=params or {}, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def find_vnl_league_id():
    """Look up Highlightly's internal league id for the Men's VNL.
    Their id system is unrelated to TheSportsDB's id (5083) -- we have to
    search by name."""
    data = api_get("/leagues", {"leagueName": "Nations League", "limit": 100})
    leagues = data.get("data", [])

    # Filter to men's league, excluding "Women" in the name.
    candidates = [
        l for l in leagues
        if "nations league" in l.get("name", "").lower()
        and "women" not in l.get("name", "").lower()
    ]

    if not candidates:
        print("Could not find 'Nations League' in Highlightly's /leagues results.")
        print("Raw leagues returned:", [l.get("name") for l in leagues])
        return None

    league = candidates[0]
    print(f"Found league: {league['name']} (id={league['id']})")
    print(f"Available seasons: {[s.get('season') for s in league.get('seasons', [])]}")
    return league["id"]


def get_or_create_team(conn, team):
    """team is the nested {id, logo, name} object from the API response."""
    if not team or not team.get("id"):
        return None

    team_id = team["id"]
    name = team.get("name", "Unknown")
    badge_url = team.get("logo")

    conn.execute(
        """
        INSERT INTO teams (id, name, country, badge_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            badge_url=excluded.badge_url
        """,
        (team_id, name, None, badge_url),
    )
    return team_id


def parse_set_score(score_block, side):
    """score_block is state.score, e.g. {"current": "3 - 1", ...}.
    'current' holds sets won as 'home - away'. Returns an int or None."""
    if not score_block:
        return None
    current = score_block.get("current")
    if not current or "-" not in current:
        return None
    try:
        home_str, away_str = [p.strip() for p in current.split("-", 1)]
        return int(home_str) if side == "home" else int(away_str)
    except (ValueError, IndexError):
        return None


def ingest_season(conn, league_id, season):
    offset = 0
    total_upserted = 0

    while True:
        data = api_get("/matches", {
            "leagueId": league_id,
            "season": season,
            "limit": PAGE_LIMIT,
            "offset": offset,
        })
        matches = data.get("data", [])
        pagination = data.get("pagination", {})
        total_count = pagination.get("totalCount", len(matches))

        if not matches:
            break

        for m in matches:
            home_team = m.get("homeTeam")
            away_team = m.get("awayTeam")
            home_id = get_or_create_team(conn, home_team)
            away_id = get_or_create_team(conn, away_team)

            state = m.get("state", {})
            score = state.get("score", {})
            home_score = parse_set_score(score, "home")
            away_score = parse_set_score(score, "away")

            conn.execute(
                """
                INSERT INTO matches (id, season, round, date, home_team_id,
                                      away_team_id, home_score, away_score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    season=excluded.season,
                    round=excluded.round,
                    date=excluded.date,
                    home_team_id=excluded.home_team_id,
                    away_team_id=excluded.away_team_id,
                    home_score=excluded.home_score,
                    away_score=excluded.away_score,
                    status=excluded.status
                """,
                (
                    m.get("id"), str(season), m.get("week"), m.get("date"),
                    home_id, away_id, home_score, away_score,
                    state.get("description"),
                ),
            )
            total_upserted += 1

        conn.commit()
        offset += PAGE_LIMIT
        if offset >= total_count:
            break
        time.sleep(0.5)  # be polite / stay well under rate limits

    print(f"Season {season}: {total_upserted} matches upserted")
    return total_upserted


def ingest_standings(conn, league_id, season):
    """Pull the official standings table from Highlightly rather than
    recomputing win/loss points ourselves -- VNL's points system isn't a
    flat 1 point per win (3-0/3-1 win = 3pts, 3-2 win = 2pts winner/1pt
    loser), so using the API's own precomputed table avoids getting that
    logic wrong."""
    data = api_get("/standings", {"leagueId": league_id, "season": season})
    groups = data.get("groups", [])

    conn.execute("DELETE FROM standings WHERE season = ?", (str(season),))

    total = 0
    for group in groups:
        for entry in group.get("standings", []):
            team = entry.get("team", {})
            team_id = get_or_create_team(conn, team)
            if not team_id:
                continue

            conn.execute(
                """
                INSERT INTO standings (season, team_id, position, wins, loses,
                                        points, games_played, scored_points, received_points)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(season, team_id) DO UPDATE SET
                    position=excluded.position,
                    wins=excluded.wins,
                    loses=excluded.loses,
                    points=excluded.points,
                    games_played=excluded.games_played,
                    scored_points=excluded.scored_points,
                    received_points=excluded.received_points
                """,
                (
                    str(season), team_id, entry.get("position"), entry.get("wins"),
                    entry.get("loses"), entry.get("points"), entry.get("gamesPlayed"),
                    entry.get("scoredPoints"), entry.get("receivedPoints"),
                ),
            )
            total += 1

    conn.commit()
    print(f"Season {season}: {total} standings rows upserted")


def sportsapipro_get(path, params=None):
    if not SPORTSAPIPRO_API_KEY:
        raise RuntimeError(
            "SPORTSAPIPRO_API_KEY environment variable is not set. "
            "Get a free key at https://sportsapipro.com and set it before running this script."
        )
    resp = requests.get(
        f"{SPORTSAPIPRO_BASE_URL}{path}", params=params or {},
        headers=SPORTSAPIPRO_HEADERS, timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_sportsapipro_team_id(conn, team_name):
    """Search SportsAPI Pro for this team's ID (a completely different ID
    system than Highlightly's). Filters to volleyball, men's, national
    teams to avoid matching a same-named club or another sport's team.

    Name matching is intentionally loose: SportsAPI Pro (SofaScore-based)
    uses official/local country names that don't always match Highlightly's
    (e.g. "Türkiye" vs "Turkey"), so an exact string match is too strict
    and silently skips real teams. The sport/gender/national filters do
    the actual safety work here -- among volleyball+men's+national-team
    results, the search API's own relevance ranking (already sorted by
    `score`) makes the first match overwhelmingly likely to be correct,
    since the query IS the team name.
    """
    def search(query):
        data = sportsapipro_get("/api/search", params={"q": query})
        results = data.get("data", {}).get("results", [])
        return [
            r for r in results
            if r.get("type") == "team"
            and r.get("entity", {}).get("sport", {}).get("slug") == "volleyball"
            and r.get("entity", {}).get("gender") == "M"
            and r.get("entity", {}).get("national") is True
        ]

    candidates = search(team_name)

    if not candidates:
        alias = SPORTSAPIPRO_NAME_ALIASES.get(team_name.lower())
        if alias:
            candidates = search(alias)

    if not candidates:
        return None

    # results are pre-sorted by relevance score; take the top match
    best = candidates[0]
    entity = best.get("entity", {})
    matched_name = entity.get("name")
    if matched_name and matched_name.lower() != team_name.lower():
        print(f"    (matched '{team_name}' -> SportsAPI Pro's '{matched_name}')")

    return entity.get("id")


def ingest_roster(conn, our_team_id, sportsapipro_team_id, team_name):
    """Pulls the current national-team roster for one team.

    Uses `nationalPlayers`, not the top-level `players` array -- the
    latter mixes in domestic club players who share the same nationality
    but aren't actually on the current VNL squad (confirmed by manually
    inspecting Poland's response: `players` included PlusLiga club-only
    players, while `nationalPlayers` correctly filtered to the roster
    tagged national=true under the Nations League team).

    Does NOT include individual performance statistics (kills, blocks,
    aces, etc.) -- confirmed unavailable through this or any other
    provider checked. See README "Data scope & known limitations".
    """
    data = sportsapipro_get(f"/api/teams/{sportsapipro_team_id}/players")
    national_players = data.get("data", {}).get("nationalPlayers", [])

    conn.execute("DELETE FROM players WHERE team_id = ?", (our_team_id,))

    for entry in national_players:
        p = entry.get("player", {})
        conn.execute(
            """
            INSERT INTO players (id, team_id, name, position, height_cm,
                                  weight_kg, jersey_number, date_of_birth, country)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                team_id=excluded.team_id,
                name=excluded.name,
                position=excluded.position,
                height_cm=excluded.height_cm,
                weight_kg=excluded.weight_kg,
                jersey_number=excluded.jersey_number,
                date_of_birth=excluded.date_of_birth,
                country=excluded.country
            """,
            (
                p.get("id"), our_team_id, p.get("name"), p.get("position"),
                p.get("height"), p.get("weight"), p.get("jerseyNumber"),
                p.get("dateOfBirth"), (p.get("country") or {}).get("name"),
            ),
        )

    conn.commit()
    print(f"  {team_name}: {len(national_players)} roster players upserted")


def ingest_all_rosters(conn):
    """For every team already in our DB (from Highlightly), resolve its
    SportsAPI Pro ID (once, cached) and pull its current roster.

    This costs up to 2 API calls per team (1 search + 1 roster) on a
    SEPARATE free tier from Highlightly's -- run this deliberately, not
    as part of routine re-ingestion, since it's a real chunk of quota."""
    if not SPORTSAPIPRO_API_KEY:
        print("SPORTSAPIPRO_API_KEY not set -- skipping roster ingestion.")
        return

    teams = conn.execute("SELECT id, name, sportsapipro_id FROM teams").fetchall()
    print(f"\nIngesting rosters for {len(teams)} teams (SportsAPI Pro)...")

    for our_id, name, sportsapipro_id in teams:
        try:
            if sportsapipro_id is None:
                sportsapipro_id = resolve_sportsapipro_team_id(conn, name)
                if sportsapipro_id is None:
                    print(f"  {name}: could not resolve SportsAPI Pro team ID, skipping")
                    continue
                conn.execute(
                    "UPDATE teams SET sportsapipro_id = ? WHERE id = ?",
                    (sportsapipro_id, our_id),
                )
                conn.commit()
                time.sleep(0.5)

            ingest_roster(conn, our_id, sportsapipro_id, name)
            time.sleep(0.5)
        except requests.RequestException as e:
            print(f"  {name}: failed to fetch roster: {e}")


def main():
    conn = get_connection()
    create_tables(conn)

    league_id = find_vnl_league_id()
    if not league_id:
        print("Aborting: could not resolve VNL league id.")
        conn.close()
        return

    for season in SEASONS:
        try:
            ingest_season(conn, league_id, season)
            ingest_standings(conn, league_id, season)
        except requests.RequestException as e:
            print(f"Failed to fetch season {season}: {e}")
        time.sleep(0.5)

    ingest_all_rosters(conn)

    team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    player_count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    print(f"\nDone. teams={team_count} matches={match_count} players={player_count} -> {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
