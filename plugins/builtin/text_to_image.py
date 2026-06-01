"""
Text-to-Image plugin — renders text as a PNG served via on-demand Flask route.

Does NOT generate the image at execute time.  Instead it constructs a URL
(``/media/text-image?t=…``) that the downstream SMS plugin sends as MMS
``media_url``.  When Twilio (or a browser) fetches that URL, the Flask route
in ``phishing/routes.py`` generates and caches the PNG.
"""
import logging
from typing import Dict, Any, Optional, List

from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
from shared.media_urls import get_phishing_base_url, build_text_image_url

logger = logging.getLogger(__name__)


class TextToImagePlugin(BasePlugin):
    """
    Plugin that converts text into a publicly-accessible image URL.

    The URL points to a Flask route that renders the text as a PNG on first
    fetch and caches the result.  Designed to slot before the SMS sender
    plugin so ``_generated_media_url`` (or a custom output variable) is
    available for MMS attachment.

    Use cases:
    - Render an MFA / device code as a branded image for MMS delivery
    - Generate a personalised text badge per target
    - Any scenario where plain-text SMS isn't sufficient and a visual is needed
    """

    @property
    def plugin_type(self) -> str:
        return "text_to_image"

    @property
    def display_name(self) -> str:
        return "Text to Image"

    @property
    def description(self) -> str:
        return (
            "Render text (e.g. an MFA code or personalised message) as a PNG image "
            "URL suitable for MMS delivery. Supports variable interpolation and "
            "configurable font size, dimensions, and colors. Place before the SMS "
            "sender plugin in the workflow."
        )

    @property
    def plugin_category(self) -> str:
        return "data_transform"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "title": "Text",
                    "description": "Text to render as an image (supports {{variable}} interpolation)",
                    "help": (
                        "The text content that will be rendered onto the PNG image. "
                        "Supports variable interpolation: {{device_code}}, "
                        "{{target.first_name}}, etc. Max 500 characters after "
                        "interpolation."
                    ),
                    "placeholder": "{{device_code}}"
                },
                "font_size": {
                    "type": "integer",
                    "title": "Font Size",
                    "description": "Font size in pixels (default 64, max 200)",
                    "help": "Pixel size of the rendered text. Larger values make the text more prominent.",
                    "default": 64
                },
                "width": {
                    "type": "integer",
                    "title": "Image Width",
                    "description": "Image width in pixels (default 600, max 2000)",
                    "help": "Width of the generated PNG in pixels.",
                    "default": 600
                },
                "height": {
                    "type": "integer",
                    "title": "Image Height",
                    "description": "Image height in pixels (default 200, max 2000)",
                    "help": "Height of the generated PNG in pixels.",
                    "default": 200
                },
                "bg_color": {
                    "type": "string",
                    "title": "Background Color",
                    "description": "Background color as hex (default #FFFFFF)",
                    "help": "Hex color code for the image background.",
                    "default": "#FFFFFF",
                    "placeholder": "#FFFFFF"
                },
                "text_color": {
                    "type": "string",
                    "title": "Text Color",
                    "description": "Text color as hex (default #000000)",
                    "help": "Hex color code for the rendered text.",
                    "default": "#000000",
                    "placeholder": "#000000"
                },
                "output_variable": {
                    "type": "string",
                    "title": "Output Variable",
                    "description": "Context variable to store the generated image URL",
                    "help": (
                        "The context variable where the image URL will be stored. "
                        "The SMS sender plugin can reference this via "
                        "{{_generated_media_url}} in its media_url field."
                    ),
                    "default": "_generated_media_url",
                    "placeholder": "_generated_media_url"
                }
            },
            "required": ["text"]
        }

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Interpolate text variables
            raw_text = config.get('text', '')
            text = interpolate_string(raw_text, context)

            if not text.strip():
                raise ValueError("Text is empty after variable interpolation")

            # Build media URL via shared builder
            base_url = get_phishing_base_url(context)
            media_url = build_text_image_url(
                base_url, text,
                font_size=config.get('font_size', 64),
                width=config.get('width', 600),
                height=config.get('height', 200),
                bg_color=config.get('bg_color', '#FFFFFF'),
                text_color=config.get('text_color', '#000000'),
            )

            # Store in context
            output_var = config.get('output_variable', '_generated_media_url')
            context[output_var] = media_url

            logger.info(f"Text-to-image URL generated: {media_url[:80]}…")
            return context

        except Exception as e:
            logger.error(f"Text-to-image plugin failed: {e}", exc_info=True)
            return self.on_error(e, context, config)

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        errors = []
        if not config.get('text'):
            errors.append("text is required")
        fs = config.get('font_size')
        if fs is not None and (int(fs) < 1 or int(fs) > 200):
            errors.append("font_size must be between 1 and 200")
        w = config.get('width')
        if w is not None and (int(w) < 1 or int(w) > 2000):
            errors.append("width must be between 1 and 2000")
        h = config.get('height')
        if h is not None and (int(h) < 1 or int(h) > 2000):
            errors.append("height must be between 1 and 2000")
        return errors if errors else None
