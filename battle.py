"""Turn-based battle engine with modular, data-driven skill effects.

Party heroes cycle Skill 1 -> Skill 2 -> Skill 3 -> repeat. Skill 3 may have the
existing CSV gate. Each skill can now contain multiple effects in a compact CSV
string, for example:
    damage:1.8|mark:2|stun:1@if_marked

Supported control effects include Stun, Silence, Taunt, Confusion, and Petrify.
"""

from __future__ import annotations

import random

import gacha
from config import CRIT_DAMAGE_BASE, BURN_STACK_RATIO, SOUL_BURN_STACK_RATIO, DEFAULT_BUFF_TURNS, HEAL_SCALE, MIN_DAMAGE_RATIO, LEGACY_BOSS_SCALE, PARTY_ROW_BY_SLOT
from skill_system import EffectSpec, parse_effects, legacy_effects, SUPPORTED_EFFECTS
from progression import stat_multiplier, speed_multiplier

MAX_ROUNDS = 50
VALID_STATS = {"atk", "def", "matk", "mdef", "speed"}
HARD_CONTROL = {"stun", "silence", "confusion", "petrify"}

# Corvayne's Tear debuff: stacking, applied by Omen Feather. Each stack shreds
# the target's effective DEF/MDEF and the healing it receives; stacks cap so it
# can't spiral into negating healing entirely. Her Storm Perch passive keys off
# the stack count (see _tear_stacks / _compute_damage / _compute_atk_damage).
TEAR_MAX_STACKS = 5
TEAR_DEF_SHRED_PER_STACK = 0.04   # -4% DEF/MDEF per stack (max -20% at 5 stacks)
TEAR_HEAL_REDUCTION_PER_STACK = 0.02  # -2% healing received per stack (max -10% at 5 stacks)
TEAR_DEFAULT_DURATION = 2
CORVAYNE_STORM_PERCH_THRESHOLD = 2
CORVAYNE_STORM_PERCH_BONUS = 0.20

# Slow (first used by Nahilra, now stacking for Sana's Sunken Tendrils): each
# application adds a stack instead of just refreshing. Total speed reduction
# is stacks * per-stack power (the "value" field on the slow effect), capped
# by SLOW_MAX_REDUCTION as a safety backstop. A hero that only ever applies
# Slow once (Nahilra) behaves exactly as before - 1 stack at her configured
# power - so this is purely additive for anything else that stacks it.
SLOW_MAX_STACKS = 5
SLOW_MAX_REDUCTION = 0.6

# Cooldown-based skill system (currently used by Sana; a general engine
# feature, not tied to her specifically - any future hero can opt in by
# setting skillN_cooldown in heroes.csv). Heroes with no cooldown values set
# keep using the classic S1->S2->S3 round-robin in _pick_skill untouched.
DEFAULT_SKILL_COOLDOWN = 2

# Multi-target selectors: name -> how many enemies they hit.
MULTI_ENEMY_TARGETS = {"two_enemies": 2, "three_enemies": 3, "four_enemies": 4, "five_enemies": 5}

# Rows, front to back. A skill written as "frontline" or "backline" targets
# whichever row is populated closest to that end; if that whole row has
# fallen, it reaches into the next row rather than doing nothing.
ROW_ORDER = ["front", "middle", "back"]


def _row_of(combatant: dict) -> str:
    return PARTY_ROW_BY_SLOT.get(int(combatant.get("position", 0)), "middle")


def _row_group(hostile: list, row: str) -> list:
    """Everyone alive in `row`, falling back toward the opposite end of the
    formation (frontline reaches back, backline reaches forward) if that row
    is empty - so a positional skill never just fizzles because one row died."""
    order = ROW_ORDER if row == "front" else list(reversed(ROW_ORDER)) if row == "back" else None
    rows_to_try = order or [row]
    for candidate in rows_to_try:
        found = [c for c in hostile if _row_of(c) == candidate]
        if found:
            return found
    return hostile  # formation data missing/inconsistent - never just whiff


def _rows_group(hostile: list, rows: set) -> list:
    """Everyone alive in any of `rows`; falls back to everyone hostile if that
    whole combination has fallen (e.g. mid+back both wiped)."""
    found = [c for c in hostile if _row_of(c) in rows]
    return found or hostile


def _status_dict(combatant: dict) -> dict:
    return combatant.setdefault("statuses", {})


def _status_duration(combatant: dict, name: str) -> int:
    data = _status_dict(combatant).get(name)
    if not data:
        return 0
    if isinstance(data, int):
        return data
    return int(data.get("duration", 0))


def _set_status(combatant: dict, name: str, duration: int, power=None, *, grace: bool = False, **extra):
    """Apply/refresh a status.

    grace=True means "don't count the turn it was applied on": if the combatant
    is applying this to itself during its own turn (self-taunt, self-buff), the
    end-of-turn tick that happens moments later would otherwise eat one turn of
    the duration before anyone else even got to act.
    """
    duration = max(int(duration), 0)
    if duration <= 0:
        return
    statuses = _status_dict(combatant)
    current_duration = _status_duration(combatant, name)
    # Reapplying a status refreshes it, but never accidentally reduces an
    # already-longer duration.
    final_duration = max(current_duration, duration)
    data = {"duration": final_duration, "power": power}
    data.update(extra)
    if grace and combatant.get("_acting"):
        data["fresh"] = True
    statuses[name] = data


def _revert_stat_mod(combatant: dict, data) -> None:
    """Undo a timed buff/debuff's stat change once the status goes away."""
    if isinstance(data, dict) and data.get("stat") and data.get("factor"):
        combatant[data["stat"]] = combatant[data["stat"]] / float(data["factor"])


def _remove_status(combatant: dict, name: str) -> bool:
    data = _status_dict(combatant).pop(name, None)
    _revert_stat_mod(combatant, data)
    return data is not None


def _has_status(combatant: dict, name: str) -> bool:
    return _status_duration(combatant, name) > 0


def _tear_stacks(combatant: dict) -> int:
    status = _status_dict(combatant).get("tear")
    return int(status.get("stacks", 0)) if isinstance(status, dict) else 0


def _tick_status(combatant: dict, name: str):
    statuses = _status_dict(combatant)
    data = statuses.get(name)
    if not data:
        return
    if isinstance(data, dict) and data.pop("fresh", False):
        return  # applied this very turn - the first tick doesn't count
    duration = _status_duration(combatant, name) - 1
    if duration <= 0:
        statuses.pop(name, None)
        _revert_stat_mod(combatant, data)
    else:
        if isinstance(data, int):
            statuses[name] = duration
        else:
            data["duration"] = duration


def _apply_stat_mod(actor: dict, target: dict, kind: str, amount: float, duration, skill_name: str, log: list, permanent: bool = False):
    """Shared buff_*/debuff_* implementation.

    Skill buffs/debuffs with no explicit duration last DEFAULT_BUFF_TURNS turns
    (only passives are permanent). Casting the same buff again REFRESHES it
    instead of stacking, so a repeated Stone Wall or Silent Storm can't snowball
    a stat to absurd values. Timed mods are reverted on expiry.
    """
    stat = kind.split("_", 1)[1]
    if stat not in VALID_STATS:
        return
    is_buff = kind.startswith("buff_")
    if not duration and not permanent:
        duration = DEFAULT_BUFF_TURNS
    if duration:
        existing = _status_dict(target).get(kind)
        if isinstance(existing, dict) and existing.get("factor"):
            _set_status(target, kind, duration, amount, grace=True, stat=stat, factor=existing["factor"])
            log.append(f"🔁 **{actor['name']}** uses **{skill_name}** — refreshes {stat.upper()} {'boost' if is_buff else 'reduction'} on **{target['name']}** ({duration} turn(s))")
            return
    factor = (1 + amount) if is_buff else max(1 - amount, 0.1)
    target[stat] *= factor
    if duration:
        _set_status(target, kind, duration, amount, grace=True, stat=stat, factor=factor)
    icon = "🔺" if is_buff else "🔻"
    verb = "boosts" if is_buff else "weakens"
    duration_text = f" for {duration} turn(s)" if duration else " permanently"
    log.append(f"{icon} **{actor['name']}** uses **{skill_name}** — {verb} **{target['name']}**'s {stat.upper()} by {int(amount * 100)}%{duration_text}")


def _normalize_crit(chance, damage):
    """Turn heroes.csv crit numbers into battle-ready values.

    heroes.csv authors crit as whole-number stat points (e.g. crit_chance 12,
    crit_damage 33). Battle math wants a 0-1 chance and a damage multiplier, so:
      - chance >= 1 is read as a percent (12 -> 0.12); 0-1 values pass through
      - damage >= 3 is read as a TOTAL percent (150 -> 1.5x, 183 -> 1.83x);
        values below 3 are already a multiplier (the 1.5 default and the legacy boss).
    """
    chance = float(chance or 0)
    if chance >= 1:
        chance /= 100.0
    chance = max(0.0, min(chance, 1.0))
    damage = float(damage or 0)
    if damage >= 3:
        damage = damage / 100.0
    elif damage < 1:
        damage = CRIT_DAMAGE_BASE
    return chance, damage


def _make_skill(name, effects_raw=None, legacy_type=None, legacy_power=1.0, target="enemy", condition=None, condition_value=None, cooldown=None):
    if effects_raw and effects_raw.strip():
        effects = parse_effects(effects_raw)
    else:
        effects = parse_effects(legacy_effects(legacy_type, legacy_power, target)) if legacy_type else []
    return {
        "name": name,
        "target": target,
        "effects_raw": effects_raw or "",
        "effects": effects,
        "condition": condition,
        "condition_value": condition_value,
        "cooldown": int(cooldown) if cooldown else None,
    }


def _make_party_combatant(position: int, warrior: dict) -> dict:
    level = int(warrior.get("level", 1) or 1)
    tier = max(4, int(warrior.get("stars", warrior.get("tier", 4)) or 4))
    # Growth counts star-ups from the hero's summon (origin) star; catalog heroes
    # (enemies) have no base_stars and simply sit at their own stars.
    origin = int(warrior.get("base_stars") or tier)
    mult = stat_multiplier(level, tier, origin)
    speed_mult = speed_multiplier(level, tier, origin)
    scaled = {
        "hp": max(1, int(warrior["base_hp"] * mult)),
        "atk": max(1, int(warrior["base_atk"] * mult)),
        "def": max(1, int(warrior["base_def"] * mult)),
        "matk": max(1, int(warrior["base_matk"] * mult)),
        "mdef": max(1, int(warrior["base_mdef"] * mult)),
        "speed": max(1, int(warrior["base_speed"] * speed_mult)),
    }
    crit_chance, crit_damage = _normalize_crit(warrior.get("crit_chance", 0.05), warrior.get("crit_damage", 1.5))
    return {
        "side": "party",
        "position": position,
        "name": warrior["name"],
        "hero_key": warrior.get("hero_key"),
        "element": warrior["element"],
        "level": level,
        "tier": tier,
        "hp": scaled["hp"],
        "max_hp": scaled["hp"],
        "atk": scaled["atk"],
        "def": scaled["def"],
        "matk": scaled["matk"],
        "mdef": scaled["mdef"],
        "speed": scaled["speed"],
        "crit_chance": crit_chance,
        "crit_damage": crit_damage,
        "resolve": 0,
        "lantern_mode": False,
        "blood_debt": 0,
        "_blood_debt_damage_override": None,
        "flamebound_stacks": 0,
        "extra_actions_used": 0,
        "petrify_since_ultimate": False,
        "statuses": {},
        "skills": [
            _make_skill(warrior["skill_1"], warrior.get("skill1_effects"), warrior.get("skill1_type"), warrior.get("skill1_power", 1.0), warrior.get("skill1_target", "enemy"), cooldown=warrior.get("skill1_cooldown")),
            _make_skill(warrior["skill_2"], warrior.get("skill2_effects"), warrior.get("skill2_type"), warrior.get("skill2_power", 1.0), warrior.get("skill2_target", "enemy"), cooldown=warrior.get("skill2_cooldown")),
            _make_skill(warrior["skill_3"], warrior.get("skill3_effects"), warrior.get("skill3_type"), warrior.get("skill3_power", 1.0), warrior.get("skill3_target", "enemy"), warrior.get("skill3_condition"), warrior.get("skill3_value"), cooldown=warrior.get("skill3_cooldown")),
        ],
        "skill_index_offset": 0,
        "cooldowns": [0, 0, 0],
        "passive": {
            "name": warrior["passive"],
            "type": warrior.get("passive_type"),
            "power": warrior.get("passive_power", 0.0),
            "effects": (parse_effects(warrior.get("passive_effects")) if warrior.get("passive_effects") else (parse_effects(legacy_effects(warrior.get("passive_type"), warrior.get("passive_power", 0), warrior.get("passive_target", "self"))) if warrior.get("passive_type") and warrior.get("passive_type") != "resolve_on_burn" else [])),
            "target": warrior.get("passive_target", "self"),
        },
    }


def _make_enemy_combatant(position: int, warrior: dict) -> dict:
    """Build an enemy from the same hero catalog used by player warriors.

    Enemy level comes from the stage definition, not from the player's owned
    copy, so the designer can freely tune stage difficulty.
    """
    combatant = _make_party_combatant(position, warrior)
    combatant["side"] = "enemy"
    combatant["enemy_slot"] = warrior.get("enemy_slot", position)
    return combatant


def _make_legacy_boss_combatant(chapter: dict) -> dict:
    """Compatibility fallback for old chapters with no enemy1..enemy6 team."""
    warrior = {
        "name": chapter["boss_name"], "element": chapter["boss_element"],
        "base_hp": chapter["boss_hp"] * LEGACY_BOSS_SCALE["hp"],
        "base_atk": chapter["boss_atk"] * LEGACY_BOSS_SCALE["power"],
        "base_def": chapter["boss_def"] * LEGACY_BOSS_SCALE["power"],
        "base_matk": chapter["boss_matk"] * LEGACY_BOSS_SCALE["power"],
        "base_mdef": chapter["boss_mdef"] * LEGACY_BOSS_SCALE["power"],
        "base_speed": chapter["boss_speed"] * LEGACY_BOSS_SCALE["speed"],
        "crit_chance": 0.05, "crit_damage": 1.5, "level": 1,
        "skill_1": "Basic Attack", "skill_2": "Basic Attack",
        "skill_3": "Basic Attack", "passive": "",
        # Without these the boss had no effects at all and never acted.
        "skill1_effects": "damage:1.0:enemy",
        "skill2_effects": "damage:1.0:enemy",
        "skill3_effects": "damage:1.0:enemy",
    }
    c = _make_party_combatant(1, warrior)
    c["side"] = "enemy"
    c["position"] = 1
    return c

def _corvayne_storm_perch_mult(attacker: dict, tear_stacks: int) -> float:
    """Corvayne's Storm Perch passive: +20% damage to a target carrying 2+
    Tear stacks (Omen Feather is her Tear source)."""
    if attacker.get("hero_key") == "corvayne" and tear_stacks >= CORVAYNE_STORM_PERCH_THRESHOLD:
        return 1 + CORVAYNE_STORM_PERCH_BONUS
    return 1.0


def _compute_damage(attacker: dict, defender: dict, power: float = 1.0):
    tear_stacks = _tear_stacks(defender)
    def_shred = min(tear_stacks * TEAR_DEF_SHRED_PER_STACK, 0.5)
    effective_def = defender["def"] * (1 - def_shred)
    effective_mdef = defender["mdef"] * (1 - def_shred)
    phys = max(attacker["atk"] - effective_def * 0.5, attacker["atk"] * MIN_DAMAGE_RATIO, 1)
    mag = max(attacker["matk"] - effective_mdef * 0.5, attacker["matk"] * MIN_DAMAGE_RATIO, 1)
    base = max(phys, mag) * (power or 1.0)
    type_mult = gacha.get_type_effectiveness(attacker["element"], defender["element"])
    damage = base * type_mult
    is_crit = random.random() < attacker["crit_chance"]
    if is_crit:
        damage *= attacker["crit_damage"]
    # Nyx gains BLOOD_DEBT_PER_STACK damage per Blood Debt stack, uncapped
    # (up to the soft backstop) - the longer a fight runs, the harder she hits.
    if attacker.get("hero_key") == "nyx":
        debt = attacker.get("blood_debt", 0)
        override = attacker.get("_blood_debt_damage_override")
        if override is not None:
            debt = override
        damage *= 1 + (BLOOD_DEBT_PER_STACK * max(0, min(int(debt), BLOOD_DEBT_SOFT_CAP)))

    # Nyx's Blood Debt passive grants 20% damage reduction while below 60% HP.
    if defender.get("hero_key") == "nyx" and defender.get("hp", 0) < defender.get("max_hp", 1) * 0.60:
        damage *= 0.80

    damage *= _corvayne_storm_perch_mult(attacker, tear_stacks)

    return max(int(damage), 1), is_crit, type_mult


def _compute_atk_damage(attacker: dict, defender: dict, power: float = 1.0):
    tear_stacks = _tear_stacks(defender)
    def_shred = min(tear_stacks * TEAR_DEF_SHRED_PER_STACK, 0.5)
    effective_def = defender["def"] * (1 - def_shred)
    base = max(attacker["atk"] - effective_def * 0.5, attacker["atk"] * MIN_DAMAGE_RATIO, 1) * (power or 1.0)
    type_mult = gacha.get_type_effectiveness(attacker["element"], defender["element"])
    damage = base * type_mult
    is_crit = random.random() < attacker["crit_chance"]
    if is_crit:
        damage *= attacker["crit_damage"]
    if attacker.get("hero_key") == "nyx":
        debt = attacker.get("blood_debt", 0)
        override = attacker.get("_blood_debt_damage_override")
        if override is not None:
            debt = override
        damage *= 1 + (BLOOD_DEBT_PER_STACK * max(0, min(int(debt), BLOOD_DEBT_SOFT_CAP)))
    if defender.get("hero_key") == "nyx" and defender.get("hp", 0) < defender.get("max_hp", 1) * 0.60:
        damage *= 0.80
    damage *= _corvayne_storm_perch_mult(attacker, tear_stacks)
    return max(int(damage), 1), is_crit, type_mult


def _first_taunter(enemies: list[dict]):
    taunting = [c for c in enemies if c["hp"] > 0 and _has_status(c, "taunt")]
    return taunting[0] if taunting else None


def _resolve_targets(actor: dict, target_type: str, party: list, enemies: list, *, effect_kind: str | None = None) -> list:
    target_type = target_type or "enemy"
    everyone = party + enemies
    allies = [c for c in everyone if c["side"] == actor["side"] and c["hp"] > 0]
    hostile = [c for c in everyone if c["side"] != actor["side"] and c["hp"] > 0]

    if target_type == "fallen_ally":
        return [c for c in everyone if c["side"] == actor["side"] and c["hp"] <= 0][:1]

    if target_type == "all_midback":
        # Everyone in the middle AND back row - excludes the front row on
        # purpose (a skill built to reach past the front line, not hit it).
        return _rows_group(hostile, {"middle", "back"}) if hostile else []

    if target_type == "lowest_backline":
        if not hostile:
            return []
        pool = _row_group(hostile, "back")  # same front-blocked/taunt-ignoring reach as "backline"
        return [min(pool, key=lambda c: c["hp"] / c["max_hp"])]

    if target_type in ("frontline", "backline", "all_frontline", "all_backline"):
        if not hostile:
            return []
        row = "front" if "front" in target_type else "back"
        pool = _row_group(hostile, row)
        if target_type.startswith("all_"):
            return pool
        # Positional single-target skills (assassins, backline burst) are the
        # point of rows existing at all, so - unlike plain "enemy" - they are
        # NOT redirected by Taunt: that's what lets a real backline strike
        # bypass a frontliner guarding the party.
        return [random.choice(pool)]

    if target_type in ("mirror", "same_slot"):
        if not hostile:
            return []
        mirror = [c for c in hostile if int(c.get("position", -1)) == int(actor.get("position", -2))]
        return mirror if mirror else [random.choice(hostile)]

    if target_type in ("enemy", "all_enemies") or target_type in MULTI_ENEMY_TARGETS:
        if not hostile:
            return []
        if target_type == "enemy":
            taunting = [c for c in hostile if _has_status(c, "taunt")]
            targets = [taunting[0]] if taunting else [random.choice(hostile)]
            if (effect_kind in {"damage", "lifesteal", "dot", "burn", "stun", "silence", "taunt", "confusion", "petrify", "slow", "mark"}
                    and _has_status(actor, "confusion") and len(allies) > 1):
                targets = [random.choice([a for a in allies if a is not actor])]
            return targets
        if target_type in MULTI_ENEMY_TARGETS:
            count = MULTI_ENEMY_TARGETS[target_type]
            taunting = [c for c in hostile if _has_status(c, "taunt")]
            others = [c for c in hostile if c not in taunting]
            chosen = taunting[:count]
            need = count - len(chosen)
            if need > 0:
                chosen += others if len(others) <= need else random.sample(others, need)
            return chosen
        return hostile

    if target_type == "self":
        return [actor] if actor["hp"] > 0 else []
    if target_type == "all_allies":
        return allies
    if target_type == "ally":
        return [min(allies, key=lambda c: c["hp"] / c["max_hp"])] if allies else []
    return []

def _check_skill3_condition(actor: dict, round_num: int, condition, value) -> bool:
    if not condition:
        return True
    if condition == "hp_below":
        return (actor["hp"] / actor["max_hp"]) < (value if value is not None else 0.5)
    if condition == "turn_min":
        return round_num >= (value if value is not None else 1)
    if condition == "chance":
        return random.random() < (value if value is not None else 0.3)
    if condition == "status":
        return _has_status(actor, str(value))
    if condition == "resolve_min":
        return float(actor.get("resolve", 0)) >= float(value if value is not None else 0)
    if condition == "petrify_since_ultimate":
        return bool(actor.get("petrify_since_ultimate", False))
    if condition == "auto_only":
        return False
    return True


def _uses_cooldown_system(actor: dict) -> bool:
    """True for heroes (like Sana) whose skills carry an explicit cooldown in
    heroes.csv - they skip the classic S1->S2->S3 round-robin entirely."""
    return any(s.get("cooldown") for s in actor.get("skills", []))


def _tick_cooldowns(actor: dict) -> None:
    """Decrement this actor's own skill cooldowns once per their own turn.

    Ticks regardless of what happens after (stunned/petrified/silenced) - a
    cooldown running out doesn't depend on whether the turn is actually used.
    """
    cooldowns = actor.get("cooldowns")
    if not cooldowns:
        return
    for i in range(len(cooldowns)):
        if cooldowns[i] > 0:
            cooldowns[i] -= 1


def _pick_skill(actor: dict, round_num: int) -> dict | None:
    if _uses_cooldown_system(actor):
        cooldowns = actor.setdefault("cooldowns", [0, 0, 0])
        skill3 = actor["skills"][2]
        if _check_skill3_condition(actor, round_num, skill3.get("condition"), skill3.get("condition_value")):
            cd = skill3.get("cooldown")
            if not cd or cooldowns[2] == 0:
                if cd:
                    cooldowns[2] = cd
                return skill3
            # Ult condition is met but she's still on ult cooldown - fall
            # through to a normal off-cooldown skill instead of wasting the turn.
        for i in (0, 1):
            skill = actor["skills"][i]
            if cooldowns[i] == 0:
                cd = skill.get("cooldown") or DEFAULT_SKILL_COOLDOWN
                cooldowns[i] = cd
                return skill
        return None  # everything on cooldown - falls back to a Basic Attack
    index = (round_num - 1) % 3
    if index == 2:
        skill3 = actor["skills"][2]
        if _check_skill3_condition(actor, round_num, skill3.get("condition"), skill3.get("condition_value")):
            return skill3
        return actor["skills"][0]
    return actor["skills"][index]


def _check_effect_condition(effect: EffectSpec, actor: dict, target: dict | None, context: dict) -> bool:
    condition = effect.condition
    if not condition:
        return True
    value = effect.condition_value
    if condition in ("if_marked", "if_status"):
        status = str(value) if condition == "if_status" else "mark"
        return bool(target and _has_status(target, status))
    if condition == "if_not_status":
        return bool(target and not _has_status(target, str(value)))
    if condition in ("if_hp_below", "if_target_hp_below"):
        return bool(target and (target["hp"] / target["max_hp"]) < (value if value is not None else 0.5))
    if condition == "if_self_hp_below":
        return (actor["hp"] / actor["max_hp"]) < (value if value is not None else 0.5)
    if condition == "if_kill":
        return bool(context.get("last_kill"))
    if condition == "if_crit":
        return bool(context.get("last_crit"))
    if condition == "chance":
        return random.random() < (value if value is not None else 0.3)
    if condition == "if_target_alive":
        return bool(target and target["hp"] > 0)
    return True


def _effect_target(actor, skill, effect, party, enemies):
    return _resolve_targets(actor, effect.target or skill.get("target", "enemy"), party, enemies, effect_kind=effect.kind)


def _apply_damage_effect(actor, target, power, log, context, name, party=None, enemies=None, allow_reactive=True):
    if party is not None and enemies is not None:
        _maybe_sairen_preempt(target, actor, party, enemies, log, allow_reactive=allow_reactive)
    damage, is_crit, type_mult = _compute_damage(actor, target, power)
    before = target["hp"]
    shield = int(target.get("shield", 0))
    absorbed = min(shield, damage)
    if absorbed:
        target["shield"] = shield - absorbed
        damage_to_hp = damage - absorbed
        if damage_to_hp:
            target["hp"] = max(target["hp"] - damage_to_hp, 0)
    else:
        damage_to_hp = damage
        target["hp"] = max(target["hp"] - damage_to_hp, 0)
    context["last_crit"] = context.get("last_crit", False) or is_crit
    context["last_kill"] = context.get("last_kill", False) or (before > 0 and target["hp"] <= 0)

    line = f"**{actor['name']}** uses **{name}** on **{target['name']}** for {damage} damage"
    if absorbed:
        line += f" ({absorbed} absorbed by shield)"
    tags = []
    if is_crit:
        tags.append("💥 Critical!")
    if type_mult >= 2.0:
        tags.append(f"✨ {actor['element']} is super effective vs {target['element']}!")
    elif type_mult <= 0.5:
        tags.append(f"🛡️ Not very effective ({actor['element']} vs {target['element']})")
    if tags:
        line += " — " + " ".join(tags)
    if target["hp"] <= 0:
        line += f" — **{target['name']} is defeated!**"
    log.append(line)
    if party is not None and enemies is not None:
        _maybe_sairen_counter(target, actor, party, enemies, log, allow_reactive=allow_reactive)
    return damage, is_crit



def _sacrifice_hp(actor: dict, ratio: float, log: list):
    """Reduce an actor's HP by a percentage of Max HP, never below 1 HP."""
    amount = max(int(actor.get("max_hp", 1) * float(ratio or 0)), 0)
    if amount <= 0:
        return 0
    before = actor["hp"]
    actor["hp"] = max(1, before - amount)
    actual = before - actor["hp"]
    if actual:
        log.append(f"🩸 **{actor['name']}** sacrifices {actual} HP ({actor['hp']}/{actor['max_hp']})")
    return actual


# Stacks aren't capped at a fixed ceiling any more - a fight that runs long
# should make her keep getting scarier, not plateau. This is just a sanity
# backstop so a 50-round stalemate can't produce an absurd number.
BLOOD_DEBT_SOFT_CAP = 40
BLOOD_DEBT_PER_STACK = 0.07  # was 0.05 - a bigger jump per stack, so 3-4 stacks (typically by round 5ish) already feels decent


def _blood_debt_gain(actor: dict, stacks: int, log: list):
    if stacks <= 0:
        return 0
    before = int(actor.get("blood_debt", 0))
    actor["blood_debt"] = min(BLOOD_DEBT_SOFT_CAP, before + int(stacks))
    actual = actor["blood_debt"] - before
    if actual:
        bonus = round(actor["blood_debt"] * BLOOD_DEBT_PER_STACK * 100)
        log.append(f"🩸 **{actor['name']}** gains {actual} Blood Debt stack(s) ({actor['blood_debt']} total) — +{bonus}% Damage")
    return actual


def _flamebound_gain(actor: dict, stacks: int, log: list):
    if stacks <= 0 or actor.get("hero_key") != "cinderwind":
        return 0
    before = int(actor.get("flamebound_stacks", 0))
    actor["flamebound_stacks"] = min(3, before + int(stacks))
    actual = actor["flamebound_stacks"] - before
    # Gaining a stack OR re-applying at the 3-stack cap both refresh the 2-turn timer.
    _set_status(actor, "flamebound", 2, actor["flamebound_stacks"])
    if actual:
        log.append(f"🔥 **{actor['name']}** gains {actual} Flamebound stack(s) ({actor['flamebound_stacks']}/3) — Extra Action limit: {2 + actor['flamebound_stacks']}")
    else:
        log.append(f"🔥 **{actor['name']}** refreshes Flamebound ({actor['flamebound_stacks']}/3)")
    return actual


def _register_deaths(party: list[dict], enemies: list[dict], log: list):
    """Count each newly fallen combatant once; a living Cinderwind gains 1 Flamebound
    for every enemy of hers that falls (any source - her own hits, allies, Burn ticks)."""
    for fallen in party + enemies:
        if fallen.get("hp", 0) > 0 or fallen.get("_death_counted"):
            continue
        fallen["_death_counted"] = True
        for cinder in party + enemies:
            if cinder.get("hero_key") == "cinderwind" and cinder.get("hp", 0) > 0 and cinder["side"] != fallen["side"]:
                _flamebound_gain(cinder, 1, log)


def _trigger_cinderwind_on_burn(target: dict, party: list[dict], enemies: list[dict], log: list, allow_reactive: bool = True):
    if not allow_reactive or target.get("hp", 0) <= 0:
        return
    cinderwinds = [c for c in party + enemies
                   if c.get("hero_key") == "cinderwind" and c.get("hp", 0) > 0 and c["side"] != target["side"]]
    for cinder in cinderwinds:
        limit = 2 + int(cinder.get("flamebound_stacks", 0))
        used = int(cinder.get("extra_actions_used", 0))
        if used >= limit:
            continue
        skill = cinder.get("skills", [None])[0]
        if not skill:
            continue
        cinder["extra_actions_used"] = used + 1
        log.append(f"🪽 **{cinder['name']}** triggers **Phoenix's Wrath** on **{target['name']}** ({cinder['extra_actions_used']}/{limit} Extra Actions)")
        _apply_skill(cinder, skill, party, enemies, log, forced_targets=[target], allow_reactive=False)


def _trigger_nyx_ultimate(actor: dict, party: list[dict], enemies: list[dict], log: list):
    """Auto-trigger Night of the Blood Moon when Nyx ends a turn below 60% HP with 3+ Debt.

    The ultimate uses Nyx's pre-consumption Blood Debt damage bonus, then 3 stacks are consumed.
    This makes the sacrifice a deliberate payoff while leaving any remaining stacks intact.
    """
    if actor.get("hero_key") != "nyx" or actor.get("hp", 0) <= 0:
        return False
    if actor.get("hp", 0) >= actor.get("max_hp", 1) * 0.60:
        return False
    debt = int(actor.get("blood_debt", 0))
    if debt < 3:
        return False

    skill = actor.get("skills", [None, None, None])[2]
    if not skill:
        return False

    actor["_blood_debt_damage_override"] = debt
    actor["blood_debt"] = debt - 3
    log.append(f"🩸 **{actor['name']}** consumes 3 Blood Debt and unleashes **{skill['name']}**!")
    _apply_skill(actor, skill, party, enemies, log)
    actor["_blood_debt_damage_override"] = None
    return True


# ---------------------------------------------------------------------------
# Sairen (Drift mantis punisher). Two reactive mechanics, both using her
# Skill 1 as the retaliation strike, both gated by allow_reactive so a
# counter/preempt can never itself trigger another one:
#   - Passive "Killing Instinct": the FIRST time she's targeted with damage
#     in a round, before her own turn has come up, mantis reflexes let her
#     strike first (once per round - doesn't stop the incoming hit).
#   - Skill 2 "Praying Stance": active only for the round it was cast (see
#     guard_round vs the combatant's live _round tag, set once per round in
#     resolve_battle). While active, every hit that lands on her - each hit
#     of a multi-hit skill included, and Burn/Soul Burn ticks - triggers an
#     immediate counter plus 1 Tear stack on the attacker. With several Burn
#     sources on her at once, she counters whoever applied the OLDEST
#     still-active stack (burn stacks preserve application order).
# ---------------------------------------------------------------------------

def _maybe_sairen_preempt(defender: dict, attacker: dict, party: list, enemies: list, log: list, allow_reactive: bool = True):
    if not allow_reactive or defender is None or attacker is None or defender is attacker:
        return
    if defender.get("hero_key") != "sairen":
        return
    current_round = defender.get("_round")
    if defender.get("_acted_round") == current_round:
        return  # she's already had her real turn this round
    if defender.get("_preempt_round") == current_round:
        return  # already struck first once this round
    if attacker.get("hp", 0) <= 0:
        return
    skill = defender.get("skills", [None])[0]
    if not skill:
        return
    defender["_preempt_round"] = current_round
    log.append(f"🦗 **{defender['name']}**'s **{defender['passive']['name']}** strikes first against **{attacker['name']}**!")
    _apply_skill(defender, skill, party, enemies, log, forced_targets=[attacker], allow_reactive=False)


def _maybe_sairen_counter(defender: dict, attacker: dict, party: list, enemies: list, log: list, allow_reactive: bool = True):
    if not allow_reactive or defender is None or attacker is None or defender is attacker:
        return
    if defender.get("hero_key") != "sairen":
        return
    if defender.get("guard_round") != defender.get("_round"):
        return
    if attacker.get("hp", 0) <= 0:
        return
    skill = defender.get("skills", [None])[0]
    if not skill:
        return
    log.append(f"🦗 **{defender['name']}**'s **{defender['skills'][1]['name']}** counters **{attacker['name']}**!")
    _apply_skill(defender, skill, party, enemies, log, forced_targets=[attacker], allow_reactive=False)
    _sairen_gain_tear(attacker, log)


def _sairen_gain_tear(target: dict, log: list):
    """1 Tear stack (Corvayne's TEAR_* constants) applied outside of any
    skill's own effect list - used by Sairen's counter-strikes."""
    stacks = min(_tear_stacks(target) + 1, TEAR_MAX_STACKS)
    current_duration = _status_duration(target, "tear")
    _status_dict(target)["tear"] = {"duration": max(current_duration, TEAR_DEFAULT_DURATION), "stacks": stacks}
    shred_pct = int(min(stacks * TEAR_DEF_SHRED_PER_STACK, 0.20) * 100)
    heal_pct = int(min(stacks * TEAR_HEAL_REDUCTION_PER_STACK, 0.10) * 100)
    log.append(
        f"🪶 **{target['name']}** gains a Tear stack from the counter "
        f"({stacks}/{TEAR_MAX_STACKS}, -{shred_pct}% DEF/MDEF, -{heal_pct}% healing received)"
    )


def _sairen_counter_from_burn(actor: dict, stacks: list, party: list, enemies: list, log: list):
    if actor.get("hero_key") != "sairen" or not stacks:
        return
    if actor.get("guard_round") != actor.get("_round"):
        return
    source_name = stacks[0].get("source_name")
    if not source_name:
        return
    attacker = next(
        (c for c in party + enemies if c.get("name") == source_name and c.get("hp", 0) > 0 and c is not actor),
        None,
    )
    if not attacker:
        return
    skill = actor.get("skills", [None])[0]
    if not skill:
        return
    log.append(f"🦗 **{actor['name']}**'s **{actor['skills'][1]['name']}** counters **{attacker['name']}** for the burn damage!")
    _apply_skill(actor, skill, party, enemies, log, forced_targets=[attacker], allow_reactive=False)
    _sairen_gain_tear(attacker, log)


def _find_elens(party: list[dict], enemies: list[dict]) -> list[dict]:
    return [c for c in party + enemies if c.get("hero_key") == "elen_hart" and c.get("hp", 0) > 0]


def _lantern_active(party: list[dict], enemies: list[dict], target: dict | None = None) -> bool:
    """A living Elen in Lantern Mode turns burns landing on HER enemies into Soul Burn."""
    return any(
        c.get("hero_key") == "elen_hart" and c.get("lantern_mode") and c.get("hp", 0) > 0
        and (target is None or c["side"] != target["side"])
        for c in party + enemies
    )


def _end_orphaned_lanterns(party: list[dict], enemies: list[dict], log: list):
    """If Elen falls while Lantern Mode is up, it must not stay on forever."""
    for c in party + enemies:
        if c.get("hero_key") == "elen_hart" and c.get("lantern_mode") and c.get("hp", 0) <= 0:
            _end_lantern(c, party, enemies, log)


def _grant_resolve_for_burn(party: list[dict], enemies: list[dict], stacks_applied: int, log: list, target: dict | None = None):
    if stacks_applied <= 0:
        return
    for elen in _find_elens(party, enemies):
        if target is not None and elen["side"] == target["side"]:
            continue  # burns on her own team don't feed her
        passive = elen.get("passive", {})
        if passive.get("type") != "resolve_on_burn":
            continue
        gain = int(passive.get("power", 6) or 6) * stacks_applied
        before = int(elen.get("resolve", 0))
        elen["resolve"] = min(100, before + gain)
        actual = elen["resolve"] - before
        if actual:
            log.append(f"🕯️ **{elen['name']}** gains **{actual} Resolve** ({elen['resolve']}/100)")


def _add_burn_stack(source: dict, target: dict, stacks: int, party: list[dict], enemies: list[dict], log: list, allow_reactive: bool = True):
    stacks = max(int(stacks), 1)
    if target.get("hp", 0) <= 0:
        return 0
    statuses = _status_dict(target)
    if _lantern_active(party, enemies, target):
        status = statuses.get("soul_burn") or {"stacks": []}
        stack_list = list(status.get("stacks", []))
        added = min(stacks, max(0, 5 - len(stack_list)))
        for _ in range(added):
            stack_list.append({"source_matk": float(source.get("matk", 0)), "source_name": source.get("name", "Unknown")})
        if added:
            statuses["soul_burn"] = {"stacks": stack_list}
            log.append(f"🔥 **{target['name']}** gains **{added} Soul Burn** stack(s) ({len(stack_list)}/5)")
            # Soul Burn is still a Burn application, so Cinderwind reacts to it just the same.
            _trigger_cinderwind_on_burn(target, party, enemies, log, allow_reactive=allow_reactive)
        return added
    status = statuses.get("burn") or {"duration": 2, "stacks": []}
    stack_list = list(status.get("stacks", []))
    added = min(stacks, max(0, 5 - len(stack_list)))
    for _ in range(added):
        stack_list.append({"source_matk": float(source.get("matk", 0)), "source_name": source.get("name", "Unknown")})
    if added:
        status["duration"] = 2
        status["stacks"] = stack_list
        statuses["burn"] = status
        log.append(f"🔥 **{target['name']}** gains **{added} Burn** stack(s) ({len(stack_list)}/5)")
        _grant_resolve_for_burn(party, enemies, added, log, target)
        _trigger_cinderwind_on_burn(target, party, enemies, log, allow_reactive=allow_reactive)
    return added


def _convert_burns_to_soul_burn(elen: dict, party: list[dict], enemies: list[dict], log: list):
    targets = [c for c in party + enemies if c["side"] != elen["side"] and c.get("hp", 0) > 0]
    converted_total = 0
    for target in targets:
        burn = _status_dict(target).pop("burn", None)
        if not burn:
            continue
        stacks = list(burn.get("stacks", []))
        if not stacks:
            continue
        soul = _status_dict(target).get("soul_burn") or {"stacks": []}
        soul_stacks = list(soul.get("stacks", []))
        converted = stacks[:max(0, 5 - len(soul_stacks))]
        if converted:
            soul_stacks.extend(converted)
            _status_dict(target)["soul_burn"] = {"stacks": soul_stacks}
            converted_total += len(converted)
    log.append(f"🏮 **{elen['name']}** enters **Lantern Mode**. Converted **{converted_total} Burn** stack(s) into Soul Burn.")


def _end_lantern(elen: dict, party: list[dict], enemies: list[dict], log: list):
    elen["lantern_mode"] = False
    _remove_status(elen, "lantern")
    removed = 0
    for combatant in party + enemies:
        soul = _remove_status(combatant, "soul_burn")
        if soul and isinstance(soul, dict):
            removed += len(soul.get("stacks", []))
    log.append(f"🏮 **{elen['name']}**'s Lantern Mode ends. {removed} Soul Burn stack(s) fade.")


def _burn_damage(source_stack: dict, target: dict, ratio: float) -> int:
    source_matk = float(source_stack.get("source_matk", 0))
    base = max(source_matk - target["mdef"] * 0.5, source_matk * MIN_DAMAGE_RATIO, 1) * ratio
    type_mult = gacha.get_type_effectiveness("Blaze", target["element"])
    return max(int(base * type_mult), 1)


def _process_end_of_turn_statuses(actor: dict, party: list[dict], enemies: list[dict], log: list, skip: frozenset | set = frozenset()):
    for status_name, ratio, label in (("burn", BURN_STACK_RATIO, "Burn"), ("soul_burn", SOUL_BURN_STACK_RATIO, "Soul Burn")):
        status = _status_dict(actor).get(status_name)
        if isinstance(status, dict) and actor["hp"] > 0:
            stacks_snapshot = list(status.get("stacks", []))
            for stack in stacks_snapshot:
                damage = _burn_damage(stack, actor, ratio)
                actor["hp"] = max(actor["hp"] - damage, 0)
                log.append(f"🔥 **{actor['name']}** takes {damage} {label} damage")
                if actor["hp"] <= 0:
                    log.append(f"☠️ **{actor['name']}** is defeated by {label}.")
                    break
            if stacks_snapshot:
                _sairen_counter_from_burn(actor, stacks_snapshot, party, enemies, log)
            if status_name == "burn":
                duration = int(status.get("duration", 2)) - 1
                if duration <= 0 or actor["hp"] <= 0:
                    _remove_status(actor, "burn")
                else:
                    status["duration"] = duration
    lantern = _status_dict(actor).get("lantern")
    if isinstance(lantern, dict) and actor.get("hero_key") == "elen_hart":
        remaining = int(lantern.get("duration", 0)) - 1
        if remaining <= 0:
            _end_lantern(actor, party, enemies, log)
        else:
            lantern["duration"] = remaining
    for status_name in list(_status_dict(actor)):
        if status_name in {"burn", "soul_burn", "lantern"} or status_name in skip:
            continue
        _tick_status(actor, status_name)


def _apply_one_effect(actor, skill, effect: EffectSpec, party, enemies, log, context):
    if effect.kind not in SUPPORTED_EFFECTS:
        log.append(f"⚠️ **{actor['name']}** tried to use unsupported effect `{effect.kind}`.")
        return

    targets = _effect_target(actor, skill, effect, party, enemies)
    if not targets and effect.kind not in {"revive"}:
        return

    value = effect.value
    name = skill["name"]

    if effect.kind == "damage":
        for target in targets:
            _apply_damage_effect(actor, target, float(value if value is not None else 1.0), log, context, name, party, enemies, allow_reactive=context.get("allow_reactive", True))

    elif effect.kind == "heal":
        amount_factor = float(value if value is not None else 1.0)
        for target in targets:
            heal_amount = max(int(actor["matk"] * amount_factor), 1)
            heal_reduction = min(_tear_stacks(target) * TEAR_HEAL_REDUCTION_PER_STACK, 0.5)
            if heal_reduction:
                heal_amount = max(int(heal_amount * (1 - heal_reduction)), 1)
            before = target["hp"]
            target["hp"] = min(target["hp"] + heal_amount, target["max_hp"])
            actual = target["hp"] - before
            log.append(f"💚 **{actor['name']}** uses **{name}** — heals **{target['name']}** for {actual} HP")

    elif effect.kind == "multi_hit":
        hits = max(int(value if value is not None else 2), 1)
        hit_power = float(effect.duration or 1.0)
        for target in targets:
            for hit in range(hits):
                _apply_damage_effect(actor, target, hit_power, log, context, name, party, enemies, allow_reactive=context.get("allow_reactive", True))
                if target["hp"] <= 0:
                    break

    elif effect.kind == "shield":
        ratio = float(value if value is not None else 0.25)
        for target in targets:
            shield = max(int(target["max_hp"] * ratio), 1)
            target["shield"] = target.get("shield", 0) + shield
            log.append(f"🛡️ **{actor['name']}** uses **{name}** — **{target['name']}** gains a {shield} HP shield")

    elif effect.kind.startswith("buff_") or effect.kind.startswith("debuff_"):
        amount = float(value if value is not None else 0)
        for target in targets:
            _apply_stat_mod(actor, target, effect.kind, amount, effect.duration, name, log, permanent=bool(skill.get("permanent")))

    elif effect.kind in {"stun", "silence", "taunt", "confusion"}:
        duration = int(value if value is not None else 1)
        for target in targets:
            _set_status(target, effect.kind, duration)
            log.append(f"🎯 **{actor['name']}** uses **{name}** — **{target['name']}** is affected by {effect.kind.title()} for {duration} turn(s)")

    elif effect.kind == "cleanse":
        for target in targets:
            removed = []
            for status in ("stun", "silence", "taunt", "confusion", "petrify", "dot"):
                if _remove_status(target, status):
                    removed.append(status)
            if removed:
                log.append(f"✨ **{actor['name']}** cleanses **{target['name']}**: {', '.join(removed)}")

    elif effect.kind == "mark":
        duration = int(value if value is not None else 2)
        for target in targets:
            _set_status(target, "mark", duration)
            log.append(f"🔖 **{actor['name']}** marks **{target['name']}** for {duration} turn(s)")

    elif effect.kind == "lifesteal":
        ratio = float(value if value is not None else 0.25)
        damage = context.get("last_damage", 0)
        if damage > 0:
            amount = max(int(damage * ratio), 1)
            before = actor["hp"]
            actor["hp"] = min(actor["hp"] + amount, actor["max_hp"])
            actual = actor["hp"] - before
            if actual:
                log.append(f"🩸 **{actor['name']}** restores {actual} HP through Lifesteal")

    elif effect.kind == "dot":
        amount = float(value if value is not None else 0.1)
        duration = int(effect.duration or 2)
        for target in targets:
            _set_status(target, "dot", duration, amount)
            log.append(f"☠️ **{target['name']}** is afflicted with Damage Over Time for {duration} turn(s)")

    elif effect.kind == "hot":
        amount = float(value if value is not None else 0.1)
        duration = int(effect.duration or 2)
        for target in targets:
            _set_status(target, "hot", duration, amount)
            log.append(f"💚 **{target['name']}** receives Heal Over Time for {duration} turn(s)")

    elif effect.kind == "revive":
        ratio = float(value if value is not None else 0.3)
        dead = [c for c in (party + enemies) if c["hp"] <= 0 and c["side"] == actor["side"]]
        revive_targets = targets if targets else dead
        for target in revive_targets:
            if target["hp"] <= 0:
                target["hp"] = max(int(target["max_hp"] * ratio), 1)
                log.append(f"✨ **{actor['name']}** revives **{target['name']}** with {target['hp']} HP")


def _apply_skill(actor: dict, skill: dict, party: list, enemies: list, log: list, forced_targets: list[dict] | None = None, allow_reactive: bool = True):
    if actor.get("hero_key") == "nahilra" and actor.get("skills") and skill is actor["skills"][2]:
        actor["petrify_since_ultimate"] = False
    context = {"last_damage": 0, "total_damage": 0, "last_crit": False, "last_kill": False, "allow_reactive": allow_reactive}
    # Resolve each target selector once per skill. This guarantees that effects
    # sharing a selector (for example Cinder Mark's damage + Burn) hit the same
    # targets instead of rolling separate random targets for each effect.
    target_cache: dict[str, list[dict]] = {}

    for effect in skill.get("effects", []):
        selector = effect.target or skill.get("target", "enemy")
        if effect.kind == "revive":
            selector = "fallen_ally"  # revive only ever makes sense on a fallen ally
        if forced_targets is not None and selector in {"enemy", "two_enemies", "three_enemies", "four_enemies", "all_enemies"}:
            target_cache[selector] = [t for t in forced_targets if t.get("hp", 0) > 0]
        elif selector == "other_enemies":
            # Corvayne's Thousand-fold Raven pattern: a primary "enemy" target
            # (resolved/cached first, same taunt-respecting single-target pick
            # as always) plus every OTHER hostile as reduced-power splash.
            # Relies on the "enemy" effect being listed before this one in the
            # skill's effects string so the primary is already resolved.
            primary = target_cache.get("enemy")
            if primary is None:
                primary = _resolve_targets(actor, "enemy", party, enemies, effect_kind=effect.kind)
                target_cache["enemy"] = primary
            everyone = party + enemies
            hostile_all = [c for c in everyone if c["side"] != actor["side"] and c["hp"] > 0]
            target_cache[selector] = [c for c in hostile_all if c not in primary]
        elif selector not in target_cache:
            target_cache[selector] = _resolve_targets(actor, selector, party, enemies, effect_kind=effect.kind)
        targets = target_cache[selector]

        if not targets:
            # Actor-only / non-targeted effects still need to run once.
            _apply_one_effect(actor, skill, effect, party, enemies, log, context)
            continue

        for target in list(targets):
            if not _check_effect_condition(effect, actor, target, context):
                continue

            if effect.kind == "damage":
                damage, crit = _apply_damage_effect(actor, target, float(effect.value or 1.0), log, context, skill["name"], party, enemies, allow_reactive=context.get("allow_reactive", True))
                context["last_damage"] = damage
                context["total_damage"] = context.get("total_damage", 0) + damage
                context["last_crit"] = context["last_crit"] or crit
                continue

            if effect.kind == "damage_atk":
                _maybe_sairen_preempt(target, actor, party, enemies, log, allow_reactive=context.get("allow_reactive", True))
                damage, crit, type_mult = _compute_atk_damage(actor, target, float(effect.value or 1.0))
                before = target["hp"]
                shield = int(target.get("shield", 0))
                absorbed = min(shield, damage)
                if absorbed:
                    target["shield"] = shield - absorbed
                    damage_to_hp = damage - absorbed
                    if damage_to_hp:
                        target["hp"] = max(target["hp"] - damage_to_hp, 0)
                else:
                    damage_to_hp = damage
                    target["hp"] = max(target["hp"] - damage_to_hp, 0)
                context["last_crit"] = context.get("last_crit", False) or crit
                context["last_kill"] = context.get("last_kill", False) or (before > 0 and target["hp"] <= 0)
                line = f"**{actor['name']}** uses **{skill['name']}** on **{target['name']}** for {damage} damage"
                if absorbed:
                    line += f" ({absorbed} absorbed by shield)"
                if crit:
                    line += " — 💥 Critical!"
                if type_mult >= 2.0:
                    line += f" — ✨ {actor['element']} is super effective vs {target['element']}!"
                elif type_mult <= 0.5:
                    line += f" — 🛡️ Not very effective ({actor['element']} vs {target['element']})"
                if target["hp"] <= 0:
                    line += f" — **{target['name']} is defeated!**"
                log.append(line)
                _maybe_sairen_counter(target, actor, party, enemies, log, allow_reactive=context.get("allow_reactive", True))
                context["last_damage"] = damage
                context["total_damage"] = context.get("total_damage", 0) + damage
                continue

            if effect.kind == "lifesteal":
                ratio = float(effect.value or 0.25)
                amount = max(int(context.get("last_damage", 0) * ratio), 1) if context.get("last_damage", 0) else 0
                if amount:
                    before = actor["hp"]
                    actor["hp"] = min(actor["hp"] + amount, actor["max_hp"])
                    actual = actor["hp"] - before
                    if actual:
                        log.append(f"🩸 **{actor['name']}** restores {actual} HP through Lifesteal")
                continue

            if effect.kind == "lifesteal_total":
                ratio = float(effect.value or 0.25)
                amount = int(context.get("total_damage", 0) * ratio)
                if amount > 0:
                    before = actor["hp"]
                    actor["hp"] = min(actor["hp"] + amount, actor["max_hp"])
                    actual = actor["hp"] - before
                    if actual:
                        log.append(f"🩸 **{actor['name']}** restores {actual} HP through Lifesteal")
                continue

            _apply_effect_to_exact_target(actor, skill, effect, target, party, enemies, log, context)

        # Count kills right after each effect so a kill mid-skill raises the
        # Extra Action limit for the Burn applications that follow it.
        _register_deaths(party, enemies, log)


def _apply_effect_to_exact_target(actor, skill, effect, target, party, enemies, log, context):
    if effect.kind == "hp_cost":
        _sacrifice_hp(actor, float(effect.value or 0), log)
        return
    if effect.kind == "blood_debt":
        _blood_debt_gain(actor, int(effect.value or 1), log)
        return
    if effect.kind == "burn":
        _add_burn_stack(actor, target, int(effect.value or 1), party, enemies, log, allow_reactive=bool(context.get("allow_reactive", True)))
        return
    if effect.kind == "flamebound":
        _flamebound_gain(actor, int(effect.value or 1), log)
        return
    if effect.kind == "consume_resolve":
        amount = max(int(effect.value or 0), 0)
        before = int(actor.get("resolve", 0))
        actor["resolve"] = max(before - amount, 0)
        log.append(f"🕯️ **{actor['name']}** consumes {before - actor['resolve']} Resolve ({actor['resolve']}/100)")
        return
    if effect.kind == "enter_lantern":
        turns = max(int(effect.value or 2), 1)
        actor["lantern_mode"] = True
        _status_dict(actor)["lantern"] = {"duration": turns}
        _convert_burns_to_soul_burn(actor, party, enemies, log)
        return
    if effect.kind == "heal":
        amount = max(int(actor["matk"] * float(effect.value or 1.0) * HEAL_SCALE), 1)
        heal_reduction = min(_tear_stacks(target) * TEAR_HEAL_REDUCTION_PER_STACK, 0.5)
        if heal_reduction:
            amount = max(int(amount * (1 - heal_reduction)), 1)
        before = target["hp"]
        target["hp"] = min(target["hp"] + amount, target["max_hp"])
        log.append(f"💚 **{actor['name']}** uses **{skill['name']}** — heals **{target['name']}** for {target['hp'] - before} HP")
        return

    if effect.kind == "shield":
        shield = max(int(target["max_hp"] * float(effect.value or 0.25)), 1)
        target["shield"] = target.get("shield", 0) + shield
        log.append(f"🛡️ **{actor['name']}** uses **{skill['name']}** — **{target['name']}** gains a {shield} HP shield")
        return

    if effect.kind.startswith("buff_") or effect.kind.startswith("debuff_"):
        _apply_stat_mod(actor, target, effect.kind, float(effect.value or 0), effect.duration, skill["name"], log, permanent=bool(skill.get("permanent")))
        return

    if effect.kind == "slow":
        duration = max(int(effect.duration or 2), 1)
        per_stack = max(min(float(effect.value if effect.value is not None else 0.20), 0.90), 0.0)
        existing = _status_dict(target).get("slow")
        current_stacks = int(existing.get("stacks", 0)) if isinstance(existing, dict) else 0
        stacks = min(current_stacks + 1, SLOW_MAX_STACKS)
        current_duration = _status_duration(target, "slow")
        _status_dict(target)["slow"] = {"duration": max(current_duration, duration), "stacks": stacks, "power": per_stack}
        total_pct = int(min(stacks * per_stack, SLOW_MAX_REDUCTION) * 100)
        log.append(
            f"🐌 **{actor['name']}** uses **{skill['name']}** — **{target['name']}** is Slowed "
            f"({stacks}/{SLOW_MAX_STACKS} stacks, -{total_pct}% Speed) for {duration} turn(s)"
        )
        return

    if effect.kind == "petrify":
        duration = max(int(effect.value if effect.value is not None else 1), 1)
        _set_status(target, "petrify", duration)
        if actor.get("hero_key") == "nahilra":
            actor["petrify_since_ultimate"] = True
        log.append(f"🗿 **{actor['name']}** uses **{skill['name']}** — **{target['name']}** is Petrified for {duration} turn(s)")
        return

    if effect.kind in HARD_CONTROL | {"taunt"}:
        duration = int(effect.value or 1)
        _set_status(target, effect.kind, duration, grace=True)
        log.append(f"🎯 **{actor['name']}** uses **{skill['name']}** — **{target['name']}** is affected by {effect.kind.title()} for {duration} turn(s)")
        return

    if effect.kind == "cleanse":
        removed = [s for s in ("stun", "silence", "taunt", "confusion", "petrify", "dot") if _remove_status(target, s)]
        if removed:
            log.append(f"✨ **{actor['name']}** cleanses **{target['name']}**: {', '.join(removed)}")
        return

    if effect.kind == "dispel":
        removed = []
        for status in list(_status_dict(target)):
            if status.startswith("buff_"):
                _remove_status(target, status)
                removed.append(status)
        if removed:
            log.append(f"🌀 **{actor['name']}** dispels buffs from **{target['name']}**")
        return

    if effect.kind == "mark":
        _set_status(target, "mark", int(effect.value or 2))
        log.append(f"🔖 **{actor['name']}** marks **{target['name']}** for {int(effect.value or 2)} turn(s)")
        return

    if effect.kind == "tear":
        duration = max(int(effect.duration or TEAR_DEFAULT_DURATION), 1)
        stacks = min(_tear_stacks(target) + 1, TEAR_MAX_STACKS)
        current_duration = _status_duration(target, "tear")
        _status_dict(target)["tear"] = {"duration": max(current_duration, duration), "stacks": stacks}
        shred_pct = int(min(stacks * TEAR_DEF_SHRED_PER_STACK, 0.20) * 100)
        heal_pct = int(min(stacks * TEAR_HEAL_REDUCTION_PER_STACK, 0.10) * 100)
        log.append(
            f"🪶 **{actor['name']}** uses **{skill['name']}** — **{target['name']}** gains a Tear stack "
            f"({stacks}/{TEAR_MAX_STACKS}, -{shred_pct}% DEF/MDEF, -{heal_pct}% healing received)"
        )
        return

    if effect.kind == "dot":
        _set_status(target, "dot", int(effect.duration or 2), float(effect.value or 0.1))
        log.append(f"☠️ **{target['name']}** is afflicted with Damage Over Time")
        return

    if effect.kind == "hot":
        _set_status(target, "hot", int(effect.duration or 2), float(effect.value or 0.1))
        log.append(f"💚 **{target['name']}** receives Heal Over Time")
        return

    if effect.kind == "revive":
        if target["hp"] <= 0:
            target["hp"] = max(int(target["max_hp"] * float(effect.value or 0.3)), 1)
            target.pop("_death_counted", None)
            for leftover in list(_status_dict(target)):
                _remove_status(target, leftover)
            log.append(f"✨ **{actor['name']}** revives **{target['name']}** with {target['hp']} HP")
        return

    if effect.kind in {"turn_advance", "turn_delay"}:
        _set_status(target, effect.kind, int(effect.value or 1))
        log.append(f"⏱️ **{actor['name']}** applies {effect.kind.replace('_', ' ').title()} to **{target['name']}**")
        return

    if effect.kind == "multi_hit":
        hits = max(int(effect.value or 2), 1)
        power = float(effect.duration or 1.0)
        for _ in range(hits):
            if target["hp"] <= 0:
                break
            damage, crit = _apply_damage_effect(actor, target, power, log, context, skill["name"], party, enemies, allow_reactive=context.get("allow_reactive", True))
            context["last_damage"] = damage
            context["total_damage"] = context.get("total_damage", 0) + damage
            context["last_crit"] = context["last_crit"] or crit


def _apply_passive(actor: dict, party: list, enemies: list, log: list):
    passive = actor.get("passive")
    if not passive:
        return
    if passive.get("type") == "resolve_on_burn":
        log.append(f"🕯️ **{actor['name']}** begins battle with **Ashen Resolve** active.")
        return
    if passive.get("type") == "blood_debt":
        actor["blood_debt"] = 0
        log.append(f"🩸 **{actor['name']}** begins with **Blood Debt** ready to build.")
        return
    if passive.get("type") == "phoenix_wrath":
        actor["flamebound_stacks"] = 0
        actor["extra_actions_used"] = 0
        log.append(f"🪽 **{actor['name']}** begins with **Phoenix's Wrath** ready.")
        return
    if passive.get("type") == "storm_perch":
        # Functionally implemented as a hook in _compute_damage/_compute_atk_damage
        # (+20% dmg vs targets holding 2+ Tear stacks) - this is just the
        # battle-start flavor line to match the rest of the roster.
        log.append(f"🪶 **{actor['name']}** begins battle with **Storm Perch** watching for Tear stacks.")
        return
    if passive.get("type") == "killing_instinct":
        # Functionally implemented as a hook in _apply_damage_effect / the
        # damage_atk block (_maybe_sairen_preempt) - this is just the
        # battle-start flavor line to match the rest of the roster.
        log.append(f"🦗 **{actor['name']}** begins battle with **{passive['name']}** ready — she'll strike first the moment she's targeted.")
        return
    if not passive.get("effects"):
        return
    fake_skill = {"name": passive["name"], "target": passive.get("target", "self"), "effects": passive["effects"], "permanent": True}
    _apply_skill(actor, fake_skill, party, enemies, log)


def _basic_attack(actor: dict, party: list, enemies: list, log: list):
    if actor.get("hero_key") == "cinderwind" and actor.get("skills"):
        _apply_skill(actor, actor["skills"][0], party, enemies, log, allow_reactive=False)
        return
    targets = _resolve_targets(actor, "enemy", party, enemies, effect_kind="damage")
    if not targets:
        return
    target = targets[0]
    context = {"last_damage": 0, "last_crit": False, "last_kill": False}
    damage, _ = _apply_damage_effect(actor, target, 1.0, log, context, "Basic Attack", party, enemies)


def _effective_speed(combatant: dict) -> float:
    """Return battle speed after temporary non-mutating speed statuses."""
    speed = float(combatant.get("speed", 0))
    slow = _status_dict(combatant).get("slow")
    if isinstance(slow, dict):
        stacks = int(slow.get("stacks", 1)) or 1
        per_stack = float(slow.get("power", 0.2))
        reduction = min(stacks * per_stack, SLOW_MAX_REDUCTION)
        speed *= max(1.0 - reduction, 0.1)
    return speed


def _process_start_of_turn_effects(actor: dict, log: list):
    dot = _status_dict(actor).get("dot")
    if isinstance(dot, dict) and actor["hp"] > 0:
        ratio = float(dot.get("power", 0.1))
        damage = max(int(actor["max_hp"] * ratio), 1)
        actor["hp"] = max(actor["hp"] - damage, 0)
        log.append(f"☠️ **{actor['name']}** takes {damage} Damage Over Time")
    hot = _status_dict(actor).get("hot")
    if isinstance(hot, dict) and actor["hp"] > 0:
        ratio = float(hot.get("power", 0.1))
        heal = max(int(actor["max_hp"] * ratio), 1)
        before = actor["hp"]
        actor["hp"] = min(actor["hp"] + heal, actor["max_hp"])
        log.append(f"💚 **{actor['name']}** recovers {actor['hp'] - before} HP from Heal Over Time")


def _consume_petrify(actor: dict, log: list) -> bool:
    if not _has_status(actor, "petrify"):
        return False
    log.append(f"🗿 **{actor['name']}** is **Petrified** and loses the turn!")
    _tick_status(actor, "petrify")
    return True


def _consume_stun(actor: dict, log: list) -> bool:
    if not _has_status(actor, "stun"):
        return False
    log.append(f"💫 **{actor['name']}** is **Stunned** and loses the turn!")
    _tick_status(actor, "stun")
    return True


def _consume_silence(actor: dict, log: list) -> bool:
    if not _has_status(actor, "silence"):
        return False
    _tick_status(actor, "silence")
    log.append(f"🔇 **{actor['name']}** is **Silenced** and can only use a Basic Attack!")
    return True


def _run_turn(actor: dict, round_num: int, party: list, enemies: list, log: list):
    """One combatant's full turn: start-of-turn effects, control checks, the skill, end-of-turn upkeep."""
    actor["_acted_round"] = round_num
    if actor.get("hero_key") == "cinderwind":
        actor["extra_actions_used"] = 0
    _process_start_of_turn_effects(actor, log)
    _tick_cooldowns(actor)
    if actor["hp"] <= 0:
        log.append(f"☠️ **{actor['name']}** is defeated by a status effect.")
        return

    # Control statuses that cost this turn are ticked when consumed, so they
    # are skipped by the generic end-of-turn tick (otherwise a 2-turn stun only
    # lasted 1 turn because it was ticked twice).
    if _consume_petrify(actor, log):
        _process_end_of_turn_statuses(actor, party, enemies, log, skip={"petrify"})
        _trigger_nyx_ultimate(actor, party, enemies, log)
        return

    if _consume_stun(actor, log):
        _process_end_of_turn_statuses(actor, party, enemies, log, skip={"stun"})
        _trigger_nyx_ultimate(actor, party, enemies, log)
        return

    # Both sides use their hero skill rotation. Enemy teams are therefore
    # built from exactly the same catalog hero definitions as the player.
    skill = _pick_skill(actor, round_num)

    skip = set()
    if _consume_silence(actor, log):
        skip.add("silence")
        _basic_attack(actor, party, enemies, log)
    elif skill is None:
        # Cooldown-based hero with everything on cooldown this turn.
        _basic_attack(actor, party, enemies, log)
    else:
        _apply_skill(actor, skill, party, enemies, log)
        if actor.get("hero_key") == "sairen" and skill is actor["skills"][1] and actor["hp"] > 0:
            actor["guard_round"] = round_num
            log.append(f"🦗 **{actor['name']}** enters **{actor['skills'][1]['name']}** — she'll counter anything that hits her this round.")

    _process_end_of_turn_statuses(actor, party, enemies, log, skip=skip)
    if actor.get("hero_key") == "cinderwind" and not _has_status(actor, "flamebound"):
        actor["flamebound_stacks"] = 0
    _trigger_nyx_ultimate(actor, party, enemies, log)


def paginate_log(lines: list[str], max_chars: int = 3900) -> list[str]:
    """Split a full battle log into Discord-sized pages, in order, with nothing
    dropped (unlike fit_log_to_limit, which keeps only the newest lines that fit).

    Lines are packed greedily: as many whole lines as fit go on one page, then
    a new page starts. A single line longer than max_chars gets its own
    page(s), split at max_chars with no line ever truncated silently - it is
    simply carried across as many pages as it needs.
    """
    max_chars = max(int(max_chars), 200)
    pages: list[str] = []
    current: list[str] = []
    used = 0
    for line in lines:
        if len(line) > max_chars:
            if current:
                pages.append("\n".join(current))
                current, used = [], 0
            for i in range(0, len(line), max_chars):
                pages.append(line[i:i + max_chars])
            continue
        cost = len(line) + (1 if current else 0)
        if used + cost > max_chars:
            pages.append("\n".join(current))
            current, used = [line], len(line)
        else:
            current.append(line)
            used += cost
    if current:
        pages.append("\n".join(current))
    return pages or [""]


def fit_log_to_limit(lines: list[str], max_chars: int) -> str:
    """Join battle-log lines, keeping the NEWEST ones that fit in max_chars.

    Used for Discord embed descriptions (hard limit 4096, and 6000 for the whole
    embed). Whole lines are dropped from the front - never cut mid-line - and a
    short header says how much was left out. A single line longer than the
    budget is truncated so the result can never exceed max_chars.
    """
    max_chars = max(int(max_chars), 100)
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full

    budget = max_chars - 60  # room for the "(showing the last N of M ...)" header
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        cost = len(line) + (1 if kept else 0)
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    if not kept:
        kept = [lines[-1][: budget - 1] + "…"]
    kept.reverse()
    return f"*(showing the last {len(kept)} of {len(lines)} log lines)*\n" + "\n".join(kept)


def resolve_battle(party: dict, chapter: dict, enemy_team: list[dict] | None = None) -> dict:
    """Run a battle between the player's party and a designer-defined enemy team.

    enemy_team is a list of catalog warrior dicts, each with a battle `level`.
    If omitted/empty, old boss fields are used as a compatibility fallback.
    """
    if not party:
        return None

    combatants = [_make_party_combatant(pos, w) for pos, w in party.items()]
    if enemy_team:
        enemies = [_make_enemy_combatant(i + 1, w) for i, w in enumerate(enemy_team[:6])]
    else:
        enemies = [_make_legacy_boss_combatant(chapter)]

    enemy_names = ", ".join(f"**{e['name']}** (Lv. {e.get('level', 1)})" for e in enemies)
    log = [f"⚔️ Enemy team enters: {enemy_names}"]

    for c in combatants + enemies:
        _apply_passive(c, combatants, enemies, log)

    rounds = 0

    def alive_party():
        return [c for c in combatants if c["hp"] > 0]

    def alive_enemies():
        return [c for c in enemies if c["hp"] > 0]

    all_combatants = combatants + enemies

    while alive_party() and alive_enemies() and rounds < MAX_ROUNDS:
        rounds += 1
        log.append(f"\n**── Round {rounds} ──**")
        for c in all_combatants:
            c["_round"] = rounds
        turn_order = sorted(
            [c for c in all_combatants if c["hp"] > 0],
            key=lambda c: (_effective_speed(c), random.random()),  # ties are a coin flip, not always the player
            reverse=True,
        )

        for actor in turn_order:
            if actor["hp"] <= 0:
                continue
            if not alive_party() or not alive_enemies():
                break

            actor["_acting"] = True
            try:
                _run_turn(actor, rounds, combatants, enemies, log)
            finally:
                actor.pop("_acting", None)
            # Kills from Burn/DoT ticks land here; Elen falling ends her Lantern.
            _register_deaths(combatants, enemies, log)
            _end_orphaned_lanterns(combatants, enemies, log)

    won = not alive_enemies()
    party_status = [
        {"name": c["name"], "hp": c["hp"], "max_hp": c["max_hp"], "alive": c["hp"] > 0, "level": c.get("level", 1), "resolve": c.get("resolve", 0), "lantern": bool(c.get("lantern_mode", False)), "blood_debt": c.get("blood_debt", 0), "flamebound": c.get("flamebound_stacks", 0), "extra_actions_used": c.get("extra_actions_used", 0)}
        for c in combatants
    ]
    enemy_status = [
        {"name": c["name"], "hp": c["hp"], "max_hp": c["max_hp"], "alive": c["hp"] > 0, "level": c.get("level", 1)}
        for c in enemies
    ]

    return {
        "won": won,
        "log": log,
        "party_status": party_status,
        "enemy_status": enemy_status,
        "rounds": rounds,
        "enemy_hp_remaining": sum(max(c["hp"], 0) for c in enemies),
        "enemy_max_hp": sum(c["max_hp"] for c in enemies),
        # Legacy aliases so older UI code has something sensible to display.
        "boss_hp_remaining": enemies[0]["hp"] if enemies else 0,
        "boss_max_hp": enemies[0]["max_hp"] if enemies else 0,
    }
