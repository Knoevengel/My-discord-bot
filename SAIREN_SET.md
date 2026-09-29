# Sairen — Drift, 6★ Mantis Punisher

Stars and base stats are the ones already in heroes.csv (HP 1183 / ATK 153 / DEF 133 / MATK 153 / MDEF 133 / Speed 53). Crit set to 20% / 190% (placeholder).

## Skills
**Gale Sever** (S1) — 110% ATK Drift damage, 1 enemy. Also the strike used by every counter and preempt.
`damage_atk:1.10:enemy`

**Silent Storm** (S2, counter stance) — Guard lasts only the round it is cast. Every damage instance that lands on her that round triggers an immediate Gale Sever on whoever dealt it, plus 1 Tear stack on them.
- Multi-hit skills counter once per hit; multi-effect skills counter per damage instance.
- Burn / Soul Burn ticks give ONE counter per tick, against whoever applied the oldest still-active stack (even if the tick kills her).
- Also gives +15% DEF/MDEF for 1 turn.
`buff_def:0.15:self:1|buff_mdef:0.15:self:1`

**Mantis Storm** (S3) — 200% ATK to 1 enemy; if the target carries Tear, a bonus 60% ATK hit follows. Normal every-3rd-round slot.
`damage_atk:2.0:enemy|damage_atk:0.6:enemy@if_status:tear`

**Wind Step** (Passive, `killing_instinct`) — The first time she is hit in a round before her own turn, she strikes first with Gale Sever. Once per round; the incoming hit still lands afterwards.

## Engine notes
- Counters/preempts run with `allow_reactive=False`, so they never trigger each other or Cinderwind's Phoenix's Wrath.
- Guard is stored as `guard_round` and compared to a per-round `_round` tag, so it cannot leak into the next round.
- Not covered: plain DoT ticks (DoT does not record its source).
- Preempt and guard counter can both fire on the same first hit of a round.
- Tested in simulation, including from the real heroes.csv.

## Install
Replace `battle.py` and `heroes.csv`, restart the bot.
