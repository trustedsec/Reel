"""
Base plugin interface for Reel v2 workflow system
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)

class BasePlugin(ABC):
    """Base class for all plugins in Reel v2"""
    
    @property
    @abstractmethod
    def plugin_type(self) -> str:
        """
        Return the plugin type identifier (e.g., 'notification', 'webhook', 'data_transform')
        Must be unique across all plugins
        """
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """Return human-readable plugin name"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Return description of what the plugin does"""
        pass
    
    @property
    @abstractmethod
    def plugin_category(self) -> str:
        """
        Return plugin category:
        - 'campaign': Used in campaign workflows
        - 'sending': Used in email sending workflows
        - 'target_selection': For selecting email targets
        - 'email_validation': For validating email templates
        - 'notification': For notifications
        - 'data_transform': For data transformation
        - 'conditional': For conditional logic
        - 'delay': For delays/timing
        """
        pass
    
    @property
    @abstractmethod
    def config_schema(self) -> Dict[str, Any]:
        """
        Return JSON schema for plugin configuration validation
        Should follow JSON Schema format
        """
        pass
    
    @abstractmethod
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the plugin with given context and configuration
        
        Args:
            context: Execution context containing data from previous plugins/workflow
            config: Plugin-specific configuration
            
        Returns:
            Modified context dictionary (may add/remove/modify keys)
            
        Raises:
            Exception: If execution fails
        """
        pass
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """
        Validate plugin configuration
        
        Args:
            config: Configuration dictionary to validate
            
        Returns:
            List of error messages if validation fails, None if valid
        """
        # Default implementation - override in subclasses for specific validation
        return None
    
    def get_required_context_keys(self) -> List[str]:
        """
        Return list of context keys that this plugin requires to execute
        
        Returns:
            List of required context key names
        """
        return []
    
    def get_provided_context_keys(self) -> List[str]:
        """
        Return list of context keys that this plugin provides after execution
        
        Returns:
            List of provided context key names
        """
        return []
    
    def get_branch_context_key(self) -> Optional[str]:
        """
        Return the context key that contains a boolean result for branching.
        
        Plugins that support multiple execution paths (e.g., pass/fail, true/false)
        should override this method to return the context key name.
        
        Returns:
            Context key name (e.g., "validation_passed", "_condition_result"), 
            or None for single path (no branching)
        """
        return None
    
    def get_branch_labels(self) -> Dict[str, str]:
        """
        Return display labels for true/false paths.

        Plugins can override this to provide custom labels for the boolean paths.

        Returns:
            Dict with "true" and "false" keys, default: {"true": "True", "false": "False"}
        """
        return {"true": "True", "false": "False"}

    def get_branch_colors(self) -> Dict[str, str]:
        """
        Return which path is positive/negative for endpoint coloring.

        Most plugins treat true=success (green) and false=failure (red).
        Plugins where true means something negative (e.g. phishing_detector
        where true="is phishing") should override this.

        Returns:
            Dict with "true" and "false" mapped to "green" or "red".
        """
        return {"true": "green", "false": "red"}
    
    def on_error(self, error: Exception, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle errors during plugin execution
        
        Args:
            error: The exception that occurred
            context: Execution context at time of error
            config: Plugin configuration
            
        Returns:
            Modified context (may add error information)
        """
        logger.error(f"Plugin {self.plugin_type} execution error: {error}", exc_info=True)
        context['_error'] = {
            'plugin': self.plugin_type,
            'message': str(error),
            'type': type(error).__name__
        }
        return context
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert plugin metadata to dictionary"""
        return {
            'plugin_type': self.plugin_type,
            'display_name': self.display_name,
            'description': self.description,
            'plugin_category': self.plugin_category,
            'config_schema': self.config_schema,
            'required_context_keys': self.get_required_context_keys(),
            'provided_context_keys': self.get_provided_context_keys(),
            'branch_context_key': self.get_branch_context_key(),
            'branch_labels': self.get_branch_labels(),
            'branch_colors': self.get_branch_colors()
        }

