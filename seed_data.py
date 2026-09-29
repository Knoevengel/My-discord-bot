"""
Placeholder warrior catalog. Names are generic on purpose (you said you'll
name real heroes later) - swap the `name` fields whenever you're ready.
Stats are rough baselines scaled by stars; tweak freely once you start
playtesting.
"""

from gacha import ELEMENTS, STAR_ORDER
from config import STAR_MULTIPLIER, BASE_STATS


def generate_placeholder_roster():
    """
    Generates a small starter roster with only 4★, 5★, and 6★ heroes.
    """
    roster = []
    for element in ELEMENTS:
        for stars in STAR_ORDER:
            mult = STAR_MULTIPLIER[stars]
            for i in range(1, 3):  # 2 per element/star combo
                roster.append({
                    "name": f"{element} Warrior {stars}★{i}",
                    "element": element,
                    "rarity": str(stars),
                    "stars": stars,
                    "base_hp": int(BASE_STATS["hp"] * mult),
                    "base_atk": int(BASE_STATS["atk"] * mult),
                    "base_def": int(BASE_STATS["def_"] * mult),
                    "base_matk": int(BASE_STATS["matk"] * mult),
                    "base_mdef": int(BASE_STATS["mdef"] * mult),
                    "base_speed": int(BASE_STATS["speed"] * mult),
                    "crit_chance": 0.05,
                    "crit_damage": 1.5,
                    "skill_1": "Strike",
                    "skill_2": "Guard",
                    "skill_3": "Surge",
                    "passive": "Resolve",
                })
    return roster


def seed_catalog_if_empty():
    """Inserts the placeholder roster into the database if the catalog is empty."""
    import database

    existing = database.get_all_catalog_warriors()
    if existing:
        return 0  # already seeded, don't duplicate

    roster = generate_placeholder_roster()
    conn = database.get_connection()
    cur = conn.cursor()
    for w in roster:
        cur.execute("""
            INSERT INTO warrior_catalog
            (name, element, rarity, stars, base_hp, base_atk, base_def, base_matk,
             base_mdef, base_speed, crit_chance, crit_damage, skill_1, skill_2, skill_3, passive)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            w["name"], w["element"], w["rarity"], w["stars"], w["base_hp"], w["base_atk"],
            w["base_def"], w["base_matk"], w["base_mdef"], w["base_speed"],
            w["crit_chance"], w["crit_damage"], w["skill_1"], w["skill_2"],
            w["skill_3"], w["passive"],
        ))
    conn.commit()
    conn.close()
    return len(roster)
