"""
Tests for SMTP Email Sender plugin — MX resolution, attachments, custom headers,
EHLO hostname, envelope_from, and smart defaults.
"""
import pytest
from unittest.mock import Mock, patch, mock_open, call
from plugins.registry import PluginRegistry


@pytest.fixture
def registry():
    """Create registry with built-in plugins"""
    reg = PluginRegistry()
    reg.discover_builtin_plugins()
    return reg


@pytest.fixture
def plugin(registry):
    """Get the SMTP sender plugin"""
    return registry.get_plugin("smtp_sender")


# ---------- MX Resolution ----------

@patch('dns.resolver.resolve')
def test_resolve_mx_returns_highest_priority(mock_resolve, plugin):
    """Test _resolve_mx returns the lowest-preference (highest priority) MX host"""
    mx1 = Mock()
    mx1.preference = 10
    mx1.exchange = Mock()
    mx1.exchange.__str__ = lambda self: 'mx1.example.com.'

    mx2 = Mock()
    mx2.preference = 5
    mx2.exchange = Mock()
    mx2.exchange.__str__ = lambda self: 'mx2.example.com.'

    mock_resolve.return_value = [mx1, mx2]

    result = plugin._resolve_mx('example.com')
    mock_resolve.assert_called_once_with('example.com', 'MX')
    assert result == 'mx2.example.com'  # preference 5 wins


@patch('dns.resolver.resolve')
def test_resolve_mx_strips_trailing_dot(mock_resolve, plugin):
    """Test trailing dot is stripped from MX hostname"""
    mx = Mock()
    mx.preference = 10
    mx.exchange = Mock()
    mx.exchange.__str__ = lambda self: 'mail.example.com.'

    mock_resolve.return_value = [mx]

    result = plugin._resolve_mx('example.com')
    assert result == 'mail.example.com'


# ---------- Smart Defaults (no smtp_host → direct MX) ----------

@patch('dns.resolver.resolve')
@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_direct_delivery_no_host(mock_smtp_class, mock_resolve, plugin):
    """When smtp_host is blank, resolve MX and use port 25, no TLS"""
    # Setup MX resolution
    mx = Mock()
    mx.preference = 10
    mx.exchange = Mock()
    mx.exchange.__str__ = lambda self: 'mx.recipient.com.'
    mock_resolve.return_value = [mx]

    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'from_email': 'spoof@attacker.com',
        'to_email': 'victim@recipient.com',
        'subject': 'Hello',
        'body_text': 'Test body',
    }

    result = plugin.execute(context, config)

    mock_resolve.assert_called_once_with('recipient.com', 'MX')
    mock_smtp_class.assert_called_once_with('mx.recipient.com', 25, timeout=30)
    mock_smtp.starttls.assert_not_called()
    mock_smtp.login.assert_not_called()
    mock_smtp.send_message.assert_called_once()
    mock_smtp.quit.assert_called_once()
    assert '_email_sent' in result


@patch('dns.resolver.resolve')
@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_direct_delivery_empty_string_host(mock_smtp_class, mock_resolve, plugin):
    """smtp_host set to empty string triggers direct delivery"""
    mx = Mock()
    mx.preference = 10
    mx.exchange = Mock()
    mx.exchange.__str__ = lambda self: 'mx.example.com.'
    mock_resolve.return_value = [mx]

    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': '   ',
        'from_email': 'sender@example.com',
        'to_email': 'user@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_resolve.assert_called_once_with('example.com', 'MX')
    mock_smtp_class.assert_called_once_with('mx.example.com', 25, timeout=30)
    mock_smtp.starttls.assert_not_called()


# ---------- Backwards Compatibility (smtp_host set → relay mode) ----------

@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_relay_mode_with_host(mock_smtp_class, plugin):
    """When smtp_host is set, use relay mode with port 587 and TLS"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.relay.com',
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    result = plugin.execute(context, config)

    mock_smtp_class.assert_called_once_with('smtp.relay.com', 587, timeout=30)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.send_message.assert_called_once()
    assert '_email_sent' in result


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_relay_mode_with_custom_port_and_tls(mock_smtp_class, plugin):
    """Relay mode respects explicit smtp_port and use_tls"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.relay.com',
        'smtp_port': 465,
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp_class.assert_called_once_with('smtp.relay.com', 465, timeout=30)
    mock_smtp.starttls.assert_not_called()


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_relay_mode_with_auth(mock_smtp_class, plugin):
    """Relay mode with authentication"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'smtp_username': 'user@example.com',
        'smtp_password': 'secret',
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp.login.assert_called_once_with('user@example.com', 'secret')


# ---------- EHLO Hostname ----------

@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_ehlo_hostname_sent(mock_smtp_class, plugin):
    """EHLO hostname is sent before STARTTLS and after"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'ehlo_hostname': 'mail.spoofed.com',
        'use_tls': True,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    # ehlo called twice: before starttls and after
    assert mock_smtp.ehlo.call_count == 2
    mock_smtp.ehlo.assert_any_call('mail.spoofed.com')


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_ehlo_hostname_no_tls(mock_smtp_class, plugin):
    """EHLO hostname sent once when TLS is off"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'ehlo_hostname': 'mail.spoofed.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp.ehlo.assert_called_once_with('mail.spoofed.com')
    mock_smtp.starttls.assert_not_called()


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_no_ehlo_when_blank(mock_smtp_class, plugin):
    """No explicit EHLO when ehlo_hostname is blank"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp.ehlo.assert_not_called()


# ---------- Custom Headers ----------

@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_custom_headers_applied(mock_smtp_class, plugin):
    """Custom headers are added to the email message"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {'target': {'name': 'John'}}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
        'custom_headers': {
            'Reply-To': 'reply@example.com',
            'X-Mailer': 'CustomMailer/1.0',
            'X-Target': '{{target.name}}',
        },
    }

    plugin.execute(context, config)

    # Grab the message that was sent
    sent_msg = mock_smtp.send_message.call_args[0][0]
    assert sent_msg['Reply-To'] == 'reply@example.com'
    assert sent_msg['X-Mailer'] == 'CustomMailer/1.0'
    assert sent_msg['X-Target'] == 'John'


# ---------- Attachments ----------

@patch('builtins.open', mock_open(read_data=b'file-content-here'))
@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_single_attachment(mock_smtp_class, plugin):
    """Single attachment creates mixed MIME structure"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test with attachment',
        'body_text': 'See attached',
        'attachments': [
            {'path': '/tmp/report.pdf', 'filename': 'report.pdf', 'mime_type': 'application/pdf'}
        ],
    }

    plugin.execute(context, config)

    sent_msg = mock_smtp.send_message.call_args[0][0]
    # Outer should be multipart/mixed
    assert sent_msg.get_content_type() == 'multipart/mixed'

    parts = sent_msg.get_payload()
    # First part is multipart/alternative (body)
    assert parts[0].get_content_type() == 'multipart/alternative'
    # Second part is the attachment
    assert parts[1].get_content_type() == 'application/pdf'
    assert parts[1].get_filename() == 'report.pdf'


@patch('builtins.open', mock_open(read_data=b'data'))
@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_attachment_default_filename_from_path(mock_smtp_class, plugin):
    """Attachment uses basename of path when filename not specified"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
        'attachments': [
            {'path': '/tmp/docs/invoice.xlsx'}
        ],
    }

    plugin.execute(context, config)

    sent_msg = mock_smtp.send_message.call_args[0][0]
    attachment_part = sent_msg.get_payload()[1]
    assert attachment_part.get_filename() == 'invoice.xlsx'
    # Default mime type
    assert attachment_part.get_content_type() == 'application/octet-stream'


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_no_attachments_uses_alternative(mock_smtp_class, plugin):
    """Without attachments, message is multipart/alternative"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
        'body_html': '<p>Hello</p>',
    }

    plugin.execute(context, config)

    sent_msg = mock_smtp.send_message.call_args[0][0]
    assert sent_msg.get_content_type() == 'multipart/alternative'


# ---------- Envelope From ----------

@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_envelope_from_uses_sendmail(mock_smtp_class, plugin):
    """When envelope_from is set, use sendmail() instead of send_message()"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'display@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
        'envelope_from': 'bounce@example.com',
    }

    plugin.execute(context, config)

    mock_smtp.send_message.assert_not_called()
    mock_smtp.sendmail.assert_called_once()
    args = mock_smtp.sendmail.call_args[0]
    assert args[0] == 'bounce@example.com'
    assert args[1] == 'recipient@example.com'


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_envelope_from_variable_interpolation(mock_smtp_class, plugin):
    """envelope_from supports variable interpolation"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {'campaign': {'bounce_addr': 'bounce@campaign.com'}}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'display@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
        'envelope_from': '{{campaign.bounce_addr}}',
    }

    plugin.execute(context, config)

    args = mock_smtp.sendmail.call_args[0]
    assert args[0] == 'bounce@campaign.com'


@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_no_envelope_from_uses_send_message(mock_smtp_class, plugin):
    """Without envelope_from, use send_message()"""
    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'use_tls': False,
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp.send_message.assert_called_once()
    mock_smtp.sendmail.assert_not_called()


# ---------- Config Schema ----------

def test_smtp_host_not_required(plugin):
    """smtp_host should not be in the required list"""
    schema = plugin.config_schema
    assert 'smtp_host' not in schema['required']
    assert 'from_email' in schema['required']
    assert 'to_email' in schema['required']
    assert 'subject' in schema['required']


def test_new_schema_fields_present(plugin):
    """New config fields exist in schema"""
    props = plugin.config_schema['properties']
    assert 'ehlo_hostname' in props
    assert 'envelope_from' in props
    assert 'custom_headers' in props
    assert 'attachments' in props


# ---------- Validate Config ----------

def test_validate_config_valid_no_host(plugin):
    """Config is valid without smtp_host"""
    config = {
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
    }
    errors = plugin.validate_config(config)
    assert errors is None


def test_validate_config_valid_with_host(plugin):
    """Config is valid with smtp_host (backwards compat)"""
    config = {
        'smtp_host': 'smtp.example.com',
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
    }
    errors = plugin.validate_config(config)
    assert errors is None


def test_validate_config_missing_from_email(plugin):
    """Missing from_email triggers validation error"""
    config = {
        'to_email': 'recipient@example.com',
        'subject': 'Test',
    }
    errors = plugin.validate_config(config)
    assert errors is not None
    assert any('from_email' in e for e in errors)


def test_validate_config_missing_to_email(plugin):
    """Missing to_email triggers validation error"""
    config = {
        'from_email': 'sender@example.com',
        'subject': 'Test',
    }
    errors = plugin.validate_config(config)
    assert errors is not None
    assert any('to_email' in e for e in errors)


def test_validate_config_invalid_port(plugin):
    """Invalid smtp_port triggers validation error"""
    config = {
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'smtp_port': 99999,
    }
    errors = plugin.validate_config(config)
    assert errors is not None
    assert any('smtp_port' in e for e in errors)


# ---------- Direct MX + port override ----------

@patch('dns.resolver.resolve')
@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_direct_delivery_custom_port(mock_smtp_class, mock_resolve, plugin):
    """Direct delivery respects explicit smtp_port override"""
    mx = Mock()
    mx.preference = 10
    mx.exchange = Mock()
    mx.exchange.__str__ = lambda self: 'mx.example.com.'
    mock_resolve.return_value = [mx]

    mock_smtp = Mock()
    mock_smtp_class.return_value = mock_smtp

    context = {}
    config = {
        'smtp_port': 2525,
        'from_email': 'sender@example.com',
        'to_email': 'user@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    plugin.execute(context, config)

    mock_smtp_class.assert_called_once_with('mx.example.com', 2525, timeout=30)


# ---------- Error handling ----------

@patch('plugins.builtin.smtp_sender.smtplib.SMTP')
def test_smtp_connection_error(mock_smtp_class, plugin):
    """SMTP connection error is handled gracefully"""
    mock_smtp_class.side_effect = Exception("Connection refused")

    context = {}
    config = {
        'smtp_host': 'smtp.example.com',
        'from_email': 'sender@example.com',
        'to_email': 'recipient@example.com',
        'subject': 'Test',
        'body_text': 'Hello',
    }

    result = plugin.execute(context, config)
    assert '_error' in result
    assert 'Connection refused' in result['_error']['message']
