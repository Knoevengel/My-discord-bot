import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "gacha.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode: writes go to a separate log file instead of rewriting/fsyncing
    # the whole database on every commit. This is the single biggest fix for
    # "every button click takes a second or two" on SQLite - the old default
    # (rollback-journal) mode does a full fsync per write, which is especially
    # slow on Windows, antivirus-scanned folders, or cloud-synced (OneDrive/
    # Dropbox) directories. synchronous=NORMAL is the recommended pairing with
    # WAL: still crash-safe, without fsyncing on every single write.
    # These PRAGMAs are cheap no-ops once already set, so it's fine to run them
    # on every connection rather than just once at startup.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    """Create all tables if they don't already exist. Safe to call every startup."""
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")  # persists in the DB file, so it only needs setting once
    cur = conn.cursor()

    # One row per Discord user
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            display_name TEXT,
            solite INTEGER NOT NULL DEFAULT 100,
            summon_charms INTEGER NOT NULL DEFAULT 5,
            exp INTEGER NOT NULL DEFAULT 0,
            memory_stones INTEGER NOT NULL DEFAULT 0,
            pity_counter INTEGER NOT NULL DEFAULT 0,
            last_daily_claim TEXT,
            story_chapter INTEGER NOT NULL DEFAULT 1,
            avatar TEXT,
            story_stage TEXT NOT NULL DEFAULT 'intro'
        )
    """)

    # Migration: add story_stage to older databases. 'intro' = hasn't started
    # reading this chapter's story yet; 'fight' = already read it and is at
    # (or has failed) the fight panel - /level should jump straight back here
    # on a loss instead of re-showing the story text.
    cur.execute("PRAGMA table_info(users)")
    existing_user_columns = {row[1] for row in cur.fetchall()}
    if "story_stage" not in existing_user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN story_stage TEXT NOT NULL DEFAULT 'intro'")
    if "exp" not in existing_user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN exp INTEGER NOT NULL DEFAULT 0")
    if "memory_stones" not in existing_user_columns:
        cur.execute("ALTER TABLE users ADD COLUMN memory_stones INTEGER NOT NULL DEFAULT 0")

    # Master list of all warriors that CAN be pulled (the "catalog")
    # This is placeholder content for now - real names/art come later
    cur.execute("""
        CREATE TABLE IF NOT EXISTS warrior_catalog (
            warrior_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            element TEXT NOT NULL,
            rarity TEXT NOT NULL,
            stars INTEGER NOT NULL DEFAULT 4,
            base_hp INTEGER NOT NULL,
            base_atk INTEGER NOT NULL,
            base_def INTEGER NOT NULL,
            base_matk INTEGER NOT NULL,
            base_mdef INTEGER NOT NULL,
            base_speed INTEGER NOT NULL,
            crit_chance REAL NOT NULL DEFAULT 0.05,
            crit_damage REAL NOT NULL DEFAULT 1.5,
            skill_1 TEXT,
            skill_2 TEXT,
            skill_3 TEXT,
            passive TEXT,
            banner_id TEXT,
            skill1_type TEXT DEFAULT 'damage',
            skill1_power REAL DEFAULT 1.0,
            skill1_target TEXT DEFAULT 'enemy',
            skill2_type TEXT DEFAULT 'damage',
            skill2_power REAL DEFAULT 1.0,
            skill2_target TEXT DEFAULT 'enemy',
            skill3_type TEXT DEFAULT 'damage',
            skill3_power REAL DEFAULT 1.0,
            skill3_target TEXT DEFAULT 'enemy',
            skill3_condition TEXT,
            skill3_value REAL,
            passive_type TEXT,
            passive_power REAL DEFAULT 0.0,
            passive_target TEXT DEFAULT 'self',
            hero_key TEXT,
            skill1_effects TEXT,
            skill2_effects TEXT,
            skill3_effects TEXT,
            passive_effects TEXT,
            skill1_cooldown INTEGER,
            skill2_cooldown INTEGER,
            skill3_cooldown INTEGER
        )
    """)

    # Migration: add the skill-mechanic columns to older databases that were
    # created before these existed (CREATE TABLE IF NOT EXISTS doesn't add
    # columns to an existing table).
    cur.execute("PRAGMA table_info(warrior_catalog)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "stars" not in existing_columns:
        cur.execute("ALTER TABLE warrior_catalog ADD COLUMN stars INTEGER NOT NULL DEFAULT 4")
    # Legacy rarity -> summon star type.
    cur.execute("UPDATE warrior_catalog SET stars = CASE rarity WHEN 'Transcendent' THEN 6 WHEN 'Radiant' THEN 5 ELSE stars END")
    cur.execute("UPDATE warrior_catalog SET stars = 4 WHERE stars NOT IN (4,5,6) OR stars IS NULL")
    skill_columns = {
        "skill1_type": "TEXT DEFAULT 'damage'",
        "skill1_power": "REAL DEFAULT 1.0",
        "skill1_target": "TEXT DEFAULT 'enemy'",
        "skill2_type": "TEXT DEFAULT 'damage'",
        "skill2_power": "REAL DEFAULT 1.0",
        "skill2_target": "TEXT DEFAULT 'enemy'",
        "skill3_type": "TEXT DEFAULT 'damage'",
        "skill3_power": "REAL DEFAULT 1.0",
        "skill3_target": "TEXT DEFAULT 'enemy'",
        "skill3_condition": "TEXT",
        "skill3_value": "REAL",
        "passive_type": "TEXT",
        "passive_power": "REAL DEFAULT 0.0",
        "passive_target": "TEXT DEFAULT 'self'",
        "hero_key": "TEXT",
        "skill1_effects": "TEXT",
        "skill2_effects": "TEXT",
        "skill3_effects": "TEXT",
        "passive_effects": "TEXT",
        # Cooldown-based skill system (first used by Sana). Blank/NULL for
        # every other hero means "no cooldown" - they keep using the classic
        # S1->S2->S3 round-robin untouched (see _uses_cooldown_system in battle.py).
        "skill1_cooldown": "INTEGER",
        "skill2_cooldown": "INTEGER",
        "skill3_cooldown": "INTEGER",
    }
    for col_name, col_def in skill_columns.items():
        if col_name not in existing_columns:
            cur.execute(f"ALTER TABLE warrior_catalog ADD COLUMN {col_name} {col_def}")

    # Migration: older databases won't have banner_id yet since it was added
    # after the table already existed - CREATE TABLE IF NOT EXISTS doesn't add
    # new columns to an existing table, so add it manually if missing.
    # (This column is now legacy/unused - see warrior_banners below - but is
    # left in place rather than dropped, to avoid relying on ALTER TABLE DROP
    # COLUMN which isn't supported on all SQLite versions.)
    cur.execute("PRAGMA table_info(warrior_catalog)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "stars" not in existing_columns:
        cur.execute("ALTER TABLE warrior_catalog ADD COLUMN stars INTEGER NOT NULL DEFAULT 4")
    # Legacy rarity -> summon star type.
    cur.execute("UPDATE warrior_catalog SET stars = CASE rarity WHEN 'Transcendent' THEN 6 WHEN 'Radiant' THEN 5 ELSE stars END")
    cur.execute("UPDATE warrior_catalog SET stars = 4 WHERE stars NOT IN (4,5,6) OR stars IS NULL")
    if "banner_id" not in existing_columns:
        cur.execute("ALTER TABLE warrior_catalog ADD COLUMN banner_id TEXT")

    # Many-to-many: which banners a warrior can be pulled from. A warrior with
    # ZERO rows here belongs to the Standard Summon pool by default. A warrior
    # can belong to any number of banners (including "standard" explicitly,
    # alongside others, if you want it in Standard AND a special banner).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS warrior_banners (
            warrior_id INTEGER NOT NULL,
            banner_id TEXT NOT NULL,
            PRIMARY KEY (warrior_id, banner_id)
        )
    """)

    # One-time migration: copy any old single banner_id assignments into the
    # new many-to-many table. INSERT OR IGNORE makes this safe to run every
    # startup without creating duplicates.
    cur.execute("SELECT warrior_id, banner_id FROM warrior_catalog WHERE banner_id IS NOT NULL AND banner_id != ''")
    for warrior_id, old_banner_id in cur.fetchall():
        cur.execute(
            "INSERT OR IGNORE INTO warrior_banners (warrior_id, banner_id) VALUES (?, ?)",
            (warrior_id, old_banner_id),
        )

    # A user's owned copy of a warrior (many-to-many: user <-> catalog warrior)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_warriors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            warrior_id INTEGER NOT NULL,
            stars INTEGER NOT NULL DEFAULT 4,
            base_stars INTEGER NOT NULL DEFAULT 4,
            level INTEGER NOT NULL DEFAULT 1,
            duplicate_copies INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (warrior_id) REFERENCES warrior_catalog(warrior_id)
        )
    """)

    # Migration for the instance-based character system. Multiple copies of the
    # same hero must be able to exist independently because later star-ups can
    # require a 6★/8★ copy of the same hero. Older databases used a unique
    # (user_id, warrior_id) row plus duplicate_copies; we expand those stored
    # duplicate counts into real rows.
    cur.execute("PRAGMA table_info(user_warriors)")
    existing_user_warrior_columns = {row[1] for row in cur.fetchall()}
    if "duplicate_copies" not in existing_user_warrior_columns:
        cur.execute("ALTER TABLE user_warriors ADD COLUMN duplicate_copies INTEGER NOT NULL DEFAULT 0")
    if "base_stars" not in existing_user_warrior_columns:
        cur.execute("ALTER TABLE user_warriors ADD COLUMN base_stars INTEGER NOT NULL DEFAULT 4")
    # base_stars is the original summon type (4/5/6). stars is the CURRENT star
    # tier, which legitimately climbs to 20 - so it must only be repaired when
    # it's out of the real 4-20 range. (This used to reset every hero above 6★
    # back to 4★ on each bot restart.)
    cur.execute("UPDATE user_warriors SET base_stars = 4 WHERE base_stars IS NULL OR base_stars NOT IN (4,5,6)")
    cur.execute("UPDATE user_warriors SET stars = 4 WHERE stars IS NULL OR stars < 4")
    cur.execute("UPDATE user_warriors SET stars = 20 WHERE stars > 20")

    # Rebuild user_warriors once if the old UNIQUE(user_id, warrior_id) index exists.
    unique_pairs = []
    for idx in cur.execute("PRAGMA index_list(user_warriors)").fetchall():
        if not idx[2]:
            continue
        idx_name = idx[1]
        cols = [r[2] for r in cur.execute(f"PRAGMA index_info(\"{idx_name}\")").fetchall()]
        unique_pairs.append(cols)
    if ["user_id", "warrior_id"] in unique_pairs:
        party_rows = []
        if cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_party'").fetchone():
            party_rows = cur.execute("SELECT user_id, position, user_warrior_id FROM user_party").fetchall()
            cur.execute("DROP TABLE user_party")

        cur.execute("ALTER TABLE user_warriors RENAME TO user_warriors_old")
        cur.execute("""
            CREATE TABLE user_warriors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                warrior_id INTEGER NOT NULL,
                stars INTEGER NOT NULL DEFAULT 4,
                base_stars INTEGER NOT NULL DEFAULT 4,
                level INTEGER NOT NULL DEFAULT 1,
                duplicate_copies INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (warrior_id) REFERENCES warrior_catalog(warrior_id)
            )
        """)
        old_rows = cur.execute("SELECT id, user_id, warrior_id, stars, base_stars, level, duplicate_copies FROM user_warriors_old").fetchall()
        for row in old_rows:
            cur.execute(
                "INSERT INTO user_warriors (id, user_id, warrior_id, stars, base_stars, level, duplicate_copies) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (row[0], row[1], row[2], row[3], row[4], row[5]),
            )
            copies = max(0, int(row[6] or 0))
            for _ in range(copies):
                cur.execute(
                    "INSERT INTO user_warriors (user_id, warrior_id, stars, base_stars, level, duplicate_copies) VALUES (?, ?, ?, ?, 1, 0)",
                    (row[1], row[2], row[4], row[4]),
                )
        cur.execute("DROP TABLE user_warriors_old")

        cur.execute("""
            CREATE TABLE user_party (
                user_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 6),
                user_warrior_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, position),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (user_warrior_id) REFERENCES user_warriors(id)
            )
        """)
        for row in party_rows:
            cur.execute("INSERT INTO user_party (user_id, position, user_warrior_id) VALUES (?, ?, ?)", tuple(row))

    # Food is an inventory, separated by element and star tier. The Lab will
    # populate these quantities once its conversion rates are authored.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_food (
            user_id INTEGER NOT NULL,
            element TEXT NOT NULL,
            stars INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, element, stars),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # Remove legacy Rainbow-food rows from older builds. Any-element food is
    # represented only by the star-up requirement itself, never as an item.
    cur.execute("DELETE FROM user_food WHERE LOWER(element) = 'rainbow'")

    # Generic item inventory. Items are created automatically the first time
    # the owner gives one to a user, so new item types never require a code/db
    # migration just to exist.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_items (
            user_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            item_name TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0,
            item_type TEXT NOT NULL DEFAULT 'item',
            PRIMARY KEY (user_id, item_key),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # A user's battle party - 6 positions, each pointing at one of their owned warriors.
    # Position layout/meaning (front row, back row, etc.) will be decided later -
    # for now this just tracks which warrior sits in which of the 6 slots.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_party (
            user_id INTEGER NOT NULL,
            position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 6),
            user_warrior_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, position),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (user_warrior_id) REFERENCES user_warriors(id)
        )
    """)

    # Cosmetic avatars a user has purchased from the shop. The `avatar` column
    # on `users` stores which owned one is currently equipped.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_avatars (
            user_id INTEGER NOT NULL,
            avatar_id TEXT NOT NULL,
            PRIMARY KEY (user_id, avatar_id)
        )
    """)

    # Story chapters - authored via story_chapters.csv and synced in by
    # sync_story.py on startup. `users.story_chapter` tracks which chapter
    # number a user is currently on. `description` is a short teaser shown
    # on the initial /level screen; `story_text` is the full narrative shown
    # after clicking Start, before the fight.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_chapters (
            chapter_number INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            story_text TEXT,
            boss_name TEXT NOT NULL,
            boss_element TEXT NOT NULL,
            boss_stars INTEGER NOT NULL DEFAULT 4,
            boss_hp INTEGER NOT NULL,
            boss_atk INTEGER NOT NULL,
            boss_def INTEGER NOT NULL,
            boss_matk INTEGER NOT NULL,
            boss_mdef INTEGER NOT NULL,
            boss_speed INTEGER NOT NULL,
            reward_solite INTEGER NOT NULL DEFAULT 0,
            reward_charms INTEGER NOT NULL DEFAULT 0,
            reward_exp INTEGER NOT NULL DEFAULT 0,
            enemy1 TEXT, enemy2 TEXT, enemy3 TEXT,
            enemy4 TEXT, enemy5 TEXT, enemy6 TEXT,
            enemy1_level INTEGER NOT NULL DEFAULT 1,
            enemy2_level INTEGER NOT NULL DEFAULT 1,
            enemy3_level INTEGER NOT NULL DEFAULT 1,
            enemy4_level INTEGER NOT NULL DEFAULT 1,
            enemy5_level INTEGER NOT NULL DEFAULT 1,
            enemy6_level INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("PRAGMA table_info(story_chapters)")
    existing_chapter_columns = {row[1] for row in cur.fetchall()}
    if "story_text" not in existing_chapter_columns:
        cur.execute("ALTER TABLE story_chapters ADD COLUMN story_text TEXT")
    cur.execute("PRAGMA table_info(story_chapters)")
    existing_chapter_columns = {row[1] for row in cur.fetchall()}
    if "reward_exp" not in existing_chapter_columns:
        cur.execute("ALTER TABLE story_chapters ADD COLUMN reward_exp INTEGER NOT NULL DEFAULT 0")
    # Migration: designer-set enemy teams (enemy1..enemy6 + their levels) -
    # lets a chapter's fight use real heroes from the catalog instead of just
    # a single generic boss. Older databases predate this and need it added.
    cur.execute("PRAGMA table_info(story_chapters)")
    existing_chapter_columns = {row[1] for row in cur.fetchall()}
    for i in range(1, 7):
        if f"enemy{i}" not in existing_chapter_columns:
            cur.execute(f"ALTER TABLE story_chapters ADD COLUMN enemy{i} TEXT")
        if f"enemy{i}_level" not in existing_chapter_columns:
            cur.execute(f"ALTER TABLE story_chapters ADD COLUMN enemy{i}_level INTEGER NOT NULL DEFAULT 1")
    # Migration: older databases scaled bosses off word-based rarity labels
    # (Dim/Glimmer/Bright/Luminous/Radiant/Transcendent), which no longer
    # matches anything in STAR_MULTIPLIER (keyed by 4/5/6) - that silently
    # scaled every boss at the weakest multiplier. Replace it with a real
    # boss_stars column using the same 4/5/6 star scale as heroes.
    cur.execute("PRAGMA table_info(story_chapters)")
    existing_chapter_columns = {row[1] for row in cur.fetchall()}
    if "boss_stars" not in existing_chapter_columns:
        cur.execute("ALTER TABLE story_chapters ADD COLUMN boss_stars INTEGER NOT NULL DEFAULT 4")
        if "boss_rarity" in existing_chapter_columns:
            cur.execute("""
                UPDATE story_chapters SET boss_stars = CASE
                    WHEN boss_rarity = 'Transcendent' THEN 6
                    WHEN boss_rarity IN ('Radiant', 'Luminous') THEN 5
                    WHEN boss_rarity IN ('4', '5', '6') THEN CAST(boss_rarity AS INTEGER)
                    ELSE 4
                END
            """)

    # Tracks progress on daily tasks. `date` is an ISO date string (YYYY-MM-DD) so
    # progress naturally resets each day without a cleanup job - a new date just
    # means a fresh row starting at 0.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_progress (
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            task_key TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, date, task_key)
        )
    """)

    # user_warriors has no index other than its own `id` primary key, so every
    # "get this player's warriors" lookup (by far the most common query in the
    # bot - /heroes, /profile, /lab, /party, Battle Power, every star-up check)
    # was a full table scan. This is the one genuinely missing index; every
    # other hot table already has user_id as the leading column of its own
    # primary key, which SQLite already indexes automatically.
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_warriors_user ON user_warriors(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_warriors_warrior ON user_warriors(warrior_id)")

    conn.commit()
    conn.close()


def get_or_create_user(user_id: int, display_name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users (user_id, display_name) VALUES (?, ?)",
            (user_id, display_name),
        )
        conn.commit()
        cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
    conn.close()
    return dict(row)


def update_user_field(user_id: int, field: str, value):
    """Generic single-field updater. `field` must be a trusted, hardcoded column name."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()


def add_warrior_to_user(user_id: int, warrior_id: int):
    """Add a fresh independent hero instance to a user's collection."""
    conn = get_connection()
    cur = conn.cursor()
    catalog = cur.execute(
        "SELECT stars FROM warrior_catalog WHERE warrior_id = ?", (warrior_id,)
    ).fetchone()
    base_stars = int(catalog["stars"]) if catalog else 4
    cur.execute(
        "INSERT INTO user_warriors (user_id, warrior_id, stars, base_stars, level, duplicate_copies) VALUES (?, ?, ?, ?, 1, 0)",
        (user_id, warrior_id, base_stars, base_stars),
    )
    instance_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {
        "result": "new",
        "instance_id": instance_id,
        "stars": base_stars,
        "base_stars": base_stars,
    }

def get_user_warriors(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT uw.id, uw.stars, uw.base_stars, uw.level, uw.duplicate_copies, wc.*
        FROM user_warriors uw
        JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
        WHERE uw.user_id = ?
        ORDER BY uw.stars DESC, wc.name ASC, uw.id ASC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_warrior(user_id: int, user_warrior_id: int):
    """Return one owned warrior, joined with its catalog data."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT uw.id, uw.user_id, uw.warrior_id, uw.stars, uw.base_stars, uw.level, uw.duplicate_copies, wc.*
        FROM user_warriors uw
        JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
        WHERE uw.user_id = ? AND uw.id = ?
    """, (user_id, user_warrior_id))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_meltable_warriors(user_id: int):
    """Owned warrior instances eligible for the Lab's Melt Down feature -
    i.e. every owned instance that ISN'T currently in the user's active
    party (so melting can never accidentally break their team)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT uw.id, uw.stars, uw.level, wc.name, wc.element
        FROM user_warriors uw
        JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
        WHERE uw.user_id = ?
          AND uw.id NOT IN (SELECT user_warrior_id FROM user_party WHERE user_id = ?)
        ORDER BY uw.stars DESC, wc.name ASC, uw.id ASC
    """, (user_id, user_id))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def melt_warriors(user_id: int, user_warrior_ids: list[int]):
    """Atomically melt multiple owned warrior instances into element food."""
    ids = list(dict.fromkeys(int(x) for x in user_warrior_ids))
    if not ids:
        return {"ok": False, "reason": "no_selection"}

    conn = get_connection()
    cur = conn.cursor()
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = cur.execute(
            f"""
            SELECT uw.id, uw.stars, wc.name, wc.element
            FROM user_warriors uw
            JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
            WHERE uw.user_id = ? AND uw.id IN ({placeholders})
            ORDER BY uw.id ASC
            """,
            [user_id, *ids],
        ).fetchall()
        found_ids = {int(r["id"]) for r in rows}
        missing = [x for x in ids if x not in found_ids]
        if missing:
            return {"ok": False, "reason": "not_found", "missing_ids": missing}

        party_rows = cur.execute(
            f"""
            SELECT user_warrior_id FROM user_party
            WHERE user_id = ? AND user_warrior_id IN ({placeholders})
            """,
            [user_id, *ids],
        ).fetchall()
        if party_rows:
            blocked = [int(r["user_warrior_id"]) for r in party_rows]
            return {"ok": False, "reason": "in_party", "blocked_ids": blocked}

        summary = []
        for row in rows:
            stars = int(row["stars"])
            element = row["element"]
            cur.execute("DELETE FROM user_warriors WHERE user_id = ? AND id = ?", (user_id, int(row["id"])))
            cur.execute(
                """INSERT INTO user_food (user_id, element, stars, quantity) VALUES (?, ?, ?, 1)
                   ON CONFLICT(user_id, element, stars) DO UPDATE SET quantity = quantity + 1""",
                (user_id, element, stars),
            )
            summary.append({
                "id": int(row["id"]),
                "name": row["name"],
                "element": element,
                "stars": stars,
            })

        conn.commit()
        return {
            "ok": True,
            "summary": summary,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def melt_warrior(user_id: int, user_warrior_id: int):
    """Atomically melt one owned warrior into matching element food."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        row = cur.execute("""
            SELECT uw.id, uw.stars, wc.name, wc.element
            FROM user_warriors uw
            JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
            WHERE uw.user_id = ? AND uw.id = ?
        """, (user_id, user_warrior_id)).fetchone()
        if not row:
            return {"ok": False, "reason": "not_found"}

        in_party = cur.execute(
            "SELECT 1 FROM user_party WHERE user_id = ? AND user_warrior_id = ?",
            (user_id, user_warrior_id),
        ).fetchone()
        if in_party:
            return {"ok": False, "reason": "in_party"}

        stars = int(row["stars"])
        element = row["element"]
        name = row["name"]

        cur.execute("DELETE FROM user_warriors WHERE user_id = ? AND id = ?", (user_id, user_warrior_id))

        cur.execute(
            """INSERT INTO user_food (user_id, element, stars, quantity) VALUES (?, ?, ?, 1)
               ON CONFLICT(user_id, element, stars) DO UPDATE SET quantity = quantity + 1""",
            (user_id, element, stars),
        )
        conn.commit()
        return {
            "ok": True,
            "name": name,
            "element": element,
            "stars": stars,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def level_up_warrior(user_id: int, user_warrior_id: int, levels: int, exp_cost: int, max_level: int | None = None):
    """Atomically spend EXP and raise an owned warrior's level.

    max_level is the hero's level cap (callers pass progression.effective_level_cap);
    when omitted it falls back to the absolute maximum level.
    """
    import progression

    levels = int(levels)
    exp_cost = int(exp_cost)
    if max_level is None:
        max_level = progression.MAX_WARRIOR_LEVEL
    if levels <= 0 or exp_cost < 0:
        return None

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT exp FROM users WHERE user_id = ?", (user_id,))
        user = cur.fetchone()
        cur.execute("SELECT id, level FROM user_warriors WHERE user_id = ? AND id = ?", (user_id, user_warrior_id))
        warrior = cur.fetchone()
        if not user or not warrior:
            conn.rollback()
            return None

        current_level = int(warrior["level"])
        new_level = current_level + levels
        if new_level > max_level:
            conn.rollback()
            return {"ok": False, "reason": "max_level", "level": current_level, "exp": int(user["exp"])}
        if int(user["exp"]) < exp_cost:
            conn.rollback()
            return {"ok": False, "reason": "not_enough_exp", "level": current_level, "exp": int(user["exp"])}

        cur.execute("UPDATE users SET exp = exp - ? WHERE user_id = ?", (exp_cost, user_id))
        cur.execute("UPDATE user_warriors SET level = ? WHERE user_id = ? AND id = ?", (new_level, user_id, user_warrior_id))
        conn.commit()
        return {"ok": True, "old_level": current_level, "level": new_level, "exp_spent": exp_cost, "exp_remaining": int(user["exp"]) - exp_cost}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _item_key_from_name(item_name: str) -> str:
    """Convert a display name into a stable inventory key."""
    import re
    key = re.sub(r"[^a-z0-9]+", "_", str(item_name).strip().lower()).strip("_")
    return key[:80] or "item"


def get_user_items(user_id: int):
    """Return generic item stacks owned by a user."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT item_key, item_name, quantity, item_type
           FROM user_items
           WHERE user_id = ? AND quantity > 0
           ORDER BY item_name COLLATE NOCASE ASC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_item(user_id: int, item_name: str, quantity: int, item_type: str = "item"):
    """Add any generic item to a user's inventory, creating it if needed."""
    item_name = str(item_name).strip()
    quantity = int(quantity)
    item_type = str(item_type or "item").strip() or "item"
    if not item_name:
        return {"ok": False, "reason": "invalid_name"}
    if quantity <= 0:
        return {"ok": False, "reason": "invalid_quantity"}

    item_key = _item_key_from_name(item_name)
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT item_name, quantity, item_type FROM user_items WHERE user_id = ? AND item_key = ?",
            (user_id, item_key),
        ).fetchone()
        if existing:
            # Keep the first established display name/type so future grants of
            # the same item don't create duplicate-looking stacks.
            conn.execute(
                "UPDATE user_items SET quantity = quantity + ? WHERE user_id = ? AND item_key = ?",
                (quantity, user_id, item_key),
            )
            new_quantity = int(existing["quantity"]) + quantity
            display_name = existing["item_name"]
            stored_type = existing["item_type"]
        else:
            conn.execute(
                "INSERT INTO user_items (user_id, item_key, item_name, quantity, item_type) VALUES (?, ?, ?, ?, ?)",
                (user_id, item_key, item_name, quantity, item_type),
            )
            new_quantity = quantity
            display_name = item_name
            stored_type = item_type
        conn.commit()
        return {
            "ok": True,
            "item_key": item_key,
            "item_name": display_name,
            "quantity": new_quantity,
            "item_type": stored_type,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_item_names() -> list[str]:
    """Every distinct generic item name that exists on any account (for admin autocomplete)."""
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT item_name FROM user_items ORDER BY item_name COLLATE NOCASE ASC").fetchall()
    conn.close()
    return [r["item_name"] for r in rows]


def add_memory_stones(user_id: int, amount: int) -> int:
    """Add Memory Stones and return the user's new total."""
    conn = get_connection()
    conn.execute("UPDATE users SET memory_stones = memory_stones + ? WHERE user_id = ?", (int(amount), user_id))
    conn.commit()
    row = conn.execute("SELECT memory_stones FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return int(row["memory_stones"]) if row else 0


def remove_item(user_id: int, item_name: str, quantity: int):
    """Remove a generic item stack amount; kept ready for future item usage."""
    quantity = int(quantity)
    if quantity <= 0:
        return {"ok": False, "reason": "invalid_quantity"}
    item_key = _item_key_from_name(item_name)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT quantity, item_name, item_type FROM user_items WHERE user_id = ? AND item_key = ?",
            (user_id, item_key),
        ).fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "reason": "not_found"}
        available = int(row["quantity"])
        if available < quantity:
            conn.rollback()
            return {"ok": False, "reason": "not_enough", "available": available}
        remaining = available - quantity
        if remaining <= 0:
            conn.execute("DELETE FROM user_items WHERE user_id = ? AND item_key = ?", (user_id, item_key))
        else:
            conn.execute("UPDATE user_items SET quantity = ? WHERE user_id = ? AND item_key = ?", (remaining, user_id, item_key))
        conn.commit()
        return {"ok": True, "item_name": row["item_name"], "quantity": remaining, "item_type": row["item_type"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_food_inventory(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT element, stars, quantity FROM user_food WHERE user_id = ? ORDER BY stars DESC, element ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_food_quantity(user_id: int, element: str, stars: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT quantity FROM user_food WHERE user_id = ? AND element = ? AND stars = ?",
        (user_id, element, int(stars)),
    ).fetchone()
    conn.close()
    return int(row["quantity"]) if row else 0


def add_food(user_id: int, element: str, stars: int, quantity: int):
    quantity = int(quantity)
    stars = int(stars)
    element = str(element).strip()
    if quantity <= 0:
        return
    if not element:
        raise ValueError("Food must have a real element.")
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_food (user_id, element, stars, quantity) VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id, element, stars) DO UPDATE SET quantity = quantity + excluded.quantity""",
        (user_id, element, stars, quantity),
    )
    conn.commit()
    conn.close()


def get_starable_food(user_id: int, min_quantity: int = 5, max_stars: int = 20):
    """Return food stacks that can be starred up at least once."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT element, stars, quantity FROM user_food
           WHERE user_id = ? AND stars < ? AND quantity >= ?
           ORDER BY stars ASC, element ASC""",
        (user_id, int(max_stars), int(min_quantity)),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def star_up_food(user_id: int, element: str, from_stars: int, times: int = 1,
                 cost_per_upgrade: int = 5, max_stars: int = 20):
    """Convert stacked food into the next star tier.

    Each upgrade consumes `cost_per_upgrade` copies of the same element and
    star tier and creates one copy at the next star tier. Food star-up is
    deliberately Memory-Stone free.
    """
    from_stars = int(from_stars)
    times = int(times)
    cost_per_upgrade = int(cost_per_upgrade)
    element = str(element).strip()
    if times <= 0 or cost_per_upgrade <= 0:
        return {"ok": False, "reason": "invalid_amount"}
    if from_stars >= int(max_stars):
        return {"ok": False, "reason": "max_stars"}

    required = cost_per_upgrade * times
    conn = get_connection()
    cur = conn.cursor()
    try:
        row = cur.execute(
            "SELECT quantity FROM user_food WHERE user_id = ? AND element = ? AND stars = ?",
            (user_id, element, from_stars),
        ).fetchone()
        available = int(row["quantity"]) if row else 0
        if available < required:
            conn.rollback()
            return {
                "ok": False,
                "reason": "not_enough_food",
                "available": available,
                "required": required,
            }

        cur.execute(
            "UPDATE user_food SET quantity = quantity - ? WHERE user_id = ? AND element = ? AND stars = ?",
            (required, user_id, element, from_stars),
        )
        to_stars = from_stars + 1
        cur.execute(
            """INSERT INTO user_food (user_id, element, stars, quantity) VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, element, stars) DO UPDATE SET quantity = quantity + excluded.quantity""",
            (user_id, element, to_stars, times),
        )
        conn.commit()
        return {
            "ok": True,
            "element": element,
            "from_stars": from_stars,
            "to_stars": to_stars,
            "times": times,
            "food_spent": required,
            "food_created": times,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mass_star_up_food(user_id: int, cost_per_upgrade: int = 5, max_stars: int = 20, reserve: dict | None = None):
    """Star up as much element-specific food as possible, from low to high tiers.

    Each element's food stack stars up independently. Character requirements
    that allow food of any element are resolved only during hero star-up.

    reserve: optional {(element, stars): count} of food to hold back (e.g. food
    heroes need for their next star-up); only the surplus is converted.
    """
    reserve = reserve or {}
    cost_per_upgrade = int(cost_per_upgrade)
    max_stars = int(max_stars)
    if cost_per_upgrade <= 0 or max_stars <= 4:
        return {"ok": False, "reason": "invalid_config"}

    conn = get_connection()
    cur = conn.cursor()
    summary = []
    total_upgrades = 0
    total_food_spent = 0
    total_food_created = 0
    try:
        # Process each tier in ascending order so generated food can cascade
        # into the next tier automatically.
        for star in range(4, max_stars):
            rows = cur.execute(
                """SELECT element, quantity FROM user_food
                   WHERE user_id = ? AND stars = ? AND quantity >= ?
                   ORDER BY element ASC""",
                (user_id, star, cost_per_upgrade),
            ).fetchall()
            for row in rows:
                element = row["element"]
                quantity = max(int(row["quantity"]) - int(reserve.get((element, star), 0)), 0)
                upgrades = quantity // cost_per_upgrade
                if upgrades <= 0:
                    continue
                spent = upgrades * cost_per_upgrade
                created = upgrades
                to_stars = star + 1
                cur.execute(
                    "UPDATE user_food SET quantity = quantity - ? WHERE user_id = ? AND element = ? AND stars = ?",
                    (spent, user_id, element, star),
                )
                cur.execute(
                    """INSERT INTO user_food (user_id, element, stars, quantity) VALUES (?, ?, ?, ?)
                       ON CONFLICT(user_id, element, stars) DO UPDATE SET quantity = quantity + excluded.quantity""",
                    (user_id, element, to_stars, created),
                )
                total_upgrades += upgrades
                total_food_spent += spent
                total_food_created += created
                summary.append({
                    "element": element,
                    "from_stars": star,
                    "to_stars": to_stars,
                    "upgrades": upgrades,
                    "food_spent": spent,
                    "food_created": created,
                })

        conn.commit()
        return {
            "ok": True,
            "summary": summary,
            "total_upgrades": total_upgrades,
            "total_food_spent": total_food_spent,
            "total_food_created": total_food_created,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _consume_food(cur, user_id: int, element: str, stars: int, quantity: int) -> bool:
    row = cur.execute(
        "SELECT quantity FROM user_food WHERE user_id = ? AND element = ? AND stars = ?",
        (user_id, element, int(stars)),
    ).fetchone()
    if not row or int(row["quantity"]) < int(quantity):
        return False
    cur.execute(
        "UPDATE user_food SET quantity = quantity - ? WHERE user_id = ? AND element = ? AND stars = ?",
        (int(quantity), user_id, element, int(stars)),
    )
    return True


def _available_self_copies(cur, user_id: int, warrior_id: int, required_stars: int, target_instance_id: int):
    return cur.execute(
        """SELECT id FROM user_warriors
           WHERE user_id = ? AND warrior_id = ? AND stars = ? AND id != ?
           ORDER BY id ASC""",
        (user_id, warrior_id, int(required_stars), target_instance_id),
    ).fetchall()


def tier_up_warrior(user_id: int, user_warrior_id: int, max_tier: int = 20, required_level: int | None = None, memory_cost: int = 600, dry_run: bool = False):
    """Atomically apply one authored star-up requirement to one hero instance.

    With dry_run=True nothing is consumed: it just reports whether the star-up
    would succeed ({"ok": True, "dry_run": True}) or what is missing.
    """
    import progression

    conn = get_connection()
    cur = conn.cursor()
    try:
        warrior = cur.execute(
            """SELECT uw.id, uw.warrior_id, uw.stars, uw.base_stars, uw.level, wc.name, wc.element
               FROM user_warriors uw JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
               WHERE uw.user_id = ? AND uw.id = ?""",
            (user_id, user_warrior_id),
        ).fetchone()
        if not warrior:
            return None

        current = int(warrior["stars"])
        base_stars = int(warrior["base_stars"])
        level = int(warrior["level"])
        max_for_character = min(max_tier, progression.character_max_stars(base_stars))
        if required_level is None:  # default: the hero must be at its current level cap
            required_level = progression.tier_level_requirement(current)
        if required_level > 0 and level < required_level:
            return {"ok": False, "reason": "level_requirement", "tier": current, "level": level}
        if current >= max_for_character:
            return {"ok": False, "reason": "max_tier", "tier": current, "max_tier": max_for_character}

        target = current + 1
        req = progression.tier_requirements(current, target)
        if not req:
            return {"ok": False, "reason": "requirements_not_authored", "tier": current, "target": target}

        memory_cost = int(memory_cost)
        user = cur.execute("SELECT memory_stones FROM users WHERE user_id = ?", (user_id,)).fetchone()
        memory = int(user["memory_stones"]) if user else 0
        missing = []

        if memory < memory_cost:
            missing.append(f"{memory_cost} Memory Stones")

        # exact self copies
        self_copy_ids = []
        for star, count in req.get("self_copy", {}).items():
            rows = _available_self_copies(cur, user_id, warrior["warrior_id"], star, user_warrior_id)
            if len(rows) < count:
                missing.append(f"{count - len(rows)} more {star}★ copy/copies of the same hero")
            else:
                self_copy_ids.extend(int(r["id"]) for r in rows[:count])

        # 4 -> 5 also needs three distinct other 4★ heroes.
        other_ids = []
        for star, count in req.get("other_hero", {}).items():
            rows = cur.execute(
                """SELECT uw.id, uw.warrior_id FROM user_warriors uw
                   WHERE uw.user_id = ? AND uw.stars = ? AND uw.warrior_id != ?
                   ORDER BY uw.warrior_id ASC, uw.id ASC""",
                (user_id, int(star), warrior["warrior_id"]),
            ).fetchall()
            chosen = []
            seen = set()
            for row in rows:
                if row["warrior_id"] in seen:
                    continue
                seen.add(row["warrior_id"])
                chosen.append(int(row["id"]))
                if len(chosen) >= count:
                    break
            if len(chosen) < count:
                missing.append(f"{count - len(chosen)} more other {star}★ hero(s) with distinct hero identities")
            else:
                other_ids.extend(chosen)

        element = warrior["element"]
        consumed_food = []
        for star, count in req.get("food_same", {}).items():
            row = cur.execute(
                "SELECT quantity FROM user_food WHERE user_id = ? AND element = ? AND stars = ?",
                (user_id, element, int(star)),
            ).fetchone()
            qty = int(row["quantity"]) if row else 0
            if qty < count:
                missing.append(f"{count - qty} more {star}★ same-element food")
            else:
                consumed_food.append((element, int(star), int(count)))

        # "Food of any element" means any real element at the required star tier.
        # Reserve same-element food first, then satisfy the any-element requirement
        # from whatever real-element food remains. There is no separate item type.
        reserved = {(food_element, int(star)): int(count) for food_element, star, count in consumed_food}
        for star, count in req.get("food_any_element", {}).items():
            star = int(star)
            count = int(count)
            rows = cur.execute(
                """SELECT element, quantity FROM user_food
                   WHERE user_id = ? AND stars = ?
                   ORDER BY element ASC""",
                (user_id, star),
            ).fetchall()
            remaining = count
            for row in rows:
                key = (str(row["element"]), star)
                available = int(row["quantity"]) - reserved.get(key, 0)
                if available <= 0:
                    continue
                use = min(available, remaining)
                reserved[key] = reserved.get(key, 0) + use
                consumed_food.append((str(row["element"]), star, use))
                remaining -= use
                if remaining <= 0:
                    break
            if remaining > 0:
                missing.append(f"{remaining} more {star}★ food of any element")

        if missing:
            conn.rollback()
            return {"ok": False, "reason": "missing_requirements", "tier": current, "target": target, "missing": missing}

        if dry_run:
            conn.rollback()
            return {"ok": True, "dry_run": True, "tier": current, "target": target}

        # Consume Memory Stones.
        cur.execute("UPDATE users SET memory_stones = memory_stones - ? WHERE user_id = ?", (memory_cost, user_id))

        # Consume exact self copies and other 4★ heroes.
        for owned_id in self_copy_ids + other_ids:
            cur.execute("DELETE FROM user_warriors WHERE user_id = ? AND id = ?", (user_id, owned_id))

        # Consume food.
        for food_element, star, count in consumed_food:
            if not _consume_food(cur, user_id, food_element, star, count):
                raise RuntimeError("Food inventory changed during tier transaction")

        cur.execute("UPDATE user_warriors SET stars = ? WHERE user_id = ? AND id = ?", (target, user_id, user_warrior_id))
        conn.commit()
        return {
            "ok": True,
            "old_tier": current,
            "tier": target,
            "level": level,
            "memory_stones_spent": memory_cost,
            "self_copies_spent": len(self_copy_ids),
            "other_heroes_spent": len(other_ids),
            "food_spent": consumed_food,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def find_owned_for_tierup(user_id: int, query: str):
    """Resolve what the player typed into /tierup to ONE owned hero copy.

    Returns (status, payload):
      ("ok", warrior_dict)       - a single copy to star up. If several copies of
                                   the same hero are owned, the highest-star (then
                                   highest-level, then oldest) copy is used; the
                                   other copies are what the star-up consumes.
      ("none", None)             - nothing owned matches.
      ("ambiguous", [names])     - the text matches more than one different hero.
    A plain number is treated as an exact copy ID.
    """
    owned = get_user_warriors(user_id)
    q = (query or "").strip().lower()
    if not q:
        return "none", None
    if q.isdigit():
        for w in owned:
            if int(w["id"]) == int(q):
                return "ok", w
    matches = [w for w in owned if q in w["name"].lower()]
    if not matches:
        return "none", None
    if len({int(w["warrior_id"]) for w in matches}) > 1:
        exact = [w for w in matches if w["name"].lower() == q]
        if exact and len({int(w["warrior_id"]) for w in exact}) == 1:
            matches = exact
        else:
            return "ambiguous", sorted({w["name"] for w in matches})
    best = max(matches, key=lambda w: (int(w["stars"]), int(w["level"]), -int(w["id"])))
    return "ok", best


def add_user_exp(user_id: int, amount: int) -> int:
    """Add EXP items to a user and return their new EXP balance."""
    amount = int(amount)
    if amount < 0:
        raise ValueError("EXP amount cannot be negative")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET exp = exp + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    cur.execute("SELECT exp FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["exp"]) if row else 0


def get_user_exp(user_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT exp FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["exp"]) if row else 0


# The catalog rarely changes (only custom_warriors.sync_custom_warriors() or an
# admin edit touches it), but it's read on nearly every screen (hero lists,
# the Lab, admin commands, test-battle). Caching it in-process avoids a fresh
# DB round trip for every single one of those reads. invalidate_catalog_cache()
# is called wherever the table is actually written to.
_catalog_cache: list[dict] | None = None


def invalidate_catalog_cache():
    global _catalog_cache
    _catalog_cache = None


def get_all_catalog_warriors():
    global _catalog_cache
    if _catalog_cache is None:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM warrior_catalog")
        rows = cur.fetchall()
        conn.close()
        _catalog_cache = [dict(row) for row in rows]
    return [dict(row) for row in _catalog_cache]


def get_catalog_warriors_for_banner(banner_id: str, default_banner_id: str = "standard"):
    """
    Returns only the warriors that belong to this banner's pool. A warrior
    belongs to the DEFAULT pool by default (zero rows in warrior_banners), or
    to any banner(s) explicitly listed for it - a warrior can be on multiple
    banners at once. `default_banner_id` should match whichever banner id
    bot.py treats as the general pool (pass this in - don't rely on the
    "standard" fallback unless that's genuinely your default banner's id).
    """
    conn = get_connection()
    cur = conn.cursor()
    if banner_id == default_banner_id:
        cur.execute("""
            SELECT wc.* FROM warrior_catalog wc
            WHERE wc.warrior_id IN (
                SELECT warrior_id FROM warrior_banners WHERE banner_id = ?
            )
            OR wc.warrior_id NOT IN (SELECT warrior_id FROM warrior_banners)
        """, (default_banner_id,))
    else:
        cur.execute("""
            SELECT wc.* FROM warrior_catalog wc
            JOIN warrior_banners wb ON wc.warrior_id = wb.warrior_id
            WHERE wb.banner_id = ?
        """, (banner_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_warrior_banners(warrior_id: int, banner_ids: list):
    """Replaces a warrior's full set of banner assignments with `banner_ids`
    (an empty list means Standard Summon only)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM warrior_banners WHERE warrior_id = ?", (warrior_id,))
    for banner_id in banner_ids:
        banner_id = (banner_id or "").strip()
        if banner_id:
            cur.execute(
                "INSERT OR IGNORE INTO warrior_banners (warrior_id, banner_id) VALUES (?, ?)",
                (warrior_id, banner_id),
            )
    conn.commit()
    conn.close()


def get_banners_for_warrior(warrior_id: int, default_banner_id: str = "standard"):
    """Returns the list of banner ids a warrior is assigned to, or [default_banner_id] if none."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT banner_id FROM warrior_banners WHERE warrior_id = ?", (warrior_id,))
    rows = cur.fetchall()
    conn.close()
    banners = [row["banner_id"] for row in rows]
    return banners if banners else [default_banner_id]


def upsert_catalog_warrior(warrior: dict):
    """
    Inserts a new catalog warrior, or updates one that already exists.
    Matching priority:
      1. If warrior["hero_key"] is set and a catalog row already has that
         same hero_key, update that row - this means you can freely rename
         a hero in heroes.csv (changing `name`) without it being treated as
         a brand new hero, as long as hero_key stays the same.
      2. Otherwise, fall back to matching by exact `name` (the original
         behavior, kept for heroes that don't use hero_key).
    `warrior["banners"]` is a list of banner ids (empty list means Standard
    Summon only) - a warrior can belong to multiple banners.
    Returns (warrior_id, was_new: bool).
    """
    conn = get_connection()
    cur = conn.cursor()

    existing = None
    hero_key = (warrior.get("hero_key") or "").strip() or None
    if hero_key:
        cur.execute("SELECT warrior_id FROM warrior_catalog WHERE hero_key = ?", (hero_key,))
        existing = cur.fetchone()
    if existing is None:
        cur.execute("SELECT warrior_id FROM warrior_catalog WHERE name = ?", (warrior["name"],))
        existing = cur.fetchone()

    fields = (
        warrior["name"], warrior["element"], warrior["rarity"], warrior.get("stars", 4), warrior["base_hp"],
        warrior["base_atk"], warrior["base_def"], warrior["base_matk"], warrior["base_mdef"],
        warrior["base_speed"], warrior["crit_chance"], warrior["crit_damage"],
        warrior["skill_1"], warrior["skill_2"], warrior["skill_3"], warrior["passive"],
        warrior["skill1_type"], warrior["skill1_power"], warrior["skill1_target"],
        warrior["skill2_type"], warrior["skill2_power"], warrior["skill2_target"],
        warrior["skill3_type"], warrior["skill3_power"], warrior["skill3_target"],
        warrior["skill3_condition"], warrior["skill3_value"],
        warrior["passive_type"], warrior["passive_power"], warrior["passive_target"],
        hero_key,
        warrior.get("skill1_effects") or "",
        warrior.get("skill2_effects") or "",
        warrior.get("skill3_effects") or "",
        warrior.get("passive_effects") or "",
        warrior.get("skill1_cooldown"),
        warrior.get("skill2_cooldown"),
        warrior.get("skill3_cooldown"),
    )

    if existing:
        warrior_id = existing["warrior_id"]
        cur.execute("""
            UPDATE warrior_catalog SET
                name = ?, element = ?, rarity = ?, stars = ?, base_hp = ?, base_atk = ?, base_def = ?,
                base_matk = ?, base_mdef = ?, base_speed = ?, crit_chance = ?,
                crit_damage = ?, skill_1 = ?, skill_2 = ?, skill_3 = ?, passive = ?,
                skill1_type = ?, skill1_power = ?, skill1_target = ?,
                skill2_type = ?, skill2_power = ?, skill2_target = ?,
                skill3_type = ?, skill3_power = ?, skill3_target = ?,
                skill3_condition = ?, skill3_value = ?,
                passive_type = ?, passive_power = ?, passive_target = ?, hero_key = ?,
                skill1_effects = ?, skill2_effects = ?, skill3_effects = ?, passive_effects = ?,
                skill1_cooldown = ?, skill2_cooldown = ?, skill3_cooldown = ?
            WHERE warrior_id = ?
        """, fields + (warrior_id,))
        was_new = False
    else:
        cur.execute("""
            INSERT INTO warrior_catalog
            (name, element, rarity, stars, base_hp, base_atk, base_def, base_matk,
             base_mdef, base_speed, crit_chance, crit_damage, skill_1, skill_2, skill_3, passive,
             skill1_type, skill1_power, skill1_target,
             skill2_type, skill2_power, skill2_target,
             skill3_type, skill3_power, skill3_target, skill3_condition, skill3_value,
             passive_type, passive_power, passive_target, hero_key,
             skill1_effects, skill2_effects, skill3_effects, passive_effects,
             skill1_cooldown, skill2_cooldown, skill3_cooldown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, fields)
        warrior_id = cur.lastrowid
        was_new = True

    conn.commit()
    conn.close()
    invalidate_catalog_cache()

    set_warrior_banners(warrior_id, warrior.get("banners", []))

    return warrior_id, was_new


def set_party_slot(user_id: int, position: int, user_warrior_id: int):
    """Places a specific owned warrior (by its user_warriors.id) into a party slot (1-6)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_party (user_id, position, user_warrior_id) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, position) DO UPDATE SET user_warrior_id = excluded.user_warrior_id",
        (user_id, position, user_warrior_id),
    )
    conn.commit()
    conn.close()


def clear_party_slot(user_id: int, position: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_party WHERE user_id = ? AND position = ?", (user_id, position))
    conn.commit()
    conn.close()


def get_party(user_id: int):
    """
    Returns a dict mapping position (1-6) -> warrior info, for whichever slots
    are filled. Missing positions simply aren't in the dict.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT up.position, uw.id AS user_warrior_id, uw.stars, uw.base_stars, uw.level, uw.duplicate_copies, wc.*
        FROM user_party up
        JOIN user_warriors uw ON up.user_warrior_id = uw.id
        JOIN warrior_catalog wc ON uw.warrior_id = wc.warrior_id
        WHERE up.user_id = ?
        ORDER BY up.position ASC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return {row["position"]: dict(row) for row in rows}


def increment_task_progress(user_id: int, date_str: str, task_key: str, amount: int = 1):
    """Adds `amount` to a task's progress for the given day. Creates the row if needed."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO daily_progress (user_id, date, task_key, count) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, date, task_key) DO UPDATE SET count = count + excluded.count",
        (user_id, date_str, task_key, amount),
    )
    conn.commit()
    conn.close()


def get_daily_progress(user_id: int, date_str: str):
    """Returns a dict of task_key -> count for the given day (missing keys just mean 0)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT task_key, count FROM daily_progress WHERE user_id = ? AND date = ?",
        (user_id, date_str),
    )
    rows = cur.fetchall()
    conn.close()
    return {row["task_key"]: row["count"] for row in rows}


def user_owns_avatar(user_id: int, avatar_id: str) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM user_avatars WHERE user_id = ? AND avatar_id = ?",
        (user_id, avatar_id),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None


def grant_avatar(user_id: int, avatar_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO user_avatars (user_id, avatar_id) VALUES (?, ?)",
        (user_id, avatar_id),
    )
    conn.commit()
    conn.close()


def get_user_avatars(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT avatar_id FROM user_avatars WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [row["avatar_id"] for row in rows]


def upsert_story_chapter(chapter: dict):
    """
    Inserts a new story chapter, or updates it if one with this
    `chapter_number` already exists - this is what lets story_chapters.csv
    be edited and re-synced safely. Returns (chapter_number, was_new: bool).
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT chapter_number FROM story_chapters WHERE chapter_number = ?",
        (chapter["chapter_number"],),
    )
    existing = cur.fetchone()

    fields = (
        chapter["title"], chapter["description"], chapter["story_text"], chapter["boss_name"],
        chapter["boss_element"], chapter["boss_stars"], chapter["boss_hp"],
        chapter["boss_atk"], chapter["boss_def"], chapter["boss_matk"],
        chapter["boss_mdef"], chapter["boss_speed"], chapter["reward_solite"],
        chapter["reward_charms"], chapter.get("reward_exp", 0),
        chapter.get("enemy1"), chapter.get("enemy2"), chapter.get("enemy3"),
        chapter.get("enemy4"), chapter.get("enemy5"), chapter.get("enemy6"),
        chapter.get("enemy1_level", 1), chapter.get("enemy2_level", 1), chapter.get("enemy3_level", 1),
        chapter.get("enemy4_level", 1), chapter.get("enemy5_level", 1), chapter.get("enemy6_level", 1),
    )

    if existing:
        cur.execute("""
            UPDATE story_chapters SET
                title = ?, description = ?, story_text = ?, boss_name = ?, boss_element = ?,
                boss_stars = ?, boss_hp = ?, boss_atk = ?, boss_def = ?,
                boss_matk = ?, boss_mdef = ?, boss_speed = ?, reward_solite = ?,
                reward_charms = ?, reward_exp = ?,
                enemy1 = ?, enemy2 = ?, enemy3 = ?, enemy4 = ?, enemy5 = ?, enemy6 = ?,
                enemy1_level = ?, enemy2_level = ?, enemy3_level = ?,
                enemy4_level = ?, enemy5_level = ?, enemy6_level = ?
            WHERE chapter_number = ?
        """, fields + (chapter["chapter_number"],))
        was_new = False
    else:
        cur.execute("""
            INSERT INTO story_chapters
            (chapter_number, title, description, story_text, boss_name, boss_element, boss_stars,
             boss_hp, boss_atk, boss_def, boss_matk, boss_mdef, boss_speed,
             reward_solite, reward_charms, reward_exp,
             enemy1, enemy2, enemy3, enemy4, enemy5, enemy6,
             enemy1_level, enemy2_level, enemy3_level, enemy4_level, enemy5_level, enemy6_level)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (chapter["chapter_number"],) + fields)
        was_new = True

    conn.commit()
    conn.close()
    return chapter["chapter_number"], was_new


def get_story_chapter(chapter_number: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM story_chapters WHERE chapter_number = ?", (chapter_number,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_chapter_enemy_team(chapter: dict) -> list[dict]:
    """Resolves a chapter's enemy1..enemy6 slots into full catalog warrior
    dicts (with a battle `level` and `enemy_slot` attached), so the fight can
    use real authored heroes instead of just the single legacy boss fields.
    Each slot is matched by hero_key first, falling back to an exact name
    match, and is skipped if left blank or if it doesn't match any hero.
    Returns an empty list if no slots are set (battle.py then falls back to
    the legacy single-boss fight).
    """
    if not chapter:
        return []
    conn = get_connection()
    cur = conn.cursor()
    team = []
    for i in range(1, 7):
        key = (chapter.get(f"enemy{i}") or "").strip()
        if not key:
            continue
        row = cur.execute("SELECT * FROM warrior_catalog WHERE hero_key = ?", (key,)).fetchone()
        if not row:
            row = cur.execute("SELECT * FROM warrior_catalog WHERE name = ?", (key,)).fetchone()
        if not row:
            continue
        warrior = dict(row)
        warrior["level"] = max(1, int(chapter.get(f"enemy{i}_level", 1) or 1))
        warrior["enemy_slot"] = i
        team.append(warrior)
    conn.close()
    return team


def get_all_story_chapters():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM story_chapters ORDER BY chapter_number ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]
