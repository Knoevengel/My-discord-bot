import os
from io import BytesIO
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

BASE_DIR = os.path.dirname(__file__)
CARD_ASSETS_DIR = os.path.join(BASE_DIR, "assets", "cards")
FRAMES_DIR = os.path.join(CARD_ASSETS_DIR, "frames")
STARS_DIR = os.path.join(CARD_ASSETS_DIR, "stars")
ELEMENTS_DIR = os.path.join(CARD_ASSETS_DIR, "elements")
CHARACTERS_DIR = os.path.join(CARD_ASSETS_DIR, "characters")

CARD_SIZE = (360, 570)
ART_BOX = (45, 58, 315, 478)  # left, top, right, bottom inside the frame

FRAME_BY_RANGE = {
    "silver": "frame_4star.png",
    "gold": "frame_gold.png",
    "red": "frame_red.png",
    "light_blue": "frame_light_blue.png",
    "teal": "frame_teal.png",
}

STAR_ICON_BY_COLOR = {
    "silver": "star_silver.png",
    "gold": "star_gold.png",
    "red": "star_red.png",
    "light_blue": "star_light_blue.png",
    "teal": "star_teal.png",
}

# Element assets are deliberately separate from character art so you can replace
# them later without touching any character card artwork.
ELEMENT_ICON_IDS = {
    "Blaze": "blaze.png",
    "Shade": "shade.png",
    "Drift": "drift.png",
    "Dawn": "dawn.png",
    "Teal": "teal.png",
    "Rust": "rust.png",
}

_FONT_CANDIDATES = {
    "serif_bold": [
        r"C:\\Windows\\Fonts\\georgiab.ttf",
        r"C:\\Windows\\Fonts\\timesbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ],
    "sans_bold": [
        r"C:\\Windows\\Fonts\\segoeuib.ttf",
        r"C:\\Windows\\Fonts\\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "sans": [
        r"C:\\Windows\\Fonts\\segoeui.ttf",
        r"C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def _font(kind: str, size: int):
    for candidate in _FONT_CANDIDATES[kind]:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _asset(path: str) -> Optional[Image.Image]:
    if not os.path.exists(path):
        return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_size = (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
    resized = img.resize(new_size, Image.Resampling.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _resolve_character_art(warrior: dict) -> str:
    hero_key = str(warrior.get("hero_key") or "").strip()
    warrior_id = warrior.get("warrior_id")
    candidates = []
    if hero_key:
        candidates.append(os.path.join(CHARACTERS_DIR, f"{hero_key}.png"))
    if warrior_id is not None:
        candidates.append(os.path.join(CHARACTERS_DIR, f"{int(warrior_id)}.png"))
    candidates.append(os.path.join(CHARACTERS_DIR, "placeholder.png"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


def _star_info(stars: int) -> tuple[str, int]:
    """Return the frame family and number of progression stars.

    The player-facing system is:
      5★       = gold frame + 5 gold base stars
      6★–10★   = red frame + 1–5 red progression stars
      11★–15★  = light-blue frame + 1–5 light-blue progression stars
      16★–20★  = teal frame + 1–5 teal progression stars

    For 4★, a neutral silver frame with four silver stars is used.
    """
    stars = max(4, min(int(stars), 20))
    if stars == 4:
        return "silver", 0
    if stars == 5:
        return "gold", 0
    if stars <= 10:
        return "red", stars - 5
    if stars <= 15:
        return "light_blue", stars - 10
    return "teal", stars - 15


def _star_row(stars: int, icon_size: int = 20) -> Image.Image:
    frame_family, progression_count = _star_info(stars)
    row = Image.new("RGBA", (250, 30), (0, 0, 0, 0))

    if frame_family == "silver":
        base_color = "silver"
        count = 4
    else:
        base_color = "gold"
        count = 5

    icons: list[Image.Image] = []
    for _ in range(count):
        p = os.path.join(STARS_DIR, STAR_ICON_BY_COLOR[base_color])
        icon = _asset(p)
        if icon:
            icons.append(icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS))

    if progression_count:
        p = os.path.join(STARS_DIR, STAR_ICON_BY_COLOR[frame_family])
        for _ in range(progression_count):
            icon = _asset(p)
            if icon:
                icons.append(icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS))

    if not icons:
        return row

    gap = 2
    total_w = len(icons) * icon_size + (len(icons) - 1) * gap
    x = max(0, (row.width - total_w) // 2)
    for icon in icons:
        row.alpha_composite(icon, (x, 4))
        x += icon_size + gap
    return row


def _element_icon(element: str, size: int = 31) -> Optional[Image.Image]:
    filename = ELEMENT_ICON_IDS.get(str(element), "")
    path = os.path.join(ELEMENTS_DIR, filename) if filename else ""
    icon = _asset(path) if path else None
    if icon is None:
        icon = _asset(os.path.join(ELEMENTS_DIR, "generic.png"))
    return icon.resize((size, size), Image.Resampling.LANCZOS) if icon else None


def _draw_centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (box[0] + box[2] - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = (box[1] + box[3] - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _draw_panel(canvas: Image.Image, box: tuple[int, int, int, int], outline=(255, 255, 255, 150), fill=(8, 10, 16, 185), radius=12):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)
    canvas.alpha_composite(layer)


def build_character_card(warrior: dict, stars: Optional[int] = None, level: Optional[int] = None) -> BytesIO:
    """Build one finished character card from separate art + UI assets.

    The character art itself never contains the stars, level, element, or name.
    Those are rendered dynamically from the warrior's current database state.
    """
    current_stars = int(stars if stars is not None else warrior.get("stars", 4))
    current_level = int(level if level is not None else warrior.get("level", 1))
    name = str(warrior.get("name", "Unknown"))
    element = str(warrior.get("element", "Unknown"))

    canvas = Image.new("RGBA", CARD_SIZE, (7, 9, 16, 255))

    art_path = _resolve_character_art(warrior)
    art = _asset(art_path) if art_path else None
    if art is None:
        art = Image.new("RGBA", (ART_BOX[2] - ART_BOX[0], ART_BOX[3] - ART_BOX[1]), (17, 21, 30, 255))
        adraw = ImageDraw.Draw(art)
        _draw_centered(adraw, (0, 0, art.width, art.height), "NO ART", _font("sans_bold", 28), (180, 190, 205, 255))
    else:
        art = _fit_cover(art, (ART_BOX[2] - ART_BOX[0], ART_BOX[3] - ART_BOX[1]))

    # Subtle dark gradient at the bottom keeps the dynamic text readable
    # without changing the character artwork itself.
    gradient = Image.new("RGBA", art.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(art.height):
        if y >= int(art.height * 0.60):
            alpha = int(175 * ((y - art.height * 0.60) / (art.height * 0.40)))
            gd.line((0, y, art.width, y), fill=(0, 0, 0, alpha))
    art.alpha_composite(gradient)
    canvas.alpha_composite(art, (ART_BOX[0], ART_BOX[1]))

    frame_family, _ = _star_info(current_stars)
    frame = _asset(os.path.join(FRAMES_DIR, FRAME_BY_RANGE[frame_family]))
    if frame is not None:
        frame = frame.resize(CARD_SIZE, Image.Resampling.LANCZOS)
        canvas.alpha_composite(frame)

    # Top star row and level panel.
    star_row = _star_row(current_stars, icon_size=20)
    canvas.alpha_composite(star_row, (55, 25))

    level_panel = (272, 25, 346, 55)
    _draw_panel(canvas, level_panel, outline=(230, 230, 240, 120), fill=(8, 10, 16, 170), radius=8)
    draw = ImageDraw.Draw(canvas)
    _draw_centered(draw, level_panel, f"Lv. {current_level}", _font("sans_bold", 15), (250, 245, 240, 255))

    # Name panel.
    name_box = (62, 444, 298, 493)
    _draw_panel(canvas, name_box, outline=(230, 230, 240, 135), fill=(8, 10, 16, 195), radius=10)
    name_text = name.upper()
    name_font = _font("serif_bold", 23 if len(name_text) <= 12 else 19)
    _draw_centered(draw, name_box, name_text, name_font, (248, 241, 228, 255))

    # Bottom element panel.
    elem_box = (92, 499, 268, 552)
    _draw_panel(canvas, elem_box, outline=(230, 230, 240, 115), fill=(8, 10, 16, 185), radius=11)
    icon = _element_icon(element, 31)
    if icon:
        canvas.alpha_composite(icon, (106, 510))
    _draw_centered(draw, (133, 505, 257, 552), element.upper(), _font("sans_bold", 16), (246, 242, 236, 255))

    buffer = BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def build_pull_grid(results: list[dict], card_size: tuple[int, int] = (240, 380)) -> BytesIO:
    """Create a 5x2 grid of the new card assets for a 10x pull."""
    columns = 5
    rows = 2
    gap = 10
    width = columns * card_size[0] + (columns + 1) * gap
    height = rows * card_size[1] + (rows + 1) * gap
    canvas = Image.new("RGBA", (width, height), (6, 8, 15, 255))

    for i, result in enumerate(results[:10]):
        warrior = dict(result["warrior"])
        warrior["stars"] = int(result.get("stars", warrior.get("stars", 4)))
        warrior["level"] = 1
        card_bytes = build_character_card(warrior)
        card = Image.open(card_bytes).convert("RGBA")
        card.thumbnail(card_size, Image.Resampling.LANCZOS)
        x = gap + (i % columns) * (card_size[0] + gap)
        y = gap + (i // columns) * (card_size[1] + gap)
        canvas.alpha_composite(card, (x, y))

    out = BytesIO()
    final = canvas.convert("RGB")
    final.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
