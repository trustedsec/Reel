"""
Variable resolution system for workflows
Handles campaign variables and interpolation in templates and configs
"""
from typing import Dict, Any, Optional
import re
import logging

logger = logging.getLogger(__name__)

def resolve_variables(context: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge campaign variables into execution context
    
    Args:
        context: Current execution context
        variables: Campaign variables dictionary
        
    Returns:
        Updated context with variables merged
    """
    if not variables:
        return context
    
    # Create a copy to avoid mutating original
    resolved = context.copy()
    
    # Merge variables into context with 'vars' prefix to avoid conflicts
    resolved['vars'] = variables.copy()
    
    # Also merge variables directly into context for easy access
    # (variables take precedence over existing context keys)
    for key, value in variables.items():
        resolved[key] = value
    
    return resolved


def interpolate_string(template: str, variables: Dict[str, Any]) -> str:
    """
    Interpolate variables in a string template
    
    Supports:
    - {{ variable_name }} - Simple variable substitution
    - {{ vars.variable_name }} - Explicit variable access
    - {{ nested.key }} - Nested object access
    
    Args:
        template: String template with {{ variable }} placeholders
        variables: Variables dictionary
        
    Returns:
        Interpolated string
    """
    if not template or not isinstance(template, str):
        return template
    
    def replace_var(match):
        var_path = match.group(1).strip()

        # Skip function calls — leave for Jinja2 to handle
        if '(' in var_path:
            return match.group(0)

        # Try to resolve the variable
        try:
            # Handle nested paths like "vars.key" or "nested.key"
            parts = var_path.split('.')
            value = variables
            
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return match.group(0)  # Return original if path invalid
            
            # Convert to string, handle None
            if value is None:
                return ''
            return str(value)
        except (KeyError, AttributeError, TypeError):
            # If variable not found, return original placeholder
            logger.debug(f"Variable not found: {var_path}")
            return match.group(0)
    
    # Match {{ variable }} or {{variable}} patterns
    pattern = r'\{\{\s*([^}]+)\s*\}\}'
    return re.sub(pattern, replace_var, template)


def interpolate_dict(data: Dict[str, Any], variables: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively interpolate variables in a dictionary
    
    Args:
        data: Dictionary that may contain string templates
        variables: Variables dictionary
        
    Returns:
        Dictionary with interpolated values
    """
    if not isinstance(data, dict):
        return data
    
    result = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = interpolate_string(value, variables)
        elif isinstance(value, dict):
            result[key] = interpolate_dict(value, variables)
        elif isinstance(value, list):
            result[key] = [interpolate_dict(item, variables) if isinstance(item, dict) 
                          else interpolate_string(item, variables) if isinstance(item, str)
                          else item for item in value]
        else:
            result[key] = value
    
    return result


def get_variable_value(variables: Dict[str, Any], path: str, default: Any = None) -> Any:
    """
    Get a variable value by path (supports nested keys)
    
    Args:
        variables: Variables dictionary
        path: Dot-separated path (e.g., "user.email" or "vars.setting")
        default: Default value if not found
        
    Returns:
        Variable value or default
    """
    if not path:
        return default
    
    parts = path.split('.')
    value = variables
    
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
            if value is None:
                return default
        else:
            return default
    
    return value




