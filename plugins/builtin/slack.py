"""
Slack plugin - sends simple messages to Slack via webhook or bot (chat.postMessage)
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import requests
import logging

logger = logging.getLogger(__name__)

class SlackPlugin(BasePlugin):
    """
    Plugin for sending simple text messages to Slack.

    Supports two modes:
    - Incoming webhook: POST to a Slack incoming webhook URL (no auth).
    - Bot: Call Slack API chat.postMessage with a Bot User OAuth Token and channel ID.

    Sends plain text notifications to Slack channels. Supports variable
    interpolation in messages for dynamic content like captured credentials,
    user data, or workflow status.

    Use cases:
    - Send notifications when credentials are captured
    - Alert on workflow events
    - Report campaign activity
    - Send real-time updates to team channels

    Requirements:
    - Webhook mode: Slack incoming webhook URL (create at api.slack.com/apps).
    - Bot mode: Bot User OAuth Token (xoxb-...) and Slack channel ID.

    Example: Send "Credentials captured: {{captured_credentials.username}}"
    to Slack when credentials are captured in a workflow.
    """

    @property
    def plugin_type(self) -> str:
        return "slack"

    @property
    def display_name(self) -> str:
        return "Send Slack Message"

    @property
    def description(self) -> str:
        return "Send text messages to Slack via incoming webhook or bot (chat.postMessage). Supports variable interpolation. Use to notify channels about captured credentials, workflow events, or campaign activity."
    
    @property
    def plugin_category(self) -> str:
        return "notification"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "send_via": {
                    "type": "string",
                    "title": "Send via",
                    "description": "How to send the message: webhook (incoming webhook URL) or bot (Slack API with Bot Token)",
                    "help": "Webhook: POST to an incoming webhook URL (no auth). Bot: call Slack API chat.postMessage with a Bot User OAuth Token and channel ID. Use webhook for simple one-channel setup; use bot when you need to post to different channels or use a bot identity.",
                    "enum": [
                        {"value": "webhook", "label": "Incoming Webhook", "description": "POST to a Slack incoming webhook URL"},
                        {"value": "bot", "label": "Bot (chat.postMessage)", "description": "Slack API with Bot Token and channel ID"}
                    ],
                    "default": "webhook"
                },
                "webhook_url": {
                    "type": "string",
                    "title": "Slack Webhook URL",
                    "format": "uri",
                    "description": "Slack incoming webhook URL",
                    "help": "Slack incoming webhook URL from your Slack app. Create at https://api.slack.com/apps by creating an app, enabling Incoming Webhooks, and adding a webhook to your workspace. Format: https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
                    "placeholder": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
                },
                "bot_token": {
                    "type": "string",
                    "title": "Bot Token",
                    "description": "Bot User OAuth Token for Slack API",
                    "help": "Bot User OAuth Token (starts with xoxb-). Create at https://api.slack.com/apps: create an app, install to workspace, use OAuth & Permissions to copy the Bot User OAuth Token. Required when Send via is Bot.",
                    "placeholder": "xoxb-..."
                },
                "channel_id": {
                    "type": "string",
                    "title": "Channel ID",
                    "description": "Slack channel ID to post to",
                    "help": "Slack channel ID (e.g. C01234567). Find it by right-clicking the channel in Slack and copying the link, or use the Slack API. Required when Send via is Bot.",
                    "placeholder": "C01234567"
                },
                "message": {
                    "type": "string",
                    "title": "Message Text",
                    "description": "Message to send to Slack (supports variable interpolation)",
                    "help": "Message text to send to Slack channel. Supports variable interpolation using {{variable}} syntax. Common variables: {{captured_credentials.username}}, {{captured_credentials.password}}, {{target.email}}, {{target.name}}, etc. Example: 'Credentials captured: {{captured_credentials.username}} / {{captured_credentials.password}}'",
                    "placeholder": "Credentials captured: {{captured_credentials.username}}, Workflow completed for {{target.email}}"
                }
            },
            "required": ["message"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Slack plugin"""
        send_via = config.get('send_via', 'webhook')
        message_template = config.get('message', '')
        if not message_template:
            raise ValueError("message is required")

        message = interpolate_string(message_template, context)

        try:
            if send_via == 'bot':
                bot_token = config.get('bot_token', '').strip()
                channel_id = (config.get('channel_id') or '').strip()
                if not bot_token:
                    raise ValueError("bot_token is required when sending via bot")
                if not channel_id:
                    raise ValueError("channel_id is required when sending via bot")
                data = {"channel": channel_id, "text": message}
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {bot_token}",
                }
                response = requests.post(
                    "https://slack.com/api/chat.postMessage",
                    json=data,
                    headers=headers,
                    timeout=10,
                )
                response.raise_for_status()
                result = response.json()
                if not result.get("ok"):
                    err = result.get("error", "unknown")
                    raise ValueError(f"Slack API error: {err}")
                context['_slack_sent'] = {
                    'send_via': 'bot',
                    'message': message,
                    'success': True,
                    'channel': channel_id,
                }
                logger.info(f"Slack message sent via bot to channel {channel_id}")
            else:
                webhook_url = config.get('webhook_url')
                if not webhook_url:
                    raise ValueError("webhook_url is required when sending via webhook")
                payload = {"text": message}
                response = requests.post(webhook_url, json=payload, timeout=10)
                response.raise_for_status()
                context['_slack_sent'] = {
                    'send_via': 'webhook',
                    'webhook_url': webhook_url,
                    'message': message,
                    'success': True,
                    'status_code': response.status_code,
                }
                logger.info(f"Slack message sent successfully to {webhook_url}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Slack message: {e}")
            context['_slack_sent'] = {
                'send_via': send_via,
                'success': False,
                'error': str(e),
            }
            return self.on_error(e, context, config)
        except Exception as e:
            logger.error(f"Slack plugin execution failed: {e}")
            context['_slack_sent'] = {
                'send_via': send_via,
                'success': False,
                'error': str(e),
            }
            return self.on_error(e, context, config)

        return context
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate Slack configuration"""
        errors = []
        if not config.get('message'):
            errors.append("message is required")

        send_via = config.get('send_via', 'webhook')
        if send_via == 'webhook':
            if not config.get('webhook_url'):
                errors.append("webhook_url is required when sending via webhook")
            else:
                url = config['webhook_url']
                if not (url.startswith('http://') or url.startswith('https://')):
                    errors.append("webhook_url must be a valid HTTP/HTTPS URL")
                elif 'hooks.slack.com' not in url:
                    errors.append("webhook_url should be a Slack webhook URL (hooks.slack.com)")
        elif send_via == 'bot':
            if not (config.get('bot_token') or '').strip():
                errors.append("bot_token is required when sending via bot")
            if not (config.get('channel_id') or '').strip():
                errors.append("channel_id is required when sending via bot")

        return errors if errors else None
