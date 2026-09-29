import io
import os
import asyncio
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database
import gacha
import seed_data
import warrior_art
import card_art
import custom_warriors
import sync_story
import battle
import progression
import testbattle
import admin_items
import hero_text
import lab_logic

# Load the token and owner ID from the .env file
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")  # your Discord user ID, as a string in .env
GUILD_ID = os.getenv("GUILD_ID")  # optional: your test server's ID, for instant command sync

# Set up the bot with the intents we enabled in the Developer Portal
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

SUMMON_CHARM_COST_SOLITE = 100  # cost to buy one Summon Charm with Solite
DAILY_TASK_REWARD_SOLITE = 300  # total reward for completing all daily tasks

# The daily tasks (add or remove lines freely - everything else counts them automatically). `track_key` matches what gets incremented elsewhere in the code.
# `target` is how many of that action are needed to complete the task.
DAILY_TASKS = [
    {"key": "pull_menu_open", "label": "Open the summon menu", "target": 1},
    {"key": "pull_total_1", "label": "Make 1 pull", "target": 1, "track_key": "pull_total"},
    {"key": "pull_total_3", "label": "Make 3 pulls", "target": 3, "track_key": "pull_total"},
    {"key": "pull_total_5", "label": "Make 5 pulls", "target": 5, "track_key": "pull_total"},
    {"key": "view_inventory", "label": "Check your inventory", "target": 1},
    {"key": "view_profile", "label": "Check your profile", "target": 1},
    {"key": "view_party", "label": "Check your party", "target": 1},
    {"key": "set_party", "label": "Assign a warrior to your party", "target": 1},
    {"key": "view_shop", "label": "Visit the shop", "target": 1},
    {"key": "view_lab", "label": "Visit the Character Lab", "target": 1},
    {"key": "view_book", "label": "Browse the hero book", "target": 1},
    {"key": "story_fight", "label": "Fight a story battle", "target": 1},
]


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def track_progress(user_id: int, track_key: str, amount: int = 1):
    """Call this whenever the user does something a daily task cares about."""
    database.increment_task_progress(user_id, today_str(), track_key, amount)


def get_daily_task_status(user_id: int):
    """Returns a list of (task, current_count, is_complete) for today."""
    progress = database.get_daily_progress(user_id, today_str())
    status = []
    for task in DAILY_TASKS:
        track_key = task.get("track_key", task["key"])
        count = progress.get(track_key, 0)
        is_complete = count >= task["target"]
        status.append((task, count, is_complete))
    return status

# Banners are structured as a list so more can be added later (featured/rate-up banners etc.)
BANNERS = [
    {
        "id": "standard_summon",
        "name": "Standard Summon",
        "description": "Pull from the full pool of warriors across all elements.",
    },
    {
        "id": "torch_of_the_moonlight",
        "name": "Torch Of The Moonlight",
        "description": "A special banner for Blaze warriors, and the new hero: Elen Hart (Rate Up!)",
    },
   {    "id": "festival_of_blood_moon",
        "name": "Festival Of Blood Moon",
        "description": "A special banner for Shade warriors, and the new hero: Nyx (Rate Up!)",
    }, 
    {    "id": "the_white_fang",
            "name": "The White Fang",
            "description": "A special banner for Drift warriors, and the new hero: Rhaiven (Rate Up!)",
        }, 
    
]

# Whichever banner is listed FIRST above is treated as the "general pool" -
# any hero with no banner explicitly assigned in heroes.csv falls into this
# one. This is computed from BANNERS itself (not hard-coded) so renaming
# this banner's id never breaks anything.
DEFAULT_BANNER_ID = BANNERS[0]["id"]


def is_owner(interaction: discord.Interaction) -> bool:
    if OWNER_ID is None:
        return False
    return str(interaction.user.id) == str(OWNER_ID)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    database.init_db()
    added = seed_data.seed_catalog_if_empty()
    if added:
        print(f"Seeded catalog with {added} placeholder warriors.")

    new_count, updated_count = custom_warriors.sync_custom_warriors()
    if new_count or updated_count:
        print(f"Custom warriors: {new_count} added, {updated_count} updated.")

    story_new, story_updated = sync_story.sync_story_chapters()
    if story_new or story_updated:
        print(f"Story chapters: {story_new} added, {story_updated} updated.")

    if not OWNER_ID:
        print("NOTE: No OWNER_ID set in .env - admin commands will not work for anyone.")

    try:
        # Always sync globally so any server you invite the bot to eventually gets commands
        # (can take up to an hour to show up there)
        global_synced = await bot.tree.sync()
        print(f"Synced {len(global_synced)} slash command(s) globally.")

        # Additionally sync instantly to your test server, if configured
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            guild_synced = await bot.tree.sync(guild=guild)
            print(f"Also synced {len(guild_synced)} command(s) instantly to test server {GUILD_ID}.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print("Bot is online and ready!")


# ---------------------------------------------------------------------------
# Pull logic (shared by all pull buttons)
# ---------------------------------------------------------------------------

def do_one_pull(user_id: int, current_pity: int, pool: list, min_stars: int | None = None):
    """Pulls one warrior from the given pool (already filtered to a specific
    banner - see database.get_catalog_warriors_for_banner). min_stars forces
    the result to be at least that many stars (used for the 10x-pull 5★
    guarantee) - it never overrides the 6★ hard pity, which always wins."""
    available_stars = {int(w.get("stars", 4)) for w in pool}
    stars, pity_triggered = gacha.roll_stars_from_pool(current_pity, available_stars, min_stars=min_stars)
    if stars is None:
        return None  # empty pool - shouldn't normally happen, caller checks first

    new_pity = 0 if stars == 6 else current_pity + 1

    warrior = gacha.pick_warrior_by_stars(pool, stars)
    if warrior is None:
        return None

    outcome = database.add_warrior_to_user(user_id, warrior["warrior_id"])

    return {
        "warrior": warrior,
        "stars": stars,
        "pity_triggered": pity_triggered,
        "outcome": outcome,
        "new_pity": new_pity,
    }


def build_single_pull_embed(result: dict) -> discord.Embed:
    warrior = result["warrior"]
    stars = result["stars"]
    outcome = result["outcome"]

    embed = discord.Embed(
        title=f"You pulled: {warrior['name']}",
        description=f"**Element:** {warrior['element']}\n**Stars:** {stars}★",
        color=discord.Color.gold() if stars == 6 else discord.Color.blue(),
    )

    if outcome["result"] == "new":
        embed.add_field(name="Result", value="New warrior added to your collection!", inline=False)
    elif outcome["result"] == "duplicate":
        embed.add_field(
            name="Result",
            value=(
                f"Duplicate copy obtained! This copy is stored for tier upgrades. "
                f"You now have **{outcome['duplicate_copies']}** copy/copies of this warrior."
            ),
            inline=False,
        )

    if result["pity_triggered"]:
        embed.set_footer(text="✨ Pity triggered — guaranteed 6★!")

    return embed


def build_multi_pull_embed(results: list, username: str) -> discord.Embed:
    lines = []
    star_emoji = {4: "⚪", 5: "🟠", 6: "🟡"}
    any_pity = False
    for r in results:
        stars = int(r.get("stars", 4))
        emoji = star_emoji.get(stars, "")
        tag = ""
        if r["pity_triggered"]:
            any_pity = True
        lines.append(f"{emoji} **{r['warrior']['name']}** — {stars}★{tag}")

    embed = discord.Embed(
        title=f"{username}'s 10x Pull Results",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    if any_pity:
        embed.set_footer(text="✨ Pity triggered during this batch!")
    return embed


async def execute_pull(interaction: discord.Interaction, count: int, banner_id: str):
    """Runs the actual pull(s) and sends the result as a follow-up message.
    Assumes the interaction has already been responded to or deferred by the caller."""
    pool = database.get_catalog_warriors_for_banner(banner_id, default_banner_id=DEFAULT_BANNER_ID)
    if not pool:
        await interaction.followup.send(
            "This banner doesn't have any heroes assigned to it yet.", ephemeral=True
        )
        return

    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))

    if user["summon_charms"] < count:
        await interaction.followup.send(
            f"You need {count} Summon Charm(s) but only have {user['summon_charms']}. "
            f"You have {user['solite']} Solite — buy more Charms with `/shop`.",
            ephemeral=True,
        )
        return

    database.update_user_field(interaction.user.id, "summon_charms", user["summon_charms"] - count)

    pity = user["pity_counter"]
    results = []
    got_five_plus = False
    for i in range(count):
        # 10x pulls are guaranteed at least one 5★ (or better) - if the first
        # 9 didn't produce one, the 10th is forced to. The 6★ hard pity (see
        # PITY_THRESHOLD) still takes priority over this whenever it's due.
        force_five = count == 10 and i == count - 1 and not got_five_plus
        result = do_one_pull(interaction.user.id, pity, pool, min_stars=5 if force_five else None)
        if result is None:
            continue
        if result["stars"] >= 5:
            got_five_plus = True
        pity = result["new_pity"]
        results.append(result)

    database.update_user_field(interaction.user.id, "pity_counter", pity)
    track_progress(interaction.user.id, "pull_total", amount=len(results))

    if not results:
        await interaction.followup.send("Something went wrong finding warriors — try again.")
        return

    if count == 1:
        embed = build_single_pull_embed(results[0])
        card_buffer = await asyncio.to_thread(card_art.build_character_card, {
            **results[0]["warrior"],
            "stars": int(results[0]["stars"]),
            "level": 1,
        })
        file = discord.File(card_buffer, filename="character_card.png")
        embed.set_image(url="attachment://character_card.png")
        await interaction.followup.send(embed=embed, file=file, view=PullAgainView(last_count=count, banner_id=banner_id))
    else:
        embed = build_multi_pull_embed(results, str(interaction.user.display_name))
        collage_buffer = await asyncio.to_thread(card_art.build_pull_grid, results)
        file = discord.File(collage_buffer, filename="pull_results.png")
        embed.set_image(url="attachment://pull_results.png")
        await interaction.followup.send(embed=embed, file=file, view=PullAgainView(last_count=count, banner_id=banner_id))


class PullAgainView(discord.ui.View):
    """Shown under every pull RESULT message so you can immediately pull again
    without scrolling back up to the banner menu. Remembers which banner this
    result came from, so "pull again" keeps pulling from the same pool."""

    def __init__(self, last_count: int, banner_id: str):
        super().__init__(timeout=300)
        self.last_count = last_count
        self.banner_id = banner_id

    @discord.ui.button(label="Pull Again x1", style=discord.ButtonStyle.primary, emoji="✨")
    async def pull_again_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await execute_pull(interaction, count=1, banner_id=self.banner_id)

    @discord.ui.button(label="Pull Again x10", style=discord.ButtonStyle.success, emoji="🎉")
    async def pull_again_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await execute_pull(interaction, count=10, banner_id=self.banner_id)


# ---------------------------------------------------------------------------
# Pull menu UI: /pull opens this menu, all pulling happens via its buttons
# ---------------------------------------------------------------------------

def build_roster_embed(banner_id: str, banner_name: str) -> discord.Embed:
    pool = database.get_catalog_warriors_for_banner(banner_id, default_banner_id=DEFAULT_BANNER_ID)
    embed = discord.Embed(title=f"{banner_name} — Roster", color=discord.Color.blurple())

    if not pool:
        embed.description = "No heroes assigned to this banner yet."
        return embed

    pool.sort(key=lambda w: (int(w.get("stars", 4)), w["name"]), reverse=True)
    lines = [f"**{w['name']}** ({w['element']})" for w in pool]
    # Discord embed descriptions cap at 4096 chars; fields cap at 1024 - chunk if needed
    description = "\n".join(lines)
    if len(description) <= 4096:
        embed.description = description
    else:
        embed.description = f"{len(pool)} heroes in this pool (too many to list here)."
    return embed


class PullMenuView(discord.ui.View):
    """The banner's pull menu - shown after picking a banner. Stays active so
    you can keep pulling from the same menu message."""

    def __init__(self, banner_id: str):
        super().__init__(timeout=300)
        self.banner_id = banner_id

    @discord.ui.button(label="Pull x1", style=discord.ButtonStyle.primary, emoji="✨")
    async def pull_once(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await execute_pull(interaction, count=1, banner_id=self.banner_id)

    @discord.ui.button(label="Pull x10", style=discord.ButtonStyle.success, emoji="🎉")
    async def pull_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await execute_pull(interaction, count=10, banner_id=self.banner_id)

    @discord.ui.button(label="View Roster", style=discord.ButtonStyle.secondary, emoji="📋")
    async def view_roster(self, interaction: discord.Interaction, button: discord.ui.Button):
        banner = next((b for b in BANNERS if b["id"] == self.banner_id), None)
        banner_name = banner["name"] if banner else self.banner_id
        embed = build_roster_embed(self.banner_id, banner_name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back to Banners", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_banner_select_embed()
        await interaction.response.edit_message(embed=embed, attachments=[], view=BannerSelectView())


class BannerSelectView(discord.ui.View):
    """The initial /pull menu - choose which banner to pull from."""

    def __init__(self):
        super().__init__(timeout=300)
        for banner in BANNERS:
            self.add_item(BannerButton(banner))


class BannerButton(discord.ui.Button):
    def __init__(self, banner: dict):
        super().__init__(label=banner["name"], style=discord.ButtonStyle.primary, emoji="🔮")
        self.banner = banner
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        database.get_or_create_user(
            interaction.user.id,
            str(interaction.user.display_name)
        )

        embed = discord.Embed(
            title=self.banner["name"],
            description=self.banner["description"],
            color=discord.Color.purple(),
        )

        embed.set_footer(text="Choose how many pulls to make below")

        image_path = warrior_art.get_banner_image_path(self.banner["id"])

        if image_path:
            optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, image_path)
            file = discord.File(optimized, filename="banner.png")
            embed.set_image(url="attachment://banner.png")

            await interaction.edit_original_response(
                embed=embed,
                attachments=[file],
                view=PullMenuView(self.banner["id"])
            )
        else:
            await interaction.edit_original_response(
                embed=embed,
                attachments=[],
                view=PullMenuView(self.banner["id"])
            )
        
                 
def build_banner_select_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Choose a Banner",
        description="Select a banner below to view details and start pulling.",
        color=discord.Color.purple(),
    )
    for banner in BANNERS:
        embed.add_field(name=banner["name"], value=banner["description"], inline=False)
    return embed


# ---------------------------------------------------------------------------
# Core commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! The bot is working.")


@bot.tree.command(name="pull", description="Open the summon menu to pull warriors")
async def pull(interaction: discord.Interaction):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    track_progress(interaction.user.id, "pull_menu_open")
    embed = build_banner_select_embed()
    await interaction.response.send_message(embed=embed, view=BannerSelectView())


class ItemInventoryView(discord.ui.View):
    """Paged browser for generic items plus food materials."""

    PAGE_SIZE = 12

    def __init__(self, user_id: int, entries: list[dict], page: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.entries = entries
        self.page = max(0, min(int(page), self.max_page))
        self._refresh_buttons()

    @property
    def max_page(self) -> int:
        return max(0, (len(self.entries) - 1) // self.PAGE_SIZE)

    def _refresh_buttons(self):
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.max_page
        self.page_indicator.label = f"Page {self.page + 1}/{self.max_page + 1}"
        self.page_indicator.disabled = True

    def build_embed(self) -> discord.Embed:
        user = database.get_or_create_user(self.user_id, "")
        start = self.page * self.PAGE_SIZE
        page_entries = self.entries[start:start + self.PAGE_SIZE]

        embed = discord.Embed(
            title=f"{user['display_name'] or 'Player'}'s Inventory",
            description="Your usable items and materials. Heroes are in `/heroes` and the complete hero book is `/book`.",
            color=discord.Color.dark_teal(),
        )
        embed.add_field(
            name="Resources",
            value=(
                f"💰 Solite: **{int(user['solite'])}**\n"
                f"✨ Summon Charms: **{int(user['summon_charms'])}**\n"
                f"🧪 EXP: **{int(user['exp'])}**\n"
                f"◆ Memory Stones: **{int(user['memory_stones'])}**"
            ),
            inline=False,
        )

        if not page_entries:
            embed.add_field(name="Items", value="Your item inventory is empty.", inline=False)
        else:
            lines = []
            for entry in page_entries:
                if entry["kind"] == "food":
                    label = f"🍖 {entry['name']}"
                else:
                    label = f"📦 {entry['name']}"
                lines.append(f"{label} — **×{entry['quantity']}**")
            embed.add_field(name="Items", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Items & materials • {self.page + 1}/{self.max_page + 1}")
        return embed

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your inventory.", ephemeral=True)
            return False
        return True

    async def _edit(self, interaction: discord.Interaction):
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        if self.page > 0:
            self.page -= 1
        await self._edit(interaction)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._guard(interaction):
            return
        if self.page < self.max_page:
            self.page += 1
        await self._edit(interaction)


def build_item_inventory_entries(user_id: int) -> list[dict]:
    entries = []
    for item in database.get_user_items(user_id):
        entries.append({
            "kind": "item",
            "name": item["item_name"],
            "quantity": int(item["quantity"]),
            "sort_key": (0, str(item["item_name"]).lower()),
        })

    for food in database.get_food_inventory(user_id):
        qty = int(food["quantity"])
        if qty <= 0:
            continue
        element = str(food["element"])
        name = f"{int(food['stars'])}★ {element} Food"
        entries.append({
            "kind": "food",
            "name": name,
            "quantity": qty,
            "sort_key": (1, -int(food["stars"]), element.lower()),
        })

    entries.sort(key=lambda e: e["sort_key"])
    return entries


@bot.tree.command(name="inventory", description="View your items and materials")
async def inventory(interaction: discord.Interaction):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    track_progress(interaction.user.id, "view_inventory")
    entries = build_item_inventory_entries(interaction.user.id)
    view = ItemInventoryView(interaction.user.id, entries, page=0)
    await interaction.response.send_message(embed=view.build_embed(), view=view)


@bot.tree.command(name="heroes", description="Browse your owned heroes")
async def heroes(interaction: discord.Interaction):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    warriors = database.get_user_warriors(interaction.user.id)

    if not warriors:
        await interaction.response.send_message("You don't own any heroes yet — try `/pull`!", ephemeral=True)
        return

    view = HeroInventoryView(interaction.user.id, sort_warriors(warriors), index=0)
    await view.send_initial(interaction)


def build_profile_embed(user: dict, warriors: list, party: dict, shown_name: str,
                        catalog_total: int, daily_done: int, daily_total: int) -> discord.Embed:
    unique = len({w["warrior_id"] for w in warriors})
    best = max((int(w["stars"]) for w in warriors), default=0)
    strongest = hero_text.strongest_heroes(warriors, 6)
    total_bp = sum(bp for bp, _ in strongest)

    header = [
        f"⚡ **Battle Power: {total_bp:,}**  *(your 6 strongest heroes)*",
        f"📖 Story: **Chapter {user['story_chapter']}**",
        f"🦸 Heroes: **{len(warriors)}** owned  ·  **{unique}/{catalog_total}** unique" + (f"  ·  best **{best}★**" if best else ""),
        f"📅 Daily tasks: **{daily_done}/{daily_total}** done",
    ]
    embed = discord.Embed(title=f"{shown_name}'s Profile", description="\n".join(header), color=discord.Color.teal())

    embed.add_field(
        name="Wallet",
        value=(
            f"🪙 **{int(user['solite']):,}** Solite\n"
            f"🎟️ **{int(user['summon_charms']):,}** Summon Charms\n"
            f"✨ **{int(user['exp']):,}** EXP\n"
            f"💎 **{int(user['memory_stones']):,}** Memory Stones"
        ),
        inline=True,
    )

    if strongest:
        embed.add_field(
            name="Strongest Six",
            value="\n".join(
                f"`{rank}` {hero_text.element_icon(w['element'])} **{w['name']}** — Lv{w['level']} · {w['stars']}★ · ⚡{bp:,}"
                for rank, (bp, w) in enumerate(strongest, 1)
            )[:1024],
            inline=False,
        )

    party_lines = []
    for slot in range(1, 7):
        w = party.get(slot)
        if w:
            party_lines.append(f"`{slot}` {hero_text.element_icon(w['element'])} {w['name']} — Lv{w['level']} · {w['stars']}★")
        else:
            party_lines.append(f"`{slot}` ▫️ *empty*")
    embed.add_field(name="Party", value="\n".join(party_lines), inline=False)
    return embed


class ProfileView(discord.ui.View):
    """Shortcut buttons under /profile - each one opens the matching screen."""

    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.message = None

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your profile.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Heroes", emoji="🦸", style=discord.ButtonStyle.primary)
    async def heroes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._guard(interaction):
            await heroes.callback(interaction)

    @discord.ui.button(label="Party", emoji="👥", style=discord.ButtonStyle.secondary)
    async def party_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._guard(interaction):
            await send_party_response(interaction)

    @discord.ui.button(label="Items", emoji="🎒", style=discord.ButtonStyle.secondary)
    async def items_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._guard(interaction):
            await inventory.callback(interaction)

    @discord.ui.button(label="Lab", emoji="🧪", style=discord.ButtonStyle.secondary)
    async def lab_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._guard(interaction):
            await lab.callback(interaction)

    @discord.ui.button(label="Daily", emoji="📅", style=discord.ButtonStyle.secondary)
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._guard(interaction):
            await daily.callback(interaction)


@bot.tree.command(name="profile", description="View your profile")
async def profile(interaction: discord.Interaction):
    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    track_progress(interaction.user.id, "view_profile")
    warriors = database.get_user_warriors(interaction.user.id)
    party = database.get_party(interaction.user.id)
    status = get_daily_task_status(interaction.user.id)

    shown_name = user["display_name"] or interaction.user.display_name
    embed = build_profile_embed(
        user, warriors, party, shown_name,
        catalog_total=len(database.get_all_catalog_warriors()),
        daily_done=sum(1 for _, _, complete in status if complete),
        daily_total=len(status),
    )
    view = ProfileView(interaction.user.id)

    avatar_path = warrior_art.get_avatar_image_path(user["avatar"]) if user["avatar"] else None
    if avatar_path:
        optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, avatar_path)
        file = discord.File(optimized, filename="avatar.png")
        embed.set_thumbnail(url="attachment://avatar.png")
        await interaction.response.send_message(embed=embed, file=file, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        pass


@bot.tree.command(name="setname", description="Set a custom display name for your profile")
@app_commands.describe(name="Your new display name (visible on your profile)")
async def setname(interaction: discord.Interaction, name: str):
    if len(name) > 32:
        await interaction.response.send_message("Names must be 32 characters or fewer.", ephemeral=True)
        return
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    database.update_user_field(interaction.user.id, "display_name", name)
    await interaction.response.send_message(f"Your profile name is now **{name}**.", ephemeral=True)


# ---------------------------------------------------------------------------
# Warrior leveling: spend EXP items to increase an owned warrior's level
# ---------------------------------------------------------------------------

@bot.tree.command(name="levelup", description="Spend EXP to level up one of your warriors")
@app_commands.describe(
    warrior_name="Name (or partial name) of a warrior you own",
    levels="How many levels to gain (1-400)",
)
async def levelup(interaction: discord.Interaction, warrior_name: str, levels: app_commands.Range[int, 1, 400]):
    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    owned = database.get_user_warriors(interaction.user.id)

    matches = [w for w in owned if warrior_name.lower() in w["name"].lower()]
    if not matches:
        await interaction.response.send_message(
            f"You don't own any warrior matching **{warrior_name}**. Check `/heroes`.",
            ephemeral=True,
        )
        return
    if len(matches) > 1:
        view = LevelUpCopySelectView(
            interaction.user.id,
            matches[:25],
            requested_levels=int(levels),
        )
        await interaction.response.send_message(
            f"You own multiple copies of **{matches[0]['name']}**. Choose the copy you want to level:",
            view=view,
            ephemeral=True,
        )
        return

    warrior = matches[0]
    current_level = int(warrior["level"])
    requested_levels = int(levels)

    level_cap = progression.effective_level_cap(int(warrior["stars"]), current_level)
    if current_level >= level_cap:
        await interaction.response.send_message(
            f"**{warrior['name']}** is at its level cap (Lv. {level_cap}). Star it up to raise the cap.",
            ephemeral=True,
        )
        return

    actual_levels = min(requested_levels, level_cap - current_level)
    cost = progression.level_up_cost_for_levels(current_level, actual_levels)

    if int(user["exp"]) < cost:
        affordable = progression.max_levels_affordable(current_level, int(user["exp"]), level_cap)
        next_cost = progression.exp_required_for_next_level(current_level)
        await interaction.response.send_message(
            f"Not enough EXP. **{warrior['name']}** needs **{cost} EXP** to reach Lv. {current_level + actual_levels}, "
            f"but you have **{user['exp']} EXP**. Next level costs {next_cost} EXP."
            + (f" You can currently afford **{affordable} level(s)**." if affordable else ""),
            ephemeral=True,
        )
        return

    result = database.level_up_warrior(
        interaction.user.id,
        warrior["id"],
        actual_levels,
        cost,
        max_level=level_cap,
    )
    if not result or not result.get("ok"):
        await interaction.response.send_message("The level-up could not be completed. Please try again.", ephemeral=True)
        return

    new_level = result["level"]
    embed = discord.Embed(
        title=f"{warrior['name']} leveled up!",
        description=(
            f"**Lv. {result['old_level']} → Lv. {new_level}**\n\n"
            f"EXP spent: **{result['exp_spent']}**\n"
            f"EXP remaining: **{result['exp_remaining']}**\n\n"
            f"All base combat stats now receive the Lv. {new_level} scaling bonus."
        ),
        color=discord.Color.green(),
    )
    if new_level >= level_cap:
        embed.set_footer(text=f"Level cap reached (Lv. {level_cap}). Star up to raise it.")
    else:
        embed.set_footer(text=f"Next level costs {progression.exp_required_for_next_level(new_level)} EXP")
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Warrior tiering: authored star-up requirements
# ---------------------------------------------------------------------------

@bot.tree.command(name="tierup", description="Star up one of your characters")
@app_commands.describe(warrior_name="Hero name (or part of it). With several copies, your highest-star copy is used. A copy's ID also works.")
async def tierup(interaction: discord.Interaction, warrior_name: str):
    await interaction.response.defer(ephemeral=True)

    status, payload = database.find_owned_for_tierup(interaction.user.id, warrior_name)
    if status == "none":
        await interaction.followup.send(
            f"You don't own any character matching **{warrior_name}**.", ephemeral=True
        )
        return
    if status == "ambiguous":
        names = ", ".join(f"**{n}**" for n in payload[:10])
        await interaction.followup.send(
            f"That matches several different heroes: {names}. Type a bit more of the name.",
            ephemeral=True,
        )
        return

    warrior = payload
    current = int(warrior["stars"])
    max_stars = progression.character_max_stars(int(warrior.get("base_stars", 4)))
    if current >= max_stars:
        await interaction.followup.send(
            f"**{warrior['name']}** is already at its maximum of **{max_stars}★**.", ephemeral=True
        )
        return

    target = current + 1
    # Same rules as the Star Up button: the hero must be at its current level cap.
    needed_level = progression.tier_level_requirement(current)
    if int(warrior["level"]) < needed_level:
        await interaction.followup.send(
            f"**{warrior['name']}** ({current}★) must reach **Level {needed_level}** before it can reach {target}★ "
            f"(it's Lv {int(warrior['level'])} now).",
            ephemeral=True,
        )
        return
    result = database.tier_up_warrior(
        interaction.user.id,
        warrior["id"],
        max_tier=max_stars,
        required_level=needed_level,
        memory_cost=progression.memory_stone_cost(warrior.get("base_stars", 4)),
    )
    if not result or not result.get("ok"):
        if result and result.get("reason") == "missing_requirements":
            missing = "\n".join(f"• {item}" for item in result.get("missing", []))
            await interaction.followup.send(
                f"**{warrior['name']}** ({current}★) cannot reach **{target}★** yet.\n\n"
                f"Required: {progression.tier_requirements_text(current, target, warrior.get('base_stars', 4))}\n\nMissing:\n{missing}",
                ephemeral=True,
            )
        else:
            reason = result.get("reason", "unknown") if result else "unknown"
            await interaction.followup.send(f"Star-up failed: `{reason}`", ephemeral=True)
        return

    spent = [f"{result['memory_stones_spent']} Memory Stones"]
    if result.get("self_copies_spent"):
        spent.append(f"{result['self_copies_spent']} extra cop{'y' if result['self_copies_spent'] == 1 else 'ies'}")
    if result.get("other_heroes_spent"):
        spent.append(f"{result['other_heroes_spent']} other hero(es)")
    for food_element, food_stars, food_count in result.get("food_spent", []):
        spent.append(f"{food_count}× {food_element} {food_stars}★ food")
    bonus = progression.breakthrough_bonus(result["tier"])
    breakthrough = f"\n🌟 **Breakthrough!** +{round((bonus - 1) * 100)}% to all stats." if bonus else ""
    await interaction.followup.send(
        f"**{warrior['name']}** reached **{result['tier']}★**!{breakthrough}\nSpent: " + ", ".join(spent) + ".",
        ephemeral=True,
    )


LAB_HELP_TEXT = (
    "**How the Lab works**\n"
    "🔥 **Melt Down** turns a hero you don't need into 1 Food of that hero's element, at the hero's current star tier "
    "(a melted 6★ gives 6★ food). Heroes in your `/party` can't be melted.\n"
    "⭐ **Star Up Food**: **5 food of the same element and tier → 1 food of the next tier**. It never costs Memory Stones.\n"
    "✨ **Mass Star Up** does every possible upgrade at once, chaining tiers (25× 4★ food can become 1× 6★). "
    "By default it **holds back the food your heroes need** for their next star-up; you can switch that off in the confirm screen.\n"
    "🎯 Hero star-ups use food too: **same-element** requirements need food matching the hero's element, "
    "**any-element** requirements accept food of any element at that tier. There is no separate any-element food item."
)


def _food_line(element: str, entries: list) -> str:
    stacks = " · ".join(f"{stars}★ ×{qty}" for stars, qty in entries)
    return f"{hero_text.element_icon(element)} **{element}** — {stacks}"


def _yield_lines(yields: dict) -> list:
    order = {e: i for i, e in enumerate(gacha.ELEMENTS)}
    return [
        f"{hero_text.element_icon(element)} {qty}× {stars}★ {element} food"
        for (element, stars), qty in sorted(yields.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1]))
    ]


def _lab_snapshot(user_id: int) -> dict:
    """Everything the Lab screens need, computed once."""
    inv = lab_logic.inventory_dict(database.get_food_inventory(user_id))
    owned = database.get_user_warriors(user_id)
    needs = lab_logic.hero_needs(owned, inv)
    reserve = lab_logic.compute_reserve(needs, inv)
    meltable = database.get_meltable_warriors(user_id)
    return {
        "inv": inv, "owned": owned, "needs": needs, "reserve": reserve, "meltable": meltable,
        "spare": lab_logic.spare_copy_ids(meltable, owned),
        "plan_all": lab_logic.plan_mass_star_up(inv, progression.FOOD_STAR_UP_COST, progression.FOOD_MAX_STARS),
        "plan_safe": lab_logic.plan_mass_star_up(inv, progression.FOOD_STAR_UP_COST, progression.FOOD_MAX_STARS, reserve),
    }


def build_lab_embed(user_id: int) -> discord.Embed:
    user = database.get_or_create_user(user_id, "")
    snap = _lab_snapshot(user_id)

    embed = discord.Embed(
        title="🧪 Character Lab",
        description="Melt spare heroes into **Food**, star Food up, and stock what your heroes need for their next star. Tap **How it works** for the rules.",
        color=discord.Color.teal(),
    )

    groups = lab_logic.food_groups(snap["inv"])
    embed.add_field(
        name="🍖 Your Food",
        value=("\n".join(_food_line(e, entries) for e, entries in groups) if groups else "*No food yet. Melt down spare heroes to get some.*")[:1024],
        inline=False,
    )
    embed.add_field(name="💎 Memory Stones", value=f"**{int(user['memory_stones']):,}**", inline=True)
    embed.add_field(
        name="🔥 Melt Down",
        value=f"**{len(snap['meltable'])}** free hero(es)\n**{len(snap['spare'])}** spare cop{'y' if len(snap['spare']) == 1 else 'ies'}",
        inline=True,
    )
    all_up, safe_up = snap["plan_all"]["total_upgrades"], snap["plan_safe"]["total_upgrades"]
    if all_up == 0:
        upgrade_text = f"Nothing yet: you need {progression.FOOD_STAR_UP_COST} of the same food."
    elif safe_up == all_up:
        upgrade_text = f"**{all_up}** upgrade(s) ready"
    else:
        upgrade_text = f"**{all_up}** ready (**{safe_up}** without touching food your heroes need)"
    embed.add_field(name="✨ Food Upgrades", value=upgrade_text, inline=True)

    needs = snap["needs"]
    if needs:
        lines = []
        for hero in needs[:6]:
            parts = []
            for kind, element, stars, need, have in hero["entries"]:
                label = f"{need}× {stars}★ " + (f"{element}" if kind == "same" else "any-element")
                parts.append(f"{label} ({'✅' if have >= need else f'❌ have {have}'})")
            lines.append(f"{hero_text.element_icon(hero['element'])} **{hero['name']}** {hero['stars']}★→{hero['target']}★: " + ", ".join(parts))
        if len(needs) > 6:
            lines.append(f"*…and {len(needs) - 6} more hero(es)*")
        embed.add_field(name="🎯 Food Your Heroes Need Next", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Hero star-ups use Memory Stones; Food star-ups never do.")
    return embed


class _LabButton(discord.ui.Button):
    def __init__(self, view, action, **kwargs):
        super().__init__(**kwargs)
        self.lab_view = view
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.lab_view.handle(interaction, self.action)


class LabView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.message = None
        self._rebuild()

    def _rebuild(self):
        snap = _lab_snapshot(self.user_id)
        starable = database.get_starable_food(self.user_id, min_quantity=progression.FOOD_STAR_UP_COST, max_stars=progression.FOOD_MAX_STARS)
        self.clear_items()
        self.add_item(_LabButton(self, "melt", label="Melt Down", emoji="🔥", style=discord.ButtonStyle.danger, disabled=not snap["meltable"]))
        self.add_item(_LabButton(self, "star", label="Star Up Food", emoji="⭐", style=discord.ButtonStyle.primary, disabled=not starable))
        self.add_item(_LabButton(
            self, "mass", label=f"Mass Star Up ({snap['plan_all']['total_upgrades']})", emoji="✨",
            style=discord.ButtonStyle.success, disabled=snap["plan_all"]["total_upgrades"] == 0,
        ))
        self.add_item(_LabButton(self, "help", label="How it works", emoji="ℹ️", style=discord.ButtonStyle.secondary))

    async def refresh(self):
        """Redraw the main Lab message after a sub-screen changed something."""
        if self.message is None:
            return
        embed = build_lab_embed(self.user_id)
        self._rebuild()
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def handle(self, interaction: discord.Interaction, action: str):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return

        if action == "help":
            await interaction.response.send_message(LAB_HELP_TEXT, ephemeral=True)
            return

        if action == "melt":
            snap = _lab_snapshot(self.user_id)
            if not snap["meltable"]:
                await interaction.response.send_message(
                    "You don't have any heroes free to melt: everything you own is in your `/party`.", ephemeral=True
                )
                return
            view = MeltSelectView(self.user_id, snap["meltable"], snap["owned"], lab_view=self)
            await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
            return

        if action == "star":
            starable = database.get_starable_food(self.user_id, min_quantity=progression.FOOD_STAR_UP_COST, max_stars=progression.FOOD_MAX_STARS)
            if not starable:
                await interaction.response.send_message(
                    f"You don't have {progression.FOOD_STAR_UP_COST} of the same food at any tier below {progression.FOOD_MAX_STARS}★ yet.",
                    ephemeral=True,
                )
                return
            view = FoodStarUpView(self.user_id, starable, lab_view=self)
            await interaction.response.send_message(view.status_text(), view=view, ephemeral=True)
            return

        if action == "mass":
            view = MassFoodConfirmView(self.user_id, lab_view=self)
            if view.plan["total_upgrades"] == 0 and not view.keep_reserve:
                await interaction.response.send_message("There is no food that can currently be starred up.", ephemeral=True)
                return
            await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)


class FoodStarSelect(discord.ui.Select):
    def __init__(self, parent_view: "FoodStarUpView", page_items: list):
        self.parent_view = parent_view
        self.page_items = page_items
        options = []
        for row in page_items:
            current = int(row["stars"])
            qty = int(row["quantity"])
            options.append(
                discord.SelectOption(
                    label=f"{row['element']} {current}★ ×{qty}",
                    description=f"Use {progression.FOOD_STAR_UP_COST}× to make 1× {current + 1}★ food",
                    value=f"{row['element']}|{current}",
                    emoji=hero_text.element_icon(row["element"]),
                )
            )
        super().__init__(placeholder="Choose food to star up...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return

        element, stars_text = self.values[0].rsplit("|", 1)
        result = database.star_up_food(
            self.parent_view.user_id, element, int(stars_text), times=1,
            cost_per_upgrade=progression.FOOD_STAR_UP_COST, max_stars=progression.FOOD_MAX_STARS,
        )
        if not result.get("ok"):
            await interaction.response.send_message(f"Food star-up failed: `{result.get('reason', 'unknown')}`", ephemeral=True)
            return

        self.parent_view.starable = database.get_starable_food(
            self.parent_view.user_id, min_quantity=progression.FOOD_STAR_UP_COST, max_stars=progression.FOOD_MAX_STARS,
        )
        self.parent_view.page = min(self.parent_view.page, self.parent_view.total_pages - 1)
        self.parent_view.refresh_components()
        await interaction.response.edit_message(
            content=(
                f"✅ Starred up **{element} {result['from_stars']}★ → {result['to_stars']}★** "
                f"(used {result['food_spent']}, made {result['food_created']})."
                f"\n\n{self.parent_view.status_text() if self.parent_view.starable else 'No more food can be starred up right now.'}"
            ),
            view=self.parent_view if self.parent_view.starable else None,
        )
        if not self.parent_view.starable:
            self.parent_view.stop()
        if self.parent_view.lab_view is not None:
            await self.parent_view.lab_view.refresh()


class FoodStarUpView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, user_id: int, starable: list, page: int = 0, lab_view=None):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.starable = list(starable)
        self.page = max(0, page)
        self.lab_view = lab_view
        self.refresh_components()

    @property
    def total_pages(self):
        return max(1, (len(self.starable) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def current_items(self):
        start = self.page * self.PAGE_SIZE
        return self.starable[start:start + self.PAGE_SIZE]

    def status_text(self):
        start = self.page * self.PAGE_SIZE + 1
        end = min((self.page + 1) * self.PAGE_SIZE, len(self.starable))
        return (
            f"Pick a food stack: **{progression.FOOD_STAR_UP_COST}×** of it becomes **1×** of the next star.\n"
            f"Page **{self.page + 1}/{self.total_pages}** · showing {start}-{end} of {len(self.starable)} stacks."
        )

    def refresh_components(self):
        self.clear_items()
        items = self.current_items()
        if items:
            self.add_item(FoodStarSelect(self, items))

        previous = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=self.page <= 0)
        next_button = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages - 1)
        cancel_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary)

        async def previous_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page -= 1
            self.refresh_components()
            await interaction.response.edit_message(content=self.status_text(), view=self)

        async def next_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page += 1
            self.refresh_components()
            await interaction.response.edit_message(content=self.status_text(), view=self)

        async def cancel_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.stop()
            await interaction.response.edit_message(content="Closed. Nothing else was changed.", view=None)

        previous.callback = previous_callback
        next_button.callback = next_callback
        cancel_button.callback = cancel_callback
        self.add_item(previous)
        self.add_item(next_button)
        self.add_item(cancel_button)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return False
        return True


class MassFoodConfirmView(discord.ui.View):
    """Preview of exactly what Mass Star Up will do, with a switch for holding back hero-needed food."""

    def __init__(self, user_id: int, lab_view=None):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.lab_view = lab_view
        self.keep_reserve = True
        self._recompute()
        self._rebuild()

    def _recompute(self):
        snap = _lab_snapshot(self.user_id)
        self.reserve = snap["reserve"] if self.keep_reserve else {}
        self.snap = snap
        self.plan = lab_logic.plan_mass_star_up(snap["inv"], progression.FOOD_STAR_UP_COST, progression.FOOD_MAX_STARS, self.reserve)

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="✨ Mass Star Up", color=discord.Color.green())
        if self.plan["total_upgrades"] == 0:
            embed.description = (
                "Nothing to upgrade while holding back the food your heroes need.\n"
                "Switch **Keep food my heroes need** off to convert everything."
            )
        else:
            lines = [
                f"{hero_text.element_icon(row['element'])} {row['element']} {row['from_stars']}★ → {row['to_stars']}★  ×{row['upgrades']}"
                for row in self.plan["summary"]
            ]
            if len(lines) > 25:
                lines = lines[:24] + [f"…and {len(lines) - 24} more"]
            embed.description = "\n".join(lines)
            embed.add_field(name="Uses", value=f"**{self.plan['food_spent']}** food", inline=True)
            embed.add_field(name="Makes", value=f"**{self.plan['food_created']}** food", inline=True)
        if self.keep_reserve and self.snap["reserve"]:
            held = ", ".join(
                f"{qty}× {stars}★ {element}" for (element, stars), qty in sorted(self.snap["reserve"].items(), key=lambda kv: (kv[0][1], kv[0][0]))
            )
            embed.add_field(name="🛡️ Held back for your heroes", value=held[:1024], inline=False)
        embed.set_footer(text="Nothing changes until you press Confirm.")
        return embed

    def _rebuild(self):
        self.clear_items()
        confirm = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success, disabled=self.plan["total_upgrades"] == 0, row=0)
        toggle = discord.ui.Button(
            label=f"Keep food my heroes need: {'ON' if self.keep_reserve else 'OFF'}",
            style=discord.ButtonStyle.primary if self.keep_reserve else discord.ButtonStyle.secondary, row=0,
        )
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=0)

        async def confirm_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            await interaction.response.defer()
            self._recompute()  # inventory may have changed since the preview
            result = database.mass_star_up_food(
                self.user_id, cost_per_upgrade=progression.FOOD_STAR_UP_COST, max_stars=progression.FOOD_MAX_STARS,
                reserve=self.reserve or None,
            )
            self.stop()
            if not result.get("ok") or not result.get("summary"):
                await interaction.edit_original_response(content="No food was available for mass star-up.", embed=None, view=None)
                return
            lines = [
                f"{hero_text.element_icon(row['element'])} {row['element']} {row['from_stars']}★ → {row['to_stars']}★  ×{row['upgrades']}"
                for row in result["summary"][:25]
            ]
            done = discord.Embed(
                title="✅ Mass Star Up complete",
                description="\n".join(lines),
                color=discord.Color.green(),
            )
            done.add_field(name="Used", value=f"**{result['total_food_spent']}**", inline=True)
            done.add_field(name="Made", value=f"**{result['total_food_created']}**", inline=True)
            await interaction.edit_original_response(content=None, embed=done, view=None)
            if self.lab_view is not None:
                await self.lab_view.refresh()

        async def toggle_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.keep_reserve = not self.keep_reserve
            self._recompute()
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def cancel_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.stop()
            await interaction.response.edit_message(content="Cancelled. Nothing was changed.", embed=None, view=None)

        confirm.callback = confirm_callback
        toggle.callback = toggle_callback
        cancel.callback = cancel_callback
        self.add_item(confirm)
        self.add_item(toggle)
        self.add_item(cancel)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return False
        return True


class MeltSelect(discord.ui.Select):
    def __init__(self, parent_view: "MeltSelectView", page_items: list):
        self.parent_view = parent_view
        self.page_items = page_items
        options = []
        for w in page_items:
            tags = f"{hero_text.element_icon(w['element'])} {w['element']} → {w['stars']}★ food"
            if int(w["id"]) in parent_view.spare:
                tags += " · spare copy"
            options.append(discord.SelectOption(
                label=f"{w['name']} · {w['stars']}★ · Lv{w['level']}"[:100],
                description=tags[:100],
                value=str(w["id"]),
                default=int(w["id"]) in parent_view.selected_ids,
            ))
        super().__init__(placeholder="Pick heroes to melt...", options=options, min_values=0, max_values=max(1, len(options)), row=0)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.parent_view.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return
        page_ids = {int(w["id"]) for w in self.page_items}
        self.parent_view.selected_ids.difference_update(page_ids)
        self.parent_view.selected_ids.update(int(value) for value in self.values)
        self.parent_view.refresh_components()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class MeltSelectView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, user_id: int, meltable: list, owned: list, page: int = 0, selected_ids=None, lab_view=None):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.owned = list(owned)
        self.spare = lab_logic.spare_copy_ids(meltable, owned)
        # Spare duplicates first, then weakest to strongest, so the obvious
        # candidates are on top and your best heroes are never the first thing you see.
        self.meltable = sorted(meltable, key=lambda w: (0 if int(w["id"]) in self.spare else 1, int(w["stars"]), str(w["name"]).lower(), int(w["id"])))
        self.page = max(0, page)
        self.selected_ids = set(int(x) for x in (selected_ids or set()))
        self.lab_view = lab_view
        self.reviewing = False
        self.refresh_components()

    @property
    def total_pages(self):
        return max(1, (len(self.meltable) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def current_items(self):
        start = self.page * self.PAGE_SIZE
        return self.meltable[start:start + self.PAGE_SIZE]

    def _analysis(self):
        return lab_logic.analyze_melt(self.selected_ids, self.owned)

    def build_embed(self) -> discord.Embed:
        analysis = self._analysis()
        if self.reviewing:
            embed = discord.Embed(title="🔥 Confirm Melt Down", color=discord.Color.red())
            names = ", ".join(analysis["names"][:15]) + (f" (+{len(analysis['names']) - 15} more)" if len(analysis["names"]) > 15 else "")
            embed.description = f"You are about to melt **{analysis['count']}** hero(es):\n{names}"
            embed.add_field(name="You will get", value="\n".join(_yield_lines(analysis["yields"]))[:1024] or "Nothing", inline=False)
            if analysis["warnings"]:
                embed.add_field(name="⚠️ Heads up", value="\n".join(f"• {w}" for w in analysis["warnings"])[:1024], inline=False)
            embed.set_footer(text="This cannot be undone.")
            return embed

        embed = discord.Embed(title="🔥 Melt Down", color=discord.Color.orange())
        start = self.page * self.PAGE_SIZE + 1
        end = min((self.page + 1) * self.PAGE_SIZE, len(self.meltable))
        embed.description = (
            "Pick heroes to turn into Food. **Spare copies** (extra duplicates) are listed first. "
            f"Nothing is melted until you review and confirm.\nPage **{self.page + 1}/{self.total_pages}** · {start}-{end} of {len(self.meltable)}"
        )
        if analysis["count"]:
            embed.add_field(name=f"Selected ({analysis['count']}) → you'd get", value="\n".join(_yield_lines(analysis["yields"]))[:1024], inline=False)
        else:
            embed.add_field(name="Selected", value="*Nothing yet.*", inline=False)
        return embed

    def refresh_components(self):
        self.clear_items()

        if self.reviewing:
            confirm = discord.ui.Button(label=f"Melt {len(self.selected_ids)} hero(es)", style=discord.ButtonStyle.danger, row=0)
            back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=0)

            async def confirm_callback(interaction: discord.Interaction):
                if not await self._guard(interaction):
                    return
                await interaction.response.defer()
                result = database.melt_warriors(self.user_id, sorted(self.selected_ids))
                if not result.get("ok"):
                    reason = {"in_party": "one of them is now in your party", "not_found": "one of them is already gone"}.get(result.get("reason"), result.get("reason", "unknown"))
                    self.reviewing = False
                    self.refresh_components()
                    await interaction.edit_original_response(embed=self.build_embed(), view=self)
                    await interaction.followup.send(f"Melt failed: {reason}. Nothing was melted.", ephemeral=True)
                    return
                self.stop()
                summary = result.get("summary", [])
                yields: dict = {}
                for row in summary:
                    yields[(row["element"], int(row["stars"]))] = yields.get((row["element"], int(row["stars"])), 0) + 1
                done = discord.Embed(title=f"✅ Melted {len(summary)} hero(es)", description="\n".join(_yield_lines(yields)) or "Nothing", color=discord.Color.green())
                await interaction.edit_original_response(embed=done, view=None)
                if self.lab_view is not None:
                    await self.lab_view.refresh()

            async def back_callback(interaction: discord.Interaction):
                if not await self._guard(interaction):
                    return
                self.reviewing = False
                self.refresh_components()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            confirm.callback = confirm_callback
            back.callback = back_callback
            self.add_item(confirm)
            self.add_item(back)
            return

        items = self.current_items()
        if items:
            self.add_item(MeltSelect(self, items))

        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages - 1, row=1)
        spare_button = discord.ui.Button(
            label=f"Select spare copies ({len(self.spare)})", style=discord.ButtonStyle.primary,
            disabled=not self.spare, row=1,
        )
        clear_button = discord.ui.Button(label="Clear", style=discord.ButtonStyle.secondary, disabled=not self.selected_ids, row=1)
        review_button = discord.ui.Button(
            label=f"Review melt ({len(self.selected_ids)})", style=discord.ButtonStyle.danger,
            disabled=not self.selected_ids, row=2,
        )
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=2)

        async def previous_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page -= 1
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def next_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page += 1
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def spare_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.selected_ids.update(self.spare)
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def clear_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.selected_ids.clear()
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def review_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            if not self.selected_ids:
                await interaction.response.send_message("Pick at least one hero first.", ephemeral=True)
                return
            self.reviewing = True
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def cancel_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.stop()
            await interaction.response.edit_message(content="Cancelled. Nothing was melted.", embed=None, view=None)

        previous.callback = previous_callback
        next_button.callback = next_callback
        spare_button.callback = spare_callback
        clear_button.callback = clear_callback
        review_button.callback = review_callback
        cancel_button.callback = cancel_callback
        for button in (previous, next_button, spare_button, clear_button, review_button, cancel_button):
            self.add_item(button)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your Lab.", ephemeral=True)
            return False
        return True


@bot.tree.command(name="lab", description="View the Character Lab and your food inventory")
async def lab(interaction: discord.Interaction):
    embed = build_lab_embed(interaction.user.id)
    track_progress(interaction.user.id, "view_lab")
    view = LabView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        pass


# Cosmetic avatars available in the shop. Add more by adding entries here AND
# dropping a matching assets/avatars/<id>.png file - same pattern as banners
# and custom warriors.
AVATARS = [
    {"id": "avatar_flame", "name": "Flame Emblem", "cost": 150},
    {"id": "avatar_wave", "name": "Wave Emblem", "cost": 150},
    {"id": "avatar_leaf", "name": "Leaf Emblem", "cost": 150},
    {"id": "avatar_star", "name": "Star Emblem", "cost": 200},
    {"id": "avatar_void", "name": "Void Emblem", "cost": 250},
]


def build_shop_embed(user: dict) -> discord.Embed:
    owned_avatars = database.get_user_avatars(user["user_id"])

    embed = discord.Embed(
        title="Shop",
        description=(
            f"**Summon Charm** — {SUMMON_CHARM_COST_SOLITE} Solite each\n"
            "Used to pull warriors via `/pull`.\n\n"
            f"**Memory Stones** — {progression.MEMORY_STONE_PACK_COST} Solite for {progression.MEMORY_STONE_PACK_SIZE}.\n"
            "Used for character star upgrades.\n\n"
            "**Avatars** — pick one below to buy (or equip, if you already own it)."
        ),
        color=discord.Color.gold(),
    )

    avatar_lines = []
    for avatar in AVATARS:
        owned = avatar["id"] in owned_avatars
        equipped = user["avatar"] == avatar["id"]
        tag = " (equipped)" if equipped else " (owned)" if owned else ""
        price = "Owned" if owned else f"{avatar['cost']} Solite"
        avatar_lines.append(f"{avatar['name']} — {price}{tag}")
    embed.add_field(name="Available Avatars", value="\n".join(avatar_lines), inline=False)

    embed.set_footer(text=f"Your balance: {user['solite']} Solite")
    return embed


class MemoryStoneBuyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label=f"Buy {progression.MEMORY_STONE_PACK_SIZE} Memory Stones ({progression.MEMORY_STONE_PACK_COST} Solite)", style=discord.ButtonStyle.primary, emoji="💎")

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if interaction.user.id != view.user_id:
            await interaction.response.send_message("This isn't your shop menu.", ephemeral=True)
            return
        user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
        cost = progression.MEMORY_STONE_PACK_COST
        if int(user["solite"]) < cost:
            await interaction.response.send_message(
                f"You need {cost} Solite but only have {user['solite']}.", ephemeral=True
            )
            return
        database.update_user_field(interaction.user.id, "solite", int(user["solite"]) - cost)
        database.update_user_field(
            interaction.user.id, "memory_stones",
            int(user["memory_stones"]) + progression.MEMORY_STONE_PACK_SIZE
        )
        refreshed = database.get_or_create_user(interaction.user.id, "")
        await interaction.response.edit_message(embed=build_shop_embed(refreshed), view=view)
        await interaction.followup.send(
            f"Bought {progression.MEMORY_STONE_PACK_SIZE} Memory Stones for {cost} Solite.", ephemeral=True
        )


class AvatarSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(label=f"{a['name']} ({a['cost']} Solite)", value=a["id"])
            for a in AVATARS
        ]
        super().__init__(placeholder="Buy or equip an avatar...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your shop menu.", ephemeral=True)
            return

        avatar_id = self.values[0]
        avatar = next(a for a in AVATARS if a["id"] == avatar_id)
        user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
        owned = database.user_owns_avatar(interaction.user.id, avatar_id)

        if not owned:
            if user["solite"] < avatar["cost"]:
                await interaction.response.send_message(
                    f"You need {avatar['cost']} Solite for {avatar['name']} but only have {user['solite']}.",
                    ephemeral=True,
                )
                return
            database.update_user_field(interaction.user.id, "solite", user["solite"] - avatar["cost"])
            database.grant_avatar(interaction.user.id, avatar_id)

        database.update_user_field(interaction.user.id, "avatar", avatar_id)

        refreshed = database.get_or_create_user(interaction.user.id, "")
        embed = build_shop_embed(refreshed)
        view = ShopView(interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)

        msg = f"Equipped {avatar['name']}." if owned else f"Bought and equipped {avatar['name']} for {avatar['cost']} Solite."
        await interaction.followup.send(msg, ephemeral=True)


class ShopView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(MemoryStoneBuyButton())
        self.add_item(AvatarSelect(user_id))

    async def _buy_charms(self, interaction: discord.Interaction, quantity: int):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your shop menu.", ephemeral=True)
            return

        user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
        cost = SUMMON_CHARM_COST_SOLITE * quantity

        if user["solite"] < cost:
            await interaction.response.send_message(
                f"You need {cost} Solite but only have {user['solite']}.",
                ephemeral=True,
            )
            return

        database.update_user_field(interaction.user.id, "solite", user["solite"] - cost)
        database.update_user_field(interaction.user.id, "summon_charms", user["summon_charms"] + quantity)

        refreshed = database.get_or_create_user(interaction.user.id, "")
        embed = build_shop_embed(refreshed)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(
            f"Bought {quantity} Summon Charm(s) for {cost} Solite.", ephemeral=True
        )

    @discord.ui.button(label=f"Buy x1 ({SUMMON_CHARM_COST_SOLITE} Solite)", style=discord.ButtonStyle.primary, emoji="🎫")
    async def buy_one(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy_charms(interaction, 1)

    @discord.ui.button(label=f"Buy x10 ({SUMMON_CHARM_COST_SOLITE * 10} Solite)", style=discord.ButtonStyle.success, emoji="🎟️")
    async def buy_ten(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._buy_charms(interaction, 10)


@bot.tree.command(name="shop", description="Buy Summon Charms and cosmetic avatars with Solite")
async def shop(interaction: discord.Interaction):
    await interaction.response.defer()
    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    track_progress(interaction.user.id, "view_shop")
    embed = build_shop_embed(user)

    icon_path = warrior_art.get_icon_path("summon_charm")
    if icon_path:
        optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, icon_path)
        file = discord.File(optimized, filename="icon.png")
        embed.set_thumbnail(url="attachment://icon.png")
        await interaction.followup.send(embed=embed, file=file, view=ShopView(interaction.user.id))
    else:
        await interaction.followup.send(embed=embed, view=ShopView(interaction.user.id))


def build_daily_embed(user_id: int, already_claimed_today: bool) -> discord.Embed:
    status = get_daily_task_status(user_id)
    all_complete = all(complete for _, _, complete in status)

    lines = []
    for task, count, complete in status:
        check = "✅" if complete else "⬜"
        lines.append(f"{check} {task['label']} ({min(count, task['target'])}/{task['target']})")

    embed = discord.Embed(
        title="Daily Tasks",
        description="\n".join(lines),
        color=discord.Color.green() if all_complete else discord.Color.blurple(),
    )
    if already_claimed_today:
        embed.set_footer(text="You've already claimed today's reward. Come back tomorrow!")
    elif all_complete:
        embed.set_footer(text=f"All tasks complete! Claim your {DAILY_TASK_REWARD_SOLITE} Solite below.")
    else:
        embed.set_footer(text="Complete all tasks to unlock your claim button.")
    return embed


class DailyClaimView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="Claim 300 Solite", style=discord.ButtonStyle.success, emoji="🎁")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your daily tasks menu.", ephemeral=True)
            return

        user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
        today = today_str()

        if user["last_daily_claim"] == today:
            await interaction.response.send_message("You've already claimed today's reward.", ephemeral=True)
            return

        status = get_daily_task_status(interaction.user.id)
        if not all(complete for _, _, complete in status):
            await interaction.response.send_message(f"Complete all {len(DAILY_TASKS)} tasks first!", ephemeral=True)
            return

        database.update_user_field(interaction.user.id, "solite", user["solite"] + DAILY_TASK_REWARD_SOLITE)
        database.update_user_field(interaction.user.id, "last_daily_claim", today)

        embed = build_daily_embed(interaction.user.id, already_claimed_today=True)
        await interaction.response.edit_message(embed=embed, view=None)
        await interaction.followup.send(f"🎉 Claimed {DAILY_TASK_REWARD_SOLITE} Solite!", ephemeral=True)


@bot.tree.command(name="daily", description="View your daily tasks and claim your reward")
async def daily(interaction: discord.Interaction):
    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    today = today_str()
    already_claimed = user["last_daily_claim"] == today

    embed = build_daily_embed(interaction.user.id, already_claimed)

    if already_claimed:
        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message(embed=embed, view=DailyClaimView(interaction.user.id))


# ---------------------------------------------------------------------------
# Party setup: /party - place 6 owned warriors into 6 battle slots
# (Slot positions/roles like front/back row will be designed later - for now
# this just tracks which warrior sits in which of the 6 numbered slots.)
# ---------------------------------------------------------------------------

party_group = app_commands.Group(name="party", description="Manage your 6-warrior battle party")


def build_party_embed(user_id: int, display_name: str) -> discord.Embed:
    party = database.get_party(user_id)
    embed = discord.Embed(title=f"{display_name}'s Party", color=discord.Color.orange())
    for position in range(1, 7):
        if position in party:
            w = party[position]
            value = f"{w['name']} ({w['element']}) — {w['stars']}★"
        else:
            value = "*Empty*"
        embed.add_field(name=f"Slot {position}", value=value, inline=True)
    return embed


async def send_party_response(interaction: discord.Interaction, content: str = None):
    """Sends the party embed, with the composed background+sprites scene attached
    if a background image has been added yet (assets/party/background.png).

    IMPORTANT: defers immediately so Discord doesn't time out the interaction
    while the image is being composited (this was causing "bot didn't respond"
    errors), and runs the actual image work in a background thread so it
    doesn't freeze the bot for everyone else while it happens.
    """
    await interaction.response.defer()

    user_id = interaction.user.id
    display_name = str(interaction.user.display_name)
    party = database.get_party(user_id)
    embed = build_party_embed(user_id, display_name)

    scene_buffer = await asyncio.to_thread(warrior_art.build_party_scene, party)
    if scene_buffer:
        file = discord.File(scene_buffer, filename="party.png")
        embed.set_image(url="attachment://party.png")
        await interaction.followup.send(content=content, embed=embed, file=file)
    else:
        await interaction.followup.send(content=content, embed=embed)


@party_group.command(name="view", description="View your current battle party")
async def party_view(interaction: discord.Interaction):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    track_progress(interaction.user.id, "view_party")
    await send_party_response(interaction)


@party_group.command(name="set", description="Place one of your warriors into a party slot")
@app_commands.describe(position="Which slot (1-6) to place the warrior in", warrior_name="Name (or partial name) of a warrior you own")
@app_commands.choices(position=[app_commands.Choice(name=str(i), value=i) for i in range(1, 7)])
async def party_set(interaction: discord.Interaction, position: app_commands.Choice[int], warrior_name: str):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    owned = database.get_user_warriors(interaction.user.id)
    matches = [w for w in owned if warrior_name.lower() in w["name"].lower()]

    if not matches:
        await interaction.response.send_message(
            f"You don't own any warrior matching '{warrior_name}'. Check `/heroes`.",
            ephemeral=True,
        )
        return

    warrior = matches[0]
    database.set_party_slot(interaction.user.id, position.value, warrior["id"])
    track_progress(interaction.user.id, "set_party")

    await send_party_response(interaction, content=f"Placed **{warrior['name']}** in Slot {position.value}.")


@party_group.command(name="clear", description="Remove the warrior from a party slot")
@app_commands.describe(position="Which slot (1-6) to clear")
@app_commands.choices(position=[app_commands.Choice(name=str(i), value=i) for i in range(1, 7)])
async def party_clear(interaction: discord.Interaction, position: app_commands.Choice[int]):
    database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    database.clear_party_slot(interaction.user.id, position.value)
    await send_party_response(interaction, content=f"Cleared Slot {position.value}.")


bot.tree.add_command(party_group)


# ---------------------------------------------------------------------------
# Admin commands - only usable by the account matching OWNER_ID in .env
# ---------------------------------------------------------------------------

admin_group = app_commands.Group(name="admin", description="Owner-only bot management commands")


@admin_group.command(name="give-solite", description="[Owner only] Give a user Solite")
@app_commands.describe(user="The user to give Solite to", amount="How much Solite to add")
async def admin_give_solite(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    target = database.get_or_create_user(user.id, str(user.display_name))
    database.update_user_field(user.id, "solite", target["solite"] + amount)
    await interaction.response.send_message(f"Gave {amount} Solite to {user.display_name}.", ephemeral=True)


@admin_group.command(name="give-exp", description="[Owner only] Give a user EXP")
@app_commands.describe(user="The user to give EXP to", amount="How much EXP to add")
async def admin_give_exp(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("EXP amount must be greater than 0.", ephemeral=True)
        return
    target = database.get_or_create_user(user.id, str(user.display_name))
    new_exp = int(target["exp"]) + amount
    database.update_user_field(user.id, "exp", new_exp)
    await interaction.response.send_message(
        f"Gave **{amount} EXP** to {user.display_name}. They now have **{new_exp} EXP**.",
        ephemeral=True,
    )


@admin_group.command(name="give-charms", description="[Owner only] Give a user Summon Charms")
@app_commands.describe(user="The user to give Charms to", amount="How many Charms to add")
async def admin_give_charms(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    target = database.get_or_create_user(user.id, str(user.display_name))
    database.update_user_field(user.id, "summon_charms", target["summon_charms"] + amount)
    await interaction.response.send_message(f"Gave {amount} Summon Charm(s) to {user.display_name}.", ephemeral=True)


@admin_group.command(name="give-item", description="[Owner only] Give food, Memory Stones, an avatar, or any inventory item")
@app_commands.describe(
    user="The user to receive it",
    item="Start typing and pick: Memory Stones, Food: <element>, Avatar: <name>, or an existing item",
    amount="How many to give (ignored for avatars)",
    stars="Food only: star tier, 4-20 (default 4)",
    item_type="Only for a brand-new item: its category label",
    create_new="Set True to create a brand-new custom item that doesn't exist yet",
)
async def admin_give_item(
    interaction: discord.Interaction,
    user: discord.Member,
    item: str,
    amount: app_commands.Range[int, 1, 1000000] = 1,
    stars: app_commands.Range[int, 4, 20] = 4,
    item_type: str = "item",
    create_new: bool = False,
):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    database.get_or_create_user(user.id, str(user.display_name))
    spec = admin_items.resolve_giveable(item, AVATARS, database.get_all_item_names())
    outcome = admin_items.give(user.id, spec, amount, stars=stars, item_type=item_type, create_new=create_new)
    prefix = "" if outcome["ok"] else "Nothing given. "
    await interaction.response.send_message(f"{prefix}{outcome['message']} (for {user.display_name})", ephemeral=True)


@admin_give_item.autocomplete("item")
async def admin_give_item_autocomplete(interaction: discord.Interaction, current: str):
    if not is_owner(interaction):
        return []  # don't leak item names to anyone who isn't the owner
    options = admin_items.suggestions(current, AVATARS, database.get_all_item_names())
    return [app_commands.Choice(name=o[:100], value=o[:100]) for o in options[:25]]


@admin_group.command(name="take-item", description="[Owner only] Remove a generic inventory item from a user (e.g. junk items)")
@app_commands.describe(
    user="The user to take from",
    item="Item name (pick from the suggestions)",
    amount="How many to remove (leave blank to remove the whole stack)",
)
async def admin_take_item(
    interaction: discord.Interaction,
    user: discord.Member,
    item: str,
    amount: app_commands.Range[int, 1, 1000000] = None,
):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    owned = {i["item_name"].lower(): i for i in database.get_user_items(user.id)}
    stack = owned.get(item.strip().lower())
    if not stack:
        await interaction.response.send_message(
            f"{user.display_name} doesn't have an inventory item called **{item}**. "
            "(This only removes generic items - not food, Memory Stones or avatars.)",
            ephemeral=True,
        )
        return
    take = int(amount) if amount else int(stack["quantity"])
    result = database.remove_item(user.id, stack["item_name"], take)
    if not result.get("ok"):
        await interaction.response.send_message(
            f"Couldn't remove that: {result.get('reason', 'unknown')} (they have {result.get('available', 0):,}).",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        f"Removed **{take:,}× {result['item_name']}** from {user.display_name}. {result['quantity']:,} left.",
        ephemeral=True,
    )


@admin_take_item.autocomplete("item")
async def admin_take_item_autocomplete(interaction: discord.Interaction, current: str):
    if not is_owner(interaction):
        return []
    q = current.strip().lower()
    names = [n for n in database.get_all_item_names() if q in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


@admin_group.command(name="give-warrior", description="[Owner only] Force-give a specific warrior to a user")
@app_commands.describe(user="The user to give the warrior to", warrior_name="Name (or partial name) of the warrior")
async def admin_give_warrior(interaction: discord.Interaction, user: discord.Member, warrior_name: str):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    database.get_or_create_user(user.id, str(user.display_name))
    catalog = database.get_all_catalog_warriors()
    matches = [w for w in catalog if warrior_name.lower() in w["name"].lower()]
    if not matches:
        await interaction.response.send_message(f"No warrior found matching '{warrior_name}'.", ephemeral=True)
        return
    warrior = matches[0]
    outcome = database.add_warrior_to_user(user.id, warrior["warrior_id"])
    await interaction.response.send_message(
        f"Gave **{warrior['name']}** to {user.display_name} ({outcome['stars']}★ copy, instance ID {outcome['instance_id']}).",
        ephemeral=True,
    )


@admin_group.command(name="reset-pity", description="[Owner only] Reset a user's pity counter to 0")
@app_commands.describe(user="The user whose pity counter to reset")
async def admin_reset_pity(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    database.get_or_create_user(user.id, str(user.display_name))
    database.update_user_field(user.id, "pity_counter", 0)
    await interaction.response.send_message(f"Reset {user.display_name}'s pity counter to 0.", ephemeral=True)


@admin_group.command(name="view-profile", description="[Owner only] View any user's full profile")
@app_commands.describe(user="The user to inspect")
async def admin_view_profile(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    target = database.get_or_create_user(user.id, str(user.display_name))
    warriors = database.get_user_warriors(user.id)

    embed = discord.Embed(title=f"[Admin View] {user.display_name}'s Profile", color=discord.Color.red())
    embed.add_field(name="User ID", value=str(user.id), inline=False)
    embed.add_field(name="Solite", value=str(target["solite"]), inline=True)
    embed.add_field(name="Summon Charms", value=str(target["summon_charms"]), inline=True)
    embed.add_field(name="EXP", value=str(target["exp"]), inline=True)
    embed.add_field(name="Pity Counter", value=str(target["pity_counter"]), inline=True)
    embed.add_field(name="Story Chapter", value=str(target["story_chapter"]), inline=True)
    embed.add_field(name="Warriors Owned", value=str(len(warriors)), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="edit-profile", description="[Owner only] Directly set a user's Solite, Charms, or Pity")
@app_commands.describe(
    user="The user to edit",
    field="Which field to set",
    value="The new value",
)
@app_commands.choices(field=[
    app_commands.Choice(name="Solite", value="solite"),
    app_commands.Choice(name="Summon Charms", value="summon_charms"),
    app_commands.Choice(name="EXP", value="exp"),
    app_commands.Choice(name="Pity Counter", value="pity_counter"),
    app_commands.Choice(name="Story Chapter", value="story_chapter"),
])
async def admin_edit_profile(interaction: discord.Interaction, user: discord.Member, field: app_commands.Choice[str], value: int):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    database.get_or_create_user(user.id, str(user.display_name))
    database.update_user_field(user.id, field.value, value)
    await interaction.response.send_message(f"Set {user.display_name}'s {field.name} to {value}.", ephemeral=True)


@admin_group.command(name="find-warrior", description="[Owner only] Look up warrior IDs by name (for naming art files)")
@app_commands.describe(name="Name or partial name to search for")
async def admin_find_warrior(interaction: discord.Interaction, name: str):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    catalog = database.get_all_catalog_warriors()
    matches = [w for w in catalog if name.lower() in w["name"].lower()]

    if not matches:
        await interaction.response.send_message(f"No warriors found matching '{name}'.", ephemeral=True)
        return

    lines = [f"**ID {w['warrior_id']}** — {w['name']} ({w['element']})" for w in matches[:25]]
    embed = discord.Embed(
        title=f"Warriors matching '{name}'",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    if len(matches) > 25:
        embed.set_footer(text=f"Showing 25 of {len(matches)} matches — search a more specific name to narrow it down.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


def _test_status_lines(statuses: list[dict]) -> str:
    lines = []
    for st in statuses:
        icon = "❤️" if st["alive"] else "💀"
        extra = ""
        if st.get("flamebound"):
            extra += f" · Flamebound {st['flamebound']}"
        if st.get("blood_debt"):
            extra += f" · Blood Debt {st['blood_debt']}"
        lines.append(f"{icon} {st['name']} Lv{st['level']} — {max(int(st['hp']), 0)}/{int(st['max_hp'])}{extra}")
    return "\n".join(lines)[:1024] or "None"


@admin_group.command(name="test-battle", description="[Owner only] Simulate a battle between any heroes (free, nothing is saved)")
@app_commands.describe(
    allies="Up to 6 heroes, comma-separated. Optional level/stars each: Elen:30:6, Nyx:25, Keil",
    enemies="Same format, or chapter:3 to fight a story chapter's enemy team",
    level="Default level for heroes with no level of their own (1-100, default 1)",
    runs="Number of battles to simulate (1-1000). 1 shows the log; more shows win-rate stats",
)
async def admin_test_battle(
    interaction: discord.Interaction,
    allies: str,
    enemies: str,
    level: app_commands.Range[int, 1, 100] = 1,
    runs: app_commands.Range[int, 1, 1000] = 1,
):
    if not is_owner(interaction):
        await interaction.response.send_message("You don't have permission to use this.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    catalog = database.get_all_catalog_warriors()
    ally_team, err = testbattle.parse_team(allies, catalog, level)
    if err:
        await interaction.followup.send(f"Allies: {err}", ephemeral=True)
        return
    enemy_team, chapter, err = testbattle.parse_enemies(enemies, catalog, level)
    if err:
        await interaction.followup.send(f"Enemies: {err}", ephemeral=True)
        return

    stats = await asyncio.to_thread(testbattle.run_many, ally_team, enemy_team, chapter, runs)
    last = stats["last"]
    enemy_label = testbattle.describe_team(enemy_team) if enemy_team else f"{chapter.get('boss_name', 'boss')} (legacy boss)"
    setup = f"**Allies:** {testbattle.describe_team(ally_team)}\n**Enemies:** {enemy_label}"

    if runs == 1:
        embed = discord.Embed(
            title="Test Battle — " + ("Victory" if last["won"] else "Defeat"),
            color=discord.Color.green() if last["won"] else discord.Color.red(),
        )
        embed.add_field(name="Setup", value=setup[:1024], inline=False)
        embed.add_field(name="Allies (end of fight)", value=_test_status_lines(last["party_status"]), inline=False)
        embed.add_field(name="Enemies (end of fight)", value=_test_status_lines(last["enemy_status"]), inline=False)
        embed.set_footer(text=f"{last['rounds']} round(s) · tap Full Log to browse everything · nothing was saved")
        room = min(4096, 6000 - len(embed) - 200)
        embed.description = battle.fit_log_to_limit(last["log"], room)
        log_file = discord.File(io.BytesIO("\n".join(last["log"]).encode("utf-8")), filename="battle_log.txt")
        log_view = BattleResultView(interaction.user.id, last["log"], title="Test Battle Log")
        await interaction.followup.send(embed=embed, file=log_file, view=log_view, ephemeral=True)
        return

    win_rate = stats["wins"] / runs * 100
    embed = discord.Embed(
        title=f"Test Battle — {runs} runs",
        color=discord.Color.green() if win_rate >= 50 else discord.Color.red(),
    )
    embed.add_field(name="Setup", value=setup[:1024], inline=False)
    embed.add_field(name="Win rate", value=f"**{win_rate:.1f}%** ({stats['wins']}/{runs})", inline=True)
    embed.add_field(
        name="Rounds",
        value=f"avg {stats['avg_rounds']:.1f} (min {stats['min_rounds']}, max {stats['max_rounds']})",
        inline=True,
    )
    embed.add_field(name="Timeouts", value=f"{stats['timeouts']} (hit the {battle.MAX_ROUNDS}-round limit)", inline=True)
    survival = "\n".join(f"{w['name']}: {rate * 100:.0f}% survive" for w, rate in zip(ally_team, stats["survival"]))
    embed.add_field(name="Ally survival", value=survival[:1024], inline=False)
    embed.set_footer(text="Use runs:1 to read a full battle log · nothing was saved")
    await interaction.followup.send(embed=embed, ephemeral=True)


bot.tree.add_command(admin_group)


# ---------------------------------------------------------------------------
# /heroes - browse every warrior in the catalog, one at a time, showing their
# FULL ART (assets/fullart/<id>.png) - a different image set from the small
# battle sprites used everywhere else (assets/warriors/<id>.png).
# ---------------------------------------------------------------------------

def build_hero_embed(warrior: dict, index: int, total: int) -> discord.Embed:
    embed = discord.Embed(
        title=warrior["name"],
        description=f"**Element:** {warrior['element']}\n**Stars:** {warrior.get('stars', 4)}★",
        color=discord.Color.purple(),
    )
    stats = hero_text.effective_stats(warrior, level=1, stars=int(warrior.get("stars", 4)))
    embed.add_field(name="HP", value=f"{stats['hp']:,}", inline=True)
    embed.add_field(name="Attack", value=f"{stats['atk']:,}", inline=True)
    embed.add_field(name="Defense", value=f"{stats['def']:,}", inline=True)
    embed.add_field(name="Magic Attack", value=f"{stats['matk']:,}", inline=True)
    embed.add_field(name="Magic Defense", value=f"{stats['mdef']:,}", inline=True)
    embed.add_field(name="Speed", value=f"{stats['speed']:,}", inline=True)
    embed.set_footer(text=f"Hero {index + 1} of {total}  ·  stats shown at Lv 1")
    return embed


class HeroDexView(discord.ui.View):
    def __init__(self, user_id: int, warriors: list, index: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.warriors = warriors
        self.index = index

    async def send_current(self, interaction: discord.Interaction):
        warrior = self.warriors[self.index]
        embed = build_hero_embed(warrior, self.index, len(self.warriors))
        image_path = warrior_art.get_warrior_fullart_path(warrior["warrior_id"])
        optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, image_path)
        file = discord.File(optimized, filename="fullart.png")
        embed.set_image(url="attachment://fullart.png")
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hero browser.", ephemeral=True)
            return
        self.index = (self.index - 1) % len(self.warriors)
        await self.send_current(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hero browser.", ephemeral=True)
            return
        self.index = (self.index + 1) % len(self.warriors)
        await self.send_current(interaction)


class LevelUpCopySelect(discord.ui.Select):
    def __init__(self, user_id: int, warriors: list, requested_levels: int):
        self.user_id = user_id
        self.warriors = warriors
        self.requested_levels = requested_levels
        options = []
        for w in warriors[:25]:
            options.append(
                discord.SelectOption(
                    label=f"{w['name']} — Lv. {w['level']} — {w['stars']}★",
                    description=f"Copy ID {w['id']}",
                    value=str(w['id']),
                )
            )
        super().__init__(placeholder="Choose the copy to level...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your level-up menu.", ephemeral=True)
            return

        user_warrior_id = int(self.values[0])
        warrior = database.get_user_warrior(self.user_id, user_warrior_id)
        if not warrior:
            await interaction.response.send_message("That warrior copy is no longer in your collection.", ephemeral=True)
            return

        current_level = int(warrior['level'])
        level_cap = progression.effective_level_cap(int(warrior['stars']), current_level)
        actual_levels = min(self.requested_levels, level_cap - current_level)
        if actual_levels <= 0:
            await interaction.response.send_message(
                f"**{warrior['name']}** is at its level cap (Lv. {level_cap}). Star it up to raise the cap.", ephemeral=True
            )
            return

        user = database.get_or_create_user(self.user_id, "")
        cost = progression.level_up_cost_for_levels(current_level, actual_levels)
        if int(user['exp']) < cost:
            await interaction.response.send_message(
                f"You need **{cost} EXP** to reach Lv. {current_level + actual_levels}, but you only have **{user['exp']} EXP**.",
                ephemeral=True,
            )
            return

        result = database.level_up_warrior(
            self.user_id,
            user_warrior_id,
            actual_levels,
            cost,
            max_level=level_cap,
        )
        if not result or not result.get('ok'):
            await interaction.response.send_message("The level-up could not be completed.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content=(
                f"Leveled **{warrior['name']}** (Copy ID {user_warrior_id}) "
                f"from Lv. {result['old_level']} → Lv. {result['level']} "
                f"using {result['exp_spent']} EXP. You have {result['exp_remaining']} EXP left."
            ),
            view=None,
        )


class LevelUpCopySelectView(discord.ui.View):
    def __init__(self, user_id: int, warriors: list, requested_levels: int):
        super().__init__(timeout=300)
        self.add_item(LevelUpCopySelect(user_id, warriors, requested_levels))


class MultiLevelUpModal(discord.ui.Modal, title="Level Up Multiple Times"):
    levels = discord.ui.TextInput(
        label="Levels to gain",
        placeholder="Example: 5",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, view: "HeroInventoryView"):
        super().__init__()
        self.inventory_view = view

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.inventory_view.user_id:
            await interaction.response.send_message("This isn't your inventory.", ephemeral=True)
            return

        try:
            requested = int(str(self.levels.value).strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number of levels.", ephemeral=True)
            return

        if requested < 1:
            await interaction.response.send_message("Levels must be at least 1.", ephemeral=True)
            return

        warrior_id = self.inventory_view.warriors[self.inventory_view.index]["id"]
        warrior = database.get_user_warrior(self.inventory_view.user_id, warrior_id)
        if not warrior:
            await interaction.response.send_message("That warrior is no longer in your collection.", ephemeral=True)
            return

        current_level = int(warrior["level"])
        level_cap = progression.effective_level_cap(int(warrior["stars"]), current_level)
        if current_level >= level_cap:
            await interaction.response.send_message(
                f"**{warrior['name']}** is at its level cap (Lv. {level_cap}). Star it up to raise the cap.", ephemeral=True
            )
            return

        actual_levels = min(requested, level_cap - current_level)
        cost = progression.level_up_cost_for_levels(current_level, actual_levels)
        user = database.get_or_create_user(self.inventory_view.user_id, "")

        if int(user["exp"]) < cost:
            affordable = progression.max_levels_affordable(current_level, int(user["exp"]), level_cap)
            await interaction.response.send_message(
                f"You need **{cost} EXP** for Lv. {current_level + actual_levels}, but you only have **{user['exp']} EXP**."
                + (f" You can currently afford **{affordable} level(s)**." if affordable else ""),
                ephemeral=True,
            )
            return

        result = database.level_up_warrior(
            self.inventory_view.user_id,
            warrior_id,
            actual_levels,
            cost,
            max_level=level_cap,
        )
        if not result or not result.get("ok"):
            await interaction.response.send_message("The level-up could not be completed.", ephemeral=True)
            return

        self.inventory_view._reload(warrior_id)
        await interaction.response.send_message(
            f"Leveled **{warrior['name']}** from Lv. {result['old_level']} → Lv. {result['level']} "
            f"using **{result['exp_spent']} EXP**. You have **{result['exp_remaining']} EXP** left.",
            ephemeral=True,
        )
        await self.inventory_view._edit_message_after_modal(interaction)


# ---------------------------------------------------------------------------
# /heroes - your collection. Three tabs (Overview / Skills / Upgrade), a
# dropdown to jump straight to any hero, and ◀ ▶ to step through them.
# ---------------------------------------------------------------------------

_CARD_CACHE: dict = {}
_CARD_CACHE_MAX = 96


def _card_key(w: dict):
    return (w["warrior_id"], int(w["stars"]), int(w["level"]))


async def get_card_bytes(warrior: dict) -> bytes:
    """Character card PNG bytes, cached so flipping between heroes/tabs is instant."""
    key = _card_key(warrior)
    data = _CARD_CACHE.get(key)
    if data is None:
        buffer = await asyncio.to_thread(card_art.build_character_card, warrior)
        data = buffer.getvalue()
        if len(_CARD_CACHE) >= _CARD_CACHE_MAX:
            _CARD_CACHE.pop(next(iter(_CARD_CACHE)))
        _CARD_CACHE[key] = data
    return data


async def get_card_file(warrior: dict) -> discord.File:
    return discord.File(io.BytesIO(await get_card_bytes(warrior)), filename="character_card.png")


def sort_warriors(warriors: list) -> list:
    """Highest stars first, then A-Z, then highest level (copies of a hero sit together)."""
    return sorted(
        warriors,
        key=lambda w: (-int(w.get("stars", 4)), str(w["name"]).lower(), -int(w["level"]), int(w["id"])),
    )


def build_hero_overview_embed(warrior: dict, index: int, total: int, party_slot, copy_no: int, copy_total: int) -> discord.Embed:
    level = int(warrior["level"])
    stars = int(warrior["stars"])
    base = int(warrior.get("base_stars", 4))
    max_stars = progression.character_max_stars(base)
    cap = progression.effective_level_cap(stars, level)
    stats = hero_text.effective_stats(warrior)

    lines = [
        f"{hero_text.element_icon(warrior['element'])} **{warrior['element']}**  ·  **{stars}★** of {max_stars}★  ·  summoned as {base}★",
        f"**Lv {level}/{cap}**  {hero_text.progress_bar(level, cap)}",
        f"⚡ **BP {hero_text.battle_power(stats):,}**",
    ]
    if max_stars >= min(progression.STAR_BREAKTHROUGH_BONUS):
        reached = set(progression.breakthroughs_reached(stars, base))
        track = "  ".join(f"{'✅' if m in reached else '▫️'} {m}★" for m in sorted(progression.STAR_BREAKTHROUGH_BONUS) if m <= max_stars)
        lines.append(f"🌟 Breakthroughs: {track}")
    if party_slot:
        lines.append(f"📌 In your party, slot {party_slot}")
    if copy_total > 1:
        lines.append(f"📚 Copy {copy_no} of {copy_total} you own")

    embed = discord.Embed(
        title=warrior["name"],
        description="\n".join(lines),
        color=discord.Color(hero_text.element_color(warrior["element"])),
    )
    embed.add_field(name="Stats (at current level & stars)", value=hero_text.stat_block(stats), inline=False)
    embed.set_footer(text=f"Hero {index + 1} of {total}  ·  tap Skills or Upgrade above")
    return embed


def build_hero_skills_embed(warrior: dict, index: int, total: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"{warrior['name']} — Skills",
        description=hero_text.rotation_text(),
        color=discord.Color(hero_text.element_color(warrior["element"])),
    )
    for number, marker in ((1, "①"), (2, "②"), (3, "③")):
        skill_name, lines = hero_text.describe_skill(warrior, number)
        body = "\n".join(f"• {line}" for line in lines) or "*No effects set up yet.*"
        if number == 3:
            body += f"\n*{hero_text.skill3_rule(warrior)}*"
        embed.add_field(name=f"{marker} {skill_name or 'Unnamed skill'}"[:256], value=body[:1024], inline=False)

    passive_name, passive_text = hero_text.describe_passive(warrior)
    if passive_name or passive_text:
        embed.add_field(
            name=(f"✨ Passive — {passive_name}" if passive_name else "✨ Passive")[:256],
            value=(passive_text or "*No description yet.*")[:1024],
            inline=False,
        )
    embed.set_footer(text=f"Hero {index + 1} of {total}  ·  'ATK damage' uses ATK; plain 'damage' uses the higher of ATK / MATK")
    return embed


def build_hero_upgrade_embed(warrior: dict, user: dict, star_status: dict, index: int, total: int) -> discord.Embed:
    level = int(warrior["level"])
    stars = int(warrior["stars"])
    max_stars = progression.character_max_stars(int(warrior.get("base_stars", 4)))
    exp = int(user["exp"])

    embed = discord.Embed(
        title=f"{warrior['name']} — Upgrade",
        color=discord.Color(hero_text.element_color(warrior["element"])),
    )

    # ---- levels ----
    level_cap = progression.effective_level_cap(stars, level)
    if level >= level_cap:
        level_text = f"**LEVEL CAP** (Lv {level_cap})  {hero_text.progress_bar(level, level_cap)}"
        if stars < max_stars:
            level_text += f"\nStar up to raise the cap to **Lv {progression.level_cap_for_stars(stars + 1)}**."
    else:
        cost = progression.exp_required_for_next_level(level)
        affordable = progression.max_levels_affordable(level, exp, level_cap)
        level_text = (
            f"**Lv {level}/{level_cap}**  {hero_text.progress_bar(level, level_cap)}\n"
            f"Next level costs **{cost:,} EXP** — you have **{exp:,}**"
        )
        level_text += f"\nYou can afford **{affordable}** level{'s' if affordable != 1 else ''} right now." if affordable else "\nNot enough EXP for the next level yet."
    embed.add_field(name="⬆️ Level", value=level_text, inline=False)

    # ---- stars ----
    if stars >= max_stars:
        star_text = f"**MAX STARS** ({stars}★)"
    else:
        target = stars + 1
        lines = [f"**{stars}★ → {target}★**", f"Needs: {progression.tier_requirements_text(stars, target, int(warrior.get('base_stars', 4)))}"]
        bonus = progression.breakthrough_bonus(target)
        if bonus:
            lines.append(f"🌟 **Breakthrough!** Reaching {target}★ gives an extra **+{round((bonus - 1) * 100)}%** to all stats.")
        else:
            upcoming = progression.next_breakthrough(stars)
            if upcoming and upcoming <= max_stars:
                lines.append(f"🌟 Next breakthrough: {upcoming}★ (+{round((progression.breakthrough_bonus(upcoming) - 1) * 100)}% all stats)")
        needed_level = progression.tier_level_requirement(stars)
        level_ok = level >= needed_level
        lines.append(f"{'✅' if level_ok else '❌'} Reach Level {needed_level} (now {level})")
        if star_status.get("ok"):
            lines.append("✅ Materials ready")
        elif star_status.get("reason") == "missing_requirements":
            lines.append("❌ Still missing:")
            lines.extend(f"   • {item}" for item in star_status.get("missing", []))
        elif star_status.get("reason") == "requirements_not_authored":
            lines.append("⏳ Requirements for this star aren't set up yet.")
        if star_status.get("ok") and level_ok:
            lines.append("\n**Ready — tap Star Up!**")
        lines.append(f"💎 Memory Stones: {int(user['memory_stones']):,}")
        star_text = "\n".join(lines)
    embed.add_field(name="⭐ Stars", value=star_text[:1024], inline=False)
    embed.set_footer(text=f"Hero {index + 1} of {total}")
    return embed


class HeroJumpSelect(discord.ui.Select):
    """Dropdown to jump straight to a hero (shows the 25 around the current one)."""

    def __init__(self, view: "HeroInventoryView"):
        total = len(view.warriors)
        start = (view.index // 25) * 25
        end = min(start + 25, total)
        options = []
        for i in range(start, end):
            w = view.warriors[i]
            options.append(discord.SelectOption(
                label=f"{w['name']} · Lv{w['level']} · {w['stars']}★"[:100],
                value=str(i),
                description=str(w["element"]),
                emoji=hero_text.element_icon(w["element"]),
                default=(i == view.index),
            ))
        super().__init__(
            placeholder=f"Jump to a hero ({start + 1}-{end} of {total})",
            options=options, min_values=1, max_values=1, row=1,
        )
        self.inventory_view = view

    async def callback(self, interaction: discord.Interaction):
        view = self.inventory_view
        if not await view._guard(interaction):
            return
        view.index = int(self.values[0])
        await interaction.response.defer()
        await view._edit(interaction)


class _HeroButton(discord.ui.Button):
    def __init__(self, view: "HeroInventoryView", action: str, *, label=None, emoji=None,
                 style=discord.ButtonStyle.secondary, row=0, disabled=False):
        super().__init__(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        self.inventory_view = view
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.inventory_view.handle(interaction, self.action)


class HeroInventoryView(discord.ui.View):
    def __init__(self, user_id: int, warriors: list, index: int = 0, tab: str = "overview"):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.warriors = warriors
        self.index = index
        self.tab = tab
        self.message = None
        self.star_status: dict = {}
        self._shown_key = None  # which card image is currently attached to the message
        self._bg = None         # background prefetch task

    # ---- helpers ----
    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your hero inventory.", ephemeral=True)
            return False
        return True

    def _reload(self, keep_id: int):
        fresh = sort_warriors(database.get_user_warriors(self.user_id))
        self.warriors = fresh
        for i, w in enumerate(fresh):
            if w["id"] == keep_id:
                self.index = i
                return
        self.index = min(self.index, max(len(fresh) - 1, 0))

    def _load_star_status(self, warrior: dict) -> dict:
        stars = int(warrior["stars"])
        max_stars = progression.character_max_stars(int(warrior.get("base_stars", 4)))
        if stars >= max_stars:
            return {"ok": False, "reason": "max_tier"}
        return database.tier_up_warrior(
            self.user_id, warrior["id"], max_tier=max_stars, required_level=0,
            memory_cost=progression.memory_stone_cost(warrior.get("base_stars", 4)), dry_run=True,
        ) or {"ok": False, "reason": "gone"}

    def _rebuild(self, user: dict, warrior: dict):
        self.clear_items()
        tab_style = lambda name: discord.ButtonStyle.primary if self.tab == name else discord.ButtonStyle.secondary
        self.add_item(_HeroButton(self, "prev", label="◀", row=0))
        self.add_item(_HeroButton(self, "overview", label="Overview", emoji="📋", style=tab_style("overview"), row=0))
        self.add_item(_HeroButton(self, "skills", label="Skills", emoji="⚔️", style=tab_style("skills"), row=0))
        self.add_item(_HeroButton(self, "upgrade", label="Upgrade", emoji="⬆️", style=tab_style("upgrade"), row=0))
        self.add_item(_HeroButton(self, "next", label="▶", row=0))
        self.add_item(HeroJumpSelect(self))

        if self.tab == "upgrade":
            level = int(warrior["level"])
            maxed = level >= progression.effective_level_cap(int(warrior["stars"]), level)
            cost = progression.exp_required_for_next_level(level)
            can_level = (not maxed) and int(user["exp"]) >= cost
            self.add_item(_HeroButton(
                self, "level", label="LEVEL CAP" if maxed else f"Level Up ({cost:,} EXP)",
                style=discord.ButtonStyle.success if can_level else discord.ButtonStyle.secondary,
                row=2, disabled=not can_level,
            ))
            self.add_item(_HeroButton(self, "level_multi", label="Level Up ×N", row=2, disabled=not can_level))
            self.add_item(_HeroButton(
                self, "level_max", label="Level to Cap", emoji="⏫",
                style=discord.ButtonStyle.success if can_level else discord.ButtonStyle.secondary,
                row=2, disabled=not can_level,
            ))
            stars = int(warrior["stars"])
            max_stars = progression.character_max_stars(int(warrior.get("base_stars", 4)))
            ready = stars < max_stars and level >= progression.tier_level_requirement(stars) and bool(self.star_status.get("ok"))
            self.add_item(_HeroButton(
                self, "star", label="MAX STARS" if stars >= max_stars else "Star Up", emoji="⭐",
                style=discord.ButtonStyle.primary if ready else discord.ButtonStyle.secondary,
                row=2, disabled=not ready,
            ))

    def _gather(self, warrior: dict) -> dict:
        """All the blocking DB work for one render; runs in a worker thread so the bot stays responsive."""
        data = {"user": database.get_or_create_user(self.user_id, "")}
        if self.tab == "upgrade":
            data["star_status"] = self._load_star_status(warrior)
        elif self.tab == "overview":
            party = database.get_party(self.user_id)
            data["slot"] = next((pos for pos, w in party.items() if w.get("user_warrior_id") == warrior["id"]), None)
        return data

    async def _build_message(self):
        warrior = self.warriors[self.index]
        total = len(self.warriors)
        data = await asyncio.to_thread(self._gather, warrior)
        user = data["user"]

        if self.tab == "skills":
            embed = build_hero_skills_embed(warrior, self.index, total)
        elif self.tab == "upgrade":
            self.star_status = data["star_status"]
            embed = build_hero_upgrade_embed(warrior, user, self.star_status, self.index, total)
        else:
            same = [w for w in self.warriors if w["warrior_id"] == warrior["warrior_id"]]
            copy_no = next((n for n, w in enumerate(same, 1) if w["id"] == warrior["id"]), 1)
            embed = build_hero_overview_embed(warrior, self.index, total, data["slot"], copy_no, len(same))

        # Only touch the attachment when the picture actually has to change.
        # The Upgrade tab shows no card, so level/star spam never builds or uploads an image.
        changes = {}
        if self.tab == "upgrade":
            if self._shown_key is not None:
                changes["attachments"] = []
                self._shown_key = None
        else:
            key = _card_key(warrior)
            if key != self._shown_key:
                changes["attachments"] = [await get_card_file(warrior)]
                self._shown_key = key
            if self.tab == "overview":
                embed.set_image(url="attachment://character_card.png")
            else:
                embed.set_thumbnail(url="attachment://character_card.png")
        self._rebuild(user, warrior)
        return embed, changes

    async def _prefetch(self):
        """Warm the card cache for the neighbouring heroes so the arrows feel instant."""
        n = len(self.warriors)
        for step in (1, -1):
            try:
                await get_card_bytes(self.warriors[(self.index + step) % n])
            except Exception:
                pass

    async def send_initial(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._edit(interaction, initial=True)

    async def _edit(self, interaction: discord.Interaction, initial: bool = False):
        embed, changes = await self._build_message()
        if initial:
            self.message = await interaction.followup.send(embed=embed, files=changes.get("attachments", []), view=self)
        else:
            await interaction.edit_original_response(embed=embed, view=self, **changes)
        if self.tab != "upgrade":
            self._bg = asyncio.create_task(self._prefetch())

    async def _edit_message_after_modal(self, interaction: discord.Interaction):
        """Refresh the hero message after the Level Up ×N pop-up (which already answered its own interaction)."""
        embed, changes = await self._build_message()
        target = self.message or interaction.message
        if target is not None:
            try:
                await target.edit(embed=embed, view=self, **changes)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # ---- button handling ----
    async def handle(self, interaction: discord.Interaction, action: str):
        if not await self._guard(interaction):
            return

        # A modal can only be shown as the FIRST response to an interaction,
        # so this one path has to stay response-first rather than deferring.
        if action == "level_multi":
            warrior = database.get_user_warrior(self.user_id, self.warriors[self.index]["id"])
            if not warrior:
                await interaction.response.send_message("That warrior is no longer in your collection.", ephemeral=True)
                return
            if int(warrior["level"]) >= progression.effective_level_cap(int(warrior["stars"]), int(warrior["level"])):
                await interaction.response.send_message("This warrior is at its level cap. Star it up to raise the cap.", ephemeral=True)
                return
            await interaction.response.send_modal(MultiLevelUpModal(self))
            return

        # Ack immediately for everything else, before any DB work, so Discord
        # shows a loading state right away instead of the click looking frozen
        # while a level-up or star-up transaction runs.
        await interaction.response.defer()

        if action in ("prev", "next"):
            step = -1 if action == "prev" else 1
            self.index = (self.index + step) % len(self.warriors)
        elif action in ("overview", "skills", "upgrade"):
            self.tab = action
        elif action == "level":
            if not await self._do_level_up(interaction):
                return
        elif action == "level_max":
            if not await self._do_level_max(interaction):
                return
        elif action == "star":
            if not await self._do_star_up(interaction):
                return

        await self._edit(interaction)
        note = getattr(self, "_note", None)
        if note:
            self._note = None
            await interaction.followup.send(note, ephemeral=True)

    async def _do_level_up(self, interaction: discord.Interaction) -> bool:
        warrior = await asyncio.to_thread(database.get_user_warrior, self.user_id, self.warriors[self.index]["id"])
        if not warrior:
            await interaction.followup.send("That warrior is no longer in your collection.", ephemeral=True)
            return False
        current_level = int(warrior["level"])
        level_cap = progression.effective_level_cap(int(warrior["stars"]), current_level)
        if current_level >= level_cap:
            await interaction.followup.send(
                f"**{warrior['name']}** is at its level cap (Lv. {level_cap}). Star it up to raise the cap.", ephemeral=True
            )
            return False
        cost = progression.exp_required_for_next_level(current_level)
        result = await asyncio.to_thread(
            database.level_up_warrior, self.user_id, warrior["id"], 1, cost, level_cap
        )
        if not result or not result.get("ok"):
            if result and result.get("reason") == "not_enough_exp":
                await interaction.followup.send(
                    f"You need **{cost:,} EXP** to level up **{warrior['name']}**, but you only have **{int(result.get('exp', 0)):,} EXP**.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send("The level-up could not be completed. Please try again.", ephemeral=True)
            return False
        self.warriors[self.index]["level"] = result["level"]  # no full re-query and re-sort
        return True

    async def _do_level_max(self, interaction: discord.Interaction) -> bool:
        """Spend EXP on as many levels as affordable, up to the hero's cap."""
        warrior = await asyncio.to_thread(database.get_user_warrior, self.user_id, self.warriors[self.index]["id"])
        if not warrior:
            await interaction.followup.send("That warrior is no longer in your collection.", ephemeral=True)
            return False
        lvl = int(warrior["level"])
        cap = progression.effective_level_cap(int(warrior["stars"]), lvl)
        user = await asyncio.to_thread(database.get_or_create_user, self.user_id, "")
        n = progression.max_levels_affordable(lvl, int(user["exp"]), cap)
        if n <= 0:
            await interaction.followup.send("Not enough EXP for another level (or already at the cap).", ephemeral=True)
            return False
        cost = progression.level_up_cost_for_levels(lvl, n)
        result = await asyncio.to_thread(database.level_up_warrior, self.user_id, warrior["id"], n, cost, cap)
        if not result or not result.get("ok"):
            await interaction.followup.send("The level-up could not be completed. Please try again.", ephemeral=True)
            return False
        self.warriors[self.index]["level"] = result["level"]
        self._note = f"⏫ **{warrior['name']}** Lv {result['old_level']} → {result['level']} for {result['exp_spent']:,} EXP."
        return True

    async def _do_star_up(self, interaction: discord.Interaction) -> bool:
        warrior = await asyncio.to_thread(database.get_user_warrior, self.user_id, self.warriors[self.index]["id"])
        if not warrior:
            await interaction.followup.send("That character is no longer in your collection.", ephemeral=True)
            return False
        current = int(warrior["stars"])
        max_stars = progression.character_max_stars(int(warrior.get("base_stars", 4)))
        if current >= max_stars:
            await interaction.followup.send("This character is already at maximum stars.", ephemeral=True)
            return False
        needed_level = progression.tier_level_requirement(current)
        if int(warrior["level"]) < needed_level:
            await interaction.followup.send(
                f"**{warrior['name']}** must reach Level {needed_level} first (now {int(warrior['level'])}).", ephemeral=True
            )
            return False
        result = await asyncio.to_thread(
            database.tier_up_warrior, self.user_id, warrior["id"], max_stars, needed_level,
            progression.memory_stone_cost(warrior.get("base_stars", 4)),
        )
        if not result or not result.get("ok"):
            if result and result.get("reason") == "missing_requirements":
                missing = "\n".join(f"• {item}" for item in result.get("missing", []))
                await interaction.followup.send(f"Cannot star up **{warrior['name']}** yet.\n{missing}", ephemeral=True)
            else:
                await interaction.followup.send(
                    f"The star-up could not be completed: {result.get('reason', 'unknown') if result else 'unknown'}.", ephemeral=True
                )
            return False
        await asyncio.to_thread(self._reload, warrior["id"])
        bonus = progression.breakthrough_bonus(result["tier"])
        self._note = f"🌟 **Breakthrough!** {warrior['name']} reached {result['tier']}★: +{round((bonus - 1) * 100)}% to all stats." if bonus else None
        return True


@bot.tree.command(name="book", description="Browse every hero in the hero book")
async def book(interaction: discord.Interaction):
    await interaction.response.defer()
    track_progress(interaction.user.id, "view_book")
    catalog = database.get_all_catalog_warriors()
    if not catalog:
        await interaction.followup.send("No heroes exist yet.", ephemeral=True)
        return

    catalog.sort(key=lambda w: (w["element"], int(w.get("stars", 4)), w["name"]))

    view = HeroDexView(interaction.user.id, catalog, index=0)
    warrior = catalog[0]
    embed = build_hero_embed(warrior, 0, len(catalog))
    image_path = warrior_art.get_warrior_fullart_path(warrior["warrior_id"])
    optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, image_path)
    file = discord.File(optimized, filename="fullart.png")
    embed.set_image(url="attachment://fullart.png")
    await interaction.followup.send(embed=embed, file=file, view=view)


# ---------------------------------------------------------------------------
# /level - play through story chapters (authored via story_chapters.csv).
#
# Flow: /level shows the current chapter + a Start button -> Start shows the
# full story text + a Begin Fight button -> Begin Fight shows your party
# (editable right there) + a Fight! button -> Fight! runs the real battle
# engine (battle.py).
#
# If you LOSE, running /level again jumps straight back to the party panel
# (skipping the intro/story text) via users.story_stage - so you're not stuck
# re-reading the story every time you want to retry. Winning resets
# story_stage back to 'intro' for the new chapter.
# ---------------------------------------------------------------------------

def build_intro_embed(chapter: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"Current Story: Chapter {chapter['chapter_number']} — {chapter['title']}",
        description=chapter["description"] or "\u200b",
        color=discord.Color.dark_purple(),
    )
    return embed


def split_story_pages(story_text: str) -> list:
    """
    Splits a chapter's story_text into pages. Put [PAGE] on its own line in
    story_chapters.csv wherever you want a page break - write as much as you
    want per page, like writing chapters/scenes in a book. No [PAGE] markers
    at all just means the whole thing is one page.
    """
    if not story_text or not story_text.strip():
        return []
    raw_pages = story_text.split("[PAGE]")
    pages = [p.strip() for p in raw_pages if p.strip()]
    return pages if pages else []


def build_story_page_embed(chapter: dict, pages: list, page_index: int) -> discord.Embed:
    text = pages[page_index] if pages else "*(no story text written for this chapter yet)*"
    if len(text) > 4000:  # stay under Discord's 4096-char embed description limit
        text = text[:4000] + "..."
    embed = discord.Embed(
        title=f"Chapter {chapter['chapter_number']}: {chapter['title']}",
        description=text,
        color=discord.Color.dark_purple(),
    )
    if len(pages) > 1:
        embed.set_footer(text=f"Page {page_index + 1} of {len(pages)}")
    return embed


def build_party_panel_embed(user_id: int, chapter: dict) -> discord.Embed:
    party = database.get_party(user_id)
    enemy_team = database.get_chapter_enemy_team(chapter)
    if enemy_team:
        enemy_text = "\n".join(
            f"**Slot {i}:** {w['name']} (Lv. {w.get('level', 1)}, {w.get('stars', 4)}★) — {w['element']}"
            for i, w in enumerate(enemy_team, 1)
        )
        title = f"Prepare for Battle — {chapter['title']}"
        description = "Enemy team set by the stage designer. Build your party to counter them."
    else:
        enemy_text = f"**{chapter['boss_name']}** — {chapter['boss_element']}"
        title = f"Prepare for Battle — {chapter['boss_name']}"
        description = f"({chapter['boss_element']}) — review or edit your party below, then Fight when ready."

    embed = discord.Embed(title=title, description=description, color=discord.Color.orange())
    for position in range(1, 7):
        if position in party:
            w = party[position]
            level = w.get('level', 1)
            value = f"{w['name']} (Lv. {level}) ({w['element']}) — {w['stars']}★"
        else:
            value = "*Empty*"
        embed.add_field(name=f"Your Slot {position}", value=value, inline=True)
    embed.add_field(name="Enemy Team", value=enemy_text, inline=False)
    return embed


async def build_party_scene_attachment(party: dict, embed: discord.Embed, filename: str = "party.png"):
    """Attach the same composed party scene used by /party to another embed.

    Returns a discord.File when the party background exists, otherwise None.
    Image composition runs in a worker thread so Discord interactions do not time out.
    """
    scene_buffer = await asyncio.to_thread(warrior_art.build_party_scene, party)
    if not scene_buffer:
        return None

    embed.set_image(url=f"attachment://{filename}")
    return discord.File(scene_buffer, filename=filename)


def build_battle_result_embed(result: dict, chapter: dict) -> discord.Embed:
    # Discord limits: 4096 chars in the description AND 6000 across the whole
    # embed (title + description + every field + footer). The description is
    # filled in LAST, from whatever room the other parts leave, so a long fight
    # can never make the message fail to send.
    embed = discord.Embed(
        title=f"vs. {chapter['boss_name']}"[:256],
        color=discord.Color.green() if result["won"] else discord.Color.red(),
    )

    party_lines = []
    for w in result["party_status"]:
        status = f"{w['hp']}/{w['max_hp']} HP" if w["alive"] else "Defeated"
        level = w.get("level", 1)
        extra = ""
        if "resolve" in w:
            extra += f" | Resolve {w.get('resolve', 0)}/100"
        if w.get("lantern"):
            extra += " | Lantern"
        party_lines.append(f"{w['name']} (Lv. {level}): {status}{extra}")
    embed.add_field(name="Your Party", value="\n".join(party_lines)[:1024], inline=False)
    enemy_lines = []
    for w in result.get("enemy_status", []):
        status = f"{w['hp']}/{w['max_hp']} HP" if w["alive"] else "Defeated"
        enemy_lines.append(f"{w['name']} (Lv. {w.get('level', 1)}): {status}")
    embed.add_field(name="Enemy Team", value=("\n".join(enemy_lines) or "None")[:1024], inline=False)
    embed.set_footer(text=f"Resolved in {result['rounds']} round(s)")

    if result["won"]:
        embed.add_field(name="Rewards", value=(
            f"+{chapter.get('reward_solite', 0)} Solite\n"
            f"+{chapter.get('reward_charms', 0)} Summon Charm(s)\n"
            f"+{chapter.get('reward_exp', 0)} EXP"
        ), inline=False)
        embed.add_field(name="Result", value=f"**Victory!** You defeated {chapter['boss_name']}.", inline=False)
    else:
        embed.add_field(name="Result", value=f"**Defeat.** {chapter['boss_name']} was too strong. Try again — you can adjust your party first.", inline=False)

    # Leave ~500 chars spare: the Fight button appends a "Next" field after this returns.
    room = min(4096, 6000 - len(embed) - 500)
    embed.description = battle.fit_log_to_limit(result["log"], room)
    return embed


class EditSlotSelect(discord.ui.Select):
    """Step 1 of editing: pick which of the 6 slots to change."""

    def __init__(self, user_id: int, chapter_number: int):
        self.user_id = user_id
        self.chapter_number = chapter_number
        party = database.get_party(user_id)
        options = []
        for position in range(1, 7):
            current = party.get(position)
            label = f"Slot {position}: {current['name']}" if current else f"Slot {position}: Empty"
            options.append(discord.SelectOption(label=label, value=str(position)))
        super().__init__(placeholder="Choose a slot to edit...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your party panel.", ephemeral=True)
            return
        position = int(self.values[0])
        owned = database.get_user_warriors(interaction.user.id)
        if not owned:
            await interaction.response.send_message("You don't own any warriors yet — try `/pull`!", ephemeral=True)
            return
        view = EditWarriorSelectView(self.user_id, self.chapter_number, position, owned)
        await interaction.response.edit_message(content=f"Choose a warrior for Slot {position}:", embed=None, attachments=[], view=view)


class EditSlotSelectView(discord.ui.View):
    def __init__(self, user_id: int, chapter_number: int):
        super().__init__(timeout=300)
        self.add_item(EditSlotSelect(user_id, chapter_number))


class EditWarriorSelect(discord.ui.Select):
    """Step 2 of editing: pick which owned warrior goes into the chosen slot."""

    def __init__(self, user_id: int, chapter_number: int, position: int, owned: list):
        self.user_id = user_id
        self.chapter_number = chapter_number
        self.position = position
        # Discord selects cap at 25 options
        options = [
            discord.SelectOption(label=f"{w['name']} ({w['stars']}★)", value=str(w["id"]))
            for w in owned[:25]
        ]
        super().__init__(placeholder="Choose a warrior...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your party panel.", ephemeral=True)
            return
        user_warrior_id = int(self.values[0])
        database.set_party_slot(self.user_id, self.position, user_warrior_id)

        chapter = database.get_story_chapter(self.chapter_number)
        embed = build_party_panel_embed(self.user_id, chapter)
        view = PartyPanelView(self.user_id, self.chapter_number)
        await interaction.response.defer()
        party = database.get_party(self.user_id)
        scene_file = await build_party_scene_attachment(party, embed, filename="battle_party.png")
        if scene_file:
            await interaction.message.edit(content=None, embed=embed, attachments=[scene_file], view=view)
        else:
            await interaction.message.edit(content=None, embed=embed, attachments=[], view=view)


class EditWarriorSelectView(discord.ui.View):
    def __init__(self, user_id: int, chapter_number: int, position: int, owned: list):
        super().__init__(timeout=300)
        self.add_item(EditWarriorSelect(user_id, chapter_number, position, owned))


class LogPagerView(discord.ui.View):
    """Browse a full battle log page by page - nothing is cut, unlike the
    embed's own log field which only shows the newest part of a long fight."""

    def __init__(self, owner_id: int, pages: list[str], title: str = "Battle Log", start: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.pages = pages or [""]
        self.title = title
        self.page = max(0, min(start, len(self.pages) - 1))
        self.message = None
        self.refresh_components()

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.title, description=self.pages[self.page] or "*(empty)*", color=discord.Color.dark_grey())
        embed.set_footer(text=f"Page {self.page + 1} of {len(self.pages)}")
        return embed

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your log.", ephemeral=True)
            return False
        return True

    def refresh_components(self):
        self.clear_items()
        previous_button = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=self.page <= 0)
        next_button = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=self.page >= len(self.pages) - 1)

        async def previous_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page -= 1
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        async def next_callback(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page += 1
            self.refresh_components()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        previous_button.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(previous_button)
        self.add_item(next_button)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class BattleResultView(discord.ui.View):
    """Sits under a finished battle's result message. Currently just offers the
    full, unabridged log - the embed itself only ever shows the newest part."""

    def __init__(self, owner_id: int, log: list, title: str = "Battle Log"):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.pages = battle.paginate_log(log, 3900)
        self.title = title

    @discord.ui.button(label="📜 Full Log", style=discord.ButtonStyle.secondary)
    async def full_log_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This isn't your battle log.", ephemeral=True)
            return
        pager = LogPagerView(self.owner_id, self.pages, self.title)
        await interaction.response.send_message(embed=pager.build_embed(), view=pager, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# Users whose battle is being resolved right now - stops a double-click from
# running (and paying out) the same fight twice.
_fights_in_progress: set[int] = set()


class PartyPanelView(discord.ui.View):
    def __init__(self, user_id: int, chapter_number: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.chapter_number = chapter_number

    @discord.ui.button(label="Fight!", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def fight(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your party panel.", ephemeral=True)
            return

        await interaction.response.defer()

        if interaction.user.id in _fights_in_progress:
            await interaction.followup.send("Your battle is already being resolved - hang on a second!", ephemeral=True)
            return

        # A panel left over from a chapter you've already beaten must not pay out again.
        current_user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
        if current_user["story_chapter"] > self.chapter_number:
            await interaction.followup.send(
                "You've already cleared this chapter. Run `/level` to continue your story.", ephemeral=True
            )
            try:
                await interaction.edit_original_response(view=None)
            except discord.HTTPException:
                pass
            return

        _fights_in_progress.add(interaction.user.id)
        try:
            chapter = database.get_story_chapter(self.chapter_number)
            party = database.get_party(interaction.user.id)

            if not party:
                await interaction.followup.send(
                    "You don't have any warriors in your party! Edit your party first.",
                    ephemeral=True,
                )
                return

            enemy_team = database.get_chapter_enemy_team(chapter)
            result = await asyncio.to_thread(battle.resolve_battle, party, chapter, enemy_team)
            track_progress(interaction.user.id, "story_fight")
            embed = build_battle_result_embed(result, chapter)

            if result["won"]:
                user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
                database.update_user_field(interaction.user.id, "solite", user["solite"] + chapter["reward_solite"])
                database.update_user_field(interaction.user.id, "summon_charms", user["summon_charms"] + chapter["reward_charms"])
                database.add_user_exp(interaction.user.id, chapter.get("reward_exp", 0))

                next_chapter_number = chapter["chapter_number"] + 1
                if user["story_chapter"] <= chapter["chapter_number"]:
                    database.update_user_field(interaction.user.id, "story_chapter", next_chapter_number)
                database.update_user_field(interaction.user.id, "story_stage", "intro")
                try:
                    await interaction.edit_original_response(view=None)  # retire this panel's buttons
                except discord.HTTPException:
                    pass

                next_chapter = database.get_story_chapter(next_chapter_number)
                if next_chapter:
                    embed.add_field(name="Next", value=f"Chapter {next_chapter_number}: {next_chapter['title']} is now unlocked! Run `/level` to continue.", inline=False)
                else:
                    embed.add_field(name="Next", value="No further chapters yet — check back later!", inline=False)
            else:
                # Stay at the fight/party stage so /level jumps straight back here next time
                database.update_user_field(interaction.user.id, "story_stage", "fight")

            party = database.get_party(interaction.user.id)
            scene_file = await build_party_scene_attachment(party, embed, filename="battle_party.png")
            log_view = BattleResultView(interaction.user.id, result["log"], title=f"vs. {chapter['boss_name']}")
            if scene_file:
                await interaction.followup.send(embed=embed, file=scene_file, view=log_view)
            else:
                await interaction.followup.send(embed=embed, view=log_view)
        finally:
            _fights_in_progress.discard(interaction.user.id)

    @discord.ui.button(label="Edit Party", style=discord.ButtonStyle.secondary, emoji="🛠️")
    async def edit_party(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your party panel.", ephemeral=True)
            return
        view = EditSlotSelectView(self.user_id, self.chapter_number)
        await interaction.response.edit_message(content="Choose a slot to edit:", embed=None, attachments=[], view=view)


async def send_party_panel(interaction: discord.Interaction, chapter: dict, edit: bool = False):
    database.update_user_field(interaction.user.id, "story_stage", "fight")
    embed = build_party_panel_embed(interaction.user.id, chapter)
    view = PartyPanelView(interaction.user.id, chapter["chapter_number"])
    party = database.get_party(interaction.user.id)

    # The battle preparation screen now gets the same composed party scene as /party.
    # Defer first because image compositing can take longer than Discord's interaction window.
    await interaction.response.defer()
    scene_file = await build_party_scene_attachment(party, embed, filename="battle_party.png")

    if edit:
        if scene_file:
            await interaction.message.edit(content=None, embed=embed, attachments=[scene_file], view=view)
        else:
            await interaction.message.edit(content=None, embed=embed, attachments=[], view=view)
    else:
        if scene_file:
            await interaction.followup.send(embed=embed, file=scene_file, view=view)
        else:
            await interaction.followup.send(embed=embed, view=view)


class StoryPageView(discord.ui.View):
    """Pages through a chapter's story text (split by [PAGE] markers).
    Shows Next on every page except the last, where Begin Fight appears instead."""

    def __init__(self, user_id: int, chapter_number: int, pages: list, page_index: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.chapter_number = chapter_number
        self.pages = pages
        self.page_index = page_index
        self._rebuild_buttons()

    def _rebuild_buttons(self):
        self.clear_items()
        is_last_page = self.page_index >= len(self.pages) - 1

        if self.page_index > 0:
            self.add_item(self._make_button("◀ Previous", discord.ButtonStyle.secondary, self._previous))

        if is_last_page:
            self.add_item(self._make_button("Begin Fight", discord.ButtonStyle.danger, self._begin_fight, emoji="⚔️"))
        else:
            self.add_item(self._make_button("Next ▶", discord.ButtonStyle.primary, self._next))

    def _make_button(self, label, style, callback, emoji=None):
        button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback
        return button

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your story menu.", ephemeral=True)
            return False
        return True

    async def _previous(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page_index -= 1
        self._rebuild_buttons()
        chapter = database.get_story_chapter(self.chapter_number)
        embed = build_story_page_embed(chapter, self.pages, self.page_index)
        await interaction.response.edit_message(embed=embed, attachments=[], view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.page_index += 1
        self._rebuild_buttons()
        chapter = database.get_story_chapter(self.chapter_number)
        embed = build_story_page_embed(chapter, self.pages, self.page_index)
        await interaction.response.edit_message(embed=embed, attachments=[], view=self)

    async def _begin_fight(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        chapter = database.get_story_chapter(self.chapter_number)
        await send_party_panel(interaction, chapter, edit=True)


class IntroView(discord.ui.View):
    def __init__(self, user_id: int, chapter_number: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.chapter_number = chapter_number

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary, emoji="📖")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your story menu.", ephemeral=True)
            return
        chapter = database.get_story_chapter(self.chapter_number)
        pages = split_story_pages(chapter["story_text"])

        if not pages:
            # No story text written - skip straight to the fight panel
            await send_party_panel(interaction, chapter, edit=True)
            return

        embed = build_story_page_embed(chapter, pages, 0)
        view = StoryPageView(self.user_id, self.chapter_number, pages, page_index=0)
        await interaction.response.edit_message(embed=embed, attachments=[], view=view)


@bot.tree.command(name="level", description="View and play your current story chapter")
async def level(interaction: discord.Interaction):
    user = database.get_or_create_user(interaction.user.id, str(interaction.user.display_name))
    chapter_number = user["story_chapter"]
    chapter = database.get_story_chapter(chapter_number)

    if not chapter:
        await interaction.response.send_message(
            "No story chapters exist yet — the developer needs to add some to `story_chapters.csv`.",
            ephemeral=True,
        )
        return

    # If they already read the story and are mid-fight (e.g. lost last time),
    # skip straight back to the party panel instead of the intro screen.
    if user["story_stage"] == "fight":
        embed = build_party_panel_embed(interaction.user.id, chapter)
        view = PartyPanelView(interaction.user.id, chapter_number)
        await interaction.response.send_message(embed=embed, view=view)
        return

    embed = build_intro_embed(chapter)
    image_path = warrior_art.get_story_chapter_image_path(chapter_number)
    view = IntroView(interaction.user.id, chapter_number)

    if image_path:
        optimized = await asyncio.to_thread(warrior_art.load_optimized_bytes, image_path)
        file = discord.File(optimized, filename="chapter.png")
        embed.set_image(url="attachment://chapter.png")
        await interaction.response.send_message(embed=embed, file=file, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)


if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: No token found. Make sure your .env file has a line like:")
        print("DISCORD_TOKEN=your_token_here")
    else:
        bot.run(TOKEN)
