"""
Conditional logic plugin - branches workflow based on conditions
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.workflow_variables import interpolate_string
import logging

logger = logging.getLogger(__name__)

class ConditionalPlugin(BasePlugin):
    """
    Plugin for conditional logic and branching in workflows.
    
    Evaluates conditions based on context variables and branches workflow execution
    into True or False paths. Supports multiple comparison types including equality,
    containment, numeric comparisons, existence checks, and regex matching.
    
    Use cases:
    - Check if credentials were captured before sending notification
    - Validate form data before processing
    - Branch based on user agent or IP address
    - Check plugin outputs to determine next steps
    - Implement multi-step flows with conditional navigation
    
    Example: Check if 'captured_credentials.username' exists, then branch to
    send notification (True) or show error (False).
    """
    
    @property
    def plugin_type(self) -> str:
        return "conditional"
    
    @property
    def display_name(self) -> str:
        return "Conditional Logic"
    
    @property
    def description(self) -> str:
        return "Evaluate conditions based on context variables and branch workflow execution into True/False paths. Supports equality, containment, numeric comparisons, existence checks, and regex matching. Use to create conditional flows, validate data, and implement multi-step processes."
    
    @property
    def plugin_category(self) -> str:
        return "conditional"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "condition": {
                    "type": "object",
                    "title": "Condition",
                    "description": "Define the condition to evaluate",
                    "properties": {
                        "type": {
                            "type": "string",
                            "title": "Condition Type",
                            "description": "Type of comparison to perform",
                            "help": "Select the comparison operator to use. 'equals' and 'not_equals' check exact matches, 'contains' checks if value exists in string/list/dict, 'greater_than'/'less_than' compare numbers, 'exists'/'not_exists' check if variable is present, 'regex' matches pattern against string.",
                            "enum": [
                                {
                                    "value": "equals",
                                    "label": "Equals",
                                    "description": "Check if context variable exactly equals the compare value. Case-sensitive for strings. Example: Check if 'status' equals 'active'."
                                },
                                {
                                    "value": "not_equals",
                                    "label": "Not Equals",
                                    "description": "Check if context variable does not equal the compare value. Case-sensitive for strings. Example: Check if 'status' is not 'blocked'."
                                },
                                {
                                    "value": "contains",
                                    "label": "Contains",
                                    "description": "Check if context variable contains the compare value. Works with strings (substring check), lists (item check), and dicts (key check). Example: Check if 'email' contains '@domain.com'."
                                },
                                {
                                    "value": "not_contains",
                                    "label": "Not Contains",
                                    "description": "Check if context variable does not contain the compare value. Works with strings, lists, and dicts. Example: Check if 'user_agent' does not contain 'bot'."
                                },
                                {
                                    "value": "greater_than",
                                    "label": "Greater Than",
                                    "description": "Check if context variable (number) is greater than compare value. Only works with numeric values. Example: Check if 'attempts' is greater than 3."
                                },
                                {
                                    "value": "less_than",
                                    "label": "Less Than",
                                    "description": "Check if context variable (number) is less than compare value. Only works with numeric values. Example: Check if 'score' is less than 100."
                                },
                                {
                                    "value": "exists",
                                    "label": "Exists",
                                    "description": "Check if context variable exists and is not None. No compare value needed. Example: Check if 'captured_credentials.username' exists."
                                },
                                {
                                    "value": "not_exists",
                                    "label": "Not Exists",
                                    "description": "Check if context variable does not exist or is None. No compare value needed. Example: Check if 'error' does not exist."
                                },
                                {
                                    "value": "regex",
                                    "label": "Regex Match",
                                    "description": "Check if context variable (string) matches the regex pattern. Requires 'regex' field to be set with the pattern. Example: Check if 'email' matches pattern '^[a-z]+@domain\\.com$'."
                                }
                            ]
                        },
                        "key": {
                            "type": "string",
                            "title": "Context Variable Key",
                            "description": "Name of the context variable to check. Can be a campaign variable (e.g., 'test_value'), request data (e.g., 'request.form_data.email'), query parameter (e.g., 'request.query_params.id'), or plugin output (e.g., 'validation_passed'). Use dot notation for nested values.",
                            "placeholder": "e.g., test_value, request.form_data.email, request.query_params.id",
                            "help": "Context variable name. Examples: campaign variables (test_value), form data (request.form_data.email), query params (request.query_params.id), or plugin outputs (validation_passed, captured_credentials.username)"
                        },
                        "value": {
                            "type": ["string", "number", "boolean"],
                            "title": "Compare Value",
                            "description": "Value to compare against (not required for 'exists' or 'not_exists' types)",
                            "help": "The value to compare the context variable against. Leave empty for 'exists' or 'not_exists' condition types."
                        },
                        "regex": {
                            "type": "string",
                            "title": "Regex Pattern",
                            "description": "Regular expression pattern (only used when condition type is 'regex')",
                            "help": "Regular expression pattern to match against the context variable value. Only used when condition type is 'regex'."
                        }
                    },
                    "required": ["type", "key"]
                },
                "true_branch": {
                    "type": "string",
                    "title": "True Branch Node ID (Legacy)",
                    "description": "Node ID to execute if condition is true (legacy - use visual connections instead)",
                    "help": "This field is deprecated. Use the visual True/False connections in the workflow builder instead."
                },
                "false_branch": {
                    "type": "string",
                    "title": "False Branch Node ID (Legacy)",
                    "description": "Node ID to execute if condition is false (legacy - use visual connections instead)",
                    "help": "This field is deprecated. Use the visual True/False connections in the workflow builder instead."
                }
            },
            "required": ["condition"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute conditional logic"""
        try:
            condition = config.get('condition', {})
            condition_type = condition.get('type')
            key = condition.get('key')
            value = condition.get('value')

            if not key:
                raise ValueError("Condition key is required")

            # Interpolate key with context (e.g. session.captcha_passed_{{campaign.id}} -> session.captcha_passed_45)
            try:
                key_resolved = interpolate_string(key, context)
                if key_resolved and key_resolved.strip():
                    key = key_resolved.strip()
            except Exception:
                pass

            # Interpolate string compare value with context when applicable
            if isinstance(value, str) and value.strip():
                try:
                    value_resolved = interpolate_string(value, context)
                    if value_resolved is not None:
                        value = value_resolved
                except Exception:
                    pass

            # Resolve nested keys (e.g., "request.form_data.email", "session.captcha_passed_45")
            context_value = self._resolve_nested_key(context, key)
            result = False

            if condition_type == 'equals':
                result = context_value == value

            elif condition_type == 'not_equals':
                result = context_value != value

            elif condition_type == 'contains':
                if isinstance(context_value, str):
                    result = str(value) in context_value
                elif isinstance(context_value, (list, dict)):
                    result = value in context_value

            elif condition_type == 'not_contains':
                if isinstance(context_value, str):
                    result = str(value) not in context_value
                elif isinstance(context_value, (list, dict)):
                    result = value not in context_value

            elif condition_type == 'greater_than':
                result = context_value > value if isinstance(context_value, (int, float)) else False

            elif condition_type == 'less_than':
                result = context_value < value if isinstance(context_value, (int, float)) else False

            elif condition_type == 'exists':
                result = context_value is not None

            elif condition_type == 'not_exists':
                result = context_value is None

            elif condition_type == 'regex':
                import re
                if isinstance(context_value, str):
                    pattern = condition.get('regex', '')
                    result = bool(re.search(pattern, context_value))
            
            # Store condition result in context for workflow engine
            context['_condition_result'] = result
            context['_condition_branch'] = config.get('true_branch' if result else 'false_branch')
            
            logger.info(f"Condition evaluated: key={key}, type={condition_type}, value={value}, context_value={context_value}, result={result}")
            logger.debug(f"Condition evaluated: {key} {condition_type} {value} = {result}")
            
        except Exception as e:
            logger.error(f"Conditional evaluation failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def _resolve_nested_key(self, context: Dict[str, Any], key: str) -> Any:
        """Resolve a nested key from context using dot notation"""
        if '.' not in key:
            return context.get(key)
        
        # Split by dots and traverse the dictionary
        parts = key.split('.')
        value = context
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
                if value is None:
                    return None
            else:
                return None
        return value
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the boolean condition result"""
        return "_condition_result"
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate conditional configuration"""
        errors = []
        
        condition = config.get('condition')
        if not condition:
            errors.append("Condition is required")
        else:
            if not condition.get('type'):
                errors.append("Condition type is required")
            if not condition.get('key'):
                errors.append("Condition key is required")
        
        return errors if errors else None

