"""
Reads heroes.csv and adds/updates those heroes in the database every time the
bot starts. Editing a row for a hero that already exists (same `name`) and
restarting UPDATES it instead of creating a duplicate.

HOW TO ADD A HERO:
Open heroes.csv in Excel, Google Sheets, or any text editor and add a row.

Columns:
  name       - required. The hero's display name (shown everywhere in the bot).
  hero_key   - optional but STRONGLY recommended. A short, stable internal
               ID that never changes even if you rename the hero later, e.g.
               "elen_hart". Once a hero has a hero_key, you can freely edit
               their `name` field (rename them) and restarting will update
               the SAME hero instead of creating a duplicate. Without a
               hero_key, renaming is treated as a brand new hero (matching
               falls back to exact name). Use lowercase with underscores,
               no spaces, and never reuse a hero_key for a different hero.
  element    - required. One of: Blaze, Teal, Rust, Drift, Dawn, Shade
  stars      - required. One of: 4, 5, 6. This is the character's summon star type.
  banners    - optional. Which banner(s) this hero belongs to (see BANNERS in
               bot.py for valid ids, e.g. "torch_of_the_moonlight"). Leave
               BLANK to put the hero in the Standard Summon pool. A hero CAN
               belong to multiple banners at once - separate multiple ids
               with a semicolon, e.g. "torch_of_the_moonlight;standard" puts
               them on that special banner AND Standard Summon.
  base_hp, base_atk, base_def, base_matk, base_mdef, base_speed,
  crit_chance, crit_damage - optional. These are REAL stats for the hero at
               4 stars, level 1 (the engine multiplies them by level and star
               growth - see progression.py). Reference agile 4-star attacker:
               HP 760, ATK 175, DEF 73, Speed 89, crit_chance 5 (percent),
               crit_damage 150 (TOTAL percent, so 150 = 1.5x; 183 = 1.83x).
               Leave blank to use the neutral defaults in config.AUTO_HERO_STATS.
               Balance new heroes with:  python balance_tune.py

  HOW COMBAT SKILLS WORK: in battle, each hero cycles through their 3 skills
  in order every fight - round 1 uses Skill 1, round 2 uses Skill 2, round 3
  uses Skill 3 (if its condition is met - see below), round 4 goes back to
  Skill 1, and so on. The passive is different: it's not used on a turn, it's
  applied ONCE at the very start of the fight and lasts the whole battle.

  skill1_name/skill2_name/skill3_name, passive_name - the names shown in
               combat logs.
  skill1_effects/skill2_effects/skill3_effects, passive_effects - NEW modular
               effect strings. Put multiple effects in one cell separated by `|`.
               Each effect is `effect:value:target@if_condition`. Examples:
                 damage:1.5
                 damage:1.8|stun:1
                 damage:1.8|mark:2|stun:1@if_marked
                 heal:0.8:ally|cleanse:all:ally
               The target is optional and defaults to the skill target.
  skill1_target/skill2_target/skill3_target, passive_target - default target
               when an individual effect does not specify one. One of: enemy,
               self, ally, all_allies, all_enemies.

  BACKWARD COMPATIBILITY: skill1_type/skill1_power/etc. are still accepted. If
               the new `skillX_effects` column is blank, the old one-effect fields
               are converted automatically.
  skill3_condition, skill3_value - OPTIONAL requirement gating Skill 3 (if
               left blank, Skill 3 always triggers on its turn like the
               others). One of:
                 hp_below   - triggers only if the hero's HP% is below
                              skill3_value (e.g. value 0.5 = below 50% HP)
                 turn_min   - triggers only from round skill3_value onward
                 chance     - triggers with probability skill3_value
                              (e.g. value 0.3 = 30% chance)
                 resolve_min - triggers only when the hero has at least
                              skill3_value Resolve
               If the condition isn't met, the hero falls back to using
               Skill 1 that round instead of wasting the turn.

  skill1_cooldown/skill2_cooldown/skill3_cooldown - OPTIONAL. Leave ALL THREE
               blank for the classic S1->S2->S3->repeat rotation above. Set
               skill1_cooldown and/or skill2_cooldown to a number of turns
               (e.g. 2) to switch that hero to a cooldown-based rotation
               instead: each turn, she uses whichever off-cooldown skill
               comes first (Skill 1 checked before Skill 2), and only uses a
               plain Basic Attack if everything is still on cooldown. Skill 3
               keeps its normal condition gate (hp_below/turn_min/etc.) on
               top of this - give it skill3_cooldown too if you don't want
               her ult reusable the instant the condition is true again.

Save the file and restart the bot (Ctrl+C, then python bot.py).
Find a hero's warrior ID afterward with /admin find-warrior if you need it
(e.g. for adding art - see assets/warriors/ naming).
"""

import csv
import os

CSV_PATH = os.path.join(os.path.dirname(__file__), "heroes.csv")

from config import STAR_MULTIPLIER, BASE_STATS, AUTO_HERO_STATS


def _int_or_none(value: str):
    value = (value or "").strip()
    return int(value) if value else None


def _float_or_none(value: str):
    value = (value or "").strip()
    return float(value) if value else None


def _fill_defaults(row: dict) -> dict:
    raw_stars = (row.get("stars") or "").strip()
    stars = int(raw_stars) if raw_stars else 4
    stars = max(4, min(6, stars))
    mult = STAR_MULTIPLIER.get(stars, 1.0)

    banners_raw = (row.get("banners") or "").strip()
    banners = [b.strip() for b in banners_raw.split(";") if b.strip()] if banners_raw else []

    filled = {
        "name": row["name"].strip(),
        "element": row["element"].strip(),
        "rarity": str(stars),  # legacy DB column, kept in sync with stars only
        "stars": stars,
        "banners": banners,
        "hero_key": (row.get("hero_key") or "").strip() or None,
        "base_hp": _int_or_none(row.get("base_hp")) or AUTO_HERO_STATS[stars][0],
        "base_atk": _int_or_none(row.get("base_atk")) or AUTO_HERO_STATS[stars][1],
        "base_def": _int_or_none(row.get("base_def")) or AUTO_HERO_STATS[stars][2],
        "base_matk": _int_or_none(row.get("base_matk")) or AUTO_HERO_STATS[stars][3],
        "base_mdef": _int_or_none(row.get("base_mdef")) or AUTO_HERO_STATS[stars][4],
        "base_speed": _int_or_none(row.get("base_speed")) or AUTO_HERO_STATS[stars][5],
        "crit_chance": _float_or_none(row.get("crit_chance")) or 0.05,
        "crit_damage": _float_or_none(row.get("crit_damage")) or 1.5,
        "skill_1": (row.get("skill1_name") or "").strip() or "Strike",
        "skill_2": (row.get("skill2_name") or "").strip() or "Guard",
        "skill_3": (row.get("skill3_name") or "").strip() or "Surge",
        "passive": (row.get("passive_name") or "").strip() or "Resolve",
        "skill1_effects": (row.get("skill1_effects") or "").strip(),
        "skill2_effects": (row.get("skill2_effects") or "").strip(),
        "skill3_effects": (row.get("skill3_effects") or "").strip(),
        "passive_effects": (row.get("passive_effects") or "").strip(),
        "skill1_type": (row.get("skill1_type") or "damage").strip(),
        "skill1_power": _float_or_none(row.get("skill1_power")) or 1.0,
        "skill1_target": (row.get("skill1_target") or "enemy").strip(),
        "skill2_type": (row.get("skill2_type") or "damage").strip(),
        "skill2_power": _float_or_none(row.get("skill2_power")) or 1.0,
        "skill2_target": (row.get("skill2_target") or "enemy").strip(),
        "skill3_type": (row.get("skill3_type") or "damage").strip(),
        "skill3_power": _float_or_none(row.get("skill3_power")) or 1.0,
        "skill3_target": (row.get("skill3_target") or "enemy").strip(),
        "skill3_condition": (row.get("skill3_condition") or "").strip() or None,
        "skill3_value": _float_or_none(row.get("skill3_value")),
        "skill1_cooldown": _int_or_none(row.get("skill1_cooldown")),
        "skill2_cooldown": _int_or_none(row.get("skill2_cooldown")),
        "skill3_cooldown": _int_or_none(row.get("skill3_cooldown")),
        "passive_type": (row.get("passive_type") or "").strip() or None,
        "passive_power": _float_or_none(row.get("passive_power")) or 0.0,
        "passive_target": (row.get("passive_target") or "self").strip(),
    }
    return filled


def sync_custom_warriors():
    """Reads heroes.csv and adds/updates every row. Returns (new_count, updated_count)."""
    import database

    if not os.path.exists(CSV_PATH):
        return 0, 0

    new_count = 0
    updated_count = 0

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("name", "").strip():
                continue  # skip blank rows
            filled = _fill_defaults(row)
            _, was_new = database.upsert_catalog_warrior(filled)
            if was_new:
                new_count += 1
            else:
                updated_count += 1

    return new_count, updated_count
