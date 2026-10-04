"""
Automated evaluation of the VNL agent.

Questions are generated from vnl.db with a fixed seed, and each expected
answer is computed independently with plain SQL (not by calling the agent's
own tool functions), so the agent is graded against the database itself.

Scoring is deterministic string/regex matching, so it is strict about
wording in places and cannot judge tone or completeness. See eval/README.md
for the limitations.

Run from the project root (needs GEMINI_API_KEY in .env):
    python eval/run_eval.py
"""

import csv
import datetime
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.gemini_agent import ask, MODEL_CHAIN  # noqa: E402

DB = ROOT / "vnl.db"
OUT = Path(__file__).resolve().parent
SEASON = "2026"
RUN_DATE = "2026-10-04"
SLEEP_BETWEEN = 6  # seconds, stays inside free-tier per-minute limits

POSITION_WORDS = {
    "OH": ["outside hitter", "outside"],
    "MB": ["middle blocker", "middle"],
    "OP": ["opposite"],
    "S": ["setter"],
    "L": ["libero"],
}
NUM_WORDS = {0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
REFUSAL = ["not available", "isn't available", "is not available", "unavailable",
           "don't have", "do not have", "doesn't include", "does not include",
           "no data", "cannot", "can't", "unable", "aren't available", "not tracked"]
NOT_FOUND = ["no team found", "couldn't find", "could not find", "check the spelling",
             "unable to find", "not find", "no team", "isn't a team", "not a team"]


def norm(text):
    return text.lower().replace("–", "-").replace("—", "-")


def has_token(text, token):
    token = token.lower()
    if token.isdigit():
        return re.search(rf"(?<![\d.]){re.escape(token)}(?!\d|\.\d)", text) is not None
    if len(token) <= 3:
        return re.search(rf"\b{re.escape(token)}\b", text) is not None
    return token in text


def build_questions():
    rnd = random.Random(2026)
    c = sqlite3.connect(DB)
    qs = []

    def add(cat, question, must, extra=None):
        qs.append({"category": cat, "question": question, "must": must, "extra": extra})

    standings = c.execute(
        """SELECT s.position, t.name, s.wins, s.loses FROM standings s
           JOIN teams t ON t.id = s.team_id WHERE s.season = ? ORDER BY s.position""",
        (SEASON,),
    ).fetchall()

    for pos in (1, 2, 3, 4, 10):
        name = next(r[1] for r in standings if r[0] == pos)
        add("standings", f"Which team finished in position {pos} of the 2026 preliminary standings?", [[name.lower()]])
    add("standings", "Which team finished bottom of the 2026 preliminary standings?", [[standings[-1][1].lower()]])
    add("standings", "Which team finished top of the 2026 preliminary standings?", [[standings[0][1].lower()]])

    for pos, name, wins, loses in rnd.sample(standings, 6):
        add("wins", f"How many preliminary round wins did {name} have in 2026?", [[str(wins), NUM_WORDS.get(wins, str(wins))]])

    teams = [r[0] for r in c.execute("SELECT name FROM teams ORDER BY name")]
    ids = {name: tid for tid, name in c.execute("SELECT id, name FROM teams")}

    pairs = c.execute(
        """SELECT home_team_id, away_team_id, home_score, away_score FROM matches
           WHERE season = ? AND home_score IS NOT NULL""", (SEASON,)).fetchall()
    meet = {}
    for h, a, hs, as_ in pairs:
        meet.setdefault(frozenset((h, a)), []).append((h, a, hs, as_))
    names = {i: n for n, i in ids.items()}
    singles = [v[0] for k, v in meet.items() if len(v) == 1]
    for h, a, hs, as_ in rnd.sample(singles, 8):
        w, l = (h, a) if hs > as_ else (a, h)
        first, second = (names[h], names[a]) if rnd.random() < 0.5 else (names[a], names[h])
        add("head_to_head",
            f"Who won the 2026 VNL match between {first} and {second}? Give the winning team's name first.",
            [[names[w].lower()]], extra={"winner": names[w].lower(), "loser": names[l].lower()})

    for name in rnd.sample(teams, 6):
        tid = ids[name]
        rows = c.execute(
            """SELECT home_team_id, home_score, away_score FROM matches
               WHERE season = ? AND (home_team_id = ? OR away_team_id = ?)
                 AND home_score IS NOT NULL AND away_score IS NOT NULL
               ORDER BY date DESC LIMIT 5""", (SEASON, tid, tid)).fetchall()
        wins = sum(1 for h, hs, as_ in rows if (hs > as_) == (h == tid))
        add("recent_form", f"How many of {name}'s last 5 finished matches did they win?", [[str(wins), NUM_WORDS[wins]]])

    players = c.execute("SELECT name, position, height_cm FROM players WHERE height_cm IS NOT NULL AND position IS NOT NULL").fetchall()
    counts = {}
    for p in players:
        counts[p[0]] = counts.get(p[0], 0) + 1
    unique = [p for p in players if counts[p[0]] == 1 and p[1] in POSITION_WORDS]
    picked = rnd.sample(unique, 14)
    for name, pos, h in picked[:8]:
        add("roster_position", f"What position does {name} play?", [[pos.lower()] + POSITION_WORDS[pos]])
    for name, pos, h in picked[8:]:
        add("roster_height", f"How tall is {name} in cm?", [[str(h)]])

    for name, pos, h in rnd.sample(unique, 5):
        stat = rnd.choice(["aces", "kills", "blocks", "digs"])
        add("unsupported_stat", f"How many {stat} did {name} have this season?", [REFUSAL])

    for fake in ("Narnia", "Atlantis", "Wakanda"):
        add("unknown_team", f"What is {fake}'s record in the 2026 VNL?", [NOT_FOUND])
    return qs


def passed(q, answer):
    text = norm(answer)
    ok = all(any(has_token(text, t) for t in group) for group in q["must"])
    if ok and q["category"] == "head_to_head":
        w, l = q["extra"]["winner"], q["extra"]["loser"]
        iw = text.find(w)
        il = text.find(l)
        ok = iw != -1 and (il == -1 or iw < il)
    return ok


def main():
    qs = build_questions()
    results = []
    for n, q in enumerate(qs, 1):
        answer, error = "", ""
        for attempt in range(3):
            try:
                answer = ask(q["question"])
                error = ""
                break
            except Exception as e:  # quota exhausted, network, etc.
                error = str(e)[:200]
                time.sleep(65)
        ok = bool(answer) and passed(q, answer)
        results.append({"id": n, "category": q["category"], "question": q["question"],
                        "answer": answer.replace("\n", " | "), "passed": ok, "error": error})
        print(f"[{n}/{len(qs)}] {'PASS' if ok else ('ERROR' if error else 'FAIL')} - {q['question']}", flush=True)
        time.sleep(SLEEP_BETWEEN)

    write_report(results)


def write_report(results):
    with open(OUT / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)

    scored = [r for r in results if not r["error"]]
    cats = {}
    for r in scored:
        cats.setdefault(r["category"], []).append(r["passed"])
    total_pass = sum(r["passed"] for r in scored)
    lines = [
        "# Evaluation results", "",
        f"Run date: {RUN_DATE}  ",
        f"Model chain: {', '.join(MODEL_CHAIN)}  ",
        f"Questions asked: {len(results)} ({len(results) - len(scored)} errored and excluded)  ",
        f"**Overall: {total_pass}/{len(scored)} correct ({100 * total_pass / max(len(scored), 1):.0f}%)**", "",
        "| Category | Correct | Total |", "|---|---|---|",
    ]
    for cat, vals in cats.items():
        lines.append(f"| {cat} | {sum(vals)} | {len(vals)} |")
    fails = [r for r in scored if not r["passed"]]
    lines += ["", "## Failures", ""]
    if not fails:
        lines.append("None.")
    for r in fails:
        lines.append(f"- **{r['question']}**  \n  Agent said: {r['answer'][:300]}")
    (OUT / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nOverall: {total_pass}/{len(scored)}")


def rescore():
    """Re-grade the stored answers in results.csv with the current scorer,
    without calling the API again."""
    by_question = {q["question"]: q for q in build_questions()}
    with open(OUT / "results.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["passed"] = bool(r["answer"]) and passed(by_question[r["question"]], r["answer"])
        r["id"] = int(r["id"])
    write_report(rows)


if __name__ == "__main__":
    rescore() if "--rescore" in sys.argv else main()
