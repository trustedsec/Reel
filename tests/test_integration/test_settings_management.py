"""
Integration tests for settings management
Tests settings CRUD and persistence
"""
import pytest
import json
from tests.helpers import get_csrf_token
from shared.database import Setting, db


@pytest.mark.integration
class TestSettingsManagement:
    """Integration tests for settings management"""
    
    def test_settings_retrieval(self, authenticated_client):
        """Test settings retrieval"""
        response = authenticated_client.get('/api/settings')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert 'success' in data
        assert data['success'] is True
        assert 'categories' in data
        assert isinstance(data['categories'], dict)
    
    def test_settings_update(self, authenticated_client, db_session):
        """Test settings update"""
        csrf_token = get_csrf_token(authenticated_client)
        
        # Update a setting
        response = authenticated_client.put('/api/settings/PHISHING_PORT',
            json={'value': 9999},
            headers={'X-CSRFToken': csrf_token}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        
        # Verify setting is updated in database
        setting = Setting.query.filter_by(key='PHISHING_PORT').first()
        assert setting is not None
        # Settings might store values as strings, check both
        assert str(setting.value) == '9999' or setting.value == 9999
    
    def test_settings_deletion(self, authenticated_client, db_session):
        """Test settings deletion (reset to default)"""
        from shared.config import Config
        
        # First, set a custom value
        csrf_token = get_csrf_token(authenticated_client)
        response = authenticated_client.put('/api/settings/PHISHING_PORT',
            json={'value': 8888},
            headers={'X-CSRFToken': csrf_token}
        )
        assert response.status_code == 200
        
        # Delete (reset) the setting
        response = authenticated_client.delete('/api/settings/PHISHING_PORT',
            headers={'X-CSRFToken': csrf_token}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        
        # Verify setting is removed from database (uses default)
        setting = Setting.query.filter_by(key='PHISHING_PORT').first()
        # Setting may be deleted or value should be default
        if setting:
            # If still exists, should be reset to default
            config = Config()
            assert setting.value == config.PHISHING_PORT or setting is None
    
    def test_settings_persistence(self, authenticated_client, db_session):
        """Test settings persist across requests"""
        csrf_token = get_csrf_token(authenticated_client)
        
        # Set a value
        test_value = 'test-persistence-value'
        response = authenticated_client.put('/api/settings/PHISHING_HOST',
            json={'value': test_value},
            headers={'X-CSRFToken': csrf_token}
        )
        assert response.status_code == 200
        
        # Retrieve settings
        response = authenticated_client.get('/api/settings')
        assert response.status_code == 200
        data = json.loads(response.data)
        
        # Find the setting in categories
        found = False
        for category_settings in data['categories'].values():
            for setting in category_settings:
                if setting['key'] == 'PHISHING_HOST':
                    assert setting['value'] == test_value
                    found = True
                    break
            if found:
                break
        
        assert found, "Setting should be found in retrieved settings"
