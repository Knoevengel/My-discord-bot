# Corvayne — Shade, 6★

## Stats (base, pre-scaling)
| HP | ATK | DEF | MATK | MDEF | Speed | Crit Chance | Crit Damage |
|----|-----|-----|------|------|-------|-------------|--------------|
| 1260 | 162 | 143 | 162 | 143 | 53 | 15% | 175% |

## Skills

**Blackwing Rend** (S1) — 150% Shade damage, same_slot target
Hits the one enemy in the same slot as Corvayne. New `same_slot` selector
(already in battle.py) — falls back to a random enemy if no one mirrors her slot.

**Omen Feather** (S2) — 115% Shade damage to 2 targets, applies Tear
Effects: `damage:1.15:two_enemies|tear:1:two_enemies:2`
Deals damage AND applies 1 Tear stack to each hit target (2-round duration).
Tear is exclusive to S2 — S1 does not apply it.

**Thousand-fold Raven** (S3 / Ultimate) — 2.4x primary + 0.6x splash, fires round 3
Effects: `damage:2.4:enemy|damage:0.6:other_enemies`
Condition: `turn_min / 3` — fires on round 3 and every 3rd round thereafter.
Hits one primary target for 2.4x and all other living enemies for 0.6x.

**Storm Perch** (Passive) — +20% damage to targets with 2+ Tear stacks
Implemented as a hook in `_compute_damage` / `_compute_atk_damage`.

## Tear debuff (applied by S2)
- Stacking, max **5 stacks**, **2-round duration** (refreshes on reapplication)
- Per stack: **−4% DEF/MDEF** and **−2% healing received**
- At 5 stacks: −20% DEF/MDEF, −10% healing received
- Registered generically — any future hero can also apply/read Tear

## Battle loop
Round 1: S1 (Blackwing Rend) — hits same-slot enemy
Round 2: S2 (Omen Feather) — damages + stacks Tear on 2 targets
Round 3: S3 (Thousand-fold Raven) — nukes with Storm Perch amplifying Tear'd targets
Then repeats from S1.
