"""
Reads story_chapters.csv and adds/updates those chapters in the database
every time the bot starts. Editing a row for a chapter that already exists
(same `chapter_number`) and restarting UPDATES it instead of duplicating.

HOW TO ADD/EDIT A CHAPTER:
Open story_chapters.csv in Excel, Google Sheets, or any text editor.

Columns:
  chapter_number - required. Chapters unlock in order (1, 2, 3, ...) - a
                    user must clear chapter N before N+1 becomes playable.
  title          - required. Shown as the chapter's name on the initial
                    /level screen (before they click Start).
  description    - optional. A short teaser shown on that initial screen.
  story_text     - optional. The full narrative shown after clicking Start -
                    see "WRITING LONGER CHAPTERS" below for a much easier way
                    to write this than typing into a CSV cell. Leave both
                    this AND the matching Word doc absent to skip straight
                    to the fight with no story text at all.
  boss_name      - required. The enemy's name.
  boss_element   - required. One of: Blaze, Teal, Rust, Drift, Dawn, Shade
  boss_stars    - required. One of: 4, 5, 6 - this scales the boss's stats
                    automatically, same star scaling as heroes. Higher star
                    number = tougher fight.
  reward_solite  - optional. Solite awarded on clearing this chapter (default 0).
  reward_charms  - optional. Summon Charms awarded on clearing (default 0).
  reward_exp     - optional. EXP awarded on clearing (default 0).
  enemy1..enemy6 - optional hero_key (or exact hero name) for each enemy slot.
                    Leave blank for an empty slot. The same hero can appear in
                    multiple slots if you want a boss duplicate/cloned unit.
  enemy1_level..enemy6_level - optional battle level for each enemy slot.
                    This is independent of the player's copy of that hero.

Save the file and restart the bot (Ctrl+C, then python bot.py).

WRITING LONGER CHAPTERS (Word documents):
Typing a full chapter into one CSV cell is awkward. Instead, you can write it
as a normal Word document: create story_docs/chapter_<N>.docx (e.g.
story_docs/chapter_1.docx for Chapter 1) and just write in it like a regular
document - one paragraph per line. Put [PAGE] as its own paragraph anywhere
you want a page break (same convention as the CSV column, just easier to type
in Word). If a chapter_<N>.docx file exists, it's used INSTEAD of that
chapter's story_text column - you don't need both. Reading the .docx happens
once when the bot starts, so file size has no effect on how the bot performs
while running.

NOTE: Chapter art is optional - drop an image at assets/story/<chapter_number>.png
(e.g. assets/story/1.png for Chapter 1) and it'll show automatically in /level.

NOTE ON COMBAT: fights use the real turn-based battle engine (battle.py) -
Speed-based turn order, type effectiveness, crits, and each hero's actual
skill rotation (Skill 1 -> 2 -> 3 -> repeat, with Skill 3 able to have a
trigger condition - see heroes.csv for how to set that up per hero).
"""

import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "story_chapters.csv")
STORY_DOCS_DIR = os.path.join(os.path.dirname(__file__), "story_docs")

# Same star scaling as heroes, reused here rather than a third copy -
# see config.py for the shared canonical version.
from config import STAR_MULTIPLIER, BASE_STATS


def _int_or_default(value: str, default: int) -> int:
    value = (value or "").strip()
    return int(value) if value else default


def read_chapter_docx(chapter_number: int):
    """
    Returns the text content of story_docs/chapter_<N>.docx as a single
    string (paragraphs joined by newlines, [PAGE] paragraphs preserved as
    page-break markers), or None if that file doesn't exist. Requires the
    python-docx package (pip install python-docx).
    """
    path = os.path.join(STORY_DOCS_DIR, f"chapter_{chapter_number}.docx")
    if not os.path.exists(path):
        return None

    try:
        import docx
    except ImportError:
        print(f"WARNING: found {path} but python-docx isn't installed - run: pip install python-docx")
        return None

    document = docx.Document(path)
    paragraphs = [p.text for p in document.paragraphs]
    # Collapse consecutive blank paragraphs but keep single blank lines
    # (blank lines between paragraphs read naturally; more than one in a row
    # usually isn't intentional formatting)
    text_lines = []
    prev_blank = False
    for line in paragraphs:
        stripped = line.strip()
        if not stripped:
            if not prev_blank:
                text_lines.append("")
            prev_blank = True
        else:
            text_lines.append(stripped)
            prev_blank = False

    return "\n".join(text_lines).strip()


def _fill_defaults(row: dict) -> dict:
    boss_stars = max(4, min(6, int((row.get("boss_stars") or "4").strip())))
    mult = STAR_MULTIPLIER.get(boss_stars, 1.0)
    chapter_number = int(row["chapter_number"].strip())

    # A Word doc, if present, wins over the CSV's story_text column - it's
    # a much nicer way to write long-form chapters.
    docx_text = read_chapter_docx(chapter_number)
    story_text = docx_text if docx_text is not None else (row.get("story_text") or "").strip()

    # Bosses hit a bit harder than a same-star hero would, to make for a
    # real fight against a full 6-warrior party - tweak this if fights feel
    # too easy/hard.
    boss_mult = mult * 1.5

    filled = {
        "chapter_number": chapter_number,
        "title": row["title"].strip(),
        "description": (row.get("description") or "").strip(),
        "story_text": story_text,
        "boss_name": row["boss_name"].strip(),
        "boss_element": row["boss_element"].strip(),
        "boss_stars": boss_stars,
        "boss_hp": int(BASE_STATS["hp"] * boss_mult * 6),  # roughly matches a 6-warrior party's total HP
        "boss_atk": int(BASE_STATS["atk"] * boss_mult),
        "boss_def": int(BASE_STATS["def_"] * boss_mult),
        "boss_matk": int(BASE_STATS["matk"] * boss_mult),
        "boss_mdef": int(BASE_STATS["mdef"] * boss_mult),
        "boss_speed": int(BASE_STATS["speed"] * boss_mult),
        "reward_solite": _int_or_default(row.get("reward_solite"), 0),
        "reward_charms": _int_or_default(row.get("reward_charms"), 0),
        "reward_exp": _int_or_default(row.get("reward_exp"), 0),
    }

    for i in range(1, 7):
        filled[f"enemy{i}"] = (row.get(f"enemy{i}") or "").strip() or None
        filled[f"enemy{i}_level"] = max(_int_or_default(row.get(f"enemy{i}_level"), 1), 1)

    return filled


def sync_story_chapters():
    """Reads story_chapters.csv and adds/updates every row. Returns (new_count, updated_count)."""
    import database

    if not os.path.exists(CSV_PATH):
        return 0, 0

    new_count = 0
    updated_count = 0

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("chapter_number", "").strip():
                continue  # skip blank rows
            filled = _fill_defaults(row)
            _, was_new = database.upsert_story_chapter(filled)
            if was_new:
                new_count += 1
            else:
                updated_count += 1

    return new_count, updated_count
