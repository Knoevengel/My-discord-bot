"""Re-balance helper. Run from your bot folder:  python balance_tune.py

Simulates thousands of random 1v1 / 2v2 / 3v3 / 5v5 fights and nudges each
hero's overall stat size until it wins about half of its fights AGAINST HEROES OF
THE SAME RARITY (4-star heroes fight 4-star heroes, and so on, each at its own
level cap). Rarity gaps are set by the base scale you author in heroes.csv
(roughly 2x for 5-star and 3.2x for 6-star), never by this tool.
It uses a THROWAWAY database, so your real gacha.db is never touched, and writes
heroes_tuned.csv - review it, then copy it over heroes.csv. Speed and crit are
never changed.
"""
import csv
import os
import random
import statistics
import tempfile

import database

database.DB_PATH = os.path.join(tempfile.mkdtemp(), "balance_scratch.db")  # never the live DB
import battle
import custom_warriors
import progression

ITERATIONS = 16
STATS = ("base_hp", "base_atk", "base_def", "base_matk", "base_mdef")


def main():
    database.init_db()
    custom_warriors.sync_custom_warriors()
    rows = {r["name"]: dict(r) for r in database.get_all_catalog_warriors()}
    orig = {n: {k: rows[n][k] for k in STATS} for n in rows}
    scale = {n: 1.0 for n in rows}

    # 5-star and 6-star are meant to be close in power (only 6-star is HARDER
    # TO RAISE, via config.py's RARITY_MEMORY_STONE_COST - not weaker in a
    # fight), so they are balanced together as one pool. 4-star stays its own,
    # intentionally weaker, tier.
    POWER_GROUP = {4: 4, 5: 56, 6: 56}
    groups: dict[int, list[str]] = {}
    for n in rows:
        groups.setdefault(POWER_GROUP.get(int(rows[n]["stars"]), int(rows[n]["stars"])), []).append(n)
    groups = {rarity: names for rarity, names in groups.items() if len(names) >= 2}

    def build(n):
        w = dict(rows[n])
        stars = int(w["stars"])
        w["base_stars"] = stars
        w["level"] = progression.level_cap_for_stars(stars)  # each hero at its own level cap (5* and 6* differ!)
        for k in STATS:
            w[k] = max(1, int(round(orig[n][k] * scale[n])))
        return w

    def evaluate(g_small, g_big, seed=11):
        rnd = random.Random(seed)
        pooled = {n for names in groups.values() for n in names}
        wins = {n: 0 for n in rows if n in pooled}
        seen = dict.fromkeys(wins, 0)
        for names in groups.values():
            for size in range(1, min(len(names) // 2, 5) + 1):
                games = g_big if size >= 4 else g_small
                for g in range(games):
                    pool = names[:]
                    rnd.shuffle(pool)
                    a, b = pool[:size], pool[size:size * 2]
                    random.seed(g * 7 + size)
                    r = battle.resolve_battle({i + 1: build(n) for i, n in enumerate(a)}, {}, [build(n) for n in b])
                    for n in a:
                        seen[n] += 1
                        wins[n] += r["won"]
                    for n in b:
                        seen[n] += 1
                        wins[n] += 1 - r["won"]
        return {n: wins[n] / max(seen[n], 1) * 100 for n in wins}

    for it in range(ITERATIONS):
        win = evaluate(400, 250)
        err = statistics.mean(abs(v - 50) for v in win.values())
        print(f"iteration {it + 1}/{ITERATIONS}: average miss {err:.1f} points")
        for n, v in win.items():
            scale[n] = min(2.0, max(0.5, scale[n] * (1 + 0.006 * (50 - v))))

    final = evaluate(1000, 600, seed=99)
    print("\nhero          rarity  scale   win%   suggested stats (hp/atk/def/matk/mdef)")
    for n in final:
        s = {k: max(1, int(round(orig[n][k] * scale[n]))) for k in STATS}
        print(f"{n:13} {rows[n]['stars']}★     x{scale[n]:.2f}  {final[n]:4.0f}%   "
              f"{s['base_hp']}/{s['base_atk']}/{s['base_def']}/{s['base_matk']}/{s['base_mdef']}")

    with open("heroes.csv", newline="", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))
        fields = list(csv_rows[0].keys())
    for r in csv_rows:
        for k in STATS:
            r[k] = str(max(1, int(round(orig[r["name"]][k] * scale[r["name"]]))))
    with open("heroes_tuned.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(csv_rows)
    print("\nWrote heroes_tuned.csv (your heroes.csv was not changed).")


if __name__ == "__main__":
    main()
