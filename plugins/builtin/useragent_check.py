"""
User agent check plugin - validates/checks user agent
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
import logging
import re

logger = logging.getLogger(__name__)

class UserAgentCheckPlugin(BasePlugin):
    """
    Plugin for checking and validating user agent strings from requests.
    
    Validates user agent strings against allow/block patterns using regex.
    Can block requests, redirect, log, or allow based on user agent matching.
    Useful for filtering bots, crawlers, and automated tools.
    
    Use cases:
    - Block automated tools and bots
    - Allow only specific browsers
    - Redirect suspicious user agents
    - Log user agent patterns for analysis
    - Implement allow/deny lists for user agents
    
    Example: Block user agents matching 'bot', 'crawler', or 'scraper' patterns,
    or allow only Chrome/Firefox browsers and redirect others.
    """
    
    @property
    def plugin_type(self) -> str:
        return "useragent_check"
    
    @property
    def display_name(self) -> str:
        return "User Agent Check"
    
    @property
    def description(self) -> str:
        return "Check and validate user agent strings against regex patterns. Supports allow/block lists, blocking requests, redirecting, or logging. Use to filter bots, crawlers, automated tools, or allow only specific browsers. Stores check result in context for conditional branching."
    
    @property
    def plugin_category(self) -> str:
        return "validation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "check_type": {
                    "type": "string",
                    "title": "Check Action",
                    "enum": [
                        {
                            "value": "block",
                            "label": "Block Request",
                            "description": "Block the request with 403 Forbidden if check fails. Prevents access completely."
                        },
                        {
                            "value": "allow",
                            "label": "Allow Only",
                            "description": "Only allow requests that pass the check. Block others with 403."
                        },
                        {
                            "value": "redirect",
                            "label": "Redirect",
                            "description": "Redirect to URL if check fails. Use redirect_url to specify destination."
                        },
                        {
                            "value": "log",
                            "label": "Log and continue",
                            "description": "Log the check result and continue the workflow. Use PASS/FAIL branch to route (e.g. redirect on FAIL)."
                        }
                    ],
                    "description": "Action to take based on user agent check result",
                    "help": "What to do when user agent check fails: Block (403 error), Allow (only allow matching), Redirect (send to redirect_url), or Log and continue (log result, follow PASS/FAIL branch). Default: log.",
                    "default": "log"
                },
                "allowed_patterns": {
                    "type": "array",
                    "title": "Allowed Patterns",
                    "items": {"type": "string"},
                    "description": "Regex patterns for allowed user agents",
                    "help": "Add one regex pattern per row. If user agent matches any pattern, check passes (when on_match is 'pass'). Examples: Chrome, Firefox, Safari, Mozilla/5\\.0",
                    "placeholder": "Chrome, Firefox, Safari, Mozilla/5\\.0"
                },
                "blocked_patterns": {
                    "type": "array",
                    "title": "Blocked Patterns",
                    "items": {"type": "string"},
                    "description": "Regex patterns for blocked user agents",
                    "help": "Add one regex pattern per row. If user agent matches any pattern, check fails.\n\nTo block common crawlers/bots, add one per row:\nbot\ncrawler\nscraper\ncurl\nwget",
                    "placeholder": "bot, crawler, scraper, curl, wget"
                },
                "redirect_url": {
                    "type": "string",
                    "title": "Redirect URL",
                    "description": "URL to redirect to if check fails",
                    "help": "URL to redirect user to when user agent check fails. Only used when check_type is 'redirect'. Supports variable interpolation. Example: /blocked, https://example.com/denied, or {{campaign.blocked_page}}.",
                    "placeholder": "/blocked, https://example.com/denied, {{campaign.blocked_page}}"
                },
                "on_match": {
                    "type": "string",
                    "title": "On Pattern Match",
                    "enum": [
                        {
                            "value": "pass",
                            "label": "Pass on Match",
                            "description": "Check passes when pattern matches. Use with allowed_patterns to allow matching UAs, or with blocked_patterns to block matching UAs (inverse)."
                        },
                        {
                            "value": "fail",
                            "label": "Fail on Match",
                            "description": "Check fails when pattern matches. Use with blocked_patterns to block matching UAs, or with allowed_patterns to block non-matching UAs (inverse)."
                        }
                    ],
                    "description": "Whether pattern match should pass or fail the check",
                    "help": "Determines if matching a pattern passes or fails the check. 'pass' means match = pass (use with allowed_patterns), 'fail' means match = fail (use with blocked_patterns). Default: pass.",
                    "default": "pass"
                }
            },
            "required": []
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute user agent check"""
        try:
            user_agent = context.get('request', {}).get('user_agent', '')
            check_type = config.get('check_type', 'log')
            allowed_patterns = config.get('allowed_patterns', [])
            blocked_patterns = config.get('blocked_patterns', [])
            on_match = config.get('on_match', 'pass')
            
            # Check against patterns
            allowed_match = False
            blocked_match = False
            
            if allowed_patterns:
                for pattern in allowed_patterns:
                    try:
                        if re.search(pattern, user_agent, re.IGNORECASE):
                            allowed_match = True
                            break
                    except re.error:
                        logger.warning(f"Invalid regex pattern: {pattern}")
            
            if blocked_patterns:
                for pattern in blocked_patterns:
                    try:
                        if re.search(pattern, user_agent, re.IGNORECASE):
                            blocked_match = True
                            break
                    except re.error:
                        logger.warning(f"Invalid regex pattern: {pattern}")
            
            # Determine result
            if on_match == 'pass':
                # Pass if matches allowed, fail if matches blocked
                check_passed = allowed_match if allowed_patterns else (not blocked_match)
            else:
                # Fail if matches allowed, pass if matches blocked (inverse)
                check_passed = not allowed_match if allowed_patterns else blocked_match
            
            context['useragent_check_passed'] = check_passed
            context['useragent_check_result'] = {
                'user_agent': user_agent,
                'allowed_match': allowed_match,
                'blocked_match': blocked_match,
                'check_passed': check_passed
            }
            
            # Handle based on check_type
            if check_type == 'block' and not check_passed:
                context['_response_status'] = 403
                context['_response_html'] = "<html><body><h1>Access Denied</h1></body></html>"
                return context
            
            if check_type == 'redirect' and not check_passed:
                redirect_url = config.get('redirect_url', '/')
                context['_response_redirect'] = redirect_url
                return context
            
            if check_type == 'log':
                logger.info(f"User agent check: {user_agent} - {'PASSED' if check_passed else 'FAILED'}")
            
            return context
            
        except Exception as e:
            logger.error(f"User agent check failed: {e}")
            return self.on_error(e, context, config)
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the check result"""
        return "useragent_check_passed"
    
    def get_branch_labels(self) -> Dict[str, str]:
        """Return custom labels for pass/fail paths"""
        return {"true": "Pass", "false": "Fail"}
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate user agent check configuration"""
        errors = []
        
        check_type = config.get('check_type', 'log')
        
        if check_type == 'redirect' and not config.get('redirect_url'):
            errors.append("redirect_url is required when check_type is 'redirect'")
        
        # Validate regex patterns
        for pattern in config.get('allowed_patterns', []) + config.get('blocked_patterns', []):
            try:
                re.compile(pattern)
            except re.error as e:
                errors.append(f"Invalid regex pattern '{pattern}': {e}")
        
        return errors if errors else None

