import random

# Star-type odds
STAR_ODDS = {
    4: 0.97,
    5: 0.02,
    6: 0.01,
}

STAR_ORDER = [4, 5, 6]

PITY_THRESHOLD = 60  # guaranteed 6★ every 60 pulls without one

# Order from lowest to highest, used for display and sorting
ELEMENTS = ["Blaze", "Teal", "Rust", "Drift", "Dawn", "Shade"]

# Simple rock-paper-scissors style cycle + Dawn/Shade counters
ELEMENT_ADVANTAGE = {
    "Blaze": "Drift",
    "Drift": "Rust",
    "Rust": "Teal",
    "Teal": "Blaze",
    "Dawn": "Shade",
    "Shade": "Dawn",
}


def roll_stars(pity_counter: int) -> tuple[int, bool]:
    """Roll 4★/5★/6★. Pity guarantees 6★ at the threshold."""
    if pity_counter + 1 >= PITY_THRESHOLD:
        return 6, True
    roll = random.random()
    cumulative = 0.0
    for stars, odds in STAR_ODDS.items():
        cumulative += odds
        if roll <= cumulative:
            return stars, False
    return 4, False


def roll_stars_from_pool(pity_counter: int, available_stars: set, min_stars: int | None = None) -> tuple[int, bool]:
    if not available_stars:
        return None, False
    eligible = available_stars if min_stars is None else {s for s in available_stars if s >= min_stars}
    if not eligible:
        eligible = available_stars  # nothing meets the floor in this pool - fall back rather than dead-end
    if pity_counter + 1 >= PITY_THRESHOLD:
        for stars in reversed(STAR_ORDER):
            if stars in eligible:
                return stars, True
        return None, True
    weights = {stars: STAR_ODDS[stars] for stars in STAR_ORDER if stars in eligible}
    total = sum(weights.values())
    roll = random.random() * total
    cumulative = 0.0
    for stars, weight in weights.items():
        cumulative += weight
        if roll <= cumulative:
            return stars, False
    return list(weights.keys())[-1], False


def pick_warrior_by_stars(catalog: list[dict], stars: int) -> dict | None:
    matches = [w for w in catalog if int(w.get("stars", 0)) == int(stars)]
    if not matches:
        return None
    return random.choice(matches)


def get_type_effectiveness(attacker_element: str, defender_element: str) -> float:
    """Returns a damage multiplier based on the element matchup."""
    if ELEMENT_ADVANTAGE.get(attacker_element) == defender_element:
        return 2.0  # super effective
    if ELEMENT_ADVANTAGE.get(defender_element) == attacker_element:
        return 0.5  # not very effective
    return 1.0  # neutral
