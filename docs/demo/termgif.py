"""Build a README demo GIF from real runs (Pillow only).

Two kinds of frames go on one timeline:

* terminal scenes: callers pass text captured from real command runs, and this
  module types the command line and reveals the output line by line.
* browser frames: screenshots (PIL images) from a real browser session, with the
  same caption bar underneath.

Nothing here runs commands or opens a browser.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1100, 620
PAD = 20
TITLE_H = 36
CAPTION_H = 48
LINE_H = 20
FONT_SIZE = 16
PROMPT = "$ "

BG = (13, 17, 23)
BAR = (22, 27, 34)
FG = (201, 209, 217)
DIM = (110, 118, 129)
WHITE = (240, 246, 252)
GREEN = (86, 211, 100)
RED = (255, 123, 114)
CAPTION_STYLE = {
    "bad": ((61, 20, 24), RED),
    "good": ((18, 48, 28), GREEN),
    "info": ((22, 38, 62), (121, 192, 255)),
}

# Size a browser screenshot must have to sit above the caption bar.
BROWSER_SIZE = (W, H - CAPTION_H)


@dataclass
class Scene:
    caption: str
    kind: str  # "bad" | "good" | "info" -> caption colour
    command: str
    output: list[str]
    then: list[tuple[str, list[str]]] = field(default_factory=list)  # more (command, output) on the same screen
    line_ms: int = 110
    first_line_by_char: bool = False  # animate a pytest progress line ".F.."
    wrap: bool = False  # wrap long output lines instead of cutting them with an ellipsis
    hold_ms: int = 2800


def _font(bold: bool) -> ImageFont.FreeTypeFont:
    names = ["consolab.ttf", "DejaVuSansMono-Bold.ttf"] if bold else ["consola.ttf", "DejaVuSansMono.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, FONT_SIZE)
        except OSError:
            continue
    raise RuntimeError("no monospace font found (tried Consolas, DejaVu Sans Mono)")


FONT, FONT_BOLD = _font(False), _font(True)
CHAR_W = FONT.getlength("M")
COLS = int((W - 2 * PAD) / CHAR_W)
ROWS = (H - TITLE_H - CAPTION_H - 2 * PAD) // LINE_H


def _spans(line: str) -> list[tuple[str, tuple[int, int, int]]]:
    if line.startswith(PROMPT):
        return [(PROMPT, GREEN), (line[len(PROMPT):], WHITE)]
    if re.match(r"^[.F]+\s+\[\s*\d+%\]$", line):  # pytest progress line
        return [(ch, GREEN if ch == "." else RED if ch == "F" else DIM) for ch in line]
    if re.match(r"^=+ .* =+$", line) or line.startswith("="):
        color = RED if " failed" in line or "FAILURES" in line else GREEN if " passed" in line else DIM
        return [(line, color)]
    if re.match(r"^\d+ (failed|passed)", line):  # `pytest -q` summary
        return [(line, RED if " failed" in line else GREEN)]
    if line.startswith("PASS "):
        return [("PASS ", GREEN), (line[5:], FG)]
    if line.startswith("FAIL "):
        return [("FAIL ", RED), (line[5:], FG)]
    if line.startswith("FAILED"):
        return [("FAILED", RED), (line[6:], FG)]
    if line.startswith("E "):
        return [(line, RED)]
    if line.startswith(("OK", "All checks passed")):
        return [(line, GREEN)]
    if line.startswith("Overall:"):
        ok = re.search(r"(\d+)/(\d+)", line)
        full = bool(ok) and ok.group(1) == ok.group(2)
        return [(line, GREEN if full else RED)]
    if " PASSED" in line:
        head, tail = line.split(" PASSED", 1)
        return [(head, FG), (" PASSED", GREEN), (tail, DIM)]
    if " FAILED" in line:
        head, tail = line.split(" FAILED", 1)
        return [(head, FG), (" FAILED", RED), (tail, DIM)]
    return [(line, FG)]


def _caption_bar(d: ImageDraw.ImageDraw, caption: str, kind: str) -> None:
    bg, fg = CAPTION_STYLE[kind]
    d.rectangle([0, H - CAPTION_H, W, H], fill=bg)
    d.text((PAD, H - CAPTION_H / 2), caption, font=FONT_BOLD, fill=fg, anchor="lm")


def _draw(lines: list[str], caption: str, kind: str, title: str, cursor: bool) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, TITLE_H], fill=BAR)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([16 + i * 22, 12, 28 + i * 22, 24], fill=c)
    d.text((W / 2, TITLE_H / 2), title, font=FONT, fill=DIM, anchor="mm")

    y = TITLE_H + PAD
    x_end = PAD
    for line in lines[-ROWS:]:
        if len(line) > COLS:
            line = line[: COLS - 1] + "…"
        x = PAD
        for text, color in _spans(line):
            d.text((x, y), text, font=FONT, fill=color)
            x += FONT.getlength(text)
        x_end = x
        y += LINE_H
    if cursor:
        d.rectangle([x_end, y - LINE_H + 2, x_end + CHAR_W - 1, y - 3], fill=FG)

    _caption_bar(d, caption, kind)
    return img


def with_caption(shot: Image.Image, caption: str, kind: str) -> Image.Image:
    """Put a browser screenshot (BROWSER_SIZE) above the caption bar."""
    if shot.size != BROWSER_SIZE:
        raise ValueError(f"screenshot is {shot.size}, expected {BROWSER_SIZE}")
    img = Image.new("RGB", (W, H), BG)
    img.paste(shot.convert("RGB"), (0, 0))
    _caption_bar(ImageDraw.Draw(img), caption, kind)
    return img


def _wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if len(line) <= COLS:
            out.append(line)
            continue
        first, rest = line[:COLS], line[COLS:]
        out.append(first)
        while rest:
            out.append("  " + rest[: COLS - 2])
            rest = rest[COLS - 2:]
    return out


class Timeline:
    """Frames are kept zlib-compressed until save(), so long GIFs stay light on memory."""

    def __init__(self) -> None:
        self._raw: list[bytes] = []
        self._ms: list[int] = []

    def add(self, img: Image.Image, ms: int) -> None:
        self._raw.append(zlib.compress(img.tobytes(), 1))
        self._ms.append(ms)

    def _get(self, i: int) -> Image.Image:
        return Image.frombytes("RGB", (W, H), zlib.decompress(self._raw[i]))

    def add_scenes(self, scenes: list[Scene], title: str) -> None:
        for sc in scenes:
            def put(lines: list[str], cursor: bool, ms: int) -> None:
                self.add(_draw(lines, sc.caption, sc.kind, title, cursor), ms)

            lines: list[str] = []
            steps = [(sc.command, sc.output), *sc.then]
            for si, (command, output) in enumerate(steps):
                base = list(lines)
                put(base + [PROMPT], True, 500 if si == 0 else 350)
                for i in range(0, len(command), 2):
                    put(base + [PROMPT + command[: i + 2]], True, 60)
                lines = base + [PROMPT + command]
                put(lines, True, 450)
                for n, text in enumerate(_wrap(output) if sc.wrap else output):
                    if n == 0 and si == 0 and sc.first_line_by_char:
                        head = text.split(" ")[0]
                        for j in range(1, len(head) + 1):
                            put(lines + [text[:j]], False, 50)
                    put(lines + [text], False, sc.line_ms)
                    lines.append(text)
                if si < len(steps) - 1:
                    put(lines, False, 900)
                    lines.append("")
            put(lines, False, sc.hold_ms)

    def save(self, out: Path) -> None:
        n = len(self._raw)
        step = max(1, n // 24)
        samples = [self._get(i) for i in range(0, n, step)][:24]
        # A palette from sampled frames (plus one synthetic sheet per caption colour)
        # keeps text edges smooth and lets consecutive frames diff cleanly.
        for kind in CAPTION_STYLE:
            samples.append(
                _draw(["$ pytest -q", "..F [ 50%]", "E boom", "FAILED x", "a PASSED", "= 3 passed ="], "Sample", kind, "t", True)
            )
        half = (W // 2, H // 2)
        tall = Image.new("RGB", (half[0], half[1] * len(samples)))
        for k, s in enumerate(samples):
            tall.paste(s.resize(half, Image.BOX), (0, k * half[1]))
        pal = tall.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)

        frames = [self._get(i).quantize(palette=pal, dither=Image.Dither.NONE) for i in range(n)]
        out.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            out, save_all=True, append_images=frames[1:], duration=self._ms, loop=0, optimize=True, disposal=1
        )


def render_gif(scenes: list[Scene], out: Path, title: str) -> None:
    tl = Timeline()
    tl.add_scenes(scenes, title)
    tl.save(out)
