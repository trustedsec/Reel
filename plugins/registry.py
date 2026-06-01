"""
Plugin registry for managing and discovering plugins
"""
from typing import Dict, Optional, List, Any
from .base import BasePlugin
import logging
import importlib
import importlib.util
import inspect
from pathlib import Path

logger = logging.getLogger(__name__)

class PluginRegistry:
    """Registry for managing plugins"""
    
    def __init__(self):
        self._plugins: Dict[str, BasePlugin] = {}
        self._plugin_classes: Dict[str, type] = {}
        self._initialized = False
    
    def register(self, plugin: BasePlugin):
        """
        Register a plugin instance
        
        Args:
            plugin: Plugin instance to register
        """
        if not isinstance(plugin, BasePlugin):
            raise ValueError("Plugin must inherit from BasePlugin")
        
        plugin_type = plugin.plugin_type
        
        if plugin_type in self._plugins:
            logger.warning(f"Overriding existing plugin: {plugin_type}")
        
        self._plugins[plugin_type] = plugin
        logger.info(f"Registered plugin: {plugin_type} ({plugin.display_name})")
    
    def register_class(self, plugin_class: type, plugin_type: str = None):
        """
        Register a plugin class (will be instantiated on demand)
        
        Args:
            plugin_class: Plugin class to register
            plugin_type: Optional plugin type override
        """
        if not issubclass(plugin_class, BasePlugin):
            raise ValueError("Plugin class must inherit from BasePlugin")
        
        # Instantiate to get plugin_type
        instance = plugin_class()
        pt = plugin_type or instance.plugin_type
        
        self._plugin_classes[pt] = plugin_class
        self._plugins[pt] = instance
        logger.info(f"Registered plugin class: {pt} ({instance.display_name})")
    
    def get_plugin(self, plugin_type: str) -> Optional[BasePlugin]:
        """
        Get plugin by type
        
        Args:
            plugin_type: Plugin type identifier
            
        Returns:
            Plugin instance or None if not found
        """
        return self._plugins.get(plugin_type)
    
    def list_plugins(self, category: str = None) -> List[Dict[str, Any]]:
        """
        List all registered plugins
        
        Args:
            category: Optional filter by plugin category
            
        Returns:
            List of plugin metadata dictionaries
        """
        plugins = []
        for plugin in self._plugins.values():
            if category is None or plugin.plugin_category == category:
                plugins.append(plugin.to_dict())
        return plugins
    
    def get_plugins_by_category(self, category: str) -> List[BasePlugin]:
        """
        Get all plugins in a specific category
        
        Args:
            category: Plugin category
            
        Returns:
            List of plugin instances
        """
        return [
            plugin for plugin in self._plugins.values()
            if plugin.plugin_category == category
        ]
    
    def discover_builtin_plugins(self):
        """Discover and register built-in plugins"""
        try:
            from . import builtin
            
            # Find all plugin classes in builtin module
            for name in dir(builtin):
                obj = getattr(builtin, name)
                if (inspect.isclass(obj) and 
                    issubclass(obj, BasePlugin) and 
                    obj != BasePlugin):
                    try:
                        instance = obj()
                        self.register(instance)
                    except Exception as e:
                        logger.error(f"Failed to register builtin plugin {name}: {e}")
            
            logger.info(f"Discovered {len([p for p in self._plugins.values() if p.plugin_category])} builtin plugins")
        except ImportError as e:
            logger.warning(f"Could not import builtin plugins: {e}")
    
    def load_plugin_from_path(self, plugin_path: str) -> Optional[BasePlugin]:
        """
        Load a plugin from a file path
        
        Args:
            plugin_path: Path to plugin Python file
            
        Returns:
            Plugin instance or None if loading failed
        """
        try:
            path = Path(plugin_path)
            if not path.exists():
                logger.error(f"Plugin file not found: {plugin_path}")
                return None
            
            # Import the module
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                logger.error(f"Could not create module spec for: {plugin_path}")
                return None
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find plugin class in module
            for name in dir(module):
                obj = getattr(module, name)
                if (inspect.isclass(obj) and 
                    issubclass(obj, BasePlugin) and 
                    obj != BasePlugin):
                    instance = obj()
                    self.register(instance)
                    return instance
            
            logger.error(f"No plugin class found in: {plugin_path}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to load plugin from {plugin_path}: {e}")
            return None
    
    def validate_plugin(self, plugin: BasePlugin) -> List[str]:
        """
        Validate a plugin has all required properties
        
        Args:
            plugin: Plugin to validate
            
        Returns:
            List of validation error messages
        """
        errors = []
        
        if not plugin.plugin_type:
            errors.append("Plugin must have plugin_type")
        
        if not plugin.display_name:
            errors.append("Plugin must have display_name")
        
        if not plugin.description:
            errors.append("Plugin must have description")
        
        if not plugin.plugin_category:
            errors.append("Plugin must have plugin_category")
        
        if not plugin.config_schema:
            errors.append("Plugin must have config_schema")
        
        # Test execute method exists
        if not hasattr(plugin, 'execute') or not callable(plugin.execute):
            errors.append("Plugin must implement execute method")
        
        return errors
    
    def initialize(self):
        """Initialize the registry and discover builtin plugins"""
        if self._initialized:
            return
        
        self.discover_builtin_plugins()
        self._initialized = True
        logger.info(f"Plugin registry initialized with {len(self._plugins)} plugins")

# Global registry instance
_registry = None

def get_registry() -> PluginRegistry:
    """Get the global plugin registry instance"""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
        _registry.initialize()
    return _registry

def register_plugin(plugin: BasePlugin):
    """Register a plugin globally"""
    get_registry().register(plugin)

def get_plugin(plugin_type: str) -> Optional[BasePlugin]:
    """Get plugin by type"""
    return get_registry().get_plugin(plugin_type)

def list_plugins(category: str = None) -> List[Dict[str, Any]]:
    """List all plugins"""
    return get_registry().list_plugins(category)

