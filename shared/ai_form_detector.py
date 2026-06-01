"""
AI-powered form field detection using vision models
"""
from typing import Dict, Any, Optional, List, Union
import base64
import logging
import json

logger = logging.getLogger(__name__)

class AIFormDetector:
    """Service for detecting login form fields using AI vision models"""
    
    def __init__(self, ai_config: Dict[str, Any] = None):
        """
        Initialize AI form detector
        
        Args:
            ai_config: Configuration dictionary with:
                - provider: 'openai' or 'anthropic'
                - model: Model name (e.g., 'gpt-4-vision-preview', 'claude-3-opus-20240229')
                - api_key: API key for the provider
        """
        self.ai_config = ai_config or {}
        self.provider = self.ai_config.get('provider', 'openai')
        self.model = self.ai_config.get('model', 'gpt-4-vision-preview')
        self.api_key = self.ai_config.get('api_key', '')
        
        if not self.api_key:
            # Try to get from environment
            import os
            if self.provider == 'openai':
                self.api_key = os.getenv('OPENAI_API_KEY', '')
            elif self.provider == 'anthropic':
                self.api_key = os.getenv('ANTHROPIC_API_KEY', '')
    
    def detect_form_fields(self, screenshot: bytes, html: Optional[str] = None) -> Dict[str, Union[str, List[str]]]:
        """
        Detect login form fields from screenshot and optionally HTML
        
        Args:
            screenshot: Screenshot image bytes
            html: Optional HTML content of the page
            
        Returns:
            Dictionary mapping credential field names to CSS selectors/XPath
            Format: {
                'email': 'input[name="email"]',
                'password': 'input[type="password"]',
                'submit_button': 'button[type="submit"]'
            }
        """
        if not self.api_key:
            logger.warning("No API key configured for AI form detection, using fallback")
            return self._fallback_detection(html)
        if not screenshot or len(screenshot) == 0:
            logger.debug("No screenshot provided, using HTML fallback for form detection")
            return self._fallback_detection(html)
        try:
            if self.provider == 'openai':
                return self._detect_with_openai(screenshot, html)
            elif self.provider == 'anthropic':
                return self._detect_with_anthropic(screenshot, html)
            else:
                logger.error(f"Unknown AI provider: {self.provider}")
                return self._fallback_detection(html)
        except Exception as e:
            logger.error(f"AI form detection failed: {e}")
            return self._fallback_detection(html)
    
    def _detect_with_openai(self, screenshot: bytes, html: Optional[str] = None) -> Dict[str, Union[str, List[str]]]:
        """Detect form fields using OpenAI vision model"""
        try:
            from openai import OpenAI
            
            client = OpenAI(api_key=self.api_key)
            
            # Encode screenshot as base64
            screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
            
            # Build prompt
            prompt = """Analyze this webpage screenshot and identify the login form fields. 
            Return a JSON object with the following structure:
            {
                "email": "CSS selector or XPath for email/username field",
                "password": "CSS selector or XPath for password field",
                "submit_button": "CSS selector or XPath for submit/login button"
            }
            
            Use CSS selectors when possible (e.g., input[name="email"], button[type="submit"]).
            If CSS selectors are not sufficient, use XPath.
            If a field is not found, use null for that field.
            Focus on finding the main login form, not other forms on the page."""
            
            if html:
                # Include relevant HTML snippet in prompt
                prompt += f"\n\nHere's the HTML structure:\n{html[:2000]}"
            
            # Call OpenAI vision API with timeout
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{screenshot_b64}"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500,
                timeout=30.0
            )
            
            # Parse response
            content = response.choices[0].message.content
            
            # Extract JSON from response (may be wrapped in markdown code blocks)
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            
            # Parse JSON with error handling
            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from OpenAI response: {e}")
                logger.debug(f"Response content: {content[:500]}")
                # Fall back to heuristics
                return self._fallback_detection(html)
            
            # Validate response structure
            if not isinstance(result, dict):
                logger.error(f"AI response is not a dictionary: {type(result)}")
                return self._fallback_detection(html)
            
            # Validate and return - normalize to lists for consistency
            email_selector = result.get('email') or result.get('username')
            password_selector = result.get('password')
            submit_selector = result.get('submit_button')
            
            # Validate that selectors are strings, lists, or None
            def normalize_selector(selector):
                if selector is None:
                    return []
                elif isinstance(selector, str):
                    return [selector] if selector.strip() else []
                elif isinstance(selector, list):
                    return [s for s in selector if s and isinstance(s, str) and s.strip()]
                else:
                    logger.warning(f"Unexpected selector type: {type(selector)}, value: {selector}")
                    return []
            
            return {
                'email': normalize_selector(email_selector),
                'password': normalize_selector(password_selector),
                'submit_button': normalize_selector(submit_selector)
            }
            
        except Exception as e:
            logger.error(f"OpenAI form detection error: {e}")
            raise
    
    def _detect_with_anthropic(self, screenshot: bytes, html: Optional[str] = None) -> Dict[str, Union[str, List[str]]]:
        """Detect form fields using Anthropic Claude vision model"""
        try:
            from anthropic import Anthropic
            
            client = Anthropic(api_key=self.api_key)
            
            # Encode screenshot as base64
            screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
            
            # Build prompt
            prompt = """Analyze this webpage screenshot and identify the login form fields. 
            Return a JSON object with the following structure:
            {
                "email": "CSS selector or XPath for email/username field",
                "password": "CSS selector or XPath for password field",
                "submit_button": "CSS selector or XPath for submit/login button"
            }
            
            Use CSS selectors when possible (e.g., input[name="email"], button[type="submit"]).
            If CSS selectors are not sufficient, use XPath.
            If a field is not found, use null for that field.
            Focus on finding the main login form, not other forms on the page."""
            
            if html:
                prompt += f"\n\nHere's the HTML structure:\n{html[:2000]}"
            
            # Call Anthropic vision API with timeout
            message = client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_b64
                                }
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ]
                    }
                ],
                timeout=30.0  # 30 second timeout
            )
            
            # Parse response
            content = message.content[0].text
            
            # Extract JSON from response
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            
            # Parse JSON with error handling
            try:
                result = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from Anthropic response: {e}")
                logger.debug(f"Response content: {content[:500]}")
                # Fall back to heuristics
                return self._fallback_detection(html)
            
            # Validate response structure
            if not isinstance(result, dict):
                logger.error(f"AI response is not a dictionary: {type(result)}")
                return self._fallback_detection(html)
            
            # Validate and return - normalize to lists for consistency
            email_selector = result.get('email') or result.get('username')
            password_selector = result.get('password')
            submit_selector = result.get('submit_button')
            
            # Validate that selectors are strings, lists, or None
            def normalize_selector(selector):
                if selector is None:
                    return []
                elif isinstance(selector, str):
                    return [selector] if selector.strip() else []
                elif isinstance(selector, list):
                    return [s for s in selector if s and isinstance(s, str) and s.strip()]
                else:
                    logger.warning(f"Unexpected selector type: {type(selector)}, value: {selector}")
                    return []
            
            return {
                'email': normalize_selector(email_selector),
                'password': normalize_selector(password_selector),
                'submit_button': normalize_selector(submit_selector)
            }
            
        except Exception as e:
            logger.error(f"Anthropic form detection error: {e}")
            raise
    
    def _fallback_detection(self, html: Optional[str] = None) -> Dict[str, List[str]]:
        """
        Fallback detection using heuristics when AI is unavailable
        
        Args:
            html: HTML content of the page
            
        Returns:
            Dictionary mapping field names to lists of selectors to try in order
        """
        # Common selectors for login forms (ordered by likelihood)
        selectors = {
            'email': [
                'input[name="email"]',
                'input[type="email"]',
                'input[name="username"]',
                'input[id*="email"]',
                'input[id*="username"]',
                'input[id*="user"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="username" i]',
                'input[placeholder*="user" i]'
            ],
            'password': [
                'input[type="password"]',
                'input[name="password"]',
                'input[id*="password"]',
                'input[id*="pass"]'
            ],
            'submit_button': [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Sign in")',
                'button:has-text("Log in")',
                'button:has-text("Login")',
                'button:has-text("Submit")',
                'button:has-text("Sign In")',
                'button:has-text("Log In")'
            ]
        }
        
        # Return lists of selectors to try
        return {
            'email': selectors.get('email', []),
            'password': selectors.get('password', []),
            'submit_button': selectors.get('submit_button', [])
        }

