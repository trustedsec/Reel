"""
Tests for BasePlugin abstract class
"""
import pytest
from abc import ABC
from plugins.base import BasePlugin


def test_base_plugin_is_abstract():
    """Test that BasePlugin cannot be instantiated directly"""
    with pytest.raises(TypeError):
        BasePlugin()


def test_base_plugin_enforces_required_properties():
    """Test that subclasses must implement required properties"""
    
    class IncompletePlugin(BasePlugin):
        pass
    
    with pytest.raises(TypeError):
        IncompletePlugin()


def test_complete_plugin_implementation():
    """Test a complete plugin implementation"""
    
    class TestPlugin(BasePlugin):
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
            return {
                "type": "object",
                "properties": {
                    "test_param": {"type": "string"}
                }
            }
        
        def execute(self, context, config):
            context['test'] = 'executed'
            return context
    
    plugin = TestPlugin()
    assert plugin.plugin_type == "test"
    assert plugin.display_name == "Test Plugin"
    assert plugin.description == "A test plugin"
    assert plugin.plugin_category == "test"
    assert plugin.config_schema is not None


def test_plugin_validate_config():
    """Test plugin config validation"""
    
    class TestPlugin(BasePlugin):
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
        
        def validate_config(self, config):
            if 'required_field' not in config:
                return ["Missing required_field"]
            return None
    
    plugin = TestPlugin()
    
    # Valid config
    assert plugin.validate_config({'required_field': 'value'}) is None
    
    # Invalid config
    errors = plugin.validate_config({})
    assert errors == ["Missing required_field"]


def test_plugin_get_required_context_keys():
    """Test plugin required context keys"""
    
    class TestPlugin(BasePlugin):
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
        
        def get_required_context_keys(self):
            return ['email', 'name']
    
    plugin = TestPlugin()
    assert plugin.get_required_context_keys() == ['email', 'name']


def test_plugin_get_provided_context_keys():
    """Test plugin provided context keys"""
    
    class TestPlugin(BasePlugin):
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
        
        def get_provided_context_keys(self):
            return ['result', 'status']
    
    plugin = TestPlugin()
    assert plugin.get_provided_context_keys() == ['result', 'status']


def test_plugin_on_error():
    """Test plugin error handling"""
    
    class TestPlugin(BasePlugin):
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
    
    plugin = TestPlugin()
    context = {'data': 'test'}
    error = ValueError("Test error")
    
    result = plugin.on_error(error, context, {})
    
    assert '_error' in result
    assert result['_error']['plugin'] == 'test'
    assert result['_error']['message'] == 'Test error'
    assert result['_error']['type'] == 'ValueError'


def test_plugin_to_dict():
    """Test plugin metadata serialization"""
    
    class TestPlugin(BasePlugin):
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
    
    plugin = TestPlugin()
    metadata = plugin.to_dict()
    
    assert metadata['plugin_type'] == 'test'
    assert metadata['display_name'] == 'Test Plugin'
    assert metadata['description'] == 'A test plugin'
    assert metadata['plugin_category'] == 'test'
    assert metadata['config_schema'] == {"type": "object"}
    assert 'required_context_keys' in metadata
    assert 'provided_context_keys' in metadata


