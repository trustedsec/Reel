"""
Twilio SMS sender plugin - sends SMS messages via Twilio API
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string, get_variable_value
from shared.media_urls import get_phishing_base_url
from shared.database import db, SmsJob
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Graceful import like aws_connect_dialer
try:
    from twilio.rest import Client as TwilioClient
    from twilio.base.exceptions import TwilioRestException
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    logger.warning("twilio not available. SMS sender plugin will not work.")


class TwilioSmsSenderPlugin(BasePlugin):
    """
    Plugin for sending SMS messages via Twilio.

    Sends text messages through Twilio's REST API to target phone numbers.
    Supports variable interpolation for personalized message content, per-target
    tracking via SmsJob records, and configurable sender IDs.

    Use cases:
    - Send SMS phishing messages with personalized landing page links
    - Deliver MFA/device codes via text message
    - SMS-based social engineering campaigns
    - Multi-target SMS notification workflows

    Requirements:
    - Twilio account with Account SID and Auth Token
    - Twilio phone number or messaging service SID
    - twilio Python library installed

    Example: Send personalized SMS to {{target.phone}} with message body
    containing {{target.first_name}} and campaign landing page {{url}}.
    """

    @property
    def plugin_type(self) -> str:
        return "sms_sender"

    @property
    def display_name(self) -> str:
        return "Twilio SMS Sender"

    @property
    def description(self) -> str:
        return (
            "Send SMS messages via Twilio to deliver phishing links, device codes, "
            "or custom messages. Supports variable interpolation for personalized "
            "content, per-target delivery tracking, and configurable sender phone "
            "numbers or messaging service SIDs. Requires Twilio account and twilio library."
        )

    @property
    def plugin_category(self) -> str:
        return "sending"

    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "account_sid": {
                    "type": "string",
                    "title": "Twilio Account SID",
                    "description": "Leave blank to use TWILIO_ACCOUNT_SID from .env",
                    "help": (
                        "Your Twilio Account SID (starts with 'AC'), found on the Twilio "
                        "Console dashboard. LEAVE BLANK to use the TWILIO_ACCOUNT_SID "
                        "environment variable from .env instead — recommended so credentials "
                        "aren't stored in the workflow config."
                    ),
                    "placeholder": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx (or set TWILIO_ACCOUNT_SID in .env)"
                },
                "auth_token": {
                    "type": "string",
                    "title": "Twilio Auth Token",
                    "description": "Leave blank to use TWILIO_AUTH_TOKEN from .env",
                    "help": (
                        "Your Twilio Auth Token, found on the Twilio Console dashboard. "
                        "LEAVE BLANK to use the TWILIO_AUTH_TOKEN environment variable "
                        "from .env instead — recommended so credentials aren't stored in "
                        "the workflow config."
                    ),
                    "placeholder": "your_auth_token (or set TWILIO_AUTH_TOKEN in .env)"
                },
                "from_phone": {
                    "type": "string",
                    "title": "From Phone Number",
                    "description": "Twilio phone number to send from (E.164 format)",
                    "help": (
                        "The Twilio phone number that will appear as the sender. "
                        "Must be in E.164 format (e.g., +12025551234). This number must "
                        "be purchased/verified in your Twilio account. Leave blank if "
                        "using a Messaging Service SID instead."
                    ),
                    "placeholder": "+12025551234"
                },
                "messaging_service_sid": {
                    "type": "string",
                    "title": "Messaging Service SID",
                    "description": "Twilio Messaging Service SID (alternative to from_phone)",
                    "help": (
                        "Optional Messaging Service SID for sender pool rotation and "
                        "compliance features. Starts with 'MG'. If provided, this is used "
                        "instead of from_phone. Useful for high-volume campaigns to avoid "
                        "carrier filtering."
                    ),
                    "placeholder": "MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                },
                "target_phone_field": {
                    "type": "string",
                    "title": "Target Phone Field",
                    "description": "Context variable name for the target phone number",
                    "help": (
                        "Dot-path to the context variable containing the target's phone "
                        "number. Supports nested paths (e.g., 'target.phone'). Phone "
                        "should be in E.164 format (+1XXXXXXXXXX). Default: 'target.phone'."
                    ),
                    "placeholder": "target.phone, phone, target.mobile",
                    "default": "target.phone"
                },
                "message_body": {
                    "type": "string",
                    "format": "textarea",
                    "title": "Message Body",
                    "description": "SMS message body text",
                    "help": (
                        "The SMS message content. Supports variable interpolation: "
                        "{{target.first_name}}, {{target.last_name}}, {{url}}, "
                        "{{tracking_url}}, {{device_code}}, etc. Standard SMS is "
                        "160 characters; longer messages are sent as multi-part. "
                        "Newlines in this field are preserved in the delivered message."
                    ),
                    "placeholder": "Hi {{target.first_name}}, verify your account: {{url}}"
                },
                "media_url": {
                    "type": "string",
                    "title": "Media URL (MMS)",
                    "description": "URL of an image to attach as MMS (optional)",
                    "help": (
                        "Public URL to an image (JPEG, PNG, GIF, max 5 MB) to send as "
                        "an MMS. Use for branded card images or visual content. Twilio "
                        "fetches the image at send time so the URL must be publicly "
                        "accessible. Supports variable interpolation. Leave blank for "
                        "plain SMS."
                    ),
                    "placeholder": "https://example.com/branded-card.png"
                },
                "mms_card_config_id": {
                    "type": "string",
                    "title": "MMS Card Template",
                    "description": "ID of a saved MMS branded card template (auto-generates media_url)",
                    "help": (
                        "If set, auto-constructs a personalized MMS card image URL per "
                        "target. Overrides media_url. Create templates via the MMS Cards page."
                    ),
                    "x-optionsSource": "mms-cards"
                },
                "status_callback_url": {
                    "type": "string",
                    "title": "Status Callback URL",
                    "description": "URL for Twilio delivery status webhooks (optional)",
                    "help": (
                        "Optional URL that Twilio will POST delivery status updates to "
                        "(queued, sent, delivered, failed, undelivered). Useful for "
                        "real-time delivery tracking. Must be publicly accessible."
                    ),
                    "placeholder": "https://example.com/sms/status"
                }
            },
            "required": []
        }

    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Twilio SMS sending"""
        if not TWILIO_AVAILABLE:
            logger.error("twilio is not available. Please install it: pip install twilio")
            context['_sms_result'] = {
                'success': False,
                'error': 'twilio not available',
                'message_sid': None
            }
            return self.on_error(Exception("twilio not available"), context, config)

        try:
            # Resolve target phone from context
            phone_field = config.get('target_phone_field', 'target.phone')
            target_phone = get_variable_value(context, phone_field)

            if not target_phone:
                raise ValueError(f"Target phone number not found in context at '{phone_field}'")

            # Validate E.164 format
            if not target_phone.startswith('+'):
                logger.warning(f"Phone '{target_phone}' missing '+' prefix. Expected E.164 format.")

            # Resolve message body with variable interpolation (optional for MMS-only)
            message_body = self._resolve_value(config.get('message_body', ''), context)

            # Get Twilio credentials (config or env vars)
            import os
            account_sid = config.get('account_sid') or os.environ.get('TWILIO_ACCOUNT_SID')
            auth_token = config.get('auth_token') or os.environ.get('TWILIO_AUTH_TOKEN')

            if not account_sid or not auth_token:
                raise ValueError(
                    "Twilio credentials required. Set account_sid/auth_token in config "
                    "or TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN environment variables."
                )

            # Build Twilio message kwargs
            client = TwilioClient(account_sid, auth_token)

            msg_kwargs: Dict[str, Any] = {'to': target_phone}
            if message_body.strip():
                msg_kwargs['body'] = message_body

            # Prefer messaging_service_sid over from_phone
            messaging_service_sid = config.get('messaging_service_sid', '').strip()
            from_phone = config.get('from_phone', '').strip()

            if messaging_service_sid:
                msg_kwargs['messaging_service_sid'] = messaging_service_sid
            elif from_phone:
                msg_kwargs['from_'] = from_phone
            else:
                raise ValueError("Either from_phone or messaging_service_sid is required")

            # Build target data for card rendering and job record
            campaign_id = context.get('campaign', {}).get('id')
            workflow_id = context.get('_workflow_id')
            card_target_data = {
                'fn': get_variable_value(context, 'target.first_name', ''),
                'ln': get_variable_value(context, 'target.last_name', ''),
                'em': get_variable_value(context, 'target.email', ''),
                'dc': context.get('device_code', ''),
                'url': context.get('url', ''),
            }

            # Optional MMS media attachment
            media_url = config.get('media_url', '').strip()

            # Auto-generate MMS card URL if mms_card_config_id is set and no explicit media_url
            mms_card_config_id = config.get('mms_card_config_id', '').strip()
            render_uuid = None
            if mms_card_config_id and not media_url and campaign_id:
                import uuid
                render_uuid = str(uuid.uuid4())

                # Create SmsJob BEFORE sending so the card route can look up target data
                sms_job = SmsJob(
                    render_uuid=render_uuid,
                    mms_card_config_id=mms_card_config_id,
                    workflow_id=workflow_id,
                    campaign_id=campaign_id,
                    target_phone=target_phone,
                    target_data={k: v for k, v in card_target_data.items() if v},
                    message_content={
                        'body': message_body,
                        'from': from_phone or messaging_service_sid,
                    },
                    status='pending',
                )
                db.session.add(sms_job)
                db.session.commit()

                # Prefer the campaign's custom_domain (set when MMS is enabled) for the
                # public base URL Twilio will fetch from. Fallback to the standard logic.
                campaign_dict = context.get('campaign', {}) or {}
                custom_domain = campaign_dict.get('custom_domain')
                if custom_domain:
                    base_url = f"https://{custom_domain}"
                else:
                    base_url = get_phishing_base_url(context)
                media_url = f"{base_url}/m/{render_uuid}"

            if media_url:
                msg_kwargs['media_url'] = [self._resolve_value(media_url, context)]

            # Optional status callback
            status_callback = config.get('status_callback_url', '').strip()
            if status_callback:
                msg_kwargs['status_callback'] = self._resolve_value(status_callback, context)

            # Send SMS
            message = client.messages.create(**msg_kwargs)

            # Update existing job or create new one
            try:
                if render_uuid:
                    # Job was already created above — update with send result
                    sms_job.status = 'sent'
                    sms_job.message_sid = message.sid
                    sms_job.sent_at = datetime.utcnow()
                    db.session.commit()
                    logger.info(f"SMS job updated: {sms_job.id} for {target_phone}")
                elif campaign_id:
                    sms_job = SmsJob(
                        workflow_id=workflow_id,
                        campaign_id=campaign_id,
                        target_phone=target_phone,
                        target_data={k: v for k, v in card_target_data.items() if v},
                        message_content={
                            'body': message_body,
                            'from': from_phone or messaging_service_sid,
                        },
                        status='sent',
                        message_sid=message.sid,
                        sent_at=datetime.utcnow(),
                    )
                    db.session.add(sms_job)
                    db.session.commit()
                    logger.info(f"SMS job created: {sms_job.id} for {target_phone}")
                else:
                    logger.warning(f"No campaign_id in context, skipping SmsJob creation for {target_phone}")
            except Exception as db_error:
                logger.error(f"Failed to update/create SmsJob record: {db_error}", exc_info=True)
                db.session.rollback()

            # Store result in context
            context['_sms_result'] = {
                'success': True,
                'message_sid': message.sid,
                'to': target_phone,
                'status': message.status,
                'error': None,
            }

            logger.info(f"SMS sent to {target_phone}: SID={message.sid}")

        except TwilioRestException as e:
            logger.error(f"Twilio API error: {e}")
            self._record_failed_job(context, config, target_phone if 'target_phone' in dir() else '', str(e))
            context['_sms_result'] = {
                'success': False,
                'error': str(e),
                'message_sid': None,
            }
            return self.on_error(e, context, config)

        except Exception as e:
            logger.error(f"SMS sending failed: {e}", exc_info=True)
            phone = get_variable_value(context, config.get('target_phone_field', 'target.phone'), '')
            self._record_failed_job(context, config, phone or '', str(e))
            context['_sms_result'] = {
                'success': False,
                'error': str(e),
                'message_sid': None,
            }
            return self.on_error(e, context, config)

        return context

    def _resolve_value(self, value: str, context: Dict[str, Any]) -> str:
        """Resolve template variables in value."""
        if not isinstance(value, str):
            return str(value)
        return interpolate_string(value, context)

    def _record_failed_job(self, context: Dict[str, Any], config: Dict[str, Any], target_phone: str, error_msg: str):
        """Record a failed SmsJob in the database."""
        try:
            campaign_id = context.get('campaign', {}).get('id')
            workflow_id = context.get('_workflow_id')
            if campaign_id and target_phone:
                sms_job = SmsJob(
                    workflow_id=workflow_id,
                    campaign_id=campaign_id,
                    target_phone=target_phone,
                    target_data={
                        'first_name': get_variable_value(context, 'target.first_name', ''),
                        'last_name': get_variable_value(context, 'target.last_name', ''),
                    },
                    message_content={
                        'body': config.get('message_body', ''),
                    },
                    status='failed',
                    error_message=error_msg,
                    completed_at=datetime.utcnow(),
                )
                db.session.add(sms_job)
                db.session.commit()
        except Exception as db_error:
            logger.error(f"Failed to create SmsJob record for failure: {db_error}", exc_info=True)
            db.session.rollback()

    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate Twilio SMS configuration"""
        errors = []

        if not TWILIO_AVAILABLE:
            errors.append("twilio is not installed. Please install it: pip install twilio")

        # Require either message_body or some media (media_url / mms_card_config_id)
        if not config.get('message_body') and not config.get('media_url') and not config.get('mms_card_config_id'):
            errors.append("message_body, media_url, or mms_card_config_id is required")

        from_phone = config.get('from_phone', '').strip()
        messaging_service_sid = config.get('messaging_service_sid', '').strip()

        if not from_phone and not messaging_service_sid:
            import os
            # Allow if env vars might provide credentials at runtime
            if not os.environ.get('TWILIO_ACCOUNT_SID'):
                errors.append("Either from_phone or messaging_service_sid is required")

        if from_phone and not from_phone.startswith('+'):
            errors.append("from_phone must be in E.164 format (start with +)")

        if messaging_service_sid and not messaging_service_sid.startswith('MG'):
            errors.append("messaging_service_sid must start with 'MG'")

        return errors if errors else None
