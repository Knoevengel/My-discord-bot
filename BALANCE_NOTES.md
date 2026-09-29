# Balance notes

See `HERO_STATS_GUIDE.md` for the stat system (reference hero, growth curves, crit units, Battle Power).

## What the game runs on now
- `heroes.csv` holds real 4-star / Lv 1 stats. Level: +10% of base per level. Stars: x1.30 each (20 stars = x66). Speed grows gently.
- Level caps: 20 at 4 stars, +5 per extra star. Star Up (button and `/tierup`) needs the hero at its current cap.
- Heroes are role-balanced at the SAME stars and level (about 46%-57% win rate each). Rarity power comes from stars.
- Fights are not forced to last any minimum: at equal stars they run about 9 rounds (median) and almost never end by round 2.
- Other engine rules still in place: Burn `BURN_STACK_RATIO` (config.py), timed non-stacking buffs (3 turns default), random speed ties, Cinderwind gains Flamebound when enemies fall, healing = MATK x value x `HEAL_SCALE`, damage never below 10% of the attacker's stat.

## Tools
- `python balance_tune.py` re-balances roles after you add or change heroes (writes `heroes_tuned.csv`, never touches your real database).
- `/admin test-battle` simulates any fight, with win-rate stats over many runs.

## Rarity scale and breakthroughs (latest change)
- heroes.csv stats now mean "a fresh copy of the hero's own rarity at Lv1". 5-star heroes were rescaled x2.0 and 6-star x3.2 (speed x1.1 / x1.2), then re-tuned against others of the same rarity.
- Star-ups multiply stats by 1.25 from the hero's summon star (was 1.30 from 4 stars); breakthroughs at 8/11/13/15/20 stars add +20/25/30/35/60% on top.
- The tuner (`balance_tune.py`) now balances within each rarity only.

## Level caps, breakthroughs, rarity parity, combat log (latest change)
- Level caps now follow the author's exact table (5★ 30 → 19★ 390, 20★+ uncapped); EXP cost compounds x1.06/level past 390.
- Breakthroughs at 8/11/13/15/20 stars: +20/25/30/35/60% to all stats, on top of the usual +25% per star.
- 5★ and 6★ rebalanced to be close in power (tuned together as one pool); 6★ instead costs 1.5x the Memory Stones per star-up.
- Nyx reshaped tankier (more HP/DEF, less ATK). Rhaiven's skill 2 now shreds enemy DEF; skill 3 now heals off its own damage.
- Battle log now has a "── Round N ──" header per round.
- Added battle.paginate_log() + a "📜 Full Log" button (story fights and /admin test-battle) for reading a full log in Discord without truncation.
