"""
Tests for BrowserAutomationService (non-mocked tests only)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from shared.browser_automation import BrowserAutomationService


@pytest.fixture
def automation_service():
    """Create automation service instance"""
    return BrowserAutomationService()


@pytest.mark.asyncio
async def test_automation_cleanup_uninitialized(automation_service):
    """Test cleanup handles uninitialized service"""
    # Should not raise error
    await automation_service.cleanup()


@pytest.mark.asyncio
async def test_initialize_passes_proxy_to_context(automation_service):
    """Test that proxy config from browser_config is passed to new_context()."""
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_chromium = AsyncMock()
    mock_chromium.launch.return_value = mock_browser

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium

    mock_pw_start = AsyncMock(return_value=mock_pw)

    with patch('shared.browser_automation.async_playwright') as mock_apw:
        mock_apw.return_value.start = mock_pw_start

        await automation_service.initialize(browser_config={
            'proxy': {'server': 'socks5://1.2.3.4:1080'},
        })

        call_kwargs = mock_browser.new_context.call_args.kwargs
        assert call_kwargs['proxy'] == {'server': 'socks5://1.2.3.4:1080'}

    await automation_service.cleanup()


@pytest.mark.asyncio
async def test_initialize_without_proxy_omits_proxy_key(automation_service):
    """Test that context_options omits proxy when not configured."""
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_chromium = AsyncMock()
    mock_chromium.launch.return_value = mock_browser

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium

    mock_pw_start = AsyncMock(return_value=mock_pw)

    with patch('shared.browser_automation.async_playwright') as mock_apw:
        mock_apw.return_value.start = mock_pw_start

        await automation_service.initialize(browser_config={
            'headless': True,
        })

        call_kwargs = mock_browser.new_context.call_args.kwargs
        assert 'proxy' not in call_kwargs

    await automation_service.cleanup()
