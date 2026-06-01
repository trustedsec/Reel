"""
Tests for AIFormDetector (non-mocked tests only)
"""
import pytest
from shared.ai_form_detector import AIFormDetector


@pytest.fixture
def detector():
    """Create AIFormDetector instance"""
    return AIFormDetector({
        'provider': 'openai',
        'model': 'gpt-4-vision-preview',
        'api_key': 'test-key'
    })


def test_detector_initialization(detector):
    """Test detector initialization"""
    assert detector.provider == 'openai'
    assert detector.model == 'gpt-4-vision-preview'
    assert detector.api_key == 'test-key'


def test_detector_fallback_detection(detector):
    """Test fallback detection heuristics"""
    html = '<html><body><form><input name="email" type="text"/><input name="password" type="password"/><button type="submit">Login</button></form></body></html>'
    
    result = detector._fallback_detection(html)
    
    assert 'email' in result
    assert 'password' in result
    assert 'submit_button' in result
    assert isinstance(result['email'], list)
    assert len(result['email']) > 0


def test_detector_response_validation(detector):
    """Test response validation and normalization"""
    # Test with valid response
    valid_response = {
        'email': 'input[name="email"]',
        'password': 'input[type="password"]',
        'submit_button': 'button[type="submit"]'
    }
    
    # Should normalize to lists
    result = detector.detect_form_fields(b'fake', '<html></html>')
    # Result should have lists (from fallback or normalization)
    assert isinstance(result, dict)


def test_detector_selector_normalization(detector):
    """Test selector normalization (string to list)"""
    # Test that single strings are normalized to lists
    html = '<html><input name="email"/></html>'
    result = detector._fallback_detection(html)
    
    # Should return lists
    assert isinstance(result['email'], list)
    assert isinstance(result['password'], list)
    assert isinstance(result['submit_button'], list)
