"""
Tests for PluginRegistry
"""
import pytest
from plugins.registry import PluginRegistry
from plugins.base import BasePlugin


class TestPlugin(BasePlugin):
    """Test plugin for registry tests"""
    
    @property
    def plugin_type(self):
        return "test"
    
    @property
    def display_name(self):
        return "Test Plugin"
    
    @property
    def description(self):
        return "A test plugin"
    
    @property
    def plugin_category(self):
        return "test"
    
    @property
    def config_schema(self):
        return {"type": "object"}
    
    def execute(self, context, config):
        return context


class AnotherPlugin(BasePlugin):
    """Another test plugin"""
    
    @property
    def plugin_type(self):
        return "another"
    
    @property
    def display_name(self):
        return "Another Plugin"
    
    @property
    def description(self):
        return "Another test plugin"
    
    @property
    def plugin_category(self):
        return "test"
    
    @property
    def config_schema(self):
        return {"type": "object"}
    
    def execute(self, context, config):
        return context


@pytest.fixture
def registry():
    """Create a fresh registry for each test"""
    return PluginRegistry()


def test_registry_register_plugin(registry):
    """Test registering a plugin instance"""
    plugin = TestPlugin()
    registry.register(plugin)
    
    assert registry.get_plugin("test") == plugin


def test_registry_register_invalid_plugin(registry):
    """Test registering an invalid plugin raises error"""
    with pytest.raises(ValueError, match="must inherit from BasePlugin"):
        registry.register("not a plugin")


def test_registry_register_duplicate_plugin(registry):
    """Test registering duplicate plugin overwrites"""
    plugin1 = TestPlugin()
    plugin2 = TestPlugin()
    
    registry.register(plugin1)
    registry.register(plugin2)
    
    # Should have only one plugin, the second one
    assert registry.get_plugin("test") == plugin2


def test_registry_register_class(registry):
    """Test registering a plugin class"""
    registry.register_class(TestPlugin)
    
    plugin = registry.get_plugin("test")
    assert plugin is not None
    assert isinstance(plugin, TestPlugin)


def test_registry_register_invalid_class(registry):
    """Test registering invalid class raises error"""
    class NotAPlugin:
        pass
    
    with pytest.raises(ValueError, match="must inherit from BasePlugin"):
        registry.register_class(NotAPlugin)


def test_registry_get_plugin(registry):
    """Test getting a plugin by type"""
    plugin = TestPlugin()
    registry.register(plugin)
    
    retrieved = registry.get_plugin("test")
    assert retrieved == plugin
    
    # Non-existent plugin
    assert registry.get_plugin("nonexistent") is None


def test_registry_list_plugins(registry):
    """Test listing all plugins"""
    plugin1 = TestPlugin()
    plugin2 = AnotherPlugin()
    
    registry.register(plugin1)
    registry.register(plugin2)
    
    plugins = registry.list_plugins()
    assert len(plugins) == 2
    
    plugin_types = [p['plugin_type'] for p in plugins]
    assert 'test' in plugin_types
    assert 'another' in plugin_types


def test_registry_list_plugins_by_category(registry):
    """Test listing plugins by category"""
    plugin1 = TestPlugin()
    plugin2 = AnotherPlugin()
    
    registry.register(plugin1)
    registry.register(plugin2)
    
    plugins = registry.list_plugins(category="test")
    assert len(plugins) == 2
    
    plugins = registry.list_plugins(category="nonexistent")
    assert len(plugins) == 0


def test_registry_get_plugins_by_category(registry):
    """Test getting plugins by category"""
    plugin1 = TestPlugin()
    plugin2 = AnotherPlugin()
    
    registry.register(plugin1)
    registry.register(plugin2)
    
    plugins = registry.get_plugins_by_category("test")
    assert len(plugins) == 2
    assert all(isinstance(p, BasePlugin) for p in plugins)


def test_registry_validate_plugin(registry):
    """Test plugin validation"""
    plugin = TestPlugin()
    errors = registry.validate_plugin(plugin)
    assert len(errors) == 0


def test_registry_validate_incomplete_plugin(registry):
    """Test validation catches incomplete plugins"""
    class IncompletePlugin(BasePlugin):
        @property
        def plugin_type(self):
            return "incomplete"
        
        @property
        def display_name(self):
            return "Incomplete"
        
        @property
        def description(self):
            return ""
        
        @property
        def plugin_category(self):
            return ""
        
        @property
        def config_schema(self):
            return {}
        
        def execute(self, context, config):
            return context
    
    plugin = IncompletePlugin()
    errors = registry.validate_plugin(plugin)
    # Should have errors for empty description and category
    assert len(errors) > 0


def test_registry_load_plugin_from_path(registry, temp_plugin_file):
    """Test loading plugin from file path"""
    plugin = registry.load_plugin_from_path(temp_plugin_file)
    
    assert plugin is not None
    assert plugin.plugin_type == "test"
    assert registry.get_plugin("test") == plugin


def test_registry_load_plugin_from_nonexistent_path(registry):
    """Test loading plugin from nonexistent path"""
    plugin = registry.load_plugin_from_path("/nonexistent/path/plugin.py")
    assert plugin is None


def test_registry_discover_builtin_plugins(registry):
    """Test discovering built-in plugins"""
    registry.discover_builtin_plugins()
    
    # Should have discovered at least some built-in plugins
    plugins = registry.list_plugins()
    assert len(plugins) > 0
    
    # Check for known built-in plugins
    plugin_types = [p['plugin_type'] for p in plugins]
    # At least one built-in plugin should be registered
    assert len(plugin_types) > 0


def test_registry_initialize(registry):
    """Test registry initialization"""
    assert not registry._initialized
    
    registry.initialize()
    assert registry._initialized
    
    # Should have discovered built-in plugins
    plugins = registry.list_plugins()
    assert len(plugins) > 0
    
    # Second initialize should not duplicate
    plugin_count = len(plugins)
    registry.initialize()
    assert len(registry.list_plugins()) == plugin_count


