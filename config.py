"""
Shared, tunable values used by more than one file. This currently just holds
the star scaling used to auto-calculate hero/boss stats - it used to be
copy-pasted identically in seed_data.py AND custom_warriors.py, which meant
editing one and forgetting the other would silently make placeholder heroes
and CSV-authored heroes scale differently. Now there's one copy.

(Other tunables like star pull odds, banners, daily tasks, and party sprite
positions still live next to the code that uses them, since each of those
only has ONE place it's defined already - moving them here wouldn't fix a
bug, just relocate working code. This file is specifically for values that
were actually duplicated.)
"""

STAR_MULTIPLIER = {4: 1.0, 5: 1.6, 6: 2.0}

BASE_STATS = {
    "hp": 100,
    "atk": 20,
    "def_": 15,
    "matk": 20,
    "mdef": 15,
    "speed": 10,
}

# Baseline critical-hit damage multiplier. heroes.csv crit_damage numbers of 3
# or more are read as "bonus percent on top of this" (33 -> 1.5 + 0.33 = 1.83x).
CRIT_DAMAGE_BASE = 1.5


# Burn damage per stack per tick, as a share of the burner's MATK (before the
# target's MDEF). Burn stacks up to 5 and refreshes, so this is easily the
# strongest damage source in the game - tune here.
BURN_STACK_RATIO = 0.3          # was 0.65
SOUL_BURN_STACK_RATIO = 0.46    # was 1.0 (keeps the same 1.5x step up from Burn)

# Skill buffs/debuffs with no duration written in heroes.csv last this many
# turns (passives stay permanent). Re-casting refreshes rather than stacks.
DEFAULT_BUFF_TURNS = 3

# Stats for heroes whose base_* cells are left blank in heroes.csv:
# (hp, atk, def, matk, mdef, speed) per catalog star. A neutral hero on the same
# scale as the reference agile attacker; star growth is applied on top by the engine.
AUTO_HERO_STATS = {
    4: (760, 175, 73, 175, 73, 89),      # the 4-star reference (agile physical attacker)
    5: (1520, 350, 146, 350, 146, 98),   # ~2.0x the 4-star scale
    6: (2432, 560, 234, 560, 234, 107),  # ~3.2x the 4-star scale
}

# ---------------------------------------------------------------------------
# Stat system. heroes.csv now holds REAL stats for a hero at 4 stars, level 1
# (e.g. the agile 4-star attacker: HP 760, ATK 175, DEF 73, Speed 89, 5% crit,
# 150% crit damage). Level and star growth (progression.py) multiply them.
# ---------------------------------------------------------------------------

# Healing = healer's MATK x skill value x HEAL_SCALE (keeps heals meaningful
# next to HP that is several times bigger than attack).
HEAL_SCALE = 4.0

# Damage never drops below this share of the attacker's stat, however high the
# defender's DEF/MDEF is, so nobody is ever completely immune.
MIN_DAMAGE_RATIO = 0.10

# Old-style single-boss chapters (no enemy1..6 in story_chapters.csv) store
# boss stats in the small legacy units; these convert them to the real scale.
LEGACY_BOSS_SCALE = {"hp": 15.0, "power": 3.7, "speed": 1.6}

# ---------------------------------------------------------------------------
# Confirmed formation: Slot 1 & Slot 4 = middle row, Slot 2 & Slot 3 = front
# row, Slot 5 & Slot 6 = back row. Every frontline/backline/mirror-slot skill
# effect reads from here.
PARTY_ROW_BY_SLOT = {
    1: "middle",
    2: "front",
    3: "front",
    4: "middle",
    5: "back",
    6: "back",
}
