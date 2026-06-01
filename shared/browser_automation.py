"""
Browser automation service using Playwright
"""
from typing import Dict, Any, Optional, List, Union
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
import asyncio
import logging
import base64
import io

logger = logging.getLogger(__name__)

class BrowserAutomationService:
    """Service for browser automation using Playwright"""
    
    def __init__(self):
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._playwright = None
    
    async def initialize(self, browser_config: Dict[str, Any] = None):
        """Initialize Playwright browser"""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            
            browser_config = browser_config or {}
            headless = browser_config.get('headless', True)
            user_agent = browser_config.get('user_agent',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            viewport = browser_config.get('viewport', {'width': 1920, 'height': 1080})
            stealth_mode = browser_config.get('stealth_mode', True)
            proxy = browser_config.get('proxy')

            # Launch browser
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                args=['--disable-blink-features=AutomationControlled'] if stealth_mode else []
            )

            # Create context with stealth settings
            context_options = {
                'viewport': viewport,
                'user_agent': user_agent,
                'ignore_https_errors': True
            }

            if proxy:
                context_options['proxy'] = proxy
            
            if stealth_mode:
                # Add additional stealth options
                context_options['extra_http_headers'] = {
                    'Accept-Language': 'en-US,en;q=0.9'
                }
            
            self._context = await self._browser.new_context(**context_options)
            
            # Add stealth scripts if enabled
            if stealth_mode:
                await self._context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
    
    async def cleanup(self):
        """Clean up browser resources"""
        try:
            if self._context:
                try:
                    await self._context.close()
                except Exception as e:
                    logger.warning(f"Error closing browser context: {e}")
                finally:
                    self._context = None
            
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")
                finally:
                    self._browser = None
            
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.warning(f"Error stopping playwright: {e}")
                finally:
                    self._playwright = None
        except Exception as e:
            logger.error(f"Unexpected error during browser cleanup: {e}")
    
    async def navigate_to_site(self, url: str, timeout: int = 30000) -> Page:
        """
        Navigate to a target site
        
        Args:
            url: Target URL
            timeout: Navigation timeout in milliseconds
            
        Returns:
            Page object
        """
        if not self._context:
            await self.initialize()
        
        page = None
        try:
            page = await self._context.new_page()
            await page.goto(url, wait_until='networkidle', timeout=timeout)
            logger.info(f"Successfully navigated to {url}")
            return page
        except Exception as e:
            logger.error(f"Failed to navigate to {url}: {e}")
            # Ensure page is closed on error
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            raise
    
    async def take_screenshot(self, page: Page, full_page: bool = False) -> bytes:
        """
        Take a screenshot of the page
        
        Args:
            page: Page object
            full_page: Whether to capture full page or just viewport
            
        Returns:
            Screenshot bytes
        """
        try:
            screenshot = await page.screenshot(full_page=full_page)
            return screenshot
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            raise
    
    async def get_page_html(self, page: Page) -> str:
        """
        Get the HTML content of the page
        
        Args:
            page: Page object
            
        Returns:
            HTML content as string
        """
        try:
            html = await page.content()
            return html
        except Exception as e:
            logger.error(f"Failed to get page HTML: {e}")
            raise
    
    async def fill_credentials(self, page: Page, credentials: Dict[str, str], 
                               form_fields: Dict[str, Union[str, List[str]]]) -> bool:
        """
        Fill credentials into detected form fields
        
        Args:
            page: Page object
            credentials: Dictionary of credential field names to values
            form_fields: Dictionary mapping credential field names to CSS selectors/XPath
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate form fields - check for None selectors
            required_fields_filled = {'email': False, 'username': False, 'password': False}
            
            # Map credentials to form fields
            for cred_field, selector_or_list in form_fields.items():
                # Handle both single selector (string) and list of selectors
                if isinstance(selector_or_list, list):
                    selectors_to_try = selector_or_list
                elif selector_or_list:
                    selectors_to_try = [selector_or_list]
                else:
                    logger.warning(f"Skipping {cred_field} field - selector is None or empty")
                    continue
                
                if cred_field in credentials:
                    value = credentials[cred_field]
                    filled = False
                    
                    # Try each selector until one works
                    for selector in selectors_to_try:
                        if not selector:
                            continue
                        
                        try:
                            # Wait for element to be visible
                            await page.wait_for_selector(selector, timeout=3000)
                            
                            # Fill the field
                            await page.fill(selector, value)
                            logger.info(f"Filled {cred_field} field with selector {selector}")
                            filled = True
                            
                            # Track required fields
                            if cred_field in ['email', 'username']:
                                required_fields_filled['email'] = True
                                required_fields_filled['username'] = True
                            elif cred_field == 'password':
                                required_fields_filled['password'] = True
                            
                            break  # Success, move to next field
                            
                        except Exception as e:
                            logger.debug(f"Selector {selector} failed for {cred_field}: {e}")
                            # Try alternative: click and type
                            try:
                                await page.click(selector, timeout=2000)
                                await page.type(selector, value, delay=100)
                                logger.info(f"Filled {cred_field} using alternative method with selector {selector}")
                                filled = True
                                
                                # Track required fields
                                if cred_field in ['email', 'username']:
                                    required_fields_filled['email'] = True
                                    required_fields_filled['username'] = True
                                elif cred_field == 'password':
                                    required_fields_filled['password'] = True
                                
                                break  # Success, move to next field
                            except Exception as e2:
                                logger.debug(f"Alternative method also failed for {cred_field} with selector {selector}: {e2}")
                                continue  # Try next selector
                    
                    # If all selectors failed for a required field, return False
                    if not filled and cred_field in ['email', 'username', 'password']:
                        logger.error(f"Failed to fill required field {cred_field} with any selector")
                        return False
            
            # Check if required fields were filled
            has_email_or_username = required_fields_filled['email'] or required_fields_filled['username']
            has_password = required_fields_filled['password']
            
            if not has_email_or_username or not has_password:
                logger.error(f"Failed to fill required fields. Email/Username: {has_email_or_username}, Password: {has_password}")
                return False
            
            return True
        except Exception as e:
            logger.error(f"Error filling credentials: {e}")
            return False
    
    async def fill_available_credentials(self, page: Page, credentials: Dict[str, str],
                                          form_fields: Dict[str, Union[str, List[str]]]) -> Dict[str, list]:
        """
        Fill whichever credential fields are visible on the page.
        Unlike fill_credentials(), this does NOT fail when some fields are missing —
        it fills what it can and reports back.

        Uses strict visibility checks (element must be visible with non-zero
        dimensions) and verifies the value actually took effect after filling.

        Returns:
            {'filled': [field_names...], 'not_found': [field_names...]}
        """
        filled = []
        not_found = []

        for cred_field, selector_or_list in form_fields.items():
            if isinstance(selector_or_list, list):
                selectors_to_try = selector_or_list
            elif selector_or_list:
                selectors_to_try = [selector_or_list]
            else:
                not_found.append(cred_field)
                continue

            if cred_field not in credentials:
                continue

            value = credentials[cred_field]
            field_filled = False

            for selector in selectors_to_try:
                if not selector:
                    continue
                try:
                    # Strict visibility: element must exist, be visible, and have real dimensions
                    element = await page.query_selector(selector)
                    if not element:
                        continue
                    if not await element.is_visible():
                        logger.debug(f"fill_available: {cred_field} selector {selector} exists but not visible, skipping")
                        continue
                    bbox = await element.bounding_box()
                    if not bbox or bbox['width'] < 2 or bbox['height'] < 2:
                        logger.debug(f"fill_available: {cred_field} selector {selector} has no/tiny bounding box, skipping")
                        continue

                    # Element is truly visible — fill it
                    await element.fill(value)

                    # Verify the value actually took effect
                    try:
                        actual = await element.input_value()
                        if actual != value:
                            logger.debug(f"fill_available: {cred_field} fill didn't stick (got '{actual[:20]}'), skipping")
                            continue
                    except Exception:
                        pass  # input_value may not work on all element types; trust the fill

                    logger.info(f"fill_available: filled {cred_field} with selector {selector}")
                    field_filled = True
                    break
                except Exception as e:
                    logger.debug(f"fill_available: selector {selector} failed for {cred_field}: {e}")
                    # Fallback: click and type (only if element was visible)
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            await element.click(timeout=2000)
                            await element.type(value, delay=100)
                            logger.info(f"fill_available: filled {cred_field} (click+type) with selector {selector}")
                            field_filled = True
                            break
                    except Exception:
                        continue

            if field_filled:
                filled.append(cred_field)
            else:
                not_found.append(cred_field)

        return {'filled': filled, 'not_found': not_found}

    async def submit_form(self, page: Page, submit_button_selector: Optional[Union[str, List[str]]] = None) -> bool:
        """
        Submit the form
        
        Args:
            page: Page object
            submit_button_selector: Optional CSS selector for submit button (string or list of strings)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if submit_button_selector:
                # Handle both string and list types
                if isinstance(submit_button_selector, list):
                    # Try each selector until one works
                    submitted = False
                    for selector in submit_button_selector:
                        if not selector:
                            continue
                        try:
                            await page.wait_for_selector(selector, timeout=3000)
                            await page.click(selector)
                            submitted = True
                            logger.info(f"Submitted form using selector: {selector}")
                            break
                        except Exception:
                            continue
                    if not submitted:
                        logger.warning("All submit button selectors failed, trying fallback")
                else:
                    # Single selector (string)
                    await page.wait_for_selector(submit_button_selector, timeout=5000)
                    await page.click(submit_button_selector)
            else:
                # Try to find and click submit button
                # Common selectors for submit buttons
                submit_selectors = [
                    'input[type="submit"]',
                    'button[type="submit"]',
                    'button:has-text("Sign in")',
                    'button:has-text("Log in")',
                    'button:has-text("Login")',
                    'button:has-text("Submit")',
                    '[data-testid*="submit"]',
                    '[id*="submit"]',
                    '[class*="submit"]'
                ]
                
                submitted = False
                for selector in submit_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            await element.click()
                            submitted = True
                            logger.info(f"Submitted form using selector: {selector}")
                            break
                    except Exception:
                        continue
                
                if not submitted:
                    # Fallback: press Enter on the last filled field
                    await page.keyboard.press('Enter')
                    logger.info("Submitted form using Enter key")
            
            # Wait for navigation or response
            await page.wait_for_load_state('networkidle', timeout=10000)
            return True
        except Exception as e:
            logger.error(f"Error submitting form: {e}")
            return False
    
    async def proxy_credentials_to_site(self, url: str, credentials: Dict[str, str],
                                       form_fields: Dict[str, str], 
                                       config: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Complete workflow: navigate, fill, submit for one site
        
        Args:
            url: Target site URL
            credentials: Credentials to fill
            form_fields: Form field mappings (from AI detector)
            config: Additional configuration (timeout, etc.)
            
        Returns:
            Dictionary with result information
        """
        config = config or {}
        timeout = (config.get('timeout') or 30) * 1000  # Convert to milliseconds
        page = None
        
        try:
            # Navigate to site
            page = await self.navigate_to_site(url, timeout=timeout)
            
            # Fill credentials
            fill_success = await self.fill_credentials(page, credentials, form_fields)
            if not fill_success:
                return {
                    'success': False,
                    'error': 'Failed to fill credentials',
                    'url': url
                }
            
            # Submit form
            submit_button = form_fields.get('submit_button')
            submit_success = await self.submit_form(page, submit_button)
            
            # Wait a bit for response
            await asyncio.sleep(2)
            
            # Get final URL and page title
            final_url = page.url
            page_title = await page.title()
            
            # Check for error messages on page
            error_messages = await self._detect_error_messages(page)
            
            # Check for success indicators
            success_indicators = await self._detect_success_indicators(page)
            
            # Determine success using multiple indicators:
            # 1. Form was submitted successfully
            # 2. No error messages present
            # 3. URL changed OR success indicators found
            url_changed = final_url != url
            has_success_indicators = len(success_indicators) > 0
            no_errors = not error_messages
            
            success = submit_success and no_errors and (url_changed or has_success_indicators)
            
            result = {
                'success': success,
                'url': url,
                'final_url': final_url,
                'page_title': page_title,
                'error_messages': error_messages,
                'success_indicators': success_indicators,
                'url_changed': url_changed,
                'submitted': submit_success
            }
            
            # Take screenshot for debugging (with size limit)
            try:
                screenshot = await self.take_screenshot(page)
                # Limit screenshot size to 500KB before encoding
                max_size = 500 * 1024  # 500KB
                if len(screenshot) > max_size:
                    # Try to compress screenshot using PIL if available
                    try:
                        from PIL import Image
                        img = Image.open(io.BytesIO(screenshot))
                        # Resize if too large (maintain aspect ratio)
                        if img.width > 1920 or img.height > 1080:
                            img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
                        # Save with compression
                        output = io.BytesIO()
                        img.save(output, format='PNG', optimize=True, quality=85)
                        screenshot = output.getvalue()
                        # If still too large, reduce quality further
                        if len(screenshot) > max_size:
                            output = io.BytesIO()
                            img.save(output, format='JPEG', quality=70)
                            screenshot = output.getvalue()
                    except ImportError:
                        # PIL not available, skip compression
                        logger.debug("PIL not available, screenshot compression skipped")
                    except Exception as e:
                        # PIL available but compression failed
                        logger.warning(f"Screenshot compression failed: {e}")
                        # If still too large after failed compression, truncate
                        if len(screenshot) > max_size:
                            logger.warning(f"Screenshot too large ({len(screenshot)} bytes), truncating")
                            screenshot = screenshot[:max_size]
                
                # Encode and store
                encoded = base64.b64encode(screenshot).decode('utf-8')
                # Only store if under reasonable size (1MB encoded)
                if len(encoded) < 1024 * 1024:
                    result['screenshot'] = encoded
                else:
                    logger.warning(f"Screenshot too large ({len(encoded)} bytes), not storing")
            except Exception as e:
                logger.warning(f"Failed to take screenshot: {e}")
            
            # Capture cookies set by the target site (for session reuse)
            try:
                cookies = await page.context.cookies()
                result['cookies'] = cookies
            except Exception as e:
                logger.warning(f"Failed to get cookies: {e}")
                result['cookies'] = []
            
            return result
            
        except Exception as e:
            logger.error(f"Error proxying credentials to {url}: {e}")
            return {
                'success': False,
                'error': str(e),
                'url': url
            }
        finally:
            # Always close page, even on error
            if page:
                try:
                    await page.close()
                except Exception as e:
                    logger.warning(f"Error closing page: {e}")
    
    async def _detect_error_messages(self, page: Page) -> List[str]:
        """
        Detect error messages on the page
        
        Args:
            page: Page object
            
        Returns:
            List of error messages found
        """
        error_messages = []
        
        # Common error message selectors
        error_selectors = [
            '.error',
            '.error-message',
            '[class*="error"]',
            '[id*="error"]',
            '[role="alert"]',
            '.alert-danger',
            '.alert-error'
        ]
        
        for selector in error_selectors:
            try:
                elements = await page.query_selector_all(selector)
                for element in elements:
                    text = await element.inner_text()
                    if text and text.strip():
                        error_messages.append(text.strip())
            except Exception:
                continue
        
        return error_messages
    
    async def _detect_success_indicators(self, page: Page) -> List[str]:
        """
        Detect success indicators on the page (dashboard, welcome messages, etc.)
        
        Args:
            page: Page object
            
        Returns:
            List of success indicators found
        """
        success_indicators = []
        
        # Common success indicators
        success_selectors = [
            '[class*="dashboard"]',
            '[class*="welcome"]',
            '[id*="dashboard"]',
            '[id*="welcome"]',
            'h1:has-text("Welcome")',
            'h1:has-text("Dashboard")',
            '[class*="success"]',
            '.alert-success'
        ]
        
        # Also check page title for success keywords
        try:
            page_title = await page.title()
            success_keywords = ['dashboard', 'welcome', 'home', 'account', 'profile']
            if any(keyword in page_title.lower() for keyword in success_keywords):
                success_indicators.append(f"Page title: {page_title}")
        except Exception:
            pass
        
        # Check for common success text patterns using targeted selectors instead of full page content
        # This is more efficient than loading the entire HTML
        success_text_selectors = [
            'h1', 'h2', 'h3',  # Headings often contain welcome messages
            '.welcome', '.dashboard', '.success',  # Common success classes
            '[class*="welcome"]', '[class*="dashboard"]', '[class*="success"]'
        ]
        
        try:
            for selector in success_text_selectors[:5]:  # Limit to first 5 to avoid too many queries
                try:
                    elements = await page.query_selector_all(selector)
                    for element in elements[:3]:  # Limit to first 3 elements
                        text = await element.inner_text()
                        if text:
                            text_lower = text.lower()
                            success_keywords = ['welcome', 'dashboard', 'logged in', 'successfully', 'account', 'home']
                            if any(keyword in text_lower for keyword in success_keywords):
                                success_indicators.append(f"Found success text: {text[:50]}")
                                break  # Found one, no need to check more
                    if success_indicators:
                        break  # Found indicator, stop checking
                except Exception:
                    continue
        except Exception:
            pass
        
        # Check for success elements
        for selector in success_selectors:
            try:
                elements = await page.query_selector_all(selector)
                if elements:
                    for element in elements[:3]:  # Limit to first 3
                        text = await element.inner_text()
                        if text and text.strip():
                            success_indicators.append(text.strip()[:100])  # Limit length
                            break  # Only add one per selector type
            except Exception:
                continue
        
        return success_indicators

# Global service instance
_automation_service = None

def get_automation_service() -> BrowserAutomationService:
    """Get global browser automation service instance"""
    global _automation_service
    if _automation_service is None:
        _automation_service = BrowserAutomationService()
    return _automation_service

