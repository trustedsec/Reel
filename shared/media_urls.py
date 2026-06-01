"""
URL builders for on-demand media routes (/media/qr, /media/text-image).

Used by:
- Jinja2 template helpers (qr_code(), text_image()) in render_template plugin
- QR Code plugin (plugins/builtin/qr_code.py)
- Text to Image plugin (plugins/builtin/text_to_image.py)
- SMS sender plugin (plugins/builtin/sms_sender.py)
"""
import base64
from typing import Dict, Any
from urllib.parse import urlencode, urlparse


def get_phishing_base_url(context: Dict[str, Any]) -> str:
    """
    Derive the phishing server base URL from workflow context.

    Priority: context['url'] scheme+netloc > Config PHISHING_HOST:PORT > fallback.
    """
    url = context.get('url', '')
    if url:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    try:
        from shared.config import Config
        cfg = Config()
        host = cfg.PHISHING_HOST
        port = cfg.PHISHING_PORT
        if host == '0.0.0.0':
            host = '127.0.0.1'
        return f"http://{host}:{port}"
    except Exception:
        return "http://127.0.0.1:1234"


def build_qr_url(
    base_url: str,
    data: str,
    box_size: int = 10,
    border: int = 4,
    fill_color: str = '#000000',
    bg_color: str = '#FFFFFF',
    error_correction: str = 'M',
) -> str:
    """Build a /media/qr URL for the given data and options."""
    data_b64 = base64.urlsafe_b64encode(data.encode('utf-8')).decode('ascii')
    params: Dict[str, Any] = {'d': data_b64}
    if box_size != 10:
        params['bs'] = int(box_size)
    if border != 4:
        params['b'] = int(border)
    if fill_color != '#000000':
        params['fc'] = fill_color
    if bg_color != '#FFFFFF':
        params['bg'] = bg_color
    if error_correction != 'M':
        params['ec'] = error_correction
    return f"{base_url}/media/qr?{urlencode(params)}"


def build_text_image_url(
    base_url: str,
    text: str,
    font_size: int = 64,
    width: int = 600,
    height: int = 200,
    bg_color: str = '#FFFFFF',
    text_color: str = '#000000',
) -> str:
    """Build a /media/text-image URL for the given text and options."""
    text_b64 = base64.urlsafe_b64encode(text.encode('utf-8')).decode('ascii')
    params: Dict[str, Any] = {'t': text_b64}
    if font_size != 64:
        params['fs'] = int(font_size)
    if width != 600:
        params['w'] = int(width)
    if height != 200:
        params['h'] = int(height)
    if bg_color != '#FFFFFF':
        params['bg'] = bg_color
    if text_color != '#000000':
        params['tc'] = text_color
    return f"{base_url}/media/text-image?{urlencode(params)}"
