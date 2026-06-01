"""
Redirect plugin - performs HTTP redirects
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import logging

logger = logging.getLogger(__name__)

class RedirectPlugin(BasePlugin):
    """
    Plugin for performing HTTP redirects with configurable status codes.
    
    Redirects users to different URLs with support for various HTTP redirect
    status codes (301, 302, 303, 307, 308). Supports variable interpolation
    in URLs and optional conditional redirects based on context variables.
    
    Use cases:
    - Post-action redirects after form submission
    - Conditional navigation based on user state
    - Multi-step flows with redirects between steps
    - Redirect to external services
    - Dynamic redirects based on captured data
    
    Example: Redirect to {{target.landing_page}} after credentials are captured,
    or conditionally redirect based on validation results.
    """
    
    @property
    def plugin_type(self) -> str:
        return "redirect"
    
    @property
    def display_name(self) -> str:
        return "Redirect"
    
    @property
    def description(self) -> str:
        return "Perform HTTP redirects to specified URLs with configurable status codes (301, 302, 303, 307, 308). Supports variable interpolation and conditional redirects. Use for post-action navigation, multi-step flows, and dynamic redirects based on workflow context."
    
    @property
    def plugin_category(self) -> str:
        return "navigation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "title": "Redirect URL",
                    "description": "URL to redirect to (supports variable interpolation)",
                    "help": "The destination URL for the redirect. Supports variable interpolation using {{variable}} syntax. Can be absolute URL (https://example.com) or relative path (/dashboard). Example: https://login.example.com/dashboard or {{target.landing_page}}",
                    "placeholder": "https://example.com/dashboard, {{target.landing_page}}, /success"
                },
                "status_code": {
                    "type": "integer",
                    "title": "HTTP Status Code",
                    "enum": [
                        {
                            "value": 301,
                            "label": "301 Moved Permanently",
                            "description": "Permanent redirect. Search engines update links. Use when URL has permanently moved."
                        },
                        {
                            "value": 302,
                            "label": "302 Found (Temporary)",
                            "description": "Temporary redirect. Most common for post-action redirects. Browsers may change POST to GET."
                        },
                        {
                            "value": 303,
                            "label": "303 See Other",
                            "description": "Temporary redirect that always changes method to GET. Use after POST requests to prevent resubmission."
                        },
                        {
                            "value": 307,
                            "label": "307 Temporary Redirect",
                            "description": "Temporary redirect that preserves HTTP method. Use when you want to keep POST as POST."
                        },
                        {
                            "value": 308,
                            "label": "308 Permanent Redirect",
                            "description": "Permanent redirect that preserves HTTP method. Use for permanent moves that keep method."
                        }
                    ],
                    "description": "HTTP redirect status code",
                    "help": "HTTP status code for the redirect. 302 is most common for temporary redirects. 301/308 for permanent moves. 303/307 preserve or change HTTP method. Choose based on whether redirect is permanent and whether to preserve POST method.",
                    "default": 302
                },
                "condition": {
                    "type": "object",
                    "title": "Conditional Redirect",
                    "description": "Optional condition to check before redirecting",
                    "help": "Optional condition that must be met before redirecting. If condition fails, redirect is skipped and workflow continues. Use to create conditional redirects based on context variables.",
                    "properties": {
                        "variable": {
                            "type": "string",
                            "title": "Context Variable",
                            "description": "Context variable name to check",
                            "help": "Name of the context variable to check. Can be a simple variable (e.g., 'validation_passed') or nested path (e.g., 'captured_credentials.username').",
                            "placeholder": "validation_passed, captured_credentials.username"
                        },
                        "operator": {
                            "type": "string",
                            "title": "Operator",
                            "enum": [
                                {
                                    "value": "equals",
                                    "label": "Equals",
                                    "description": "Check if variable equals the value"
                                },
                                {
                                    "value": "not_equals",
                                    "label": "Not Equals",
                                    "description": "Check if variable does not equal the value"
                                },
                                {
                                    "value": "exists",
                                    "label": "Exists",
                                    "description": "Check if variable exists and is not None (no value needed)"
                                },
                                {
                                    "value": "not_exists",
                                    "label": "Not Exists",
                                    "description": "Check if variable does not exist or is None (no value needed)"
                                }
                            ],
                            "description": "Comparison operator"
                        },
                        "value": {
                            "type": ["string", "number", "boolean"],
                            "title": "Compare Value",
                            "description": "Value to compare against (not needed for exists/not_exists)",
                            "help": "Value to compare the variable against. Not required for 'exists' or 'not_exists' operators."
                        }
                    }
                }
            },
            "required": ["url"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute redirect"""
        try:
            # Check condition if provided
            condition = config.get('condition')
            if condition:
                if not self._check_condition(context, condition):
                    logger.debug("Redirect condition not met, skipping redirect")
                    return context
            
            # Get and interpolate URL
            url = config.get('url', '')
            if not url:
                raise ValueError("Redirect URL is required")
            
            url = interpolate_string(url, context)
            status_code = config.get('status_code', 302)
            
            # Set redirect in context
            context['_response_redirect'] = url
            context['_response_status'] = status_code
            
            logger.debug(f"Redirecting to {url} with status {status_code}")
            return context
            
        except Exception as e:
            logger.error(f"Redirect failed: {e}")
            return self.on_error(e, context, config)
    
    def _check_condition(self, context: Dict[str, Any], condition: Dict[str, Any]) -> bool:
        """Check if condition is met"""
        variable = condition.get('variable')
        operator = condition.get('operator')
        value = condition.get('value')
        
        if not variable or not operator:
            return True  # Invalid condition, pass through
        
        # Get variable value from context
        var_value = context.get(variable)
        
        if operator == 'exists':
            return var_value is not None
        elif operator == 'not_exists':
            return var_value is None
        elif operator == 'equals':
            return var_value == value
        elif operator == 'not_equals':
            return var_value != value
        
        return True
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate redirect configuration"""
        errors = []
        
        if not config.get('url'):
            errors.append("url is required")
        
        status_code = config.get('status_code', 302)
        # Convert to int if it's a string (e.g., from form data)
        if isinstance(status_code, str):
            try:
                status_code = int(status_code)
            except (ValueError, TypeError):
                errors.append(f"Invalid status_code: {status_code} (must be an integer)")
                return errors if errors else None
        
        if status_code not in [301, 302, 303, 307, 308]:
            errors.append(f"Invalid status_code: {status_code}")
        
        return errors if errors else None


