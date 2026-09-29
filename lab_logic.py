"""Pure Lab logic (no Discord): food summaries, what your heroes need, the
mass-star-up plan, and melt previews. The bot's Lab screens are thin wrappers
around these so everything here can be unit-tested.
"""
from __future__ import annotations

from collections import defaultdict

import gacha
import progression


def inventory_dict(rows) -> dict:
    """[{'element','stars','quantity'}] -> {(element, stars): qty} (zero stacks dropped)."""
    return {(r["element"], int(r["stars"])): int(r["quantity"]) for r in rows if int(r["quantity"]) > 0}


def food_groups(inv: dict) -> list[tuple[str, list[tuple[int, int]]]]:
    """[(element, [(stars, qty), ...])] in the game's element order, lowest star first."""
    by_element: dict[str, list] = defaultdict(list)
    for (element, stars), qty in inv.items():
        by_element[element].append((stars, qty))
    order = {e: i for i, e in enumerate(gacha.ELEMENTS)}
    return [
        (element, sorted(entries))
        for element, entries in sorted(by_element.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
    ]


# ------------------------------------------------------------ hero needs ----

def best_copies(owned: list[dict]) -> list[dict]:
    """One entry per hero: its strongest copy (highest stars, then level, then oldest)."""
    best: dict[int, dict] = {}
    for w in owned:
        key = int(w["warrior_id"])
        rank = (int(w["stars"]), int(w["level"]), -int(w["id"]))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, w)
    return [w for _, w in best.values()]


def hero_needs(owned: list[dict], inv: dict) -> list[dict]:
    """Food each hero's NEXT star-up asks for, and whether you have it.

    Each item: {name, element, stars, target, entries:[(kind, element, stars, need, have)], ok}
    kind is 'same' (that hero's element) or 'any' (any real element).
    """
    needs = []
    for w in best_copies(owned):
        stars = int(w["stars"])
        if stars >= progression.character_max_stars(int(w.get("base_stars", 4))):
            continue
        req = progression.tier_requirements(stars, stars + 1)
        if not req:
            continue
        entries = []
        for s, count in sorted(req.get("food_same", {}).items(), reverse=True):
            entries.append(("same", w["element"], int(s), int(count), inv.get((w["element"], int(s)), 0)))
        for s, count in sorted(req.get("food_any_element", {}).items(), reverse=True):
            have = sum(q for (_, st), q in inv.items() if st == int(s))
            entries.append(("any", None, int(s), int(count), have))
        if entries:
            needs.append({
                "name": w["name"], "element": w["element"], "stars": stars, "target": stars + 1,
                "entries": entries, "ok": all(have >= need for *_, need, have in entries),
            })
    needs.sort(key=lambda n: (-n["stars"], n["name"].lower()))
    return needs


def compute_reserve(needs: list[dict], inv: dict) -> dict:
    """Food to hold back so mass star-up doesn't eat what heroes need next.

    Same-element needs are reserved on their exact stack; any-element needs are
    taken greedily from whichever elements have the most spare at that tier.
    """
    reserve: dict = defaultdict(int)
    for hero in needs:
        for kind, element, stars, need, _ in hero["entries"]:
            if kind == "same":
                reserve[(element, stars)] += need
    for hero in needs:
        for kind, _, stars, need, _ in hero["entries"]:
            if kind != "any":
                continue
            remaining = need
            stacks = sorted(
                ((e, q - reserve[(e, st)]) for (e, st), q in inv.items() if st == stars),
                key=lambda item: -item[1],
            )
            for element, spare in stacks:
                if remaining <= 0:
                    break
                take = min(remaining, max(spare, 0))
                if take:
                    reserve[(element, stars)] += take
                    remaining -= take
    return {k: min(v, inv.get(k, 0)) for k, v in reserve.items() if v > 0}


# ------------------------------------------------------------ mass upgrade ----

def plan_mass_star_up(inv: dict, cost: int = 5, max_stars: int = 20, reserve: dict | None = None) -> dict:
    """Exactly what database.mass_star_up_food would do (same order, same chaining)."""
    reserve = reserve or {}
    stock = dict(inv)
    summary = []
    spent_total = created_total = 0
    for star in range(4, int(max_stars)):
        for element in sorted(e for (e, s) in stock if s == star):
            available = max(stock[(element, star)] - reserve.get((element, star), 0), 0)
            upgrades = available // cost
            if upgrades <= 0:
                continue
            stock[(element, star)] -= upgrades * cost
            stock[(element, star + 1)] = stock.get((element, star + 1), 0) + upgrades
            spent_total += upgrades * cost
            created_total += upgrades
            summary.append({"element": element, "from_stars": star, "to_stars": star + 1, "upgrades": upgrades})
    return {
        "total_upgrades": created_total, "food_spent": spent_total, "food_created": created_total,
        "summary": summary, "after": {k: v for k, v in stock.items() if v > 0},
    }


# ------------------------------------------------------------------- melt ----

def spare_copy_ids(meltable: list[dict], owned: list[dict]) -> set[int]:
    """Meltable copies that are NOT the best copy of their hero (i.e. true duplicates)."""
    keep = {int(w["id"]) for w in best_copies(owned)}
    return {int(w["id"]) for w in meltable if int(w["id"]) not in keep}


def analyze_melt(selected_ids: set[int], owned: list[dict]) -> dict:
    """Preview of melting these copies: food yield plus things worth a warning."""
    by_id = {int(w["id"]): w for w in owned}
    chosen = [by_id[i] for i in sorted(selected_ids) if i in by_id]
    yields: dict = defaultdict(int)
    for w in chosen:
        yields[(w["element"], int(w["stars"]))] += 1

    warnings = []
    copies_of: dict[int, set] = defaultdict(set)
    for w in owned:
        copies_of[int(w["warrior_id"])].add(int(w["id"]))
    chosen_ids = {int(w["id"]) for w in chosen}
    last_copies = sorted({w["name"] for w in chosen if copies_of[int(w["warrior_id"])] <= chosen_ids})
    if last_copies:
        warnings.append("Last copy of: " + ", ".join(last_copies))
    invested = [f"{w['name']} Lv{w['level']}" for w in chosen if int(w["level"]) > 1]
    if invested:
        warnings.append("Levels are lost: " + ", ".join(invested[:6]) + (f" (+{len(invested) - 6} more)" if len(invested) > 6 else ""))
    upgraded = [f"{w['name']} {w['stars']}★" for w in chosen if int(w["stars"]) > int(w.get("base_stars", w["stars"]))]
    if upgraded:
        warnings.append("Already starred up: " + ", ".join(upgraded[:6]) + (f" (+{len(upgraded) - 6} more)" if len(upgraded) > 6 else ""))
    return {"count": len(chosen), "yields": dict(yields), "warnings": warnings, "names": [w["name"] for w in chosen]}
