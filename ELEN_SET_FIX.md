# Elen Set Fix

This patch fixes Elen's character data and enables the custom battle effects needed by her finalized kit.

## Final Elen kit data
- **Skill 1 — Cinder Mark:** 110% MATK Blaze damage to 2 enemies; those same 2 enemies each receive 1 Burn stack.
- **Skill 2 — Crimson Veil:** 130% MATK Blaze damage to all enemies; those same targets each receive 1 Burn stack.
- **Skill 3 — Scarlet Eclipse:** 300% MATK Blaze damage to 1 enemy; only available at 60+ Resolve; consumes 60 Resolve; enters Lantern Mode for 2 turns.
- **Ashen Resolve:** Whenever any source applies 1 Burn stack, Elen gains 6 Resolve; Resolve caps at 100.
- **Burn:** 65% of the inflicter's snapshot MATK per stack, maximum 5 stacks, 2 turns.
- **Lantern:** Existing Burn on enemies is converted to Soul Burn; any Burn applied by any source during Lantern becomes Soul Burn.
- **Soul Burn:** 100% of the inflicter's snapshot MATK per stack, maximum 5 stacks, removed when Lantern ends.

## Engine fixes
- `burn`, `consume_resolve`, and `enter_lantern` are registered as supported effects.
- Effects with the same target selector are resolved once per skill, so Cinder Mark damage and Burn always hit the exact same 2 targets.

Replace:
- `battle.py`
- `skill_system.py`
- `heroes.csv`

Do not replace your existing database file.
