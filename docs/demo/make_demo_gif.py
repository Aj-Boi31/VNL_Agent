"""Rebuild docs/demo.gif from real runs of this project.

Browser part: starts the Django dev server, opens the real dashboard in Chrome with
Playwright and asks three live questions (needs GEMINI_API_KEY in .env, and vnl.db
already built; uses a few free-tier Gemini calls).

Terminal part: runs the project's own checks and replays their output:
  - eval run 1: the saved run-1 answers are graded again with
    `python eval/run_eval.py --rescore` (48/49, no API calls), then the miss is shown
  - eval run 2: same, with the saved run-2 answers after `find_player` was added (49/49)
  - `python ingestion/verify_data.py` and `python manage.py test`

Everything runs in a throwaway copy of the project, so the committed eval CSVs and
vnl.db are never touched. Only the caption text and the typing animation are added.

    pip install Pillow playwright
    python docs/demo/make_demo_gif.py [--channel chrome|msedge]
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from termgif import BROWSER_SIZE, Scene, Timeline, with_caption

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "demo.gif"
TITLE = "vnl_agent_site"
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}

QUESTIONS = [
    (
        "What's Poland's record this season?",
        "info",
        "Ask in plain English: Gemini picks a query tool and answers from the match database",
    ),
    (
        "How tall is Cory Schoenherr in cm?",
        "good",
        "The eval's one miss, fixed: find_player(name) finds a player without a team name",
    ),
    (
        "Who has the most kills this season?",
        "info",
        "Declines what the data doesn't have (per-player stats) instead of making something up",
    ),
]


def run_merged(cmd: list[str], cwd: Path) -> list[str]:
    """stdout and stderr interleaved, as they would appear in a terminal."""
    proc = subprocess.run(
        cmd, cwd=cwd, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8"
    )
    return proc.stdout.splitlines()


def shown(cmd: list[str]) -> str:
    return " ".join(["python" if c == sys.executable else c for c in cmd])


def eval_scene(site: Path, saved_csv: str, caption: str, kind: str) -> Scene:
    shutil.copy(site / "eval" / saved_csv, site / "eval" / "results.csv")
    rescore = [sys.executable, "eval/run_eval.py", "--rescore"]
    find = ["findstr", "Schoenherr", r"eval\results.csv"] if os.name == "nt" else ["grep", "Schoenherr", "eval/results.csv"]
    return Scene(
        caption=caption,
        kind=kind,
        command=shown(rescore),
        output=[l for l in run_merged(rescore, site) if l.strip()],
        then=[(shown(find), run_merged(find, site))],
        wrap=True,
        hold_ms=3200,
    )


def terminal_scenes(site: Path) -> list[Scene]:
    run1 = eval_scene(
        site,
        "results_run1_before_find_player.csv",
        "EVAL RUN 1 (saved answers)  48/49: a player lookup with no team name missed",
        "bad",
    )
    run2 = eval_scene(
        site,
        "results_run2.csv",
        "EVAL RUN 2 (saved answers)  49/49 after adding find_player(name)",
        "good",
    )
    verify = [sys.executable, "ingestion/verify_data.py"]
    tests = [sys.executable, "manage.py", "test"]
    checks = Scene(
        caption="Data checked against official results (116 matches); 6 tests with Gemini mocked",
        kind="good",
        command=shown(verify),
        output=run_merged(verify, site),
        then=[(shown(tests), [l for l in run_merged(tests, site) if l.strip()])],
        line_ms=70,
        hold_ms=3500,
    )
    return [run1, run2, checks]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def record_dashboard(site: Path, channel: str, tl: Timeline) -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=site, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(url, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("dev server did not start")

        with sync_playwright() as p:
            browser = p.chromium.launch(channel=channel, headless=True)
            page = browser.new_page(viewport={"width": BROWSER_SIZE[0], "height": BROWSER_SIZE[1]})
            page.goto(url, wait_until="load")
            page.wait_for_timeout(800)

            def shot(caption: str, kind: str, ms: int) -> None:
                tl.add(with_caption(Image.open(io.BytesIO(page.screenshot())), caption, kind), ms)

            shot("2026 VNL standings, straight from the match database (vnl.db)", "info", 2200)

            target = page.evaluate(
                "document.querySelector('.agent-panel').getBoundingClientRect().top + window.scrollY - 120"
            )
            for k in range(1, 6):
                page.evaluate(f"window.scrollTo(0, {target * k / 5})")
                page.wait_for_timeout(60)
                shot("Ask the agent a question", "info", 90)

            for question, kind, caption in QUESTIONS:
                page.click("#question-input")
                for i in range(3, len(question) + 3, 3):
                    page.fill("#question-input", question[:i])
                    shot(caption, kind, 70)
                shot(caption, kind, 400)
                page.click("#ask-button")
                shot(caption, kind, 700)
                page.wait_for_function(
                    "() => !document.getElementById('ask-button').disabled", timeout=120_000
                )
                page.wait_for_timeout(200)
                if page.eval_on_selector("#answer-box", "e => e.classList.contains('error')"):
                    raise RuntimeError("agent returned an error: " + page.inner_text("#answer-box"))
                shot(caption, kind, 3600)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="chrome", help="installed browser for Playwright: chrome or msedge")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        shutil.copytree(ROOT, site, ignore=shutil.ignore_patterns("__pycache__", ".git", "docs"))
        shutil.copy(site / "eval" / "results.csv", site / "eval" / "results_run2.csv")

        tl = Timeline()
        record_dashboard(site, args.channel, tl)
        tl.add_scenes(terminal_scenes(site), TITLE)
        tl.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
