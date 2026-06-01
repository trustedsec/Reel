"""
Tests for PhishingDetectorPlugin (non-mocked tests only)
"""
import pytest
from plugins.builtin.phishing_detector import PhishingDetectorPlugin


@pytest.fixture
def plugin():
    """Create plugin instance"""
    return PhishingDetectorPlugin()


def test_plugin_properties(plugin):
    """Test plugin properties"""
    assert plugin.plugin_type == "phishing_detector"
    assert plugin.display_name == "Phishing Detector (BERT)"
    assert plugin.plugin_category == "email_validation"


def test_plugin_text_extraction(plugin):
    """Test HTML text extraction"""
    html = '<html><body><h1>Test</h1><p>Content with {{variable}}</p></body></html>'
    text = plugin._extract_text_from_html(html)
    
    assert 'Test' in text
    assert 'Content' in text
    assert '{{variable}}' not in text  # Jinja2 syntax should be removed
