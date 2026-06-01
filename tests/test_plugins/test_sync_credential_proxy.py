"""
Tests for the SyncCredentialProxyPlugin.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from plugins.builtin.sync_credential_proxy import SyncCredentialProxyPlugin


@pytest.fixture
def plugin():
    return SyncCredentialProxyPlugin()


@pytest.fixture
def base_context():
    return {
        'campaign': {'id': 1, 'uid': 'test-uid'},
        'session': {},
        'request': {'form_data': {}},
        'captured_credentials': {'email': 'test@example.com', 'password': 'pass123'},
    }


@pytest.fixture
def mock_manager():
    """Mock SyncProxyManager."""
    manager = Mock()
    manager.get_session.return_value = None
    manager.remove_session = Mock()
    return manager


@pytest.fixture
def mock_proxy_session():
    """Mock SyncProxySession."""
    session = Mock()
    session.session_id = 'test-session-id-123'
    return session


class TestPluginProperties:
    def test_plugin_type(self, plugin):
        assert plugin.plugin_type == "sync_credential_proxy"

    def test_display_name(self, plugin):
        assert plugin.display_name == "Sync Credential Proxy"

    def test_category(self, plugin):
        assert plugin.plugin_category == "capture"

    def test_branch_context_key(self, plugin):
        assert plugin.get_branch_context_key() == "sync_proxy_success"

    def test_branch_labels(self, plugin):
        labels = plugin.get_branch_labels()
        assert labels["true"] == "Success"
        assert labels["false"] == "Needs Input / Failed"

    def test_config_schema(self, plugin):
        schema = plugin.config_schema
        assert schema["required"] == ["target_url"]
        assert "target_url" in schema["properties"]
        assert "mfa_template_source" in schema["properties"]
        assert "timeout" in schema["properties"]

    def test_config_schema_has_proxy(self, plugin):
        schema = plugin.config_schema
        assert "proxy" in schema["properties"]
        proxy_schema = schema["properties"]["proxy"]
        assert proxy_schema["type"] == "object"
        assert "server" in proxy_schema["properties"]
        assert "username" in proxy_schema["properties"]
        assert "password" in proxy_schema["properties"]


class TestConfigValidation:
    def test_valid_config(self, plugin):
        result = plugin.validate_config({"target_url": "https://login.example.com"})
        assert result is None

    def test_missing_target_url(self, plugin):
        result = plugin.validate_config({})
        assert result is not None
        assert any("target_url" in e for e in result)

    def test_custom_template_required(self, plugin):
        result = plugin.validate_config({
            "target_url": "https://login.example.com",
            "mfa_template_source": "custom",
        })
        assert result is not None
        assert any("mfa_custom_template" in e for e in result)

    def test_library_template_required(self, plugin):
        result = plugin.validate_config({
            "target_url": "https://login.example.com",
            "mfa_template_source": "library",
        })
        assert result is not None
        assert any("mfa_template_id" in e for e in result)


class TestExecuteInitial:
    """Test the initial POST (first credential submission)."""

    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_no_manager_returns_error(self, mock_get_mgr, plugin, base_context):
        mock_get_mgr.return_value = None
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result

    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_no_credentials_skips(self, mock_get_mgr, plugin, mock_manager):
        mock_get_mgr.return_value = mock_manager
        context = {
            'campaign': {'id': 1, 'uid': 'test'},
            'session': {},
            'request': {'form_data': {}},
        }
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(context, config)

        assert result['sync_proxy_success'] is False
        mock_manager.create_session.assert_not_called()

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_success_result(self, mock_get_mgr, mock_db, plugin, base_context,
                            mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'success',
            'cookies': [{'name': 'sid', 'value': 'abc'}],
            'final_url': 'https://login.example.com/dashboard',
            'page_title': 'Dashboard',
        }
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is True
        assert result['sync_proxy_result']['status'] == 'success'
        mock_manager.remove_session.assert_called_once_with('test-session-id-123')

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_needs_input_result(self, mock_get_mgr, mock_db, plugin, base_context,
                                mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'needs_input',
            'prompt_text': 'Enter your verification code',
            'input_type': 'text',
        }
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result
        assert 'verification' in result['_response_html'].lower()
        # Session key should be set
        assert result['_set_session']['_sync_proxy_session_id'] == 'test-session-id-123'
        mock_manager.remove_session.assert_not_called()

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_needs_display_result(self, mock_get_mgr, mock_db, plugin, base_context,
                                  mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'needs_display',
            'display_value': '42',
            'prompt_text': 'Select the number on your phone',
        }
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result
        assert '42' in result['_response_html']
        assert result['_set_session']['_sync_proxy_session_id'] == 'test-session-id-123'

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_failed_result(self, mock_get_mgr, mock_db, plugin, base_context,
                           mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'failed',
            'error': 'Login failed',
        }
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result
        mock_manager.remove_session.assert_called_once()


class TestExecuteContinue:
    """Test continuation POST (MFA code submission)."""

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_continue_success(self, mock_get_mgr, mock_db, plugin, base_context,
                              mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.get_session.return_value = mock_proxy_session
        mock_proxy_session.execute_continue.return_value = {
            'status': 'success',
            'cookies': [{'name': 'sid', 'value': 'abc'}],
            'final_url': 'https://login.example.com/dashboard',
            'page_title': 'Dashboard',
        }
        base_context['session']['_sync_proxy_session_id'] = 'test-session-id-123'
        base_context['request']['form_data'] = {'mfa_code': '123456'}
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is True
        mock_proxy_session.execute_continue.assert_called_once_with('123456', timeout=30)
        mock_manager.remove_session.assert_called_once()

    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_expired_session(self, mock_get_mgr, plugin, base_context, mock_manager):
        mock_get_mgr.return_value = mock_manager
        mock_manager.get_session.return_value = None  # Session expired
        base_context['session']['_sync_proxy_session_id'] = 'expired-id'
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result
        assert 'expired' in result['_response_html'].lower()

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_empty_mfa_re_renders(self, mock_get_mgr, mock_db, plugin, base_context,
                                  mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.get_session.return_value = mock_proxy_session
        base_context['session']['_sync_proxy_session_id'] = 'test-session-id-123'
        base_context['request']['form_data'] = {'mfa_code': ''}
        config = {"target_url": "https://login.example.com"}

        result = plugin.execute(base_context, config)

        assert result['sync_proxy_success'] is False
        assert '_response_html' in result
        mock_proxy_session.execute_continue.assert_not_called()


class TestMfaPageRendering:
    def test_auto_mfa_page(self, plugin):
        html = plugin._render_auto_mfa_page({
            'mfa_prompt_text': 'Enter code',
            'mfa_field_name': 'mfa_code',
            'form_action': '/test-uid',
            'input_type': 'text',
        })
        assert 'Enter code' in html
        assert 'name="mfa_code"' in html
        assert 'action="/test-uid"' in html

    def test_auto_number_matching_page(self, plugin):
        html = plugin._render_auto_number_matching_page({
            'mfa_prompt_text': 'Approve sign-in',
            'display_value': '42',
            'form_action': '/test-uid',
            'mfa_field_name': 'mfa_code',
            'poll_url': '/test-uid/proxy-status/abc123',
        })
        assert '42' in html
        assert 'Approve sign-in' in html
        assert '/test-uid/proxy-status/abc123' in html

    def test_error_page(self, plugin):
        html = plugin._render_error_page('Something broke')
        assert 'Something broke' in html

class TestProxyConfigMerge:
    """Test that proxy config is merged into browser_config."""

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_proxy_merged_into_browser_config(self, mock_get_mgr, mock_db, plugin,
                                               base_context, mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'success', 'cookies': [],
        }
        config = {
            "target_url": "https://login.example.com",
            "proxy": {"server": "socks5://1.2.3.4:1080"},
        }

        plugin.execute(base_context, config)

        call_kwargs = mock_manager.create_session.call_args
        browser_cfg = call_kwargs[1]['config']['browser_config'] if 'config' in (call_kwargs[1] or {}) else call_kwargs.kwargs['config']['browser_config']
        assert browser_cfg['proxy'] == {"server": "socks5://1.2.3.4:1080"}

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_proxy_merged_with_existing_browser_config(self, mock_get_mgr, mock_db, plugin,
                                                        base_context, mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'success', 'cookies': [],
        }
        config = {
            "target_url": "https://login.example.com",
            "browser_config": {"headless": False, "stealth_mode": True},
            "proxy": {"server": "socks5://1.2.3.4:1080", "username": "user", "password": "pass"},
        }

        plugin.execute(base_context, config)

        call_kwargs = mock_manager.create_session.call_args
        browser_cfg = call_kwargs.kwargs['config']['browser_config']
        assert browser_cfg['headless'] is False
        assert browser_cfg['stealth_mode'] is True
        assert browser_cfg['proxy'] == {"server": "socks5://1.2.3.4:1080", "username": "user", "password": "pass"}

    @patch('plugins.builtin.sync_credential_proxy.db')
    @patch('workflows.sync_proxy_manager.get_sync_proxy_manager')
    def test_no_proxy_leaves_browser_config_unchanged(self, mock_get_mgr, mock_db, plugin,
                                                       base_context, mock_manager, mock_proxy_session):
        mock_get_mgr.return_value = mock_manager
        mock_manager.create_session.return_value = mock_proxy_session
        mock_proxy_session.execute_initial.return_value = {
            'status': 'success', 'cookies': [],
        }
        config = {
            "target_url": "https://login.example.com",
            "browser_config": {"headless": True},
        }

        plugin.execute(base_context, config)

        call_kwargs = mock_manager.create_session.call_args
        browser_cfg = call_kwargs.kwargs['config']['browser_config']
        assert 'proxy' not in browser_cfg
        assert browser_cfg['headless'] is True


    def test_xss_prevention_in_mfa_page(self, plugin):
        html = plugin._render_auto_mfa_page({
            'mfa_prompt_text': '<script>alert("xss")</script>',
            'mfa_field_name': 'mfa_code',
            'form_action': '/test',
            'input_type': 'text',
        })
        assert '<script>' not in html
        assert '&lt;script&gt;' in html
