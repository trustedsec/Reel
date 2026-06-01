"""
Error handling tests for all plugins
"""
import pytest
from unittest.mock import Mock, patch
from plugins.registry import PluginRegistry


@pytest.fixture
def registry():
    """Create registry with built-in plugins"""
    reg = PluginRegistry()
    reg.discover_builtin_plugins()
    return reg


def test_all_plugins_handle_missing_config(registry):
    """Test that all plugins handle missing config gracefully"""
    plugin_types = [
        "phishing_detector", "conditional", "data_transform", "delay",
        "email_validator", "slack", "pushover", "target_selector",
        "url_obfuscator", "capture_credentials", "render_template", "redirect",
        "log_event", "validate_input", "useragent_check", "smtp_sender",
        "captcha", "aws_connect_dialer", "graphspy"
    ]
    
    for plugin_type in plugin_types:
        plugin = registry.get_plugin(plugin_type)
        if plugin:
            context = {}
            config = {}  # Empty config
            
            result = plugin.execute(context, config)
            # Should not crash, either return context or error
            assert result is not None
            # May have _error key if config is invalid
            assert isinstance(result, dict)


def test_all_plugins_handle_invalid_config_values(registry):
    """Test that all plugins handle invalid config values"""
    plugin_types = [
        "phishing_detector", "conditional", "data_transform", "delay",
        "email_validator", "slack", "pushover", "target_selector",
        "url_obfuscator", "capture_credentials", "render_template", "redirect",
        "log_event", "validate_input", "useragent_check", "smtp_sender",
        "captcha", "aws_connect_dialer", "graphspy"
    ]
    
    for plugin_type in plugin_types:
        plugin = registry.get_plugin(plugin_type)
        if plugin:
            context = {}
            config = {
                'invalid_field': 'invalid_value',
                'another_invalid': 99999
            }
            
            result = plugin.execute(context, config)
            # Should not crash
            assert result is not None
            assert isinstance(result, dict)


def test_all_plugins_handle_missing_context_variables(registry):
    """Test that all plugins handle missing context variables"""
    plugin_types = [
        "phishing_detector", "conditional", "data_transform", "delay",
        "email_validator", "slack", "pushover", "target_selector",
        "url_obfuscator", "capture_credentials", "render_template", "redirect",
        "log_event", "validate_input", "useragent_check", "smtp_sender",
        "captcha", "aws_connect_dialer", "graphspy"
    ]
    
    for plugin_type in plugin_types:
        plugin = registry.get_plugin(plugin_type)
        if plugin:
            context = {}  # Empty context
            config = plugin.config_schema.get('properties', {})
            # Create minimal valid config
            minimal_config = {}
            for key, value in config.items():
                if 'default' in value:
                    minimal_config[key] = value['default']
            
            result = plugin.execute(context, minimal_config)
            # Should not crash
            assert result is not None
            assert isinstance(result, dict)


@patch('plugins.builtin.slack.requests.post')
def test_slack_network_error(mock_post, registry):
    """Test Slack plugin handles network errors"""
    plugin = registry.get_plugin("slack")
    
    mock_post.side_effect = Exception("Network connection failed")
    
    context = {}
    config = {
        'webhook_url': 'https://hooks.slack.com/test',
        'message': 'Test message'
    }
    
    result = plugin.execute(context, config)
    # Slack plugin returns _error on failure
    assert '_error' in result


@patch('plugins.builtin.pushover.requests.post')
def test_pushover_network_error(mock_post, registry):
    """Test Pushover plugin handles network errors"""
    plugin = registry.get_plugin("pushover")
    
    mock_post.side_effect = Exception("Network connection failed")
    
    context = {}
    config = {
        'api_token': 'test_token',
        'user_key': 'test_user',
        'message': 'Test message'
    }
    
    result = plugin.execute(context, config)
    # Slack plugin returns _error on failure
    assert '_error' in result


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_smtp_connection_error(mock_smtp_class, registry):
    """Test SMTP plugin handles connection errors"""
    plugin = registry.get_plugin("smtp_sender")
    
    mock_smtp_class.side_effect = Exception("SMTP connection failed")
    
    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Test message'
    }
    
    result = plugin.execute(context, config)
    # Slack plugin returns _error on failure
    assert '_error' in result


@patch('plugins.builtin.graphspy.requests.post')
def test_graphspy_network_error(mock_post, registry):
    """Test GraphSpy plugin handles network errors"""
    plugin = registry.get_plugin("graphspy")
    
    mock_post.side_effect = Exception("API connection failed")
    
    context = {}
    config = {
        'graphspy_url': 'http://graphspy.example.com/api/generate_device_code'
    }
    
    result = plugin.execute(context, config)
    # Slack plugin returns _error on failure
    assert '_error' in result


@patch('plugins.builtin.aws_connect_dialer.BOTO3_AVAILABLE', True)
@patch('plugins.builtin.aws_connect_dialer.boto3')
def test_aws_connect_network_error(mock_boto3, registry):
    """Test AWS Connect plugin handles network errors"""
    from botocore.exceptions import ClientError
    
    plugin = registry.get_plugin("aws_connect_dialer")
    
    mock_boto3.client.side_effect = ClientError(
        {'Error': {'Code': 'NetworkError'}},
        'StartOutboundVoiceContact'
    )
    
    context = {'phone': '+1234567890'}
    config = {
        'instance_id': 'test-instance-id',
        'contact_flow_id': 'test-flow-id',
        'source_phone': '+1987654321'
    }
    
    result = plugin.execute(context, config)
    # AWS Connect plugin returns _call_result on failure
    assert '_call_result' in result
    assert result['_call_result']['success'] is False
    assert result['_call_result']['error'] == 'Call failed'


def test_capture_credentials_database_error(registry, db_session):
    """Test Capture Credentials plugin handles database errors"""
    from shared.database import db
    
    plugin = registry.get_plugin("capture_credentials")
    
    # Force a database error by using invalid data
    context = {
        'campaign': {'id': 999999},  # Non-existent campaign
        'request': {
            'form_data': {
                'email': 'test@example.com',
                'password': 'secret123'
            },
            'ip_address': '192.168.1.100',
            'user_agent': 'Mozilla/5.0'
        }
    }
    config = {
        'username_field': 'email',
        'password_field': 'password'
    }
    
    result = plugin.execute(context, config)
    # Should handle error gracefully
    assert result is not None


def test_log_event_database_error(registry, db_session):
    """Test Log Event plugin handles database errors"""
    plugin = registry.get_plugin("log_event")
    
    # Force a database error
    context = {
        'campaign': {'id': 999999},  # Non-existent campaign
        'request': {
            'ip_address': '192.168.1.100',
            'user_agent': 'Mozilla/5.0'
        }
    }
    config = {
        'event_type': 'custom'
    }
    
    result = plugin.execute(context, config)
    # Should handle error gracefully
    assert result is not None


def test_plugins_handle_timeout_errors(registry):
    """Test that plugins handle timeout errors gracefully"""
    # This is a general test - specific timeout handling depends on plugin
    plugin = registry.get_plugin("delay")
    
    context = {}
    config = {
        'delay_type': 'fixed',
        'delay_seconds': 0.01  # Very short delay
    }
    
    result = plugin.execute(context, config)
    assert result is not None


def test_plugins_handle_malformed_input(registry):
    """Test that plugins handle malformed input data"""
    plugin = registry.get_plugin("validate_input")
    
    context = {
        'request': {
            'form_data': {
                'email': None,  # None instead of string
                'password': 12345  # Number instead of string
            }
        }
    }
    config = {
        'fields': [
            {'name': 'email', 'type': 'email'},
            {'name': 'password', 'type': 'string'}
        ],
        'on_error': 'continue'
    }
    
    result = plugin.execute(context, config)
    # Should handle gracefully
    assert result is not None


def test_plugins_on_error_method(registry):
    """Test that all plugins have on_error method"""
    plugin_types = [
        "phishing_detector", "conditional", "data_transform", "delay",
        "email_validator", "slack", "pushover", "target_selector",
        "url_obfuscator", "capture_credentials", "render_template", "redirect",
        "log_event", "validate_input", "useragent_check", "smtp_sender",
        "captcha", "aws_connect_dialer", "graphspy"
    ]
    
    for plugin_type in plugin_types:
        plugin = registry.get_plugin(plugin_type)
        if plugin:
            assert hasattr(plugin, 'on_error')
            assert callable(plugin.on_error)
            
            # Test on_error with sample error
            context = {}
            config = {}
            error = Exception("Test error")
            
            result = plugin.on_error(error, context, config)
            assert result is not None
            assert isinstance(result, dict)
            assert '_error' in result
