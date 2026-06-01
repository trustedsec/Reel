"""
HTML-to-image MMS card renderer.

Renders a Jinja2 HTML template to PNG via Playwright screenshot.
Image helpers (qr_code, text_image, logo) return data: URIs so the
HTML is fully self-contained — no external fetches needed.
"""
import asyncio
import base64
import logging
import mimetypes
import threading
from pathlib import Path
from typing import Dict, Any

from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data URI helpers — used as Jinja2 globals inside card templates
# ---------------------------------------------------------------------------

def qr_code_data_uri(data: str, box_size=10, border=4, fill_color='#000000',
                     bg_color='#FFFFFF', error_correction='M') -> str:
    """Generate a QR code as a data:image/png;base64,... URI."""
    from shared.media_generator import generate_qr_image
    config = {
        'box_size': box_size, 'border': border,
        'fill_color': fill_color, 'bg_color': bg_color,
        'error_correction': error_correction,
    }
    png_bytes = generate_qr_image(str(data), config)
    return 'data:image/png;base64,' + base64.b64encode(png_bytes).decode('ascii')


def text_image_data_uri(text: str, font_size=64, width=600, height=200,
                        bg_color='#FFFFFF', text_color='#000000') -> str:
    """Generate a text image as a data:image/png;base64,... URI."""
    from shared.media_generator import generate_text_image
    config = {
        'font_size': font_size, 'width': width, 'height': height,
        'bg_color': bg_color, 'text_color': text_color,
    }
    png_bytes = generate_text_image(str(text), config)
    return 'data:image/png;base64,' + base64.b64encode(png_bytes).decode('ascii')


def logo_data_uri(logo_path: str) -> str:
    """Read a logo file from disk and return as a data: URI."""
    path = Path(logo_path)
    if not path.is_file():
        return ''
    mime = mimetypes.guess_type(str(path))[0] or 'image/png'
    data = path.read_bytes()
    return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')


# ---------------------------------------------------------------------------
# Playwright card renderer — keeps a warm browser instance
# ---------------------------------------------------------------------------

async def _render_html_to_png(html: str, width: int = 1080, height: int = 1350) -> bytes:
    """Launch browser, screenshot HTML, tear down. Self-contained per call.

    Uses low-memory Chromium flags so it survives on small VPS instances.
    Retries the screenshot once on CDP "Unable to capture" errors, which
    typically indicate transient renderer pressure rather than a real failure.
    """
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                # Don't rely on /dev/shm — small Docker/VPS images often have a
                # tiny shm and Chromium will OOM trying to write screenshots.
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-gpu',
                '--disable-software-rasterizer',
                # Reduce memory footprint
                '--disable-extensions',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-background-networking',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-breakpad',
                '--disable-component-update',
                '--disable-default-apps',
                '--disable-features=TranslateUI,BlinkGenPropertyTrees',
                '--disable-ipc-flooding-protection',
                '--disable-renderer-backgrounding',
                '--disable-sync',
                '--metrics-recording-only',
                '--mute-audio',
            ],
        )
        page = await browser.new_page(viewport={'width': width, 'height': height})
        try:
            await page.set_content(html, wait_until='load')
            # Brief settle pause so any deferred rendering (large data URIs,
            # font loading) finishes before the screenshot. Helps on small
            # boxes where the renderer is competing for CPU.
            await asyncio.sleep(0.3)

            # Screenshot the body element itself rather than the viewport.
            # This makes the output PNG exactly the size of the rendered body
            # — so a template with `body { width: 402px; height: 874px }`
            # produces a 402×874 image with no viewport whitespace, and a
            # template designed for 1080×1350 still produces that.
            async def _shot():
                body_el = await page.query_selector('body')
                if body_el is not None:
                    return await body_el.screenshot(type='png')
                return await page.screenshot(full_page=False, type='png')

            try:
                png = await _shot()
            except Exception as first_err:
                # CDP screenshot can fail transiently on memory pressure.
                # Wait a moment, force one more layout cycle, retry once.
                logger.warning(f"Screenshot failed once, retrying: {first_err}")
                await asyncio.sleep(0.5)
                try:
                    await page.evaluate("document.body.offsetHeight")
                except Exception:
                    pass
                png = await _shot()
            return png
        finally:
            try:
                await page.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass
    finally:
        await pw.stop()


_render_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Main entry point — sync wrapper
# ---------------------------------------------------------------------------

def generate_card_image_html(config: Dict[str, Any], target_data: Dict[str, str]) -> bytes:
    """
    Render an HTML card template to PNG bytes.

    *config* must contain 'html_template' (Jinja2 HTML string).
    *target_data* provides per-target values (fn, ln, dc, url, em, etc.).
    """
    html_template = config.get('html_template', '')
    if not html_template:
        raise ValueError('html_template is empty')

    # Build Jinja2 context: config fields + target_data + helpers
    ctx: Dict[str, Any] = {}

    # Config fields available as top-level variables
    for key in ('company_name', 'department', 'heading', 'body',
                'code_label', 'cta_text', 'footer_text', 'logo_path'):
        ctx[key] = config.get(key, '')
    ctx['colors'] = config.get('colors', {})

    # Target data (fn, ln, dc, url, em, ...) available as top-level variables
    ctx.update(target_data)

    # Helper functions. MMS cards are screenshotted by Playwright, which can't
    # fetch external URLs, so all helpers return inline data: URIs. We expose
    # both the short names and the *_inline aliases so templates copied from
    # browser-served contexts (where _inline distinguishes from URL helpers)
    # still work here.
    ctx['qr_code'] = qr_code_data_uri
    ctx['qr_code_inline'] = qr_code_data_uri
    ctx['text_image'] = text_image_data_uri
    ctx['text_image_inline'] = text_image_data_uri
    ctx['logo'] = logo_data_uri
    ctx['logo_inline'] = logo_data_uri

    # Render with sandboxed Jinja2
    env = SandboxedEnvironment()
    try:
        template = env.from_string(html_template)
        rendered_html = template.render(**ctx)
    except Exception as e:
        logger.error(f"Jinja2 rendering failed: {e}", exc_info=True)
        raise

    # Screenshot via Playwright — full lifecycle per call to avoid cross-loop issues
    card_width = int(config.get('card_width', 1080))
    card_height = int(config.get('card_height', 1350))

    loop = asyncio.new_event_loop()
    with _render_lock:
        try:
            return loop.run_until_complete(
                _render_html_to_png(rendered_html, card_width, card_height)
            )
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Default HTML template — replicates the Pillow card layout
# ---------------------------------------------------------------------------

DEFAULT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    width: 1080px; height: 1350px;
    font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    background: #ffffff;
    overflow: hidden;
  }
  .banner {
    background: {{ colors.primary or '#4A5568' }};
    height: 200px;
    display: flex;
    align-items: center;
    padding: 0 72px;
    gap: 24px;
  }
  .banner img { max-height: 160px; max-width: 280px; }
  .banner-text { color: {{ colors.text_on_primary or '#FFFFFF' }}; }
  .banner-text .company { font-size: 38px; font-weight: bold; }
  .banner-text .dept { font-size: 26px; margin-top: 6px; opacity: 0.9; }
  .accent-bar { height: 8px; background: {{ colors.accent or '#3182CE' }}; }
  .content { padding: 48px 72px 0; }
  h1 { font-size: 48px; color: #1A202C; margin-bottom: 16px; line-height: 1.2; }
  .body-text { font-size: 32px; color: #4A5568; line-height: 1.5; margin-bottom: 24px; }
  .code-box {
    background: #F7FAFC; border: 2px solid #E2E8F0; border-radius: 16px;
    padding: 24px 32px; text-align: center; margin-bottom: 32px;
  }
  .code-box .label { font-size: 24px; color: #718096; margin-bottom: 12px; }
  .code-box .code { font-size: 52px; font-weight: bold; color: #1A202C; }
  .code-box img { max-width: 100%; }
  .cta {
    display: block; width: fit-content; min-width: 400px; margin: 8px auto 32px;
    background: {{ colors.accent or '#3182CE' }}; color: {{ colors.text_on_accent or '#FFFFFF' }};
    font-size: 34px; font-weight: bold; text-align: center;
    padding: 18px 50px; border-radius: 36px; text-decoration: none;
  }
  .footer {
    position: absolute; bottom: 32px; left: 0; right: 0;
    text-align: center; font-size: 22px; color: #A0AEC0; padding: 0 72px;
  }
  .bottom-bar {
    position: absolute; bottom: 0; left: 0; right: 0; height: 16px;
    display: flex;
  }
  .bottom-bar .left { flex: 1; background: {{ colors.primary or '#4A5568' }}; }
  .bottom-bar .right { flex: 1; background: {{ colors.accent or '#3182CE' }}; }
</style>
</head>
<body>
  <div class="banner">
    {% if logo_path %}<img src="{{ logo(logo_path) }}" alt="">{% endif %}
    <div class="banner-text">
      {% if company_name %}<div class="company">{{ company_name }}</div>{% endif %}
      {% if department %}<div class="dept">{{ department }}</div>{% endif %}
    </div>
  </div>
  <div class="accent-bar"></div>
  <div class="content">
    {% if heading %}<h1>{{ heading }}</h1>{% endif %}
    {% if body %}<p class="body-text">{{ body }}</p>{% endif %}
    {% if dc %}
    <div class="code-box">
      {% if code_label %}<div class="label">{{ code_label }}</div>{% endif %}
      <div class="code">{{ dc }}</div>
    </div>
    {% endif %}
    {% if cta_text %}<div class="cta">{{ cta_text }}</div>{% endif %}
  </div>
  {% if footer_text %}<div class="footer">{{ footer_text }}</div>{% endif %}
  <div class="bottom-bar"><div class="left"></div><div class="right"></div></div>
</body>
</html>
"""
