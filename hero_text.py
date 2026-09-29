"""Player-facing text for heroes: effective stats, plain-English skill and
passive descriptions, progress bars. Pure Python (no Discord) so it's testable.

Skill text is generated from the same effect strings the battle engine runs, so
it can never drift out of sync with what a skill actually does.
"""
from __future__ import annotations

import battle
import progression
from skill_system import EffectSpec, parse_effects, legacy_effects

ELEMENT_EMOJI = {"Blaze": "🔥", "Teal": "💧", "Rust": "⛰️", "Drift": "🌪️", "Dawn": "☀️", "Shade": "🌑"}
STAT_NAMES = {"atk": "ATK", "def": "DEF", "matk": "MATK", "mdef": "MDEF", "speed": "Speed"}

TARGET_TEXT = {
    "enemy": "a random enemy",
    "all_enemies": "all enemies",
    "two_enemies": "2 enemies",
    "three_enemies": "3 enemies",
    "four_enemies": "4 enemies",
    "five_enemies": "5 enemies",
    "self": "self",
    "ally": "the lowest-HP ally",
    "all_allies": "all allies",
    "fallen_ally": "a fallen ally",
    "frontline": "a front-row enemy (bypasses Taunt)",
    "backline": "a back-row enemy (bypasses Taunt)",
    "all_frontline": "the whole front row",
    "all_backline": "the whole back row",
    "all_midback": "everyone in the middle and back rows",
    "lowest_backline": "the lowest-HP back-row enemy (bypasses Taunt)",
    "mirror": "the enemy in your mirror slot",
    "same_slot": "the enemy in your mirror slot",
}


ELEMENT_COLOR = {"Blaze": 0xE8552D, "Teal": 0x2DA8C9, "Rust": 0xB5651D, "Drift": 0x7FD1AE, "Dawn": 0xF2C94C, "Shade": 0x6D4BA0}


def element_color(element: str) -> int:
    return ELEMENT_COLOR.get(str(element), 0x8E6BBF)


def _poss(text: str) -> str:
    """'this hero' -> "this hero's", '2 enemies' -> "2 enemies'"."""
    return text + ("'" if text.endswith("s") else "'s")


def element_icon(element: str) -> str:
    return ELEMENT_EMOJI.get(str(element), "✨")


def progress_bar(current: int, total: int, width: int = 10) -> str:
    total = max(int(total), 1)
    filled = round(width * max(0, min(int(current), total)) / total)
    return "▰" * filled + "▱" * (width - filled)


def _pct(value) -> str:
    try:
        return f"{round(float(value) * 100):g}%"
    except (TypeError, ValueError):
        return str(value)


def _turns(n) -> str:
    n = int(n)
    return f"{n} turn" + ("" if n == 1 else "s")


# ---------------------------------------------------------------- stats ----

def effective_stats(warrior: dict, level: int | None = None, stars: int | None = None) -> dict:
    """Stats exactly as the battle engine will scale them for this hero."""
    level = int(level if level is not None else warrior.get("level", 1) or 1)
    origin = int(warrior.get("base_stars") or warrior.get("stars") or 4)
    stars = max(4, int(stars if stars is not None else warrior.get("stars", 4) or 4))
    mult = progression.stat_multiplier(level, stars, origin)
    stats = {
        "hp": max(1, int(warrior["base_hp"] * mult)),
        "atk": max(1, int(warrior["base_atk"] * mult)),
        "def": max(1, int(warrior["base_def"] * mult)),
        "matk": max(1, int(warrior["base_matk"] * mult)),
        "mdef": max(1, int(warrior["base_mdef"] * mult)),
        "speed": max(1, int(warrior["base_speed"] * progression.speed_multiplier(level, stars, origin))),
    }
    chance, damage = battle._normalize_crit(warrior.get("crit_chance", 0.05), warrior.get("crit_damage", 1.5))
    stats["crit_chance"] = chance
    stats["crit_damage"] = damage
    stats["multiplier"] = mult
    return stats


def battle_power(stats: dict) -> int:
    """One number for how strong a hero is (BP). Uses the higher of ATK/MATK as
    the main damage stat, plus a quarter of the other, then HP, defenses, speed
    and crit. Reference: a fresh 4-star agile attacker is roughly 900 BP."""
    high, low = max(stats["atk"], stats["matk"]), min(stats["atk"], stats["matk"])
    offense = high + 0.25 * low
    defense = (stats["def"] + stats["mdef"]) / 2
    crit = 1 + stats["crit_chance"] * (stats["crit_damage"] - 1)
    return int(round((2.0 * offense + 0.35 * stats["hp"] + 2.0 * defense + 1.5 * stats["speed"]) * crit))


def strongest_heroes(warriors: list[dict], count: int = 6) -> list[tuple[int, dict]]:
    """[(BP, warrior)] for your `count` strongest DIFFERENT heroes (best copy of each)."""
    best: dict = {}
    for w in warriors:
        key = int(w["warrior_id"])
        rank = (int(w["stars"]), int(w["level"]), -int(w["id"]))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, w)
    scored = [(battle_power(effective_stats(w)), w) for _, w in best.values()]
    scored.sort(key=lambda item: (-item[0], -int(item[1]["stars"]), str(item[1]["name"]).lower()))
    return scored[:count]


def stat_block(stats: dict) -> str:
    """Two-column monospace stat table for an embed field."""
    rows = [
        ("HP", stats["hp"], "ATK", stats["atk"]),
        ("DEF", stats["def"], "MATK", stats["matk"]),
        ("MDEF", stats["mdef"], "SPD", stats["speed"]),
    ]
    lines = [f"{a:<5}{av:>8,}   {b:<5}{bv:>8,}" for a, av, b, bv in rows]
    lines.append(f"{'CRIT':<5}{stats['crit_chance'] * 100:>7.0f}%   {'CDMG':<5}{stats['crit_damage'] * 100:>7.0f}%")
    return "```\n" + "\n".join(lines) + "\n```"


# --------------------------------------------------------------- skills ----

def _condition_text(effect: EffectSpec) -> str:
    c, v = effect.condition, effect.condition_value
    if not c:
        return ""
    if c == "chance":
        return f" ({_pct(v if v is not None else 0.3)} chance)"
    if c == "if_marked":
        return " (if the target is marked)"
    if c == "if_status":
        return f" (if the target has {v})"
    if c == "if_not_status":
        return f" (if the target doesn't have {v})"
    if c in ("if_hp_below", "if_target_hp_below"):
        return f" (if the target is below {_pct(v if v is not None else 0.5)} HP)"
    if c == "if_self_hp_below":
        return f" (if this hero is below {_pct(v if v is not None else 0.5)} HP)"
    if c == "if_kill":
        return " (if it defeats the target)"
    return ""


def describe_effect(effect: EffectSpec, default_target: str = "enemy") -> str:
    kind = effect.kind
    value = effect.value
    target_key = effect.target or default_target or "enemy"
    who = TARGET_TEXT.get(target_key, target_key)
    own = "this hero" if target_key == "self" else who
    suffix = _condition_text(effect)

    if kind == "damage":
        text = f"Deal **{_pct(value if value is not None else 1)}** damage to {who}"
    elif kind == "damage_atk":
        text = f"Deal **{_pct(value if value is not None else 1)}** ATK damage to {who}"
    elif kind == "multi_hit":
        text = f"Strike {who} **{int(value or 2)}** times for {_pct(effect.duration or 1.0)} damage each"
    elif kind == "heal":
        text = f"Heal {own} for **{_pct(value if value is not None else 1)}** of this hero's MATK"
    elif kind == "shield":
        text = f"Give {own} a shield worth **{_pct(value if value is not None else 0.25)}** of max HP"
    elif kind.startswith(("buff_", "debuff_")):
        stat = STAT_NAMES.get(kind.split("_", 1)[1], kind.split("_", 1)[1].upper())
        verb = "Raise" if kind.startswith("buff_") else "Lower"
        length = f" for {_turns(effect.duration)}" if effect.duration else (" for the whole fight" if kind.startswith("buff_") else "")
        text = f"{verb} {_poss(own)} {stat} by **{_pct(value)}**{length}"
    elif kind in ("stun", "silence", "confusion", "petrify"):
        text = f"{kind.title()} {who} for {_turns(value or 1)}"
    elif kind == "taunt":
        text = f"Force enemies to target {own} for {_turns(value or 1)}"
    elif kind == "slow":
        text = f"Slow {who} by **{_pct(value if value is not None else 0.2)}** for {_turns(effect.duration or 2)}"
    elif kind == "cleanse":
        text = f"Remove negative effects from {own}"
    elif kind == "mark":
        text = f"Mark {who} for {_turns(value or 2)}"
    elif kind == "lifesteal":
        text = f"Heal for **{_pct(value)}** of damage dealt"
    elif kind == "lifesteal_total":
        text = f"Heal for **{_pct(value)}** of all damage dealt by this skill"
    elif kind == "dot":
        text = f"Deal damage over time to {who} ({_pct(value if value is not None else 0.1)} per turn, {_turns(effect.duration or 2)})"
    elif kind == "hot":
        text = f"Heal {own} over time ({_pct(value if value is not None else 0.1)} per turn, {_turns(effect.duration or 2)})"
    elif kind == "revive":
        text = f"Revive a fallen ally with **{_pct(value if value is not None else 0.3)}** HP"
    elif kind == "burn":
        n = int(value or 1)
        text = f"Apply **{n}** Burn stack{'s' if n != 1 else ''} to {who}"
    elif kind == "consume_resolve":
        text = f"Spend **{int(value or 0)}** Resolve"
    elif kind == "enter_lantern":
        text = f"Enter Lantern Mode for {_turns(value or 2)}"
    elif kind == "hp_cost":
        text = f"Sacrifice **{_pct(value)}** of max HP"
    elif kind == "blood_debt":
        n = int(value or 1)
        text = f"Gain **{n}** Blood Debt"
    elif kind == "flamebound":
        n = int(value or 1)
        text = f"Gain **{n}** Flamebound stack{'s' if n != 1 else ''}"
    else:
        shown = "" if value is None else f" {value}"
        text = f"{kind.replace('_', ' ').title()}{shown} → {who}"
    return text + suffix


def skill_effects(warrior: dict, number: int) -> list[EffectSpec]:
    raw = warrior.get(f"skill{number}_effects")
    if raw and str(raw).strip():
        return parse_effects(raw)
    legacy = legacy_effects(warrior.get(f"skill{number}_type"), warrior.get(f"skill{number}_power", 1.0), warrior.get(f"skill{number}_target", "enemy"))
    return parse_effects(legacy)


def describe_skill(warrior: dict, number: int) -> tuple[str, list[str]]:
    """(skill name, description lines) for skill 1-3."""
    name = str(warrior.get(f"skill_{number}") or "").strip()
    default_target = warrior.get(f"skill{number}_target") or "enemy"
    lines = [describe_effect(e, default_target) for e in skill_effects(warrior, number)]
    return name, lines


def skill3_rule(warrior: dict) -> str:
    cond = warrior.get("skill3_condition")
    val = warrior.get("skill3_value")
    try:
        val = float(val) if val not in (None, "") else None
    except (TypeError, ValueError):
        val = None
    if not cond:
        return "Used every 3rd round."
    if cond == "resolve_min":
        return f"Every 3rd round, if Resolve is at least {int(val or 0)} (otherwise Skill 1)."
    if cond == "turn_min":
        return f"Every 3rd round from round {int(val or 1)} on (otherwise Skill 1)."
    if cond == "chance":
        return f"Every 3rd round with a {_pct(val if val is not None else 0.3)} chance (otherwise Skill 1)."
    if cond == "hp_below":
        return f"Every 3rd round while below {_pct(val if val is not None else 0.5)} HP (otherwise Skill 1)."
    if cond == "petrify_since_ultimate":
        return "Every 3rd round, but only after a Petrify has landed since it was last used (otherwise Skill 1)."
    if cond == "auto_only":
        return "Not part of the normal rotation - it triggers automatically (see the passive)."
    return "Every 3rd round, if its condition is met (otherwise Skill 1)."


def rotation_text() -> str:
    return "Turn order repeats **Skill 1 → Skill 2 → Skill 3**."


# -------------------------------------------------------------- passive ----

PASSIVE_TEXT = {
    "resolve_on_burn": "Whenever any source applies a Burn stack, gain **{power}** Resolve (max 100).",
    "blood_debt": (
        "Blood Debt has no cap - every stack adds **+7%** damage, and it keeps building the longer the fight runs. "
        "Below 60% HP: **20%** less damage taken. "
        "Ending a turn below 60% HP with 3+ stacks automatically unleashes Skill 3 (uses 3 stacks, keeps the rest)."
    ),
    "phoenix_wrath": (
        "Whenever Burn is applied to an enemy, immediately use Skill 1 on it as an Extra Action "
        "(up to **{power}** per turn, +1 for each Flamebound stack). Gains Flamebound whenever an enemy falls."
    ),
}


def describe_passive(warrior: dict) -> tuple[str, str]:
    """(passive name, text). Text is '' when the hero has no passive yet."""
    name = str(warrior.get("passive") or "").strip()
    ptype = str(warrior.get("passive_type") or "").strip()
    power = warrior.get("passive_power")
    try:
        power_text = f"{float(power):g}"
    except (TypeError, ValueError):
        power_text = str(power or "")

    if ptype in PASSIVE_TEXT:
        return name, PASSIVE_TEXT[ptype].format(power=power_text)

    raw = warrior.get("passive_effects")
    if raw and str(raw).strip():
        parts = [describe_effect(e, "self") for e in parse_effects(raw)]
        return name, "At the start of battle: " + "; ".join(parts) + "."
    if ptype:
        legacy = parse_effects(legacy_effects(ptype, power, warrior.get("passive_target") or "self"))
        if legacy:
            return name, "At the start of battle: " + "; ".join(describe_effect(e, "self") for e in legacy) + "."
    return name, ""
