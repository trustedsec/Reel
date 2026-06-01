"""
QR Code plugin — encodes data as a QR code PNG served via on-demand Flask route.

Same deferred-generation pattern as TextToImagePlugin: constructs a URL
(``/media/qr?d=…``) that the SMS plugin sends as MMS media_url.  The Flask
route renders and caches the QR image when Twilio fetches it.
"""
import logging
from typing import Dict, Any, Optional, List

from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
from shared.media_urls import get_phishing_base_url, build_qr_url

logger = logging.getLogger(__name__)


class QrCodePlugin(BasePlugin):
    """
    Plugin that encodes data into a QR code image URL.

    The URL points to a Flask route that generates the QR PNG on first fetch
    and caches it.  Place before the SMS sender plugin so
    ``_generated_media_url`` is available for MMS attachment.

    Use cases:
    - Encode a campaign landing page URL as a QR code for MMS delivery
    - Generate per-target QR codes containing unique tracking URLs
    - Deliver scannable codes for device enrollment or MFA flows
    """

    @property
    def plugin_type(self) -> str:
        return "qr_code"

    @property
    def display_name(self) -> str:
        return "QR Code Generator"

    @property
    def description(self) -> str:
        return (
            "Encode data (URL, text, etc.) as a QR code image URL suitable for "
            "MMS delivery. Supports variable interpolation, configurable size, "
            "colors, and error correction level. Place before the SMS sender "
            "plugin in the workflow."
        )

    @property
    def plugin_category(self) -> str:
        return "data_transform"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "data": {
                    "type": "string",
                    "title": "Data",
                    "description": "Data to encode as QR code (supports {{variable}} interpolation)",
                    "help": (
                        "The content encoded into the QR code. Typically a URL "
                        "like {{url}} or {{tracking_url}}, but can be any text. "
                        "Supports variable interpolation. Max 2000 characters "
                        "after interpolation."
                    ),
                    "placeholder": "{{url}}"
                },
                "box_size": {
                    "type": "integer",
                    "title": "Module Size",
                    "description": "Size of each QR module in pixels (default 10)",
                    "help": "Pixel size of each black/white square in the QR code.",
                    "default": 10
                },
                "border": {
                    "type": "integer",
                    "title": "Border",
                    "description": "Quiet zone width in modules (default 4)",
                    "help": "Width of the white border around the QR code, in modules.",
                    "default": 4
                },
                "fill_color": {
                    "type": "string",
                    "title": "QR Color",
                    "description": "Color of QR modules as hex (default #000000)",
                    "help": "Hex color for the dark modules of the QR code.",
                    "default": "#000000",
                    "placeholder": "#000000"
                },
                "bg_color": {
                    "type": "string",
                    "title": "Background Color",
                    "description": "Background color as hex (default #FFFFFF)",
                    "help": "Hex color for the background and light modules.",
                    "default": "#FFFFFF",
                    "placeholder": "#FFFFFF"
                },
                "error_correction": {
                    "type": "string",
                    "title": "Error Correction",
                    "description": "QR error correction level",
                    "help": (
                        "Error correction level: L (~7%), M (~15%), Q (~25%), "
                        "H (~30%). Higher levels produce larger codes but can "
                        "tolerate more damage/obstruction."
                    ),
                    "enum": [
                        {"value": "L", "label": "Low (7%)", "description": "~7% error correction, smallest code"},
                        {"value": "M", "label": "Medium (15%)", "description": "~15% error correction (default)"},
                        {"value": "Q", "label": "Quartile (25%)", "description": "~25% error correction"},
                        {"value": "H", "label": "High (30%)", "description": "~30% error correction, largest code"}
                    ],
                    "default": "M"
                },
                "output_variable": {
                    "type": "string",
                    "title": "Output Variable",
                    "description": "Context variable to store the generated QR image URL",
                    "help": (
                        "The context variable where the QR image URL will be stored. "
                        "The SMS sender plugin can reference this via "
                        "{{_generated_media_url}} in its media_url field."
                    ),
                    "default": "_generated_media_url",
                    "placeholder": "_generated_media_url"
                }
            },
            "required": ["data"]
        }

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # Interpolate data variables
            raw_data = config.get('data', '')
            data = interpolate_string(raw_data, context)

            if not data.strip():
                raise ValueError("Data is empty after variable interpolation")

            # Build media URL via shared builder
            base_url = get_phishing_base_url(context)
            media_url = build_qr_url(
                base_url, data,
                box_size=config.get('box_size', 10),
                border=config.get('border', 4),
                fill_color=config.get('fill_color', '#000000'),
                bg_color=config.get('bg_color', '#FFFFFF'),
                error_correction=config.get('error_correction', 'M'),
            )

            # Store in context
            output_var = config.get('output_variable', '_generated_media_url')
            context[output_var] = media_url

            logger.info(f"QR code URL generated: {media_url[:80]}…")
            return context

        except Exception as e:
            logger.error(f"QR code plugin failed: {e}", exc_info=True)
            return self.on_error(e, context, config)

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        errors = []
        if not config.get('data'):
            errors.append("data is required")
        ec = config.get('error_correction', 'M')
        if ec not in ('L', 'M', 'Q', 'H'):
            errors.append("error_correction must be one of: L, M, Q, H")
        return errors if errors else None
