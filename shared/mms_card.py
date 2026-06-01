"""
MMS branded card image generator.

Extracts brand colors from a logo and generates personalized MMS card images
using Pillow. Cards are 1080x1350 PNG images with banner, heading, body,
optional code box, CTA button, and footer.
"""
import colorsys
import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_font_cache: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

# Common system paths for DejaVu Sans (Linux containers)
_SYSTEM_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

_SYSTEM_FONT_PATHS_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def _find_system_font(bold: bool = True) -> Optional[str]:
    paths = _SYSTEM_FONT_PATHS if bold else _SYSTEM_FONT_PATHS_REGULAR
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (bold, size)
    if key in _font_cache:
        return _font_cache[key]

    path = _find_system_font(bold)
    if path:
        font = ImageFont.truetype(path, size)
    else:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
        except (OSError, IOError):
            font = ImageFont.load_default()
    _font_cache[key] = font
    return font

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _luminance(r: int, g: int, b: int) -> float:
    """Relative luminance per WCAG 2.0."""
    def _c(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * _c(r) + 0.7152 * _c(g) + 0.0722 * _c(b)


def _contrast_color(hex_bg: str) -> str:
    """Return '#FFFFFF' or '#000000' for best contrast on *hex_bg*."""
    r, g, b = _hex_to_rgb(hex_bg)
    lum = _luminance(r, g, b)
    return "#FFFFFF" if lum < 0.4 else "#000000"


def _hsv_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    h1, s1, v1 = colorsys.rgb_to_hsv(c1[0] / 255, c1[1] / 255, c1[2] / 255)
    h2, s2, v2 = colorsys.rgb_to_hsv(c2[0] / 255, c2[1] / 255, c2[2] / 255)
    dh = min(abs(h1 - h2), 1 - abs(h1 - h2))
    return (dh ** 2 + (s1 - s2) ** 2 + (v1 - v2) ** 2) ** 0.5

# ---------------------------------------------------------------------------
# Brand color extraction
# ---------------------------------------------------------------------------

_FALLBACK_COLORS = {
    "primary": "#4A5568",
    "accent": "#3182CE",
    "text_on_primary": "#FFFFFF",
    "text_on_accent": "#FFFFFF",
}


def extract_brand_colors(logo_path: str) -> Dict[str, str]:
    """
    Extract primary and accent brand colors from a logo image.

    Opens the logo with Pillow, filters out transparent/near-white/near-black
    pixels, quantizes to 8 colors, and picks the most dominant as primary and
    the most different (by HSV distance) as accent.

    Returns dict with keys: primary, accent, text_on_primary, text_on_accent.
    Falls back to a neutral gray/blue palette if no usable colors are found.
    """
    try:
        img = Image.open(logo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"Cannot open logo {logo_path}: {e}")
        return dict(_FALLBACK_COLORS)

    pixels = list(img.getdata())

    # Filter: keep only opaque, non-extreme-luminance pixels
    filtered = []
    for r, g, b, a in pixels:
        if a < 128:
            continue
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        if lum < 20 or lum > 240:
            continue
        filtered.append((r, g, b))

    if len(filtered) < 10:
        return dict(_FALLBACK_COLORS)

    # Build a small RGB image from filtered pixels for quantization
    side = int(len(filtered) ** 0.5) + 1
    quant_img = Image.new("RGB", (side, side), (128, 128, 128))
    for i, px in enumerate(filtered):
        quant_img.putpixel((i % side, i // side), px)

    try:
        quantized = quant_img.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    except Exception:
        quantized = quant_img.quantize(colors=8)

    palette = quantized.getpalette()
    if not palette:
        return dict(_FALLBACK_COLORS)

    # Count frequency of each palette index
    freq: Dict[int, int] = {}
    for idx in quantized.getdata():
        freq[idx] = freq.get(idx, 0) + 1

    sorted_indices = sorted(freq, key=lambda i: freq[i], reverse=True)

    def _palette_rgb(idx: int) -> Tuple[int, int, int]:
        return (palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2])

    primary_rgb = _palette_rgb(sorted_indices[0])
    primary_hex = _rgb_to_hex(*primary_rgb)

    # Pick accent: most different hue from primary
    accent_rgb = primary_rgb
    best_dist = 0.0
    for idx in sorted_indices[1:]:
        candidate = _palette_rgb(idx)
        d = _hsv_distance(primary_rgb, candidate)
        if d > best_dist:
            best_dist = d
            accent_rgb = candidate

    accent_hex = _rgb_to_hex(*accent_rgb)

    # If accent ended up too close to primary, use fallback accent
    if best_dist < 0.1:
        accent_hex = _FALLBACK_COLORS["accent"]

    return {
        "primary": primary_hex,
        "accent": accent_hex,
        "text_on_primary": _contrast_color(primary_hex),
        "text_on_accent": _contrast_color(accent_hex),
    }

# ---------------------------------------------------------------------------
# Text wrapping helper
# ---------------------------------------------------------------------------

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    """Word-wrap *text* to fit within *max_width* pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        try:
            w = draw.textlength(test, font=font)
        except AttributeError:
            w = font.getlength(test)
        if w <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]

# ---------------------------------------------------------------------------
# Card image generator
# ---------------------------------------------------------------------------

# Canvas dimensions (same as tested REI card)
CARD_W, CARD_H = 1080, 1350


def generate_card_image(config: Dict[str, Any], target_data: Dict[str, str]) -> bytes:
    """
    Generate a branded MMS card image.

    Dispatches to HTML renderer (Playwright) or Pillow renderer based on
    ``config['render_mode']``.  Default is ``'pillow'`` for backward compat.
    """
    render_mode = config.get('render_mode', 'pillow')
    if render_mode == 'html':
        from shared.mms_card_html import generate_card_image_html
        return generate_card_image_html(config, target_data)
    return _generate_card_image_pillow(config, target_data)


def _generate_card_image_pillow(config: Dict[str, Any], target_data: Dict[str, str]) -> bytes:
    """
    Generate a branded MMS card image using Pillow.

    *config* is the saved card config dict (colors, texts, logo_path, etc.).
    *target_data* provides per-target values for ``{{variable}}`` interpolation
    in heading, body, footer, and code_label fields.

    Returns PNG bytes.
    """
    colors = config.get("colors", _FALLBACK_COLORS)
    primary = colors.get("primary", _FALLBACK_COLORS["primary"])
    accent = colors.get("accent", _FALLBACK_COLORS["accent"])
    text_on_primary = colors.get("text_on_primary", "#FFFFFF")
    text_on_accent = colors.get("text_on_accent", "#FFFFFF")

    # Interpolate placeholders
    def _interp(text: str) -> str:
        if not text:
            return ""
        def replacer(m):
            key = m.group(1).strip()
            return target_data.get(key, m.group(0))
        return re.sub(r"\{\{(.+?)\}\}", replacer, text)

    heading = _interp(config.get("heading", ""))
    body = _interp(config.get("body", ""))
    cta_text = _interp(config.get("cta_text", ""))
    footer_text = _interp(config.get("footer_text", ""))
    code_label = _interp(config.get("code_label", ""))
    company_name = config.get("company_name", "")
    department = config.get("department", "")
    device_code = target_data.get("dc", "")

    # Create canvas
    img = Image.new("RGB", (CARD_W, CARD_H), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    # Margins
    mx = 72  # horizontal margin
    content_w = CARD_W - mx * 2

    y = 0  # current vertical cursor

    # ------------------------------------------------------------------
    # Banner (primary color background with logo + company + department)
    # ------------------------------------------------------------------
    banner_h = 200
    draw.rectangle([0, 0, CARD_W, banner_h], fill=primary)

    logo_right_edge = mx
    logo_path = config.get("logo_path", "")
    if logo_path and Path(logo_path).is_file():
        try:
            logo = Image.open(logo_path).convert("RGBA")
            # Resize logo to fit banner with padding
            max_logo_h = banner_h - 40
            max_logo_w = 280
            logo.thumbnail((max_logo_w, max_logo_h), Image.LANCZOS)
            # Center vertically in banner
            logo_y = (banner_h - logo.size[1]) // 2
            img.paste(logo, (mx, logo_y), logo)
            logo_right_edge = mx + logo.size[0] + 24
        except Exception as e:
            logger.warning(f"Failed to load logo: {e}")

    # Company name + department to the right of logo
    if company_name:
        company_font = _get_font(38, bold=True)
        draw.text((logo_right_edge, banner_h // 2 - 32), company_name,
                   fill=text_on_primary, font=company_font)
    if department:
        dept_font = _get_font(26, bold=False)
        draw.text((logo_right_edge, banner_h // 2 + 14), department,
                   fill=text_on_primary, font=dept_font)

    y = banner_h

    # ------------------------------------------------------------------
    # Accent bar
    # ------------------------------------------------------------------
    accent_bar_h = 8
    draw.rectangle([0, y, CARD_W, y + accent_bar_h], fill=accent)
    y += accent_bar_h + 48

    # ------------------------------------------------------------------
    # Heading
    # ------------------------------------------------------------------
    if heading:
        heading_font = _get_font(48, bold=True)
        heading_lines = _wrap_text(draw, heading, heading_font, content_w)
        for line in heading_lines:
            draw.text((mx, y), line, fill="#1A202C", font=heading_font)
            y += 60
        y += 16

    # ------------------------------------------------------------------
    # Body text
    # ------------------------------------------------------------------
    if body:
        body_font = _get_font(32, bold=False)
        body_lines = _wrap_text(draw, body, body_font, content_w)
        for line in body_lines:
            draw.text((mx, y), line, fill="#4A5568", font=body_font)
            y += 44
        y += 24

    # ------------------------------------------------------------------
    # Info box (device code display, if dc present)
    # ------------------------------------------------------------------
    if device_code:
        box_h = 140
        box_top = y + 8
        # Rounded rectangle background
        draw.rounded_rectangle(
            [mx, box_top, CARD_W - mx, box_top + box_h],
            radius=16, fill="#F7FAFC", outline="#E2E8F0", width=2,
        )
        if code_label:
            label_font = _get_font(24, bold=False)
            draw.text((mx + 32, box_top + 16), code_label, fill="#718096", font=label_font)
        code_font = _get_font(52, bold=True)
        # Center code text
        try:
            code_w = draw.textlength(device_code, font=code_font)
        except AttributeError:
            code_w = code_font.getlength(device_code)
        code_x = (CARD_W - code_w) / 2
        code_y = box_top + (box_h - 52) / 2 + (12 if code_label else 0)
        draw.text((code_x, code_y), device_code, fill="#1A202C", font=code_font)
        y = box_top + box_h + 32

    # ------------------------------------------------------------------
    # CTA button
    # ------------------------------------------------------------------
    if cta_text:
        btn_font = _get_font(34, bold=True)
        try:
            btn_text_w = draw.textlength(cta_text, font=btn_font)
        except AttributeError:
            btn_text_w = btn_font.getlength(cta_text)
        btn_w = max(int(btn_text_w + 100), 400)
        btn_h = 72
        btn_x = (CARD_W - btn_w) // 2
        btn_y = y + 8
        draw.rounded_rectangle(
            [btn_x, btn_y, btn_x + btn_w, btn_y + btn_h],
            radius=36, fill=accent,
        )
        # Center text in button
        txt_x = btn_x + (btn_w - btn_text_w) / 2
        txt_y = btn_y + (btn_h - 34) / 2
        draw.text((txt_x, txt_y), cta_text, fill=text_on_accent, font=btn_font)
        y = btn_y + btn_h + 32

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    if footer_text:
        footer_font = _get_font(22, bold=False)
        footer_lines = _wrap_text(draw, footer_text, footer_font, content_w)
        # Position footer near bottom, but at least below current y
        footer_y = max(y + 16, CARD_H - 120)
        for line in footer_lines:
            # Center each line
            try:
                lw = draw.textlength(line, font=footer_font)
            except AttributeError:
                lw = footer_font.getlength(line)
            draw.text(((CARD_W - lw) / 2, footer_y), line, fill="#A0AEC0", font=footer_font)
            footer_y += 30

    # ------------------------------------------------------------------
    # Bottom bar (primary + accent stripes)
    # ------------------------------------------------------------------
    bar_h = 16
    draw.rectangle([0, CARD_H - bar_h, CARD_W // 2, CARD_H], fill=primary)
    draw.rectangle([CARD_W // 2, CARD_H - bar_h, CARD_W, CARD_H], fill=accent)

    # Encode to PNG bytes
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
