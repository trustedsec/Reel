"""
Validate input plugin - validates form inputs
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import re

logger = logging.getLogger(__name__)

class ValidateInputPlugin(BasePlugin):
    """
    Plugin for validating form input data against validation rules.
    
    Validates form fields from POST requests against rules including required
    fields, data types (email, URL, number, string, regex), and length constraints.
    Can block requests, redirect, or continue workflow based on validation results.
    
    Use cases:
    - Validate login forms before processing
    - Check email format and required fields
    - Validate custom form inputs
    - Enforce data format requirements
    - Prevent invalid data from reaching downstream plugins
    
    Example: Validate that 'email' field is required and valid email format,
    'password' is at least 8 characters, then branch workflow based on
    validation result (pass/fail).
    """
    
    @property
    def plugin_type(self) -> str:
        return "validate_input"
    
    @property
    def display_name(self) -> str:
        return "Validate Input"
    
    @property
    def description(self) -> str:
        return "Validate form input data against rules including required fields, data types (email, URL, number, regex), and length constraints. Supports blocking, redirecting, or continuing workflow based on validation results. Example: validate login form email and password (e.g. required email, password min 8 chars) before processing."
    
    @property
    def plugin_category(self) -> str:
        return "validation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "title": "Validation Fields",
                    "description": "List of form fields to validate",
                    "help": "Array of field validation rules. Each field can have name, required flag, type (email, url, number, string, regex), length constraints, and custom error messages.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "title": "Field Name",
                                "description": "Form field name to validate",
                                "help": "Name of the HTML form input field to validate. Must match the 'name' attribute of the form input. Example: 'email', 'password', 'username'.",
                                "placeholder": "email, password, username"
                            },
                            "required": {
                                "type": "boolean",
                                "title": "Required",
                                "description": "Whether field is required",
                                "help": "If true, field must have a value. If false, field is optional but will be validated if present. Default: false.",
                                "default": False
                            },
                            "type": {
                                "type": "string",
                                "title": "Field Type",
                                "enum": [
                                    {
                                        "value": "string",
                                        "label": "String",
                                        "description": "Any text string. Basic validation only."
                                    },
                                    {
                                        "value": "email",
                                        "label": "Email",
                                        "description": "Valid email address format. Checks for @ symbol and domain."
                                    },
                                    {
                                        "value": "url",
                                        "label": "URL",
                                        "description": "Valid HTTP/HTTPS URL. Must start with http:// or https://."
                                    },
                                    {
                                        "value": "number",
                                        "label": "Number",
                                        "description": "Numeric value (integer or decimal). Can be converted to float."
                                    },
                                    {
                                        "value": "regex",
                                        "label": "Regex Pattern",
                                        "description": "Custom regex pattern validation. Requires 'pattern' field to be set."
                                    }
                                ],
                                "description": "Data type to validate against",
                                "help": "Type of data expected in the field. Email validates email format, URL validates URL format, number validates numeric, regex uses custom pattern, string is basic text.",
                                "default": "string"
                            },
                            "min_length": {
                                "type": "integer",
                                "title": "Minimum Length",
                                "description": "Minimum character length",
                                "help": "Minimum number of characters required. Example: 8 for password minimum length.",
                                "placeholder": "8, 10, 1"
                            },
                            "max_length": {
                                "type": "integer",
                                "title": "Maximum Length",
                                "description": "Maximum character length",
                                "help": "Maximum number of characters allowed. Example: 100 for email max length, 255 for username.",
                                "placeholder": "100, 255, 50"
                            },
                            "pattern": {
                                "type": "string",
                                "title": "Regex Pattern",
                                "description": "Custom regex pattern (for regex type only)",
                                "help": "Regular expression pattern to match against field value. Only used when type is 'regex'. Example: '^[A-Z0-9]+$' for uppercase alphanumeric, '^\\d{4}$' for 4 digits.",
                                "placeholder": "^[A-Z0-9]+$, ^\\d{4}$"
                            },
                            "error_message": {
                                "type": "string",
                                "title": "Custom Error Message",
                                "description": "Custom error message for this field",
                                "help": "Custom error message to display when validation fails for this field. If not provided, uses default message. Example: 'Email address is invalid' or 'Password must be at least 8 characters'.",
                                "placeholder": "Email address is invalid, Password must be at least 8 characters"
                            }
                        },
                        "required": ["name"]
                    }
                },
                "on_error": {
                    "type": "string",
                    "title": "Error Action",
                    "enum": [
                        {
                            "value": "block",
                            "label": "Block Request",
                            "description": "Block request and show error message. Prevents workflow from continuing. Use when validation is required."
                        },
                        {
                            "value": "redirect",
                            "label": "Redirect",
                            "description": "Redirect to URL on validation error. Use error_redirect to specify URL. Allows graceful error handling."
                        },
                        {
                            "value": "continue",
                            "label": "Continue Workflow",
                            "description": "Continue workflow even if validation fails. Sets validation_passed=false in context for conditional logic. Use when validation is optional or for logging."
                        }
                    ],
                    "description": "Action to take when validation fails",
                    "help": "What to do when validation fails: Block (show error, stop workflow), Redirect (send to error_redirect URL), or Continue (set validation_passed=false, allow workflow to continue). Default: block.",
                    "default": "block"
                },
                "error_redirect": {
                    "type": "string",
                    "title": "Error Redirect URL",
                    "description": "URL to redirect to on validation error",
                    "help": "URL to redirect user to when validation fails. Only used when on_error is 'redirect'. Supports variable interpolation. Example: /error, https://example.com/invalid, or {{campaign.error_page}}.",
                    "placeholder": "/error, https://example.com/invalid, {{campaign.error_page}}"
                },
                "error_message": {
                    "type": "string",
                    "title": "Error Message",
                    "description": "Error message to display when validation fails",
                    "help": "Error message to show to user when validation fails. Only used when on_error is 'block'. Supports HTML. If not provided, shows field-specific error messages. Example: '<h1>Validation Error</h1><p>Please check your input.</p>'.",
                    "placeholder": "<h1>Validation Error</h1><p>Please check your input.</p>"
                }
            },
            "required": ["fields"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute input validation"""
        try:
            form_data = context.get('request', {}).get('form_data', {})
            fields = config.get('fields', [])
            on_error = config.get('on_error', 'block')
            
            validation_errors = []
            
            for field_config in fields:
                field_name = field_config.get('name')
                field_value = form_data.get(field_name, '')
                
                # Check required
                if field_config.get('required', False) and not field_value:
                    error_msg = field_config.get('error_message', f"Field '{field_name}' is required")
                    validation_errors.append(error_msg)
                    continue
                
                # Type validation
                field_type = field_config.get('type', 'string')
                
                # For string type, None is invalid (even if not required)
                if field_type == 'string' and field_value is None:
                    validation_errors.append(field_config.get('error_message', f"'{field_name}' must be a string"))
                    continue
                
                if not field_value:
                    continue  # Skip validation if field is empty and not required
                
                if field_type == 'email':
                    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', field_value):
                        validation_errors.append(field_config.get('error_message', f"'{field_name}' must be a valid email"))
                
                elif field_type == 'url':
                    if not re.match(r'^https?://', field_value):
                        validation_errors.append(field_config.get('error_message', f"'{field_name}' must be a valid URL"))
                
                elif field_type == 'number':
                    try:
                        float(field_value)
                    except ValueError:
                        validation_errors.append(field_config.get('error_message', f"'{field_name}' must be a number"))
                
                elif field_type == 'regex':
                    pattern = field_config.get('pattern')
                    if pattern:
                        try:
                            if not re.match(pattern, field_value):
                                validation_errors.append(field_config.get('error_message', f"'{field_name}' format is invalid"))
                        except re.error:
                            logger.warning(f"Invalid regex pattern: {pattern}")
                
                # Length validation
                if 'min_length' in field_config:
                    if len(field_value) < field_config['min_length']:
                        validation_errors.append(field_config.get('error_message', f"'{field_name}' must be at least {field_config['min_length']} characters"))
                
                if 'max_length' in field_config:
                    if len(field_value) > field_config['max_length']:
                        validation_errors.append(field_config.get('error_message', f"'{field_name}' must be at most {field_config['max_length']} characters"))
            
            # Store validation result
            context['validation_passed'] = len(validation_errors) == 0
            context['validation_errors'] = validation_errors
            
            # Handle errors
            if validation_errors:
                if on_error == 'block':
                    error_html = config.get('error_message', '<br>'.join(validation_errors))
                    context['_response_html'] = f"<html><body><h1>Validation Error</h1><p>{error_html}</p></body></html>"
                    context['_response_status'] = 400
                elif on_error == 'redirect':
                    redirect_url = config.get('error_redirect', '/')
                    context['_response_redirect'] = redirect_url
                # If 'continue', just store errors in context
            
            return context
            
        except Exception as e:
            logger.error(f"Input validation failed: {e}")
            return self.on_error(e, context, config)
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the validation result"""
        return "validation_passed"
    
    def get_branch_labels(self) -> Dict[str, str]:
        """Return custom labels for pass/fail paths"""
        return {"true": "Pass", "false": "Fail"}
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate input validation configuration"""
        errors = []
        
        if not config.get('fields'):
            errors.append("fields is required")
        
        on_error = config.get('on_error', 'block')
        if on_error == 'redirect' and not config.get('error_redirect'):
            errors.append("error_redirect is required when on_error is 'redirect'")
        
        # Validate regex patterns
        for field_config in config.get('fields', []):
            if field_config.get('type') == 'regex':
                pattern = field_config.get('pattern')
                if not pattern:
                    errors.append(f"pattern is required for field '{field_config.get('name')}' with type 'regex'")
                else:
                    try:
                        re.compile(pattern)
                    except re.error as e:
                        errors.append(f"Invalid regex pattern for field '{field_config.get('name')}': {e}")
        
        return errors if errors else None

