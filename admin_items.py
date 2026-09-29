"""Owner-only item granting logic for /admin give-item (no Discord imports).

Everything a player can actually hold - Lab food, Memory Stones, shop avatars,
and generic inventory items - is resolved from one text box, so the command can
offer a proper autocomplete list and typos can't quietly create junk items.
"""
import database
import gacha

MEMORY_NAMES = {"memory stones", "memory stone", "memory", "stones"}
FOOD_STARS_MIN, FOOD_STARS_MAX = 4, 20
MAX_NEW_ITEM_NAME = 60


def _clean(text: str) -> str:
    return " ".join((text or "").strip().split())


def suggestions(current: str, avatars: list[dict], custom_names: list[str]) -> list[str]:
    """Autocomplete entries: Memory Stones, every Food element, avatars, existing items."""
    options = ["Memory Stones"]
    options += [f"Food: {element}" for element in gacha.ELEMENTS]
    options += [f"Avatar: {a['name']}" for a in avatars]
    options += sorted({n for n in custom_names if n}, key=str.lower)
    q = _clean(current).lower()
    if not q:
        return options
    starts = [o for o in options if o.lower().startswith(q)]
    contains = [o for o in options if q in o.lower() and o not in starts]
    return starts + contains


def resolve_giveable(text: str, avatars: list[dict], custom_names: list[str]) -> dict:
    """Work out what an admin typed. Returns a dict with a 'kind':
    memory | food | avatar | item | error."""
    t = _clean(text)
    low = t.lower()
    if not t:
        return {"kind": "error", "reason": "Type or pick an item."}

    if low in MEMORY_NAMES:
        return {"kind": "memory"}

    # Avatars: "Avatar: Flame Emblem", "Flame Emblem", or the raw id.
    avatar_query = low[len("avatar:"):].strip() if low.startswith("avatar:") else low
    for a in avatars:
        if avatar_query in (a["id"].lower(), a["name"].lower()):
            return {"kind": "avatar", "avatar": a}
    if low.startswith("avatar:"):
        names = ", ".join(a["name"] for a in avatars)
        return {"kind": "error", "reason": f"No avatar called **{t[7:].strip()}**. Avatars: {names}."}

    # Food: "Food: Blaze", "Blaze Food", or just the element.
    words = [w for w in low.replace(":", " ").replace("-", " ").split() if w]
    remainder = " ".join(w for w in words if w != "food")
    for element in gacha.ELEMENTS:
        if remainder == element.lower():
            return {"kind": "food", "element": element}
    if "food" in words:
        return {"kind": "error", "reason": f"Unknown food element **{remainder or '(none)'}**. Elements: {', '.join(gacha.ELEMENTS)}."}

    for name in custom_names:
        if name and name.lower() == low:
            return {"kind": "item", "name": name, "existing": True}
    return {"kind": "item", "name": t, "existing": False}


def give(user_id: int, spec: dict, amount: int, stars: int = 4, item_type: str = "item", create_new: bool = False) -> dict:
    """Perform the grant. Returns {"ok": bool, "message": str}."""
    kind = spec["kind"]
    amount = int(amount)
    if kind == "error":
        return {"ok": False, "message": spec["reason"]}
    if amount < 1:
        return {"ok": False, "message": "Amount must be at least 1."}

    if kind == "memory":
        total = database.add_memory_stones(user_id, amount)
        return {"ok": True, "message": f"Gave **{amount:,}× Memory Stones**. They now have **{total:,}**."}

    if kind == "food":
        stars = int(stars)
        if not FOOD_STARS_MIN <= stars <= FOOD_STARS_MAX:
            return {"ok": False, "message": f"Food stars must be {FOOD_STARS_MIN}-{FOOD_STARS_MAX}."}
        database.add_food(user_id, spec["element"], stars, amount)
        total = database.get_food_quantity(user_id, spec["element"], stars)
        return {"ok": True, "message": f"Gave **{amount:,}× {stars}★ {spec['element']} Food**. They now have **{total:,}**."}

    if kind == "avatar":
        a = spec["avatar"]
        if database.user_owns_avatar(user_id, a["id"]):
            return {"ok": False, "message": f"They already own the **{a['name']}** avatar."}
        database.grant_avatar(user_id, a["id"])
        return {"ok": True, "message": f"Unlocked the **{a['name']}** avatar for them."}

    # generic item
    name = spec["name"]
    if not spec.get("existing") and not create_new:
        return {
            "ok": False,
            "message": (
                f"There's no existing item called **{name}**. Pick one from the suggestion list, "
                f"or set `create_new` to True if you really want to create it."
            ),
        }
    if len(name) > MAX_NEW_ITEM_NAME:
        return {"ok": False, "message": f"Item names can be at most {MAX_NEW_ITEM_NAME} characters."}
    result = database.add_item(user_id, name, amount, item_type=item_type)
    if not result.get("ok"):
        return {"ok": False, "message": f"Could not add that item: {result.get('reason', 'unknown error')}."}
    verb = "Gave" if spec.get("existing") else "Created and gave"
    return {"ok": True, "message": f"{verb} **{amount:,}× {result['item_name']}**. They now have **{result['quantity']:,}**."}
