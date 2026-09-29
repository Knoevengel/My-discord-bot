"""Owner-only battle testing helpers (used by /admin test-battle).

Pure Python - no Discord imports - so it can be tested and reused from a
terminal. Nothing here touches player data: heroes come straight from the
catalog and battles are simulated only.
"""
import battle
import database
import progression

MAX_PER_SIDE = 6


def _find_hero(token: str, catalog: list[dict]):
    """Match by hero_key, exact name, then unique partial name. Returns (hero, error)."""
    q = token.strip().lower()
    if not q:
        return None, "empty hero name"
    for hero in catalog:
        if (hero.get("hero_key") or "").lower() == q or hero["name"].lower() == q:
            return hero, None
    partial = [h for h in catalog if q in h["name"].lower() or q in (h.get("hero_key") or "").lower()]
    if len(partial) == 1:
        return partial[0], None
    if not partial:
        return None, f"no hero matches **{token.strip()}** (try `/admin find-warrior`)"
    names = ", ".join(sorted({h["name"] for h in partial})[:8])
    return None, f"**{token.strip()}** matches several heroes: {names}"


def parse_team(text: str, catalog: list[dict], default_level: int = 1):
    """Parse 'Elen:30:6, Nyx, Keil:12' -> ([warrior dicts], error).

    Each entry is name[:level[:stars]]. Level is 1-30 (defaults to
    default_level), stars is 4-20 (defaults to the hero's catalog stars).
    """
    entries = [e for e in (text or "").split(",") if e.strip()]
    if not entries:
        return None, "give at least one hero"
    if len(entries) > MAX_PER_SIDE:
        return None, f"a team has at most {MAX_PER_SIDE} heroes (you gave {len(entries)})"
    team = []
    for entry in entries:
        parts = [p.strip() for p in entry.split(":")]
        hero, err = _find_hero(parts[0], catalog)
        if err:
            return None, err
        warrior = dict(hero)
        try:
            level = int(parts[1]) if len(parts) > 1 and parts[1] else default_level
            stars = int(parts[2]) if len(parts) > 2 and parts[2] else int(hero.get("stars", 4))
        except ValueError:
            return None, f"`{entry.strip()}`: level and stars must be numbers (format name:level:stars)"
        if not 1 <= level <= progression.MAX_WARRIOR_LEVEL:
            return None, f"`{entry.strip()}`: level must be 1-{progression.MAX_WARRIOR_LEVEL}"
        if not 4 <= stars <= 20:
            return None, f"`{entry.strip()}`: stars must be 4-20"
        warrior["level"] = level
        warrior["stars"] = stars
        team.append(warrior)
    return team, None


def parse_enemies(text: str, catalog: list[dict], default_level: int = 1):
    """Like parse_team, but 'chapter:3' loads that story chapter's enemy team.

    Returns (enemy_team, chapter_dict, error). chapter_dict is only needed for
    old-style boss chapters that have no enemy1..6 heroes.
    """
    t = (text or "").strip()
    if t.lower().startswith("chapter"):
        digits = "".join(ch for ch in t if ch.isdigit())
        if not digits:
            return None, None, "use `chapter:3` to load a story chapter's enemies"
        chapter = database.get_story_chapter(int(digits))
        if not chapter:
            return None, None, f"chapter {int(digits)} doesn't exist"
        return database.get_chapter_enemy_team(chapter), chapter, None
    team, err = parse_team(t, catalog, default_level)
    return team, {}, err


def describe_team(team: list[dict]) -> str:
    return ", ".join(f"{w['name']} (Lv{w.get('level', 1)}, {w.get('stars', 4)}★)" for w in team) or "legacy boss"


def run_many(allies: list[dict], enemies: list[dict], chapter: dict, runs: int) -> dict:
    """Simulate `runs` battles. Returns aggregate stats plus the LAST battle's full result."""
    wins = timeouts = 0
    rounds_list: list[int] = []
    alive_totals = [0] * len(allies)
    last = None
    for _ in range(runs):
        party = {i + 1: dict(w) for i, w in enumerate(allies)}
        result = battle.resolve_battle(party, chapter or {}, [dict(e) for e in enemies] or None)
        last = result
        wins += bool(result["won"])
        rounds_list.append(result["rounds"])
        if not result["won"] and result["rounds"] >= battle.MAX_ROUNDS:
            timeouts += 1
        for i, status in enumerate(result["party_status"]):
            alive_totals[i] += bool(status["alive"])
    return {
        "runs": runs,
        "wins": wins,
        "timeouts": timeouts,
        "avg_rounds": sum(rounds_list) / runs,
        "min_rounds": min(rounds_list),
        "max_rounds": max(rounds_list),
        "survival": [t / runs for t in alive_totals],
        "last": last,
    }
