"""
Pytest configuration and shared fixtures for Reel v2 tests
"""
import pytest
import os
import tempfile
from pathlib import Path
from flask import Flask
from unittest.mock import Mock, AsyncMock, MagicMock, patch

# Set test environment variables before imports
os.environ['TESTING'] = 'True'
os.environ['SECRET_KEY'] = 'test-secret-key-for-testing-only'
os.environ['JWT_SECRET_KEY'] = 'test-jwt-secret-key-for-testing-only'
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

from app import create_admin_app
from shared.database import db, init_app as init_database
from shared.auth import init_app as init_auth
from shared.config import TestingConfig


@pytest.fixture(scope='session')
def app():
    """Create Flask application for testing"""
    # Use create_admin_app to get proper CSRF setup
    app = create_admin_app()
    
    # Create test directories
    test_dirs = ['storage/uploads', 'storage/templates', 'storage/assets', 'storage/plugins', 'logs']
    for dir_path in test_dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
    
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    """Flask test client"""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """Database session with automatic rollback"""
    with app.app_context():
        # Start a transaction
        connection = db.engine.connect()
        transaction = connection.begin()
        
        # Create a session bound to this connection
        session = db.session
        
        yield session
        
        # Rollback transaction
        transaction.rollback()
        connection.close()


@pytest.fixture
def authenticated_client(client, db_session, app):
    """Flask test client with authenticated user"""
    from shared.database import User
    from werkzeug.security import generate_password_hash
    from flask_login import login_user
    import uuid
    
    # Use unique email per test to avoid conflicts
    unique_email = f'test-{uuid.uuid4().hex[:8]}@example.com'
    unique_username = f'testuser-{uuid.uuid4().hex[:8]}'
    
    # Check if user exists (shouldn't, but just in case)
    user = User.query.filter_by(email=unique_email).first()
    if not user:
        user = User(
            username=unique_username,
            email=unique_email,
            password_hash=generate_password_hash('testpass'),
            is_active=True,
            is_admin=True
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    
    # Login using Flask-Login's login_user within request context
    with app.test_request_context():
        login_user(user, remember=True)
        # Also set session directly for compatibility
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
    
    return client


@pytest.fixture
def mock_openai():
    """Mock OpenAI client"""
    with patch('openai.OpenAI') as mock:
        mock_client = Mock()
        mock.return_value = mock_client
        
        # Mock chat completions
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message = Mock()
        mock_response.choices[0].message.content = '{"email": "input[name=\\"email\\"]", "password": "input[type=\\"password\\"]", "submit_button": "button[type=\\"submit\\"]"}'
        
        mock_client.chat.completions.create = Mock(return_value=mock_response)
        
        yield mock_client


@pytest.fixture
def mock_anthropic():
    """Mock Anthropic client"""
    with patch('anthropic.Anthropic') as mock:
        mock_client = Mock()
        mock.return_value = mock_client
        
        # Mock messages
        mock_message = Mock()
        mock_message.content = [Mock()]
        mock_message.content[0].text = '{"email": "input[name=\\"email\\"]", "password": "input[type=\\"password\\"]", "submit_button": "button[type=\\"submit\\"]"}'
        
        mock_response = Mock()
        mock_response.content = [mock_message]
        
        mock_client.messages.create = Mock(return_value=mock_response)
        
        yield mock_client


@pytest.fixture
def mock_playwright():
    """Mock Playwright browser automation"""
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    
    # Setup mock chain: browser -> context -> page
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    
    # Mock page methods
    mock_page.url = 'https://example.com'
    mock_page.title = AsyncMock(return_value='Test Page')
    mock_page.goto = AsyncMock()
    mock_page.screenshot = AsyncMock(return_value=b'fake_screenshot_data')
    mock_page.content = AsyncMock(return_value='<html><body>Test</body></html>')
    mock_page.query_selector = AsyncMock(return_value=Mock())
    mock_page.query_selector_all = AsyncMock(return_value=[])
    mock_page.wait_for_selector = AsyncMock()
    mock_page.click = AsyncMock()
    mock_page.fill = AsyncMock()
    mock_page.keyboard.press = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.close = AsyncMock()
    mock_page.inner_text = AsyncMock(return_value='Test Text')
    
    # Mock playwright instance
    mock_playwright_instance = AsyncMock()
    mock_playwright_instance.chromium.launch.return_value = mock_browser
    mock_playwright_instance.stop = AsyncMock()
    
    with patch('playwright.async_api.async_playwright') as mock_playwright_func:
        mock_playwright_func.return_value.__aenter__.return_value = mock_playwright_instance
        yield {
            'playwright': mock_playwright_instance,
            'browser': mock_browser,
            'context': mock_context,
            'page': mock_page
        }


@pytest.fixture
def sample_campaign():
    """Sample campaign data"""
    return {
        'id': 1,
        'uid': 'test-campaign-123',
        'name': 'Test Campaign',
        'campaign_type': 'credential_proxy',
        'template_id': 1,
        'config': {
            'fields': ['email', 'password'],
            'target_sites': [
                {
                    'url': 'https://example.com/login',
                    'timeout': 30
                }
            ],
            'ai_config': {
                'provider': 'openai',
                'model': 'gpt-4-vision-preview',
                'api_key': 'test-api-key'
            },
            'browser_config': {
                'headless': True
            },
            'automation_timeout': 60,
            'max_retries': 2,
            'redirect_url': 'https://www.microsoft.com/404'
        },
        'is_active': True
    }


@pytest.fixture
def sample_template():
    """Sample template data"""
    return {
        'id': 1,
        'name': 'Test Template',
        'content': '<html><body><form><input name="email"/><input type="password" name="password"/><button type="submit">Login</button></form></body></html>',
        'phishing_detection_result': None,
        'phishing_detection_confidence': None,
        'phishing_detection_override': False
    }


@pytest.fixture
def sample_credentials():
    """Sample credential data"""
    return {
        'email': 'test@example.com',
        'password': 'testpassword123'
    }


@pytest.fixture
def sample_plugin_code():
    """Sample plugin code for testing"""
    return '''
from plugins.base import BasePlugin
from typing import Dict, Any

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
            "properties": {},
            "required": []
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any] = None) -> Dict[str, Any]:
        return {"status": "success", "message": "Test plugin executed"}
'''


@pytest.fixture
def temp_plugin_file(sample_plugin_code, tmp_path):
    """Create a temporary plugin file"""
    plugin_file = tmp_path / "test_plugin.py"
    plugin_file.write_text(sample_plugin_code)
    return str(plugin_file)


@pytest.fixture(autouse=True)
def reset_plugin_registry():
    """Reset plugin registry before each test"""
    from plugins.registry import PluginRegistry
    # Clear registry before test
    registry = PluginRegistry()
    registry._plugins.clear()
    registry._plugin_classes.clear()
    yield
    # Clear registry after test
    registry._plugins.clear()
    registry._plugin_classes.clear()


@pytest.fixture
def mock_requests():
    """Mock requests library for HTTP calls"""
    with patch('requests.post') as mock_post, \
         patch('requests.get') as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'status': 'success'}
        mock_response.text = 'OK'
        mock_post.return_value = mock_response
        mock_get.return_value = mock_response
        yield {'post': mock_post, 'get': mock_get}


@pytest.fixture
def mock_file_operations(tmp_path):
    """Mock file operations with temporary directory"""
    upload_dir = tmp_path / "uploads"
    templates_dir = tmp_path / "templates"
    plugins_dir = tmp_path / "plugins"
    
    upload_dir.mkdir()
    templates_dir.mkdir()
    plugins_dir.mkdir()
    
    with patch('shared.config.Config.UPLOAD_FOLDER', upload_dir), \
         patch('shared.config.Config.TEMPLATES_FOLDER', templates_dir), \
         patch('shared.config.Config.ASSETS_FOLDER', tmp_path / "assets"):
        yield {
            'upload_dir': upload_dir,
            'templates_dir': templates_dir,
            'plugins_dir': plugins_dir
        }


