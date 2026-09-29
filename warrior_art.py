import os
from io import BytesIO
from PIL import Image, ImageDraw

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "warriors")
PLACEHOLDER_PATH = os.path.join(ASSETS_DIR, "placeholder.png")

# ---------------------------------------------------------------------------
# IMAGE OPTIMIZATION / CACHING
#
# Discord embeds only ever display images at modest size - there's no visual
# benefit to sending a huge multi-megabyte file, only slower uploads (this is
# almost always why an image "takes forever to load": the bot is uploading
# the full original file every single time).
#
# load_optimized_bytes() resizes any image down to MAX_DIMENSION on its
# longest side (only if it's actually bigger than that) and keeps the result
# cached in memory, keyed by file path + last-modified time. So: the FIRST
# time a given image is used it gets resized once, and every use after that
# (even across different commands) is instant - no re-reading or re-resizing.
# If you replace a file with new art, its changed modified-time automatically
# invalidates the old cached version.
# ---------------------------------------------------------------------------

MAX_DIMENSION = 1024  # max width/height in pixels - plenty for Discord embeds
_IMAGE_CACHE = {}  # path -> (mtime, optimized_bytes)


def load_optimized_bytes(path: str, max_dimension: int = MAX_DIMENSION) -> BytesIO:
    """Returns a BytesIO of the image at `path`, downsized if needed and cached
    for instant reuse. Use this instead of passing raw file paths to discord.File
    for anything that gets uploaded to Discord."""
    mtime = os.path.getmtime(path)
    cached = _IMAGE_CACHE.get(path)
    if cached and cached[0] == mtime:
        return BytesIO(cached[1])

    img = Image.open(path)
    img_format = "PNG" if img.mode in ("RGBA", "P") else "JPEG"

    if max(img.size) > max_dimension:
        img = img.copy()
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    buffer = BytesIO()
    if img_format == "JPEG" and img.mode != "RGB":
        img = img.convert("RGB")
    img.save(buffer, format=img_format, optimize=True)
    data = buffer.getvalue()

    _IMAGE_CACHE[path] = (mtime, data)
    return BytesIO(data)


# Separate cache for DECODED images used in compositing (party scenes, collages).
# load_optimized_bytes() above caches final encoded bytes for direct upload;
# this one caches the decoded PIL Image itself so repeated compositing (which
# needs to paste/resize the actual pixels, not just re-upload a file) doesn't
# re-read and re-decode a potentially large source file from disk every time.
_DECODED_IMAGE_CACHE = {}  # path -> (mtime, PIL.Image)


def _get_cached_decoded_image(path: str, max_dimension: int = 1600) -> Image.Image:
    """Returns a decoded, RGBA PIL Image for `path`, downsized to max_dimension
    if larger, cached by (path, mtime). Always returns a COPY so callers can
    freely modify/paste onto it without corrupting the cached original."""
    mtime = os.path.getmtime(path)
    cached = _DECODED_IMAGE_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1].copy()

    img = Image.open(path).convert("RGBA")
    if max(img.size) > max_dimension:
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

    _DECODED_IMAGE_CACHE[path] = (mtime, img)
    return img.copy()

BANNER_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "banners")
BANNER_PLACEHOLDER_PATH = os.path.join(BANNER_ASSETS_DIR, "placeholder.png")

PARTY_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "party")
PARTY_BACKGROUND_PATH = os.path.join(PARTY_ASSETS_DIR, "background.png")

AVATAR_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "avatars")
ICON_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "icons")

# Full-art images (for the /heroes gallery command) are a SEPARATE set of
# files from battle sprites (assets/warriors/) - same warrior_id naming
# convention, but its own folder, so you can use a big detailed piece of art
# here while keeping a simpler/smaller sprite for battle use.
FULLART_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fullart")
FULLART_PLACEHOLDER_PATH = os.path.join(FULLART_ASSETS_DIR, "placeholder.png")

STORY_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets", "story")
STORY_PLACEHOLDER_PATH = os.path.join(STORY_ASSETS_DIR, "placeholder.png")


def get_story_chapter_image_path(chapter_number: int):
    """Returns the path to a story chapter's art, or the placeholder if none exists."""
    path = os.path.join(STORY_ASSETS_DIR, f"{chapter_number}.png")
    if os.path.exists(path):
        return path
    if os.path.exists(STORY_PLACEHOLDER_PATH):
        return STORY_PLACEHOLDER_PATH
    return None


def get_warrior_fullart_path(warrior_id: int) -> str:
    """Returns the path to a warrior's FULL ART (for /heroes), separate from
    its battle sprite. Falls back to the fullart placeholder if none exists."""
    path = os.path.join(FULLART_ASSETS_DIR, f"{warrior_id}.png")
    if os.path.exists(path):
        return path
    return FULLART_PLACEHOLDER_PATH


def get_avatar_image_path(avatar_id: str):
    """Returns the path to a cosmetic avatar's image, or None if it doesn't exist."""
    path = os.path.join(AVATAR_ASSETS_DIR, f"{avatar_id}.png")
    return path if os.path.exists(path) else None


def get_icon_path(icon_id: str):
    """Returns the path to a shop icon (e.g. 'summon_charm'), or None if it doesn't exist."""
    path = os.path.join(ICON_ASSETS_DIR, f"{icon_id}.png")
    return path if os.path.exists(path) else None

# ---------------------------------------------------------------------------
# TWEAK THESE to match your background art once you have it.
# Each entry is (x, y) for the TOP-LEFT corner of where that slot's sprite
# gets pasted, and SPRITE_SIZE is how big each sprite is drawn (width, height).
# Position 1 = first tuple, position 2 = second, etc.
# Open your background image in any image editor (even MS Paint shows pixel
# coordinates in the corner) to figure out where you want each of the 6
# warriors to stand, then update the numbers below and restart the bot.
# ---------------------------------------------------------------------------
PARTY_SLOT_POSITIONS = [
    (835, 250),    # Slot 1
    (950, 400),   # Slot 2
    (950, 600),   # Slot 3
    (834, 730),   # Slot 4
    (715, 400),   # Slot 5
    (715, 600),   # Slot 6
]
PARTY_SPRITE_SIZE = (140, 140)  # (width, height) each sprite is resized to

# Border colors per star tier, used both for single-pull framing (later) and the 10x collage
STAR_BORDER_COLORS = {
    4: (150, 150, 150),
    5: (240, 150, 40),
    6: (255, 215, 0),
}


def get_warrior_image_path(warrior_id: int) -> str:
    """Returns the path to a warrior's art, or the fallback placeholder if none exists."""
    path = os.path.join(ASSETS_DIR, f"{warrior_id}.png")
    if os.path.exists(path):
        return path
    return PLACEHOLDER_PATH


def get_banner_image_path(banner_id: str):
    """
    Returns the path to a banner's image (assets/banners/<banner_id>.png),
    or the banner placeholder if none exists, or None if there's no
    placeholder either (so the caller can just skip showing an image).
    """
    path = os.path.join(BANNER_ASSETS_DIR, f"{banner_id}.png")
    if os.path.exists(path):
        return path
    if os.path.exists(BANNER_PLACEHOLDER_PATH):
        return BANNER_PLACEHOLDER_PATH
    return None


def build_party_scene(party: dict):
    """
    Composes the party background with each occupied slot's warrior sprite
    pasted at its configured position (see PARTY_SLOT_POSITIONS above).
    `party` is a dict of position (1-6) -> warrior info (same shape as
    database.get_party() returns). Empty slots are simply left blank.

    Returns a BytesIO buffer ready to attach to a Discord message, or None
    if no background.png has been added yet.
    """
    if not os.path.exists(PARTY_BACKGROUND_PATH):
        return None

    canvas = _get_cached_decoded_image(PARTY_BACKGROUND_PATH, max_dimension=1600)

    for position in range(1, 7):
        if position not in party:
            continue
        if position > len(PARTY_SLOT_POSITIONS):
            continue  # more occupied slots than configured positions - skip safely

        warrior = party[position]
        img_path = get_warrior_image_path(warrior["warrior_id"])
        try:
            sprite = _get_cached_decoded_image(img_path, max_dimension=400)
        except Exception:
            continue

        sprite = sprite.resize(PARTY_SPRITE_SIZE)
        x, y = PARTY_SLOT_POSITIONS[position - 1]
        canvas.paste(sprite, (x, y), sprite)  # sprite used as its own mask for transparency

    buffer = BytesIO()
    final = canvas.convert("RGB")
    if max(final.size) > MAX_DIMENSION:
        final.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
    final.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def build_collage(results: list, cell_size: int = 200, border_width: int = 6) -> BytesIO:
    """
    Combines up to 10 pull results into a single grid image (5 columns x 2 rows),
    with a colored border per warrior based on its star tier. Returns a BytesIO buffer
    ready to attach to a Discord message.
    """
    columns = 5
    rows = 2
    canvas_w = columns * cell_size
    canvas_h = rows * cell_size

    canvas = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))

    for index, result in enumerate(results[:10]):
        col = index % columns
        row = index // columns
        x0 = col * cell_size
        y0 = row * cell_size

        warrior_id = result["warrior"]["warrior_id"]
        stars = result["stars"]
        border_color = STAR_BORDER_COLORS.get(stars, (255, 255, 255))

        img_path = get_warrior_image_path(warrior_id)
        try:
            art = Image.open(img_path).convert("RGB")
        except Exception:
            art = Image.new("RGB", (cell_size, cell_size), (80, 80, 80))

        # Resize/crop art to fill the inner cell (leaving room for the border)
        inner_size = cell_size - (border_width * 2)
        art = art.resize((inner_size, inner_size))

        # Paste the colored border as a filled rectangle, then the art on top
        draw = ImageDraw.Draw(canvas)
        draw.rectangle(
            [x0, y0, x0 + cell_size - 1, y0 + cell_size - 1],
            fill=border_color,
        )
        canvas.paste(art, (x0 + border_width, y0 + border_width))

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
