# Sana — Teal, 5★

## Stats (base, pre-scaling)
| HP | ATK | DEF | MATK | MDEF | Speed | Crit Chance | Crit Damage |
|----|-----|-----|------|------|-------|-------------|--------------|
| 1761 | 221 | 204 | 221 | 204 | 50 | 5%* | 150%* |

*crit stats not explicitly set for Sana — currently falling back to the engine default (5% / 150%). Flag if you want her own values.

## Skills

**Riptide** (S1) — 125% Teal damage, 3 enemies, **2-turn cooldown**
Effects: `damage:1.25:three_enemies`

**Sunken Tendrils** (S2) — 110% Teal damage, 4 enemies, applies stacking Slow, **2-turn cooldown**
Effects: `damage:1.10:four_enemies|slow:0.10:four_enemies:2`
Each hit adds 1 Slow stack (max 5, 2-round duration, refreshes on reapplication).
Per stack: **-10% Speed**. At 5 stacks: -50% Speed (hard-capped at -60% engine-wide as a safety backstop).

**Moonfall Blossom** (S3 / Ultimate) — 130% Teal damage to **all** enemies + team buff, **3-turn cooldown**, auto-triggers below 50% HP
Effects: `damage:1.30:all_enemies|buff_atk:0.30:all_allies:1|buff_matk:0.30:all_allies:1`
Condition: `hp_below / 0.5` — fires the instant Sana's HP drops below 50%, overriding whatever S1/S2 would otherwise be off cooldown. Buffs the whole team's ATK and MATK by +30% for 1 turn.
The 3-turn cooldown on the *ult itself* is a new addition (not explicitly requested) — it stops her from re-firing every single turn while she's stuck below 50% HP. If she's below the threshold but the ult is still on cooldown, she falls back to whichever of S1/S2 is off cooldown instead of wasting the turn.
The one-time "shackle" effect originally planned for this skill has been dropped entirely — Moonfall Blossom no longer applies any control effect.

## Cooldown-based rotation (new engine feature, not Sana-exclusive)
Sana is the first hero to use cooldowns instead of the classic S1→S2→S3→repeat round-robin. Each of her own turns:
1. Cooldowns tick down by 1 (regardless of whether she's stunned/silenced that turn).
2. If HP < 50% **and** the ult isn't on cooldown → Moonfall Blossom fires, ult cooldown set to 3.
3. Otherwise, whichever of Riptide/Sunken Tendrils is off cooldown fires first (Riptide checked before Sunken Tendrils), cooldown reset to 2.
4. If both are still on cooldown and the ult didn't trigger → plain Basic Attack.

Any future hero can opt into this same system just by setting `skill1_cooldown`/`skill2_cooldown`/`skill3_cooldown` in heroes.csv — heroes that leave those columns blank (everyone else right now) are completely unaffected and keep the original round-robin.

## Slow (now stacking, engine-wide)
Previously a flat refresh-only debuff (still used once by Nahilra, unchanged behavior). Now stacks up to **5**, each stack contributing its own configured Speed-reduction amount (Sana: 10%/stack), capped at -60% total as a hard safety backstop. Duration refreshes to the longest of current/new on reapplication.

## Battle loop (typical)
Turn 1: Riptide — hits 3 enemies, 2-turn cooldown starts
Turn 2: Sunken Tendrils — hits 4 enemies, stacks Slow, 2-turn cooldown starts
Turn 3+: Riptide/Sunken Tendrils alternate as they come off cooldown, or Basic Attack if both are down — UNLESS Sana drops below 50% HP, in which case Moonfall Blossom interrupts immediately for a full-wipe nuke + team buff, then goes on its own 3-turn cooldown before it can trigger again.

## Verified
Ran two live simulated battles through `battle.py` directly:
- 4v3 fight: confirmed Riptide → Sunken Tendrils (Slow stacking visibly logged, "1/5 stacks, -10% Speed") → Moonfall Blossom auto-firing the instant HP crossed below 50%, correctly buffing both Sana and an ally for 1 turn.
- 1v1 lopsided fight: confirmed the plain rotation (Riptide) with no errors.
No runtime errors in either run.
