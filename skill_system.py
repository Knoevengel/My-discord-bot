"""Modular skill-effect parser and shared effect utilities for the battle engine.

CSV skill syntax:
    damage:1.5
    damage:1.5|stun:1
    damage:1.8|mark:2|stun:1@if_marked

General form for an effect:
    effect:value:target@if_condition

- value is optional for effects that do not need one.
- target is optional; when omitted the skill's default target is used.
- a fourth colon-separated field is supported as duration for stat buffs/debuffs.
- multiple effects are separated with |.

The parser deliberately stays small and data-driven so new heroes can be made in
heroes.csv without adding hero-specific Python code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EffectSpec:
    kind: str
    value: object = None
    target: Optional[str] = None
    duration: Optional[int] = None
    condition: Optional[str] = None
    condition_value: object = None


# Effects currently understood by battle.py. Keeping the registry here makes the
# allowed vocabulary easy to inspect and document.
SUPPORTED_EFFECTS = {
    "damage",
    "heal",
    "multi_hit",
    "shield",
    "buff_atk",
    "buff_def",
    "buff_matk",
    "buff_mdef",
    "buff_speed",
    "debuff_atk",
    "debuff_def",
    "debuff_matk",
    "debuff_mdef",
    "debuff_speed",
    "stun",
    "silence",
    "taunt",
    "confusion",
    "cleanse",
    "mark",
    "lifesteal",
    "dot",
    "hot",
    "revive",
    "burn",
    "consume_resolve",
    "enter_lantern",
    "hp_cost",
    "blood_debt",
    "lifesteal_total",
    "damage_atk",
    "flamebound",
    "petrify",
    "slow",
    "tear",
}


def _parse_number(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _parse_condition(raw: str | None):
    if not raw:
        return None, None
    condition = raw.strip()
    if not condition:
        return None, None
    if ":" in condition:
        name, value = condition.split(":", 1)
        return name.strip(), _parse_number(value)
    return condition, None


def parse_effects(raw: str | None) -> list[EffectSpec]:
    """Parse the compact CSV effect language into EffectSpec objects.

    Bad/unknown effects are retained as specs so battle.py can report them in
    the log instead of silently deleting the hero's action.
    """
    if not raw or not raw.strip():
        return []

    effects: list[EffectSpec] = []
    for token in raw.split("|"):
        token = token.strip()
        if not token:
            continue

        effect_part, _, condition_part = token.partition("@")
        parts = [p.strip() for p in effect_part.split(":")]
        kind = parts[0].lower()
        value = _parse_number(parts[1]) if len(parts) >= 2 else None
        target = parts[2] if len(parts) >= 3 and parts[2] else None
        duration = None
        if len(parts) >= 4 and parts[3]:
            parsed_duration = _parse_number(parts[3])
            duration = int(parsed_duration) if isinstance(parsed_duration, (int, float)) else None

        condition, condition_value = _parse_condition(condition_part)
        effects.append(
            EffectSpec(
                kind=kind,
                value=value,
                target=target,
                duration=duration,
                condition=condition,
                condition_value=condition_value,
            )
        )
    return effects


def legacy_effects(skill_type, power, target) -> str:
    """Convert the old one-effect columns to the new syntax."""
    if not skill_type:
        return ""
    return f"{skill_type}:{power}:{target}"
