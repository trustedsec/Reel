"""
Tests for CampaignService (non-mocked tests only)
"""
import pytest
from api.services.services import CampaignService


@pytest.fixture
def campaign_service():
    """Create CampaignService instance"""
    return CampaignService()
