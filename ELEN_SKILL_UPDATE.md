# Elen — Skill System Update

This patch updates Elen (6★ Blaze) and the battle engine.

## Elen's kit

### Skill 1 — Cinder Mark
- 110% power
- Targets 2 enemies
- Applies 1 Burn stack to each target

### Skill 2 — Crimson Veil
- 130% power
- Targets all enemies
- Applies 1 Burn stack to each target

### Skill 3 — Scarlet Eclipse
- 300% power
- Targets 1 enemy
- Only available at 60+ Resolve
- Consumes 60 Resolve when used
- Enters Lantern Mode for 2 turns
- Converts existing Burn on enemies into Soul Burn
- While Lantern Mode is active, any new Burn from any source becomes Soul Burn instead

### Passive — Ashen Resolve
- Whenever any source successfully applies 1 Burn stack, Elen gains 6 Resolve
- Resolve caps at 100
- Skill 3 requires 60 Resolve

## Burn
- Maximum 5 stacks
- Lasts 2 turns
- Deals 65% of the inflicter's snapshot MATK per stack at the end of the afflicted target's turn
- Each applied stack records the inflicter's MATK when it was applied
- Reapplying Burn refreshes its 2-turn duration
- If Burn is already at 5 stacks, no extra Resolve is granted because no new stack was applied

## Soul Burn
- Maximum 5 stacks
- Deals 100% of the inflicter's snapshot MATK per stack at the end of the afflicted target's turn
- Existing Burn stacks are converted when Lantern Mode begins
- Any Burn applied during Lantern Mode is immediately applied as Soul Burn instead
- Soul Burn is removed when Lantern Mode ends

## Important
- No Rainbow Food changes are introduced by this patch.
- No database reset is required.
- Character art does not need to be replaced for this skill update.
