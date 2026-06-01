"""
Tests for API routes (non-mocked tests only)
"""
import pytest
import json


def test_list_plugins_unauthorized(client):
    """Test listing plugins without authentication"""
    response = client.get('/api/plugins')
    assert response.status_code == 401


def test_list_plugins_authorized(authenticated_client):
    """Test listing plugins with authentication"""
    response = authenticated_client.get('/api/plugins')
    assert response.status_code == 200
    data = json.loads(response.data)
    # API returns {'plugins': [...]} not a list
    assert isinstance(data, dict)
    assert 'plugins' in data
    assert isinstance(data['plugins'], list)
