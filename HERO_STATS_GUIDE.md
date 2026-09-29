# Hero stat system

## How heroes.csv stats work
`heroes.csv` holds a hero's stats as a **fresh copy of its own rarity at Level 1**. Everything else (levels, star-ups, breakthroughs) is growth applied by the engine, counted from the rarity the hero was summoned at.

### The 4-star reference (agile physical attacker)
| HP | ATK | DEF | Speed | Crit chance | Crit damage |
|---|---|---|---|---|---|
| 760 | 175 | 73 | 89 | 5% | 150% |

**This is a 4-star yardstick only.** 4-stars are the weak tier, so 5-star and 6-star heroes are authored on their own, bigger scales (blank cells default to these too, see `AUTO_HERO_STATS` in config.py):

| Rarity | Base scale vs the 4-star | Blank-cell default (HP / ATK / DEF / Speed) |
|---|---|---|
| 4-star | x1.0 | 760 / 175 / 73 / 89 |
| 5-star | about x2.0 | 1520 / 350 / 146 / 98 |
| 6-star | about x3.2 | 2432 / 560 / 234 / 107 |

Author each hero's own spread on its rarity's scale, then run `python balance_tune.py`. It balances heroes against others of the SAME rarity (each at its level cap); the gap between rarities comes only from the scale you author.

## Growth (progression.py)
- **Level:** +10% of the base per level (`LEVEL_STAT_GROWTH`).
- **Level caps (your table):** author-set per star tier, no cap from 20 stars on:
  | Stars | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20+ |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | Cap | 20 | 30 | 50 | 65 | 100 | 120 | 150 | 170 | 190 | 220 | 240 | 270 | 300 | 330 | 370 | 390 | none |
  Past level 390 (19-star's cap, the last authored tier), EXP cost stops growing linearly and starts compounding **x1.06 per level** (`EXP_HARD_GROWTH_RATE`) - levelling becomes ridiculously difficult without a hard stop.
- **Stars:** every star-up multiplies all stats by 1.25 (`STAR_STAT_GROWTH`), counted from the hero's origin star.
- **Breakthroughs:** reaching **8, 11, 13, 15 and 20 stars** gives an extra one-time multiplier on all stats on top of the +25%: **+20%, +25%, +30%, +35%, +60%** (`STAR_BREAKTHROUGH_BONUS`).
- **Speed** grows gently on purpose (+1% per level, x1.03 per star, x1.03 per breakthrough), so turn order stays readable.
- **5-star vs 6-star:** the two are tuned to be CLOSE in power (balanced together as one pool, each at its own level cap) - 6-star is meant to be *harder to raise*, not stronger. Every star-up on a 6-star-origin hero costs **1.5x** the Memory Stones a 5-star-origin hero pays for the same star-up (`RARITY_MEMORY_STONE_MULTIPLIER` in progression.py).

## Authoring rules
- `crit_chance` is a percent (5 = 5%). `crit_damage` is the TOTAL percent (150 = 1.5x, 183 = 1.83x).
- Damage = ATK (or MATK) minus half the defender's DEF (or MDEF), never below 10% of the attacker's stat (`MIN_DAMAGE_RATIO`).
- Healing = healer's MATK x skill value x `HEAL_SCALE` (config.py); Burn = burner's MATK x `BURN_STACK_RATIO` per stack.

## Battle Power (BP)
`BP = (2 x offense + 0.35 x HP + 2 x defense + 1.5 x Speed) x (1 + crit chance x (crit damage - 1))`, where offense is the higher of ATK/MATK plus a quarter of the other, and defense is the average of DEF and MDEF. Your profile BP is the sum of your 6 strongest different heroes. A fresh 4-star is about 1,000 BP, a fresh 6-star about 2,000, and a fully upgraded hero is in the millions.

## Not built yet (mentioned for later)
Control res, crit res, crit damage res, physical/magic defense split, physical/magic penetration.

## Role-specific tuning notes
- **Nyx (tank → damage):** stats shifted toward more HP/DEF and less raw ATK. Her kit (Blood Debt) already sacrifices HP to stack a damage bonus, takes 20% less damage below 60% HP, and auto-unleashes her ultimate at 3+ stacks below 60% HP - the stat shift makes her survive to actually use that pattern instead of just being a squishy attacker with a gimmick.
- **Rhaiven (crit + defense shred, hard to remove):** Skill 2 now also applies `debuff_def:0.20:enemy:2` (weakens the target's DEF for 2 turns). Skill 3 now also applies `lifesteal_total:0.30:self` (heals her for 30% of the damage it deals), giving her sustain to go with her high crit stats.
- **Combat log:** every round now opens with a `── Round N ──` header in the log, so turns are grouped instead of one unbroken wall of lines.
- **Discord text limit:** `battle.paginate_log()` splits a full log into browsable pages with nothing dropped. Story-fight results and `/admin test-battle` both get a **📜 Full Log** button that opens an ephemeral, owner-only pager (Previous/Next) over the whole fight - a better fallback than the embed's own truncated description or a downloaded .txt.
- **Round pacing:** across 1v1 to 5v5 lineups at each hero's own level cap, mean fight length is currently 7.6-10.1 rounds (target was 7-12). The median is lower (5-7) because a large share of fights end fast, balanced out by a smaller share (about 9-26%, worse in 1v1) that run long or hit the 50-round stalemate cap (~1-3%, mostly heal-heavy matchups). The mean lands in range, but the spread is wide - worth another pass if the fast/slow split feels off in practice.

## Row targeting (frontline / backline / mirror slot)
Party slots 1-6 now map to a row (front/middle/back) via `PARTY_ROW_BY_SLOT` in config.py -
**this mapping is a placeholder guessed from pixel coordinates and needs to be confirmed
against the real party formation image** (see the note in config.py itself).

New target keywords for skill effect strings (`kind:value:target:duration`):
- `frontline` / `backline` - single target, picks a random enemy from that row. If the whole
  row has fallen, it reaches into the next row instead of doing nothing. Unlike plain
  `enemy`, these **ignore Taunt** on purpose - that's what lets an assassin or a burst
  skill actually reach the backline past a frontliner guarding the party.
- `all_frontline` / `all_backline` - hits every enemy currently in that row.
- `mirror` / `same_slot` - hits the enemy in the exact same slot number as the caster
  (slot 3 hits slot 3). Falls back to a random enemy if nothing occupies that slot.

None of the current roster's skills use these yet - nobody's targeting was changed without
being asked. To make a hero an assassin/backline-burst type, just change that skill's
`target` (or its effect string's target) to `backline`; row placement then actually matters
for it. `hero_text.py` already writes plain-English descriptions for all of these.
