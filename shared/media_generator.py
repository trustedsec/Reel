"""
Dynamic media generators for text-to-image and QR code rendering.

Produces PNG bytes on demand; used by Flask routes in phishing/routes.py
when Twilio (or a browser) fetches a generated media URL.
"""
import logging
from io import BytesIO
from typing import Dict, Any

from PIL import Image, ImageDraw

from shared.mms_card import _get_font, _wrap_text

logger = logging.getLogger(__name__)



def generate_text_image(text: str, config: Dict[str, Any] = None) -> bytes:
    """
    Render *text* centered on a PNG image.

    Config keys (all optional):
        width      – canvas width  (default 600, max 2000)
        height     – canvas height (default 200, max 2000)
        font_size  – pixel size    (default 64, max 200)
        bg_color   – hex           (default #FFFFFF)
        text_color – hex           (default #000000)
    """
    config = config or {}
    width = min(int(config.get('width', 600)), 2000)
    height = min(int(config.get('height', 200)), 2000)
    font_size = min(int(config.get('font_size', 64)), 200)
    bg_color = config.get('bg_color', '#FFFFFF')
    text_color = config.get('text_color', '#000000')

    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    font = _get_font(font_size, bold=True)

    # Wrap text to fit width with some margin
    margin = 20
    lines = _wrap_text(draw, text, font, width - margin * 2)

    # Calculate total text block height
    line_height = font_size + 8
    total_height = line_height * len(lines)

    # Start y so the block is vertically centered
    y = max(0, (height - total_height) // 2)

    for line in lines:
        try:
            lw = draw.textlength(line, font=font)
        except AttributeError:
            lw = font.getlength(line)
        x = max(0, (width - lw) / 2)
        draw.text((x, y), line, fill=text_color, font=font)
        y += line_height

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def generate_qr_image(data: str, config: Dict[str, Any] = None) -> bytes:
    """
    Generate a QR code PNG for *data*.

    Uses the ``qrcode[pil]`` library (graceful import).

    Config keys (all optional):
        box_size         – module pixel size (default 10)
        border           – quiet-zone modules (default 4)
        fill_color       – hex (default #000000)
        bg_color         – hex (default #FFFFFF)
        error_correction – L / M / Q / H (default M)
    """
    try:
        import qrcode
        from qrcode.constants import (
            ERROR_CORRECT_L, ERROR_CORRECT_M,
            ERROR_CORRECT_Q, ERROR_CORRECT_H,
        )
    except ImportError:
        raise RuntimeError(
            "qrcode[pil] is not installed. Install it: pip install 'qrcode[pil]'"
        )

    config = config or {}
    box_size = int(config.get('box_size', 10))
    border = int(config.get('border', 4))
    fill_color = config.get('fill_color', '#000000')
    bg_color = config.get('bg_color', '#FFFFFF')

    ec_map = {
        'L': ERROR_CORRECT_L,
        'M': ERROR_CORRECT_M,
        'Q': ERROR_CORRECT_Q,
        'H': ERROR_CORRECT_H,
    }
    ec_level = ec_map.get(
        config.get('error_correction', 'M').upper(),
        ERROR_CORRECT_M,
    )

    qr = qrcode.QRCode(
        version=None,  # auto-size
        error_correction=ec_level,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color=fill_color, back_color=bg_color).convert('RGB')

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()
