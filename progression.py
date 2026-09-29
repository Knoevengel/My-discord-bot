"""Character level and star-tier progression rules."""

MAX_WARRIOR_TIER = 20
CHARACTER_STAR_TYPES = (4, 5, 6)

# Level cap grows with stars. A 4★ hero can reach Lv 20; every extra star raises
# the cap by LEVEL_CAP_PER_STAR (5★ = 25, 6★ = 30 ... 20★ = 100). Starring up
# needs the hero to be AT its current cap, so the level needed for each star-up
# climbs as you go: Lv 20 for the first, then 25, 30, 35 ...
# Level cap by star tier (author-set table). 20 stars and above have NO cap -
# represented by a very high practical ceiling, since EXP cost past that point
# grows so fast (see exp_required_for_next_level) that it is never really reached.
LEVEL_CAP_BY_STAR = {
    4: 20, 5: 30, 6: 50, 7: 65, 8: 100, 9: 120, 10: 150,
    11: 170, 12: 190, 13: 220, 14: 240, 15: 270, 16: 300,
    17: 330, 18: 370, 19: 390,
}
UNCAPPED_FROM_STARS = 20
# Highest level any hero can ever reach. Not a real design target - EXP cost
# makes anything far past 19-star's cap (390) essentially unreachable - this
# is only a ceiling so level math and UI never have to handle "infinite".
MAX_WARRIOR_LEVEL = 9999


def level_cap_for_stars(stars: int) -> int:
    stars = max(4, min(int(stars), MAX_WARRIOR_TIER))
    if stars >= UNCAPPED_FROM_STARS:
        return MAX_WARRIOR_LEVEL
    if stars in LEVEL_CAP_BY_STAR:
        return LEVEL_CAP_BY_STAR[stars]
    lower = max(k for k in LEVEL_CAP_BY_STAR if k <= stars)
    return LEVEL_CAP_BY_STAR[lower]


def effective_level_cap(stars: int, level: int = 1) -> int:
    """A hero's level cap. Heroes that were levelled past it under an older,
    lower cap keep their levels (their cap is never below their current level)."""
    return max(level_cap_for_stars(stars), int(level))


def tier_level_requirement(stars: int) -> int:
    """Level a hero must have to star up FROM `stars` (= its current level cap)."""
    return level_cap_for_stars(stars)

# Original summon type controls the ultimate star cap.
CHARACTER_MAX_TIER = {4: 5, 5: 20, 6: 20}

# Each star-up consumes one Memory Stone payment. The shop sells 600 for
# 1200 Solite; 600 is used here as the current per-upgrade amount because it
# is the only quantity supplied for Memory Stones so far.
MEMORY_STONES_PER_UPGRADE = 600

# 5-star and 6-star heroes are meant to be close in power - the difference is
# that 6-star heroes are HARDER TO RAISE. Every star-up a 6-star hero performs
# costs this much more in Memory Stones than the same star-up on a 5-star hero.
RARITY_MEMORY_STONE_MULTIPLIER = {4: 1.0, 5: 1.0, 6: 1.5}


def memory_stone_cost(base_stars: int) -> int:
    mult = RARITY_MEMORY_STONE_MULTIPLIER.get(int(base_stars), 1.0)
    return int(round(MEMORY_STONES_PER_UPGRADE * mult))
MEMORY_STONE_PACK_SIZE = 600
MEMORY_STONE_PACK_COST = 1200

# Food star-up uses 5 copies of the current food tier to create 1 food of the
# next tier. Unlike character star-up, food star-up never costs Memory Stones.
FOOD_STAR_UP_COST = 5
FOOD_MAX_STARS = MAX_WARRIOR_TIER

# Requirement keys:
#   self_copy: exact same hero instance at a given star tier
#   food_same: same-element food at a given star tier
#   food_any_element: food of any real element at a given star tier
#   other_hero: non-self hero instances at a given star tier (4->5 only)
TIER_REQUIREMENTS = {
    (4, 5): {
        "self_copy": {4: 2},
        "other_hero": {4: 3},
    },
    (5, 6): {
        "self_copy": {5: 1},
        "food_same": {5: 1},
    },
    (6, 7): {
        "food_same": {5: 2},
    },
    (7, 8): {
        "self_copy": {6: 1},
        "food_same": {6: 1, 5: 2},
    },
    (8, 9): {
        "food_same": {5: 4},
    },
    (9, 10): {
        "food_same": {5: 5},
    },
    (10, 11): {
        "self_copy": {6: 1},
        "food_same": {8: 1},
    },
    (11, 12): {
        "food_same": {5: 6},
    },
    (12, 13): {
        "self_copy": {6: 1},
        "food_same": {8: 1},
    },
    (13, 14): {
        "food_same": {5: 6},
    },
    (14, 15): {
        "food_same": {5: 8},
    },
    (15, 16): {
        "self_copy": {6: 2},
        "food_same": {8: 1},
        "food_any_element": {8: 1},
    },
    (16, 17): {
        "food_any_element": {8: 1},
    },
    (17, 18): {
        "self_copy": {8: 1},
        "food_any_element": {11: 1},
    },
    (18, 19): {
        "self_copy": {8: 1},
        "food_any_element": {8: 1, 11: 1},
    },
    (19, 20): {
        "self_copy": {8: 1},
        "food_same": {11: 1},
        "food_any_element": {11: 1},
    },
}

FIRST_LEVEL_EXP_COST = 100
EXP_COST_GROWTH = 50
# Stat growth. heroes.csv holds a hero's stats as a FRESH copy of its own rarity
# (4/5/6 stars) at Lv1; growth is applied from there.
#  - Level adds +10% of the Lv1 base per level (Lv20 = x2.9, Lv100 = x10.9).
#  - Every star-up multiplies ALL stats by 1.25.
#  - BREAKTHROUGHS: reaching 8, 11, 13, 15 and 20 stars gives an extra one-time
#    multiplier on top (a step up in power, not just another +25%).
# Together a fully starred hero reaches hundreds of thousands to millions of HP.
LEVEL_STAT_GROWTH = 0.10
STAR_STAT_GROWTH = 1.25
STAR_BREAKTHROUGH_BONUS = {8: 1.20, 11: 1.25, 13: 1.30, 15: 1.35, 20: 1.60}
# Speed grows far more gently (turn order, not raw power): +1% per level,
# x1.03 per star and x1.03 per breakthrough.
SPEED_LEVEL_GROWTH = 0.01
SPEED_STAR_GROWTH = 1.03
SPEED_BREAKTHROUGH_BONUS = 1.03


def breakthroughs_reached(stars: int, base_tier: int = 4) -> list[int]:
    """Breakthrough star levels a hero has passed (only those above its summon star)."""
    return [m for m in sorted(STAR_BREAKTHROUGH_BONUS) if int(base_tier) < m <= int(stars)]


def breakthrough_bonus(stars: int) -> float | None:
    """The extra multiplier granted the moment a hero reaches `stars` (None if it isn't a breakthrough)."""
    return STAR_BREAKTHROUGH_BONUS.get(int(stars))


def next_breakthrough(stars: int) -> int | None:
    return next((m for m in sorted(STAR_BREAKTHROUGH_BONUS) if m > int(stars)), None)


# Past this level (19-star's cap - the last AUTHORED tier), cost stops growing
# linearly and starts compounding, so levelling "becomes ridiculously difficult"
# once a 20-star hero pushes beyond where the table ends - exactly what was
# asked for, without an arbitrary hard stop.
EXP_SOFT_CAP_LEVEL = LEVEL_CAP_BY_STAR[19]
EXP_HARD_GROWTH_RATE = 1.06


def _linear_exp_cost(level: int) -> float:
    return FIRST_LEVEL_EXP_COST + (level - 1) * EXP_COST_GROWTH


def exp_required_for_next_level(level: int) -> int:
    level = int(level)
    if level >= MAX_WARRIOR_LEVEL:
        return 0
    if level < EXP_SOFT_CAP_LEVEL:
        return int(_linear_exp_cost(level))
    base = _linear_exp_cost(EXP_SOFT_CAP_LEVEL)
    return int(base * (EXP_HARD_GROWTH_RATE ** (level - EXP_SOFT_CAP_LEVEL)))


def total_exp_to_reach_level(level: int) -> int:
    level = max(1, min(int(level), MAX_WARRIOR_LEVEL))
    return sum(exp_required_for_next_level(lvl) for lvl in range(1, level))


def _tiers(tier: int, base_tier) -> tuple[int, int]:
    tier = max(4, min(int(tier), MAX_WARRIOR_TIER))
    base = tier if base_tier is None else max(4, min(int(base_tier), tier))
    return tier, base


def stat_multiplier(level: int, tier: int = 4, base_tier: int | None = 4) -> float:
    """Multiplier on the heroes.csv stats for HP/ATK/DEF/MATK/MDEF.

    `tier` is the hero's current stars and `base_tier` its summon (origin) stars;
    growth counts star-ups from the origin, so a fresh 6-star is exactly what
    heroes.csv says. Pass base_tier=None to treat `tier` as the origin (x1).
    """
    level = max(1, min(int(level), MAX_WARRIOR_LEVEL))
    tier, base = _tiers(tier, base_tier)
    level_mult = 1.0 + LEVEL_STAT_GROWTH * (level - 1)
    star_mult = STAR_STAT_GROWTH ** (tier - base)
    for m in breakthroughs_reached(tier, base):
        star_mult *= STAR_BREAKTHROUGH_BONUS[m]
    return level_mult * star_mult


def speed_multiplier(level: int, tier: int = 4, base_tier: int | None = 4) -> float:
    level = max(1, min(int(level), MAX_WARRIOR_LEVEL))
    tier, base = _tiers(tier, base_tier)
    mult = (1.0 + SPEED_LEVEL_GROWTH * (level - 1)) * (SPEED_STAR_GROWTH ** (tier - base))
    return mult * (SPEED_BREAKTHROUGH_BONUS ** len(breakthroughs_reached(tier, base)))


def tier_requirements(from_stars: int, to_stars: int) -> dict | None:
    return TIER_REQUIREMENTS.get((int(from_stars), int(to_stars)))


def character_max_stars(base_stars: int) -> int:
    return CHARACTER_MAX_TIER.get(int(base_stars), int(base_stars))


def tier_requirements_text(from_stars: int, to_stars: int, base_stars: int = 4) -> str:
    req = tier_requirements(from_stars, to_stars)
    if not req:
        return "Requirements not authored yet."
    parts = []
    for star, count in sorted(req.get("self_copy", {}).items(), reverse=True):
        parts.append(f"{count}× {star}★ copy of the same hero")
    for star, count in sorted(req.get("other_hero", {}).items(), reverse=True):
        parts.append(f"{count}× other {star}★ hero")
    for star, count in sorted(req.get("food_same", {}).items(), reverse=True):
        parts.append(f"{count}× {star}★ same-element food")
    for star, count in sorted(req.get("food_any_element", {}).items(), reverse=True):
        parts.append(f"{count}× {star}★ food of any element")
    parts.append(f"{memory_stone_cost(base_stars)} Memory Stones")
    return " + ".join(parts)


def known_tier_targets(from_stars: int) -> list[int]:
    return [to for (frm, to) in sorted(TIER_REQUIREMENTS) if frm == int(from_stars)]


def level_up_cost_for_levels(current_level: int, levels: int) -> int:
    levels = max(0, int(levels))
    total = 0
    level = int(current_level)
    for _ in range(levels):
        cost = exp_required_for_next_level(level)
        if cost <= 0:
            break
        total += cost
        level += 1
    return int(total)


def max_levels_affordable(current_level: int, exp: int, max_level: int | None = None) -> int:
    levels = 0
    level = int(current_level)
    remaining = int(exp)
    ceiling = MAX_WARRIOR_LEVEL if max_level is None else min(int(max_level), MAX_WARRIOR_LEVEL)
    while level < ceiling:
        cost = exp_required_for_next_level(level)
        if remaining < cost:
            break
        remaining -= cost
        level += 1
        levels += 1
    return levels
