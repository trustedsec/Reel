"""
Tests for QR Code and Text to Image plugins + media generator
"""
import base64
import pytest
from urllib.parse import unquote
from unittest.mock import patch, Mock
from plugins.registry import PluginRegistry


@pytest.fixture
def registry():
    """Create registry with built-in plugins"""
    reg = PluginRegistry()
    reg.discover_builtin_plugins()
    return reg


@pytest.fixture
def phishing_client():
    """Client backed by the phishing app (has /media/* routes)."""
    from app import create_app
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        from shared.database import db
        db.create_all()
        yield app.test_client()
        db.drop_all()


# ---------------------------------------------------------------------------
# TextToImagePlugin
# ---------------------------------------------------------------------------

def test_text_to_image_registered(registry):
    plugin = registry.get_plugin("text_to_image")
    assert plugin is not None
    assert plugin.plugin_type == "text_to_image"
    assert plugin.display_name == "Text to Image"


def test_text_to_image_properties(registry):
    plugin = registry.get_plugin("text_to_image")
    assert plugin.plugin_category == "data_transform"
    assert "text" in plugin.config_schema["properties"]
    assert "text" in plugin.config_schema["required"]


def test_text_to_image_generates_url(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {"url": "https://phish.example.com/c/abc123"}
    config = {"text": "Hello World"}

    result = plugin.execute(context, config)

    media_url = result.get("_generated_media_url", "")
    assert media_url.startswith("https://phish.example.com/media/text-image?t=")
    # Decode the t param (URL-decode first) and verify round-trip
    t_param = unquote(media_url.split("t=")[1].split("&")[0])
    assert base64.urlsafe_b64decode(t_param).decode() == "Hello World"


def test_text_to_image_variable_interpolation(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {
        "url": "https://phish.example.com/c/abc",
        "device_code": "XYZ789",
    }
    config = {"text": "Code: {{device_code}}"}

    result = plugin.execute(context, config)
    t_param = unquote(result["_generated_media_url"].split("t=")[1].split("&")[0])
    assert "XYZ789" in base64.urlsafe_b64decode(t_param).decode()


def test_text_to_image_custom_output_variable(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"text": "test", "output_variable": "my_image"}

    result = plugin.execute(context, config)
    assert "my_image" in result
    assert "_generated_media_url" not in result


def test_text_to_image_non_default_params_in_url(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {
        "text": "hi",
        "font_size": 100,
        "width": 800,
        "height": 400,
        "bg_color": "#FF0000",
        "text_color": "#00FF00",
    }

    result = plugin.execute(context, config)
    url = result["_generated_media_url"]
    assert "fs=100" in url
    assert "w=800" in url
    assert "h=400" in url
    assert "bg=%23FF0000" in url
    assert "tc=%2300FF00" in url


def test_text_to_image_default_params_omitted(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"text": "hi", "font_size": 64, "width": 600, "height": 200}

    result = plugin.execute(context, config)
    url = result["_generated_media_url"]
    # Defaults should not appear
    assert "fs=" not in url
    assert "w=" not in url
    assert "h=" not in url


def test_text_to_image_empty_text_errors(registry):
    plugin = registry.get_plugin("text_to_image")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"text": ""}

    result = plugin.execute(context, config)
    assert "_error" in result


def test_text_to_image_fallback_base_url(registry):
    """When no url in context, falls back to Config or hardcoded default."""
    plugin = registry.get_plugin("text_to_image")

    context = {}  # no url
    config = {"text": "test"}

    result = plugin.execute(context, config)
    # Should still produce a URL (from config fallback or hardcoded)
    assert "_generated_media_url" in result
    assert "/media/text-image?" in result["_generated_media_url"]


def test_text_to_image_validate_config(registry):
    plugin = registry.get_plugin("text_to_image")

    assert plugin.validate_config({"text": "hi"}) is None

    errors = plugin.validate_config({})
    assert errors is not None
    assert any("text" in e for e in errors)


def test_text_to_image_validate_font_size_bounds(registry):
    plugin = registry.get_plugin("text_to_image")

    errors = plugin.validate_config({"text": "hi", "font_size": 999})
    assert errors is not None

    errors = plugin.validate_config({"text": "hi", "font_size": 0})
    assert errors is not None


def test_text_to_image_validate_dimension_bounds(registry):
    plugin = registry.get_plugin("text_to_image")

    errors = plugin.validate_config({"text": "hi", "width": 5000})
    assert errors is not None

    errors = plugin.validate_config({"text": "hi", "height": 5000})
    assert errors is not None


# ---------------------------------------------------------------------------
# QrCodePlugin
# ---------------------------------------------------------------------------

def test_qr_code_registered(registry):
    plugin = registry.get_plugin("qr_code")
    assert plugin is not None
    assert plugin.plugin_type == "qr_code"
    assert plugin.display_name == "QR Code Generator"


def test_qr_code_properties(registry):
    plugin = registry.get_plugin("qr_code")
    assert plugin.plugin_category == "data_transform"
    assert "data" in plugin.config_schema["properties"]
    assert "data" in plugin.config_schema["required"]


def test_qr_code_generates_url(registry):
    plugin = registry.get_plugin("qr_code")

    context = {"url": "https://phish.example.com/c/abc123"}
    config = {"data": "https://target.example.com"}

    result = plugin.execute(context, config)

    media_url = result.get("_generated_media_url", "")
    assert media_url.startswith("https://phish.example.com/media/qr?d=")
    d_param = unquote(media_url.split("d=")[1].split("&")[0])
    assert base64.urlsafe_b64decode(d_param).decode() == "https://target.example.com"


def test_qr_code_variable_interpolation(registry):
    plugin = registry.get_plugin("qr_code")

    context = {
        "url": "https://phish.example.com/c/abc",
        "tracking_url": "https://track.example.com/u/123",
    }
    config = {"data": "{{tracking_url}}"}

    result = plugin.execute(context, config)
    d_param = unquote(result["_generated_media_url"].split("d=")[1].split("&")[0])
    assert base64.urlsafe_b64decode(d_param).decode() == "https://track.example.com/u/123"


def test_qr_code_custom_output_variable(registry):
    plugin = registry.get_plugin("qr_code")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"data": "test", "output_variable": "my_qr"}

    result = plugin.execute(context, config)
    assert "my_qr" in result
    assert "_generated_media_url" not in result


def test_qr_code_non_default_params_in_url(registry):
    plugin = registry.get_plugin("qr_code")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {
        "data": "test",
        "box_size": 20,
        "border": 2,
        "fill_color": "#FF0000",
        "bg_color": "#00FF00",
        "error_correction": "H",
    }

    result = plugin.execute(context, config)
    url = result["_generated_media_url"]
    assert "bs=20" in url
    assert "b=2" in url
    assert "fc=%23FF0000" in url
    assert "bg=%2300FF00" in url
    assert "ec=H" in url


def test_qr_code_default_params_omitted(registry):
    plugin = registry.get_plugin("qr_code")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"data": "test", "box_size": 10, "border": 4, "error_correction": "M"}

    result = plugin.execute(context, config)
    url = result["_generated_media_url"]
    assert "bs=" not in url
    assert "b=" not in url
    assert "ec=" not in url


def test_qr_code_empty_data_errors(registry):
    plugin = registry.get_plugin("qr_code")

    context = {"url": "https://phish.example.com/c/abc"}
    config = {"data": ""}

    result = plugin.execute(context, config)
    assert "_error" in result


def test_qr_code_validate_config(registry):
    plugin = registry.get_plugin("qr_code")

    assert plugin.validate_config({"data": "https://example.com"}) is None

    errors = plugin.validate_config({})
    assert errors is not None
    assert any("data" in e for e in errors)


def test_qr_code_validate_error_correction(registry):
    plugin = registry.get_plugin("qr_code")

    errors = plugin.validate_config({"data": "test", "error_correction": "Z"})
    assert errors is not None
    assert any("error_correction" in e for e in errors)

    for level in ("L", "M", "Q", "H"):
        assert plugin.validate_config({"data": "test", "error_correction": level}) is None


# ---------------------------------------------------------------------------
# Shared URL builders (shared/media_urls.py)
# ---------------------------------------------------------------------------

def test_get_phishing_base_url_from_context():
    from shared.media_urls import get_phishing_base_url

    ctx = {"url": "https://phish.example.com/c/abc123"}
    assert get_phishing_base_url(ctx) == "https://phish.example.com"


def test_get_phishing_base_url_fallback():
    from shared.media_urls import get_phishing_base_url

    # No url in context — falls back to config or hardcoded
    ctx = {}
    result = get_phishing_base_url(ctx)
    assert result.startswith("http")


def test_build_qr_url_defaults_omitted():
    from shared.media_urls import build_qr_url

    url = build_qr_url("https://example.com", "hello")
    assert url.startswith("https://example.com/media/qr?d=")
    assert "bs=" not in url
    assert "b=" not in url
    assert "ec=" not in url


def test_build_qr_url_all_params():
    from shared.media_urls import build_qr_url

    url = build_qr_url(
        "https://example.com", "hello",
        box_size=20, border=2, fill_color="#FF0000",
        bg_color="#00FF00", error_correction="H",
    )
    assert "bs=20" in url
    assert "b=2" in url
    assert "ec=H" in url


def test_build_qr_url_roundtrip():
    from shared.media_urls import build_qr_url

    url = build_qr_url("https://example.com", "https://target.com")
    d_param = unquote(url.split("d=")[1].split("&")[0])
    assert base64.urlsafe_b64decode(d_param).decode() == "https://target.com"


def test_build_text_image_url_defaults_omitted():
    from shared.media_urls import build_text_image_url

    url = build_text_image_url("https://example.com", "hello")
    assert url.startswith("https://example.com/media/text-image?t=")
    assert "fs=" not in url
    assert "w=" not in url
    assert "h=" not in url


def test_build_text_image_url_all_params():
    from shared.media_urls import build_text_image_url

    url = build_text_image_url(
        "https://example.com", "hello",
        font_size=100, width=800, height=400,
        bg_color="#FF0000", text_color="#00FF00",
    )
    assert "fs=100" in url
    assert "w=800" in url
    assert "h=400" in url


def test_build_text_image_url_roundtrip():
    from shared.media_urls import build_text_image_url

    url = build_text_image_url("https://example.com", "Code: 123456")
    t_param = unquote(url.split("t=")[1].split("&")[0])
    assert base64.urlsafe_b64decode(t_param).decode() == "Code: 123456"


# ---------------------------------------------------------------------------
# Jinja2 template helpers (render_template plugin integration)
# ---------------------------------------------------------------------------

def test_render_template_qr_code_helper(registry, app):
    with app.app_context():
        plugin = registry.get_plugin("render_template")
        context = {
            "url": "https://phish.example.com/c/abc",
            "campaign": {"template_html": '<img src="{{ qr_code("https://example.com") }}">'},
        }
        config = {"template_source": "campaign"}
        result = plugin.execute(context, config)
        html = result["_response_html"]
        assert "/media/qr?d=" in html
        # Verify the encoded data round-trips
        d_param = unquote(html.split("d=")[1].split('"')[0].split("&")[0])
        assert base64.urlsafe_b64decode(d_param).decode() == "https://example.com"


def test_render_template_text_image_helper(registry, app):
    with app.app_context():
        plugin = registry.get_plugin("render_template")
        context = {
            "url": "https://phish.example.com/c/abc",
            "campaign": {"template_html": '<img src="{{ text_image("CODE123") }}">'},
        }
        config = {"template_source": "campaign"}
        result = plugin.execute(context, config)
        html = result["_response_html"]
        assert "/media/text-image?t=" in html


def test_render_template_helper_with_context_variable(registry, app):
    with app.app_context():
        plugin = registry.get_plugin("render_template")
        context = {
            "url": "https://phish.example.com/c/abc",
            "tracking_url": "https://phish.example.com/t/xyz",
            "campaign": {"template_html": '<img src="{{ qr_code(tracking_url) }}">'},
        }
        config = {"template_source": "campaign"}
        result = plugin.execute(context, config)
        html = result["_response_html"]
        assert "/media/qr?d=" in html
        d_param = unquote(html.split("d=")[1].split('"')[0].split("&")[0])
        assert base64.urlsafe_b64decode(d_param).decode() == "https://phish.example.com/t/xyz"


def test_render_template_both_helpers(registry, app):
    with app.app_context():
        plugin = registry.get_plugin("render_template")
        context = {
            "url": "https://phish.example.com/c/abc",
            "device_code": "ABC123",
            "campaign": {
                "template_html": (
                    '<img src="{{ qr_code(url) }}">'
                    '<img src="{{ text_image(device_code, font_size=48) }}">'
                ),
            },
        }
        config = {"template_source": "campaign"}
        result = plugin.execute(context, config)
        html = result["_response_html"]
        assert "/media/qr?d=" in html
        assert "/media/text-image?t=" in html
        assert "fs=48" in html


def test_render_template_helpers_cleaned_from_context(registry, app):
    with app.app_context():
        plugin = registry.get_plugin("render_template")
        context = {
            "url": "https://phish.example.com/c/abc",
            "campaign": {"template_html": "<p>{{ qr_code('test') }}</p>"},
        }
        config = {"template_source": "campaign"}
        result = plugin.execute(context, config)
        assert "qr_code" not in result
        assert "text_image" not in result


# ---------------------------------------------------------------------------
# Media generator functions (shared/media_generator.py)
# ---------------------------------------------------------------------------

def test_generate_text_image_returns_png():
    from shared.media_generator import generate_text_image

    png = generate_text_image("Hello")
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_text_image_respects_dimensions():
    from shared.media_generator import generate_text_image
    from PIL import Image
    from io import BytesIO

    png = generate_text_image("Test", {"width": 300, "height": 100})
    img = Image.open(BytesIO(png))
    assert img.size == (300, 100)


def test_generate_text_image_clamps_dimensions():
    from shared.media_generator import generate_text_image
    from PIL import Image
    from io import BytesIO

    png = generate_text_image("Test", {"width": 9999, "height": 9999, "font_size": 9999})
    img = Image.open(BytesIO(png))
    assert img.size[0] <= 2000
    assert img.size[1] <= 2000


def test_generate_text_image_custom_colors():
    from shared.media_generator import generate_text_image

    # Should not raise with valid hex colors
    png = generate_text_image("Test", {"bg_color": "#FF0000", "text_color": "#00FF00"})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_qr_image_returns_png():
    from shared.media_generator import generate_qr_image

    png = generate_qr_image("https://example.com")
    assert isinstance(png, bytes)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_qr_image_custom_config():
    from shared.media_generator import generate_qr_image

    png = generate_qr_image("test", {
        "box_size": 5,
        "border": 2,
        "fill_color": "#0000FF",
        "bg_color": "#FFFFFF",
        "error_correction": "H",
    })
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_qr_image_all_ec_levels():
    from shared.media_generator import generate_qr_image

    for level in ("L", "M", "Q", "H"):
        png = generate_qr_image("test", {"error_correction": level})
        assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Flask route tests (phishing/routes.py media endpoints)
# ---------------------------------------------------------------------------

def test_text_image_route_returns_png(phishing_client):
    t = base64.urlsafe_b64encode(b"Hello").decode()
    resp = phishing_client.get(f"/media/text-image?t={t}")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"
    assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_text_image_route_missing_param(phishing_client):
    resp = phishing_client.get("/media/text-image")
    assert resp.status_code == 400


def test_text_image_route_bad_base64(phishing_client):
    resp = phishing_client.get("/media/text-image?t=!!!invalid!!!")
    assert resp.status_code == 400


def test_text_image_route_text_too_long(phishing_client):
    text = "x" * 501
    t = base64.urlsafe_b64encode(text.encode()).decode()
    resp = phishing_client.get(f"/media/text-image?t={t}")
    assert resp.status_code == 400


def test_text_image_route_bad_numeric_param(phishing_client):
    t = base64.urlsafe_b64encode(b"hi").decode()
    resp = phishing_client.get(f"/media/text-image?t={t}&fs=abc")
    assert resp.status_code == 400


def test_text_image_route_with_style_params(phishing_client):
    t = base64.urlsafe_b64encode(b"Styled").decode()
    resp = phishing_client.get(f"/media/text-image?t={t}&fs=80&w=400&h=150")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"


def test_qr_route_returns_png(phishing_client):
    d = base64.urlsafe_b64encode(b"https://example.com").decode()
    resp = phishing_client.get(f"/media/qr?d={d}")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"
    assert resp.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_route_missing_param(phishing_client):
    resp = phishing_client.get("/media/qr")
    assert resp.status_code == 400


def test_qr_route_bad_base64(phishing_client):
    resp = phishing_client.get("/media/qr?d=!!!invalid!!!")
    assert resp.status_code == 400


def test_qr_route_data_too_long(phishing_client):
    data = "x" * 2001
    d = base64.urlsafe_b64encode(data.encode()).decode()
    resp = phishing_client.get(f"/media/qr?d={d}")
    assert resp.status_code == 400


def test_qr_route_bad_numeric_param(phishing_client):
    d = base64.urlsafe_b64encode(b"test").decode()
    resp = phishing_client.get(f"/media/qr?d={d}&bs=notanumber")
    assert resp.status_code == 400


def test_qr_route_with_params(phishing_client):
    d = base64.urlsafe_b64encode(b"test").decode()
    resp = phishing_client.get(f"/media/qr?d={d}&bs=5&b=2&ec=H")
    assert resp.status_code == 200
    assert resp.content_type == "image/png"


def test_qr_route_invalid_ec_ignored(phishing_client):
    """Invalid error correction value should be silently ignored (uses default M)."""
    d = base64.urlsafe_b64encode(b"test").decode()
    resp = phishing_client.get(f"/media/qr?d={d}&ec=Z")
    assert resp.status_code == 200
