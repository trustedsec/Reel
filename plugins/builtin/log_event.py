"""
Log event plugin - logs events to database
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.database import db, Event
from shared.workflow_variables import interpolate_string, interpolate_dict
import logging
import json

logger = logging.getLogger(__name__)


class LogEventPlugin(BasePlugin):
    """
    Plugin for logging custom events to the database for tracking and analysis.
    
    Creates event records in the database with custom event types and data.
    Can include request data (IP, user agent, method, path) and session data
    for comprehensive tracking. Events are stored with timestamps and can be
    queried later for analysis.
    
    Use cases:
    - Log custom workflow events
    - Track user actions and interactions
    - Record important milestones in workflows
    - Create audit trails
    - Analytics and reporting
    
    Example: Log 'form_submitted' event after form processing, or log
    'workflow_completed' event at the end of a workflow with custom data.
    """
    
    @property
    def plugin_type(self) -> str:
        return "log_event"
    
    @property
    def display_name(self) -> str:
        return "Log Event"
    
    @property
    def description(self) -> str:
        return "Log custom events to the database for tracking and analysis. Supports custom event types, data payloads, and automatic inclusion of request/session data. Use to create audit trails, track user actions, record milestones, and enable analytics."
    
    @property
    def plugin_category(self) -> str:
        return "logging"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "title": "Event Type",
                    "description": "Type/category of event to log",
                    "help": "Event type identifier for categorizing events. Supports variable interpolation: {{target.email}}, {{phishing_detection.confidence}}, etc. Common types: 'custom', 'form_submitted', 'workflow_completed', 'user_action', 'error', 'milestone'. Default: 'custom'.",
                    "placeholder": "custom, form_submitted, workflow_completed, BERT",
                    "default": "custom"
                },
                "event_data": {
                    "type": "object",
                    "title": "Event Data",
                    "description": "Additional custom data to store with event",
                    "help": "Custom data object to store with the event. Supports variable interpolation: {{target.email}}, {{target.first_name}}, {{phishing_detection.confidence}}, {{phishing_detection.is_phishing}}, {{campaign.name}}, etc. Example: {\"target\": \"{{target.email}}\", \"confidence\": \"{{phishing_detection.confidence}}\"}.",
                    "placeholder": "{\"target\": \"{{target.email}}\", \"confidence\": \"{{phishing_detection.confidence}}\"}"
                },
                "include_request_data": {
                    "type": "boolean",
                    "title": "Include Request Data",
                    "description": "Automatically include request data (IP, user agent, method, path) in event",
                    "help": "When enabled, automatically includes request information (HTTP method, path, query params, IP address, user agent) in the event data. Useful for tracking request context. Default: true.",
                    "default": True
                },
                "include_session_data": {
                    "type": "boolean",
                    "title": "Include Session Data",
                    "description": "Automatically include session data in event",
                    "help": "When enabled, automatically includes session information (session ID, session variables) in the event data. Useful for tracking user sessions across requests. Default: false.",
                    "default": False
                }
            },
            "required": ["event_type"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute event logging"""
        try:
            campaign_id = context.get('campaign', {}).get('id')
            if not campaign_id:
                logger.warning("No campaign ID in context, cannot log event")
                return context
            
            event_type = config.get('event_type', 'custom')
            raw_event_data = config.get('event_data', {})
            # Normalize event_data to a dict (config may store it as a string if JSON parse failed in UI)
            if isinstance(raw_event_data, dict):
                event_data = dict(raw_event_data)
            elif isinstance(raw_event_data, str) and raw_event_data.strip():
                try:
                    event_data = json.loads(raw_event_data)
                    if not isinstance(event_data, dict):
                        event_data = {"_raw": raw_event_data[:500]}
                except (ValueError, TypeError):
                    event_data = {"_raw": raw_event_data[:500]}
            else:
                event_data = {}
            
            # Build event data
            if config.get('include_request_data', True):
                request_data = context.get('request', {})
                event_data['request'] = {
                    'method': request_data.get('method'),
                    'path': request_data.get('path'),
                    'query_params': request_data.get('query_params'),
                    'ip_address': request_data.get('ip_address'),
                    'user_agent': request_data.get('user_agent')
                }
            
            if config.get('include_session_data', False):
                event_data['session'] = context.get('session', {})
            
            # Interpolate template variables in event_type and event_data
            event_type = interpolate_string(str(event_type), context)
            event_data = interpolate_dict(event_data, context)
            
            # Create event
            event = Event(
                campaign_id=campaign_id,
                event_type=event_type,
                data=event_data,
                ip_address=context.get('request', {}).get('ip_address'),
                user_agent=context.get('request', {}).get('user_agent'),
                session_id=context.get('session', {}).get('session_id')
            )
            
            db.session.add(event)
            db.session.commit()
            
            logger.info(f"Event logged: {event_type} for campaign {campaign_id}")
            
            # Add event ID to context
            context['last_event_id'] = event.id
            context['last_event_type'] = event_type
            
            return context
            
        except Exception as e:
            logger.error(f"Event logging failed: {e}")
            db.session.rollback()
            return self.on_error(e, context, config)
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate event logging configuration"""
        errors = []
        
        if not config.get('event_type'):
            errors.append("event_type is required")
        
        return errors if errors else None




