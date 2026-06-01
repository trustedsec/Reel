"""
Delay plugin - adds delays or timing to workflow execution
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class DelayPlugin(BasePlugin):
    """
    Plugin for adding delays or wait periods in workflow execution.
    
    Pauses workflow execution for a specified duration using fixed delays,
    random delays within a range, or delays until a specific datetime.
    Useful for rate limiting, timing coordination, and human-like behavior.
    
    Use cases:
    - Add delays between API calls to avoid rate limits
    - Create realistic timing in automated flows
    - Schedule actions for specific times
    - Add random delays to avoid detection patterns
    - Coordinate timing between workflow steps
    
    Example: Delay 5 seconds after form submission before redirect, or
    use random delay between 3-7 seconds to appear more natural.
    """
    
    @property
    def plugin_type(self) -> str:
        return "delay"
    
    @property
    def display_name(self) -> str:
        return "Delay"
    
    @property
    def description(self) -> str:
        return "Add delays or wait periods in workflow execution. Supports fixed delays, random delays within a range, or delays until a specific datetime. Use for rate limiting, timing coordination, scheduling, and creating realistic timing patterns."
    
    @property
    def plugin_category(self) -> str:
        return "delay"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "delay_type": {
                    "type": "string",
                    "title": "Delay Type",
                    "enum": [
                        {
                            "value": "fixed",
                            "label": "Fixed Delay",
                            "description": "Wait for a fixed number of seconds. Use for predictable timing. Requires 'delay_seconds' field."
                        },
                        {
                            "value": "random",
                            "label": "Random Delay",
                            "description": "Wait for a random number of seconds between min and max. Use to avoid detection patterns. Requires 'random_min' and 'random_max' fields."
                        },
                        {
                            "value": "until",
                            "label": "Delay Until",
                            "description": "Wait until a specific datetime. Use for scheduled actions. Requires 'delay_until' field in ISO format."
                        }
                    ],
                    "description": "Type of delay to apply",
                    "help": "Choose the delay mechanism: Fixed for exact timing, Random for variable timing (avoids patterns), or Until for scheduled delays to a specific time.",
                    "default": "fixed"
                },
                "delay_seconds": {
                    "type": "integer",
                    "title": "Delay Duration (seconds)",
                    "minimum": 0,
                    "maximum": 3600,
                    "description": "Number of seconds to delay (for fixed delay type)",
                    "help": "Fixed delay duration in seconds. Must be between 0 and 3600 (1 hour). Only used when delay_type is 'fixed'. Example: 5 for 5 second delay, 30 for 30 second delay.",
                    "placeholder": "5, 30, 60"
                },
                "random_min": {
                    "type": "integer",
                    "title": "Random Minimum (seconds)",
                    "description": "Minimum delay duration in seconds (for random delay type)",
                    "help": "Minimum number of seconds for random delay. Must be non-negative. Only used when delay_type is 'random'. Example: 3 for minimum 3 second delay.",
                    "placeholder": "3, 10, 30"
                },
                "random_max": {
                    "type": "integer",
                    "title": "Random Maximum (seconds)",
                    "description": "Maximum delay duration in seconds (for random delay type)",
                    "help": "Maximum number of seconds for random delay. Must be >= random_min. Only used when delay_type is 'random'. Example: 7 for maximum 7 second delay (creates 3-7 second range).",
                    "placeholder": "7, 20, 60"
                },
                "delay_until": {
                    "type": "string",
                    "title": "Delay Until (ISO datetime)",
                    "format": "date-time",
                    "description": "Target datetime to delay until (ISO format)",
                    "help": "Target datetime in ISO 8601 format. Workflow will delay until this time. Only used when delay_type is 'until'. Format: 2024-01-15T14:30:00Z or 2024-01-15T14:30:00+00:00",
                    "placeholder": "2024-01-15T14:30:00Z, 2024-01-15T14:30:00+00:00"
                }
            }
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute delay"""
        try:
            delay_type = config.get('delay_type', 'fixed')
            
            if delay_type == 'fixed':
                delay_seconds = config.get('delay_seconds', 0)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                    logger.debug(f"Delayed for {delay_seconds} seconds")
            
            elif delay_type == 'random':
                import random
                min_delay = config.get('random_min', 0)
                max_delay = config.get('random_max', min_delay)
                delay_seconds = random.randint(min_delay, max_delay)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                    logger.debug(f"Delayed for {delay_seconds} seconds (random)")
            
            elif delay_type == 'until':
                delay_until_str = config.get('delay_until')
                if delay_until_str:
                    delay_until = datetime.fromisoformat(delay_until_str.replace('Z', '+00:00'))
                    now = datetime.utcnow()
                    if delay_until > now:
                        delay_seconds = (delay_until - now).total_seconds()
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                            logger.debug(f"Delayed until {delay_until}")
            
            context['_delay_executed'] = {
                'type': delay_type,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Delay execution failed: {e}")
            return self.on_error(e, context, config)
        
        return context
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate delay configuration"""
        errors = []
        
        delay_type = config.get('delay_type', 'fixed')
        
        if delay_type == 'fixed':
            delay_seconds = config.get('delay_seconds', 0)
            if delay_seconds < 0:
                errors.append("Delay seconds must be non-negative")
            if delay_seconds > 3600:
                errors.append("Delay seconds cannot exceed 3600")
        
        elif delay_type == 'random':
            min_delay = config.get('random_min', 0)
            max_delay = config.get('random_max', 0)
            if min_delay < 0:
                errors.append("Random min delay must be non-negative")
            if max_delay < min_delay:
                errors.append("Random max delay must be >= min delay")
        
        elif delay_type == 'until':
            delay_until = config.get('delay_until')
            if not delay_until:
                errors.append("delay_until is required for 'until' delay type")
            else:
                try:
                    datetime.fromisoformat(delay_until.replace('Z', '+00:00'))
                except ValueError:
                    errors.append("delay_until must be a valid ISO datetime")
        
        return errors if errors else None

