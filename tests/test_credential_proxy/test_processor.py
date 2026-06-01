"""
Tests for CredentialProxyProcessor (non-mocked tests only)
"""
import pytest
from workflows.credential_proxy_processor import CredentialProxyProcessor


@pytest.fixture
def processor():
    """Create processor instance"""
    return CredentialProxyProcessor()


def test_processor_start_stop(processor):
    """Test processor start and stop"""
    assert not processor.running
    
    processor.start()
    assert processor.running
    assert processor.thread is not None
    
    processor.stop()
    assert not processor.running
