"""
Tests for TemplateService (non-mocked tests only)
"""
import pytest
from api.services.services import TemplateService


@pytest.fixture
def template_service():
    """Create TemplateService instance"""
    return TemplateService()
