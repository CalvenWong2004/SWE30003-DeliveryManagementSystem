"""Render the scenario transcripts as terminal-style PNG screenshots.

Each transcript is cut at screen boundaries into pages of at most
``MAX_LINES`` lines. Errors ([!]) are red, confirmations ([OK]) green and
typed input bold yellow. Run from the project root after run_scenarios.py:

    python tools/render_screenshots.py
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evidence" / "transcripts"
TARGET = ROOT / "evidence" / "screenshots"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
MAX_LINES = 46
SIZE, LINE_H, PAD, COLS = 15, 20, 18, 100
BG, FG = (24, 26, 33), (220, 223, 228)
RED, GREEN, INPUT, TITLE = (255, 110, 110), (120, 220, 140), \
    (255, 214, 102), (130, 180, 255)
PROMPT = re.compile(r"^(\s+[^\[\]]*?(?:\[[^\]]*\])?(?: \(Enter = [^)]*\))?: )"
                    r"(.*)$")


def pages(lines: list[str]) -> list[list[str]]:
    """Split lines into pages, preferring to cut before a screen title."""
    starts = [i for i in range(len(lines)) if lines[i].startswith("=====")
              and i + 2 < len(lines) and lines[i + 2].startswith("=====")]
    result, current_start = [], 0
    for index, start in enumerate(starts + [len(lines)]):
        if start - current_start > MAX_LINES:
            previous = starts[index - 1] if index else start
            cut = previous if previous > current_start else \
                current_start + MAX_LINES
            result.append(lines[current_start:cut])
            current_start = cut
            while start - current_start > MAX_LINES:
                result.append(lines[current_start:current_start + MAX_LINES])
                current_start += MAX_LINES
    result.append(lines[current_start:])
    return [p for p in result if any(line.strip() for line in p)]


def draw_page(lines: list[str], title: str, path: Path) -> None:
    """Draw one page as a window with a title bar."""
    font = ImageFont.truetype(FONT, SIZE)
    bold = ImageFont.truetype(FONT_BOLD, SIZE)
    width = PAD * 2 + int(font.getlength("M") * COLS)
    height = PAD * 2 + 34 + LINE_H * len(lines)
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 30], fill=(48, 52, 62))
    for i, colour in enumerate([(255, 95, 86), (255, 189, 46),
                                (39, 201, 63)]):
        draw.ellipse([12 + i * 20, 9, 24 + i * 20, 21], fill=colour)
    draw.text((80, 7), title, font=font, fill=(200, 200, 200))
    y = 34 + PAD
    for line in lines:
        x = PAD
        line = line[:COLS + 30]
        if "[!]" in line:
            draw.text((x, y), line, font=font, fill=RED)
        elif "[OK]" in line:
            draw.text((x, y), line, font=font, fill=GREEN)
        elif line.startswith("=") or (line.startswith(" ") and lines and
                                      line.strip() and _is_title(lines,
                                                                 line)):
            draw.text((x, y), line, font=bold, fill=TITLE)
        else:
            match = PROMPT.match(line)
            if match and match.group(2):
                draw.text((x, y), match.group(1), font=font, fill=FG)
                x += font.getlength(match.group(1))
                draw.text((x, y), match.group(2), font=bold, fill=INPUT)
            elif line.startswith("$ "):
                draw.text((x, y), line, font=bold, fill=INPUT)
            else:
                draw.text((x, y), line, font=font, fill=FG)
        y += LINE_H
    image.save(path)


def _is_title(lines: list[str], line: str) -> bool:
    index = lines.index(line)
    return 0 < index < len(lines) - 1 and \
        lines[index - 1].startswith("=====") and \
        lines[index + 1].startswith("=====")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for old in TARGET.glob("*.png"):
        old.unlink()
    for transcript in sorted(SOURCE.glob("*.txt")):
        lines = transcript.read_text(encoding="utf-8").splitlines()
        for number, page in enumerate(pages(lines), start=1):
            name = f"{transcript.stem}_{number:02d}.png"
            draw_page(page, f"{transcript.stem}  -  page {number}",
                      TARGET / name)
            print(name, len(page))


if __name__ == "__main__":
    main()
