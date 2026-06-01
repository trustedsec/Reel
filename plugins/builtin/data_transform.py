"""
Data transformation plugin - modifies context data
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import json

logger = logging.getLogger(__name__)

class DataTransformPlugin(BasePlugin):
    """
    Plugin for transforming, filtering, and modifying data in the execution context.
    
    Performs various data manipulation operations on context variables including setting,
    removing, copying, renaming, merging, and filtering. Operations are executed in
    sequence, allowing complex data transformations in a single plugin step.
    
    Use cases:
    - Normalize variable names across plugins
    - Clean up context by removing unnecessary variables
    - Copy data for backup before modification
    - Merge data from multiple sources
    - Filter context to only keep relevant variables
    - Prepare data for downstream plugins
    
    Example: Copy 'captured_credentials.username' to 'email', then remove the original
    'captured_credentials' object to clean up context.
    """
    
    @property
    def plugin_type(self) -> str:
        return "data_transform"
    
    @property
    def display_name(self) -> str:
        return "Transform Data"
    
    @property
    def description(self) -> str:
        return "Transform, filter, or modify data in the execution context. Supports set, remove, copy, rename, merge, and filter operations. Use to normalize variable names, clean up context, merge data sources, or prepare data for downstream plugins."
    
    @property
    def plugin_category(self) -> str:
        return "data_transform"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "title": "Transformation Operations",
                    "description": "List of data transformation operations to execute in sequence",
                    "help": "Define one or more operations to transform context data. Operations execute in order, so later operations can use results from earlier ones. Each operation can set, remove, copy, rename, merge, or filter context variables.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "title": "Operation Type",
                                "description": "Type of transformation to perform",
                                "enum": [
                                    {
                                        "value": "set",
                                        "label": "Set",
                                        "description": "Set a context variable to a specific value. Creates new variable or overwrites existing. Requires 'target_key' and 'value'. Example: Set 'status' to 'active'."
                                    },
                                    {
                                        "value": "remove",
                                        "label": "Remove",
                                        "description": "Remove a context variable. Deletes the variable from context. Requires 'source_key'. Example: Remove 'temp_data' variable."
                                    },
                                    {
                                        "value": "copy",
                                        "label": "Copy",
                                        "description": "Copy a context variable to a new key. Creates duplicate with different name. Requires 'source_key' and 'target_key'. Example: Copy 'username' to 'email'."
                                    },
                                    {
                                        "value": "rename",
                                        "label": "Rename",
                                        "description": "Rename a context variable. Moves variable to new key and removes old key. Requires 'source_key' and 'target_key'. Example: Rename 'old_name' to 'new_name'."
                                    },
                                    {
                                        "value": "merge",
                                        "label": "Merge",
                                        "description": "Merge source object into target object. If both are dicts, merges keys. Otherwise replaces target. Requires 'source_key' and 'target_key'. Example: Merge 'user_data' into 'profile'."
                                    },
                                    {
                                        "value": "filter",
                                        "label": "Filter",
                                        "description": "Remove context variables that don't match condition. Filters based on key name, value, or type. Requires 'condition'. Example: Remove all variables starting with 'temp_'."
                                    }
                                ]
                            },
                            "source_key": {
                                "type": "string",
                                "title": "Source Key",
                                "description": "Context variable name to read from (for copy, rename, merge, remove operations)",
                                "help": "Name of the context variable to use as source. Can be a simple key (e.g., 'username') or nested path (e.g., 'user.profile.email'). Required for copy, rename, merge, and remove operations.",
                                "placeholder": "username, user.profile.email, captured_credentials.username"
                            },
                            "target_key": {
                                "type": "string",
                                "title": "Target Key",
                                "description": "Context variable name to write to (for set, copy, rename, merge operations)",
                                "help": "Name of the context variable to create or update. Can be a simple key (e.g., 'email') or nested path (e.g., 'user.email'). Required for set, copy, rename, and merge operations.",
                                "placeholder": "email, user.email, normalized_data"
                            },
                            "value": {
                                "type": ["string", "number", "boolean", "object", "array"],
                                "title": "Value",
                                "description": "Value to set (for set operation only)",
                                "help": "The value to assign to the target key. Can be a string, number, boolean, object, or array. Only used for 'set' operation. Example: 'active', 100, true, {'key': 'value'}, ['item1', 'item2']."
                            },
                            "condition": {
                                "type": "object",
                                "title": "Filter Condition",
                                "description": "Condition for filter operation (key_contains, value_equals, value_type)",
                                "help": "Define filtering criteria. 'key_contains': filter by key name substring, 'value_equals': filter by exact value match, 'value_type': filter by value type (e.g., 'str', 'int', 'dict'). Only used for 'filter' operation."
                            }
                        },
                        "required": ["operation"]
                    }
                }
            },
            "required": ["operations"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute data transformation"""
        try:
            operations = config.get('operations', [])
            
            for op in operations:
                op_type = op.get('operation')
                
                if op_type == 'set':
                    target_key = op.get('target_key')
                    value = op.get('value')
                    if target_key:
                        context[target_key] = value
                
                elif op_type == 'remove':
                    source_key = op.get('source_key')
                    if source_key and source_key in context:
                        del context[source_key]
                
                elif op_type == 'copy':
                    source_key = op.get('source_key')
                    target_key = op.get('target_key')
                    if source_key and target_key and source_key in context:
                        context[target_key] = context[source_key]
                
                elif op_type == 'rename':
                    source_key = op.get('source_key')
                    target_key = op.get('target_key')
                    if source_key and target_key and source_key in context:
                        context[target_key] = context.pop(source_key)
                
                elif op_type == 'merge':
                    source_key = op.get('source_key')
                    target_key = op.get('target_key')
                    if source_key and target_key and source_key in context:
                        if isinstance(context.get(target_key), dict) and isinstance(context[source_key], dict):
                            context[target_key].update(context[source_key])
                        else:
                            context[target_key] = context[source_key]
                
                elif op_type == 'filter':
                    # Filter context keys based on condition
                    condition = op.get('condition', {})
                    if condition:
                        keys_to_remove = []
                        for key in context.keys():
                            if not self._evaluate_condition(key, context[key], condition):
                                keys_to_remove.append(key)
                        for key in keys_to_remove:
                            del context[key]
            
            logger.debug(f"Data transformation completed: {len(operations)} operations")
            
        except Exception as e:
            logger.error(f"Data transformation failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def _evaluate_condition(self, key: str, value: Any, condition: Dict[str, Any]) -> bool:
        """Evaluate a condition for filtering"""
        # Simple condition evaluation
        if 'key_contains' in condition:
            return condition['key_contains'] in key
        if 'value_equals' in condition:
            return value == condition['value_equals']
        if 'value_type' in condition:
            return isinstance(value, eval(condition['value_type']))
        return True
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate transformation configuration"""
        errors = []
        
        if not config.get('operations'):
            errors.append("At least one operation is required")
        
        valid_operations = ['set', 'remove', 'copy', 'rename', 'merge', 'filter']
        for i, op in enumerate(config.get('operations', [])):
            op_type = op.get('operation')
            if not op_type:
                errors.append(f"Operation {i+1}: operation type is required")
            elif op_type not in valid_operations:
                errors.append(f"Operation {i+1}: invalid operation type '{op_type}'")
            
            if op_type in ['copy', 'rename', 'merge']:
                if not op.get('source_key'):
                    errors.append(f"Operation {i+1}: source_key is required for {op_type}")
                if not op.get('target_key'):
                    errors.append(f"Operation {i+1}: target_key is required for {op_type}")
        
        return errors if errors else None

