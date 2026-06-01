"""
Pushover plugin - sends notifications via Pushover API
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class PushoverPlugin(BasePlugin):
    """
    Plugin for sending push notifications via Pushover API.
    
    Sends notifications to iOS, Android, and desktop devices using Pushover.
    Use for real-time alerts, credential capture notifications, and workflow events.
    
    Requirements:
    - Pushover account (pushover.net)
    - Application API token (create at pushover.net/apps)
    - User key from Pushover account settings
    """
    
    @property
    def plugin_type(self) -> str:
        return "pushover"
    
    @property
    def display_name(self) -> str:
        return "Send Pushover"
    
    @property
    def description(self) -> str:
        return "Send push notifications to iOS, Android, and desktop via Pushover API. Use for real-time alerts, credential capture notifications, and workflow events. Requires Pushover account and API token."
    
    @property
    def plugin_category(self) -> str:
        return "notification"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "api_token": {
                    "type": "string",
                    "title": "Pushover API Token",
                    "description": "Pushover application API token",
                    "help": "Your Pushover application API token. Create an application at https://pushover.net/apps to get a token. This identifies your application to Pushover.",
                    "placeholder": "azGDORePK8gMaC0QOYAMyEEuzJnyUi"
                },
                "user_key": {
                    "type": "string",
                    "title": "Pushover User Key",
                    "description": "Pushover user or group key",
                    "help": "Your Pushover user key or group key. Find this in your Pushover account settings at https://pushover.net/. This identifies who receives the notification.",
                    "placeholder": "uQiRzpo4DXghDmr9QzzfQu27cmVRsG"
                },
                "message": {
                    "type": "string",
                    "title": "Message Text",
                    "description": "Notification message (supports variable interpolation)",
                    "help": "Message text to send in the notification. Supports variable interpolation using {{variable}} syntax. Example: 'Credentials captured: {{captured_credentials.username}}' or 'Workflow completed for {{target.email}}'.",
                    "placeholder": "Credentials captured: {{captured_credentials.username}}, Alert: {{event.message}}"
                },
                "title": {
                    "type": "string",
                    "title": "Notification Title",
                    "description": "Title for the notification",
                    "help": "Title/heading for the notification. Supports variable interpolation. Default: 'Reel Notification'. Example: 'Credential Alert', '{{campaign.name}} Notification'.",
                    "placeholder": "Credential Alert, {{campaign.name}} Notification",
                    "default": "Reel Notification"
                }
            },
            "required": ["api_token", "user_key", "message"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Pushover plugin"""
        try:
            api_token = config.get('api_token')
            user_key = config.get('user_key')
            message_template = config.get('message', '')
            title_template = config.get('title', 'Reel Notification')
            
            if not api_token:
                raise ValueError("api_token is required")
            if not user_key:
                raise ValueError("user_key is required")
            if not message_template:
                raise ValueError("message is required")
            
            # Interpolate template variables in message and title
            message = interpolate_string(message_template, context)
            title = interpolate_string(title_template, context)
            
            # Prepare Pushover API payload
            data = {
                "token": api_token,
                "user": user_key,
                "title": title,
                "message": message,
                "timestamp": int(datetime.utcnow().timestamp())
            }
            
            # Send to Pushover API
            api_url = "https://api.pushover.net/1/messages.json"
            response = requests.post(api_url, data=data, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            
            # Store result in context
            context['_pushover_sent'] = {
                'title': title,
                'message': message,
                'success': result.get('status') == 1,
                'request_id': result.get('request')
            }
            
            if result.get('status') == 1:
                logger.info(f"Pushover notification sent successfully: {title}")
            else:
                logger.warning(f"Pushover notification may have failed: {result}")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send Pushover notification: {e}")
            context['_pushover_sent'] = {
                'success': False,
                'error': str(e)
            }
            return self.on_error(e, context, config)
        except Exception as e:
            logger.error(f"Pushover plugin execution failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate Pushover configuration"""
        errors = []
        
        if not config.get('api_token'):
            errors.append("api_token is required")
        
        if not config.get('user_key'):
            errors.append("user_key is required")
        
        if not config.get('message'):
            errors.append("message is required")
        
        return errors if errors else None
