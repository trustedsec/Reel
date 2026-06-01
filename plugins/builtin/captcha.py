"""
CAPTCHA plugin - validates and renders CAPTCHA widgets
Supports Cloudflare Turnstile and is extensible for other providers
"""
from typing import Dict, Any, Optional, List
from ..base import BasePlugin
from shared.database import db, Template
from shared.template_render import render_sandboxed
import logging
import requests

logger = logging.getLogger(__name__)

class CaptchaPlugin(BasePlugin):
    """
    Plugin for CAPTCHA validation and rendering in workflows.
    
    Integrates Cloudflare Turnstile CAPTCHA into campaign workflows. Can render
    CAPTCHA widgets in templates, validate tokens from form submissions, or do
    both. Supports custom templates and configurable failure handling.
    
    Use cases:
    - Add CAPTCHA to login forms to prevent automated attacks
    - Protect forms from bot submissions
    - Implement CAPTCHA challenges in multi-step flows
    - Custom CAPTCHA pages with branding
    
    Requirements:
    - Cloudflare Turnstile account
    - Site key (public) and secret key (private)
    - CAPTCHA widget script loaded in template
    
    Example: Render CAPTCHA widget on GET request, then validate token on POST
    submission. Block request if validation fails.
    """
    
    @property
    def plugin_type(self) -> str:
        return "captcha"
    
    @property
    def display_name(self) -> str:
        return "CAPTCHA"
    
    @property
    def description(self) -> str:
        return "Validate CAPTCHA tokens and render CAPTCHA widgets using Cloudflare Turnstile. Supports render-only, validate-only, or both modes. Use to protect forms from bots, add challenges to workflows, and implement custom CAPTCHA pages. Requires Cloudflare Turnstile site key and secret key."
    
    @property
    def plugin_category(self) -> str:
        return "validation"
    
    @property
    def config_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "captcha_type": {
                    "type": "string",
                    "title": "CAPTCHA Provider",
                    "enum": [
                        {
                            "value": "turnstile",
                            "label": "Cloudflare Turnstile",
                            "description": "Cloudflare Turnstile CAPTCHA. Privacy-focused, invisible CAPTCHA alternative. Requires Cloudflare account and Turnstile site/secret keys."
                        }
                    ],
                    "description": "CAPTCHA provider to use",
                    "help": "Currently supports Cloudflare Turnstile. Get keys from Cloudflare dashboard (Zero Trust > Turnstile).",
                    "default": "turnstile"
                },
                "site_key": {
                    "type": "string",
                    "title": "Site Key (Public)",
                    "description": "CAPTCHA site key (public key for widget rendering)",
                    "help": "Public site key from Cloudflare Turnstile. Used to render the CAPTCHA widget in templates. Get this from Cloudflare dashboard: Zero Trust > Turnstile > Create Site. Format: 0x4AAA...",
                    "placeholder": "0x4AAAAAAABkMYinukE8K5Y0g"
                },
                "secret_key": {
                    "type": "string",
                    "title": "Secret Key (Private)",
                    "description": "CAPTCHA secret key (private key for token validation)",
                    "help": "Private secret key from Cloudflare Turnstile. Used to validate CAPTCHA tokens on the server. Keep this secret! Get this from Cloudflare dashboard: Zero Trust > Turnstile > Site Settings. Format: 0x4AAA...",
                    "placeholder": "0x4AAAAAAABkMYinukE8K5Y0g"
                },
                "template_id": {
                    "type": "integer",
                    "title": "Custom Template",
                    "description": "Optional template for custom CAPTCHA page",
                    "help": "Optional template to use for rendering CAPTCHA. If provided, this template will be rendered instead of injecting widget into existing HTML. Template should include Turnstile widget script and div. Leave empty to inject widget into current response HTML.",
                    "placeholder": "Select a template",
                    "x-optionsSource": "templates"
                },
                "mode": {
                    "type": "string",
                    "title": "Plugin Mode",
                    "enum": [
                        {
                            "value": "render",
                            "label": "Render Only",
                            "description": "Only render CAPTCHA widget in template. Use in GET workflow to display widget. Does not validate tokens."
                        },
                        {
                            "value": "validate",
                            "label": "Validate Only",
                            "description": "Only validate CAPTCHA token from form submission. Use in POST workflow to check token. Does not render widget."
                        },
                        {
                            "value": "both",
                            "label": "Render and Validate",
                            "description": "Render widget on GET and validate token on POST. Use in workflows that handle both GET (show form) and POST (process form) requests."
                        }
                    ],
                    "description": "Plugin operation mode",
                    "help": "Choose what the plugin should do: Render widget (GET requests), validate token (POST requests), or both (handles GET and POST). 'both' is most common for forms that need widget display and validation.",
                    "default": "both"
                },
                "on_failure": {
                    "type": "string",
                    "title": "Failure Action",
                    "enum": [
                        {
                            "value": "block",
                            "label": "Block Request",
                            "description": "Block the request and show error message. Prevents workflow from continuing. Use when CAPTCHA is required."
                        },
                        {
                            "value": "redirect",
                            "description": "Redirect to URL on validation failure. Use failure_redirect to specify URL. Allows graceful handling of failures."
                        },
                        {
                            "value": "continue",
                            "label": "Continue Workflow",
                            "description": "Continue workflow execution even if validation fails. Sets captcha_passed=false in context for conditional logic. Use when CAPTCHA is optional or for logging purposes."
                        }
                    ],
                    "description": "Action to take when CAPTCHA validation fails",
                    "help": "What to do when CAPTCHA validation fails: Block (show error, stop workflow), Redirect (send to failure_redirect URL), or Continue (set captcha_passed=false, allow workflow to continue). Default: block.",
                    "default": "block"
                },
                "failure_redirect": {
                    "type": "string",
                    "title": "Failure Redirect URL",
                    "description": "URL to redirect to on validation failure",
                    "help": "URL to redirect user to when CAPTCHA validation fails. Only used when on_failure is 'redirect'. Supports variable interpolation. Example: /error, https://example.com/failed, or {{campaign.error_page}}.",
                    "placeholder": "/error, https://example.com/failed, {{campaign.error_page}}"
                },
                "failure_message": {
                    "type": "string",
                    "title": "Failure Error Message",
                    "description": "Error message to display when validation fails",
                    "help": "Error message to show to user when CAPTCHA validation fails. Only used when on_failure is 'block'. Supports HTML. Default: 'CAPTCHA verification failed. Please try again.'",
                    "placeholder": "CAPTCHA verification failed. Please try again.",
                    "default": "CAPTCHA verification failed. Please try again."
                },
                "token_field": {
                    "type": "string",
                    "title": "Token Field Name",
                    "description": "Form field name containing CAPTCHA token",
                    "help": "Name of the HTML form field that contains the CAPTCHA token. For Cloudflare Turnstile, this is typically 'cf-turnstile-response'. The token is automatically submitted by Turnstile widget. Only change if using custom field name.",
                    "placeholder": "cf-turnstile-response, captcha_token",
                    "default": "cf-turnstile-response"
                }
            },
            "required": ["site_key", "secret_key"]
        }
    
    def execute(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute CAPTCHA validation or rendering"""
        try:
            mode = config.get('mode', 'both')
            captcha_type = config.get('captcha_type', 'turnstile')
            
            # Handle render mode
            if mode in ('render', 'both'):
                context = self._render_captcha(context, config)
            
            # Handle validate mode
            if mode in ('validate', 'both'):
                context = self._validate_captcha(context, config, captcha_type)
            
            return context
            
        except Exception as e:
            logger.error(f"CAPTCHA plugin execution failed: {e}")
            return self.on_error(e, context, config)
    
    def _render_captcha(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Render CAPTCHA widget in template"""
        try:
            site_key = config.get('site_key')
            if not site_key:
                logger.warning("No site_key provided for CAPTCHA rendering")
                return context
            
            template_id = config.get('template_id')
            
            # If template_id provided, render that template
            if template_id:
                template = Template.query.get(template_id)
                if template:
                    template_html = template.template_html
                    # Inject site_key into template context
                    context['captcha_site_key'] = site_key
                    context['captcha_type'] = config.get('captcha_type', 'turnstile')
                    
                    # Render template (sandboxed, single-pass)
                    try:
                        rendered_html = render_sandboxed(template_html, context)
                    except Exception as e:
                        logger.error(f"Template rendering error: {e}")
                        rendered_html = (
                            "<html><body><h1>Template error</h1>"
                            "<p>The CAPTCHA template could not be rendered.</p></body></html>"
                        )
                    
                    context['_response_html'] = rendered_html
                    logger.debug(f"Rendered CAPTCHA template {template_id}")
                    return context
                else:
                    logger.warning(f"Template {template_id} not found, falling back to widget injection")
            
            # Otherwise, inject widget into existing HTML
            html = context.get('html', '') or context.get('_response_html', '')
            if not html:
                # Try to get from campaign template
                html = context.get('campaign', {}).get('template_html', '')
            
            if html:
                # Inject Turnstile widget script and div
                captcha_widget = self._generate_turnstile_widget(site_key)
                
                # Inject before closing body tag, or at end if no body tag
                if '</body>' in html:
                    html = html.replace('</body>', f'{captcha_widget}\n</body>')
                else:
                    html = html + captcha_widget
                
                context['html'] = html
                context['_response_html'] = html
                logger.debug("Injected CAPTCHA widget into template")
            else:
                logger.warning("No HTML template found to inject CAPTCHA widget")
            
            return context
            
        except Exception as e:
            logger.error(f"CAPTCHA rendering failed: {e}")
            return context
    
    def _validate_captcha(self, context: Dict[str, Any], config: Dict[str, Any], captcha_type: str) -> Dict[str, Any]:
        """Validate CAPTCHA token"""
        try:
            form_data = context.get('request', {}).get('form_data', {})
            token_field = config.get('token_field', 'cf-turnstile-response')
            token = form_data.get(token_field, '')
            
            if not token:
                logger.warning("No CAPTCHA token found in form data")
                context['captcha_passed'] = False
                context['captcha_error'] = "CAPTCHA token missing"
                return self._handle_validation_failure(context, config)
            
            # Validate based on captcha type
            if captcha_type == 'turnstile':
                is_valid = self._validate_turnstile(token, config, context)
            else:
                logger.error(f"Unsupported CAPTCHA type: {captcha_type}")
                context['captcha_passed'] = False
                context['captcha_error'] = f"Unsupported CAPTCHA type: {captcha_type}"
                return self._handle_validation_failure(context, config)
            
            context['captcha_passed'] = is_valid

            if not is_valid:
                logger.warning("CAPTCHA validation failed")
                context['captcha_error'] = "CAPTCHA verification failed"
                return self._handle_validation_failure(context, config)

            # Session gating: mark this campaign as passed for this session (persisted by executor)
            campaign_id = context.get('campaign', {}).get('id')
            if campaign_id is not None:
                existing = context.get('_set_session')
                if isinstance(existing, dict):
                    existing[f'captcha_passed_{campaign_id}'] = True
                else:
                    context['_set_session'] = {f'captcha_passed_{campaign_id}': True}

            logger.debug("CAPTCHA validation passed")
            return context
            
        except Exception as e:
            logger.error(f"CAPTCHA validation error: {e}")
            context['captcha_passed'] = False
            context['captcha_error'] = str(e)
            return self._handle_validation_failure(context, config)
    
    def _validate_turnstile(self, token: str, config: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Validate Cloudflare Turnstile token"""
        try:
            secret_key = config.get('secret_key')
            if not secret_key:
                logger.error("No secret_key provided for Turnstile validation")
                return False
            
            # Get client IP from context
            client_ip = context.get('request', {}).get('ip_address', '')
            
            # Validate with Cloudflare API
            verify_url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
            data = {
                'secret': secret_key,
                'response': token
            }
            
            if client_ip:
                data['remoteip'] = client_ip
            
            response = requests.post(verify_url, data=data, timeout=5)
            
            if response.status_code != 200:
                logger.error(f"Turnstile API returned status {response.status_code}")
                return False
            
            result = response.json()
            success = result.get('success', False)
            
            if not success:
                error_codes = result.get('error-codes', [])
                logger.warning(f"Turnstile validation failed: {error_codes}")
            
            return success
            
        except requests.RequestException as e:
            logger.error(f"Turnstile API request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Turnstile validation error: {e}")
            return False
    
    def _generate_turnstile_widget(self, site_key: str) -> str:
        """Generate Turnstile widget HTML and script"""
        return f"""
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<div class="cf-turnstile" data-sitekey="{site_key}"></div>
"""
    
    def _handle_validation_failure(self, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Handle CAPTCHA validation failure"""
        on_failure = config.get('on_failure', 'block')
        
        if on_failure == 'block':
            error_message = config.get('failure_message', 'CAPTCHA verification failed. Please try again.')
            error_html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>CAPTCHA Verification Failed</title>
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center; margin-top: 50px; }}
        .error {{ color: #e74c3c; }}
    </style>
</head>
<body>
    <h1 class="error">CAPTCHA Verification Failed</h1>
    <p>{error_message}</p>
    <p><a href="javascript:history.back()">Go back</a></p>
</body>
</html>
"""
            context['_response_html'] = error_html
            context['_response_status'] = 400
        elif on_failure == 'redirect':
            redirect_url = config.get('failure_redirect', '/')
            context['_response_redirect'] = redirect_url
        # If 'continue', just store failure in context
        
        return context
    
    def get_branch_context_key(self) -> Optional[str]:
        """Return the context key that contains the validation result"""
        return "captcha_passed"
    
    def get_branch_labels(self) -> Dict[str, str]:
        """Return custom labels for pass/fail paths"""
        return {"true": "Pass", "false": "Fail"}
    
    def validate_config(self, config: Dict[str, Any]) -> Optional[List[str]]:
        """Validate CAPTCHA configuration"""
        errors = []
        
        if not config.get('site_key'):
            errors.append("site_key is required")
        
        if not config.get('secret_key'):
            errors.append("secret_key is required")
        
        mode = config.get('mode', 'both')
        if mode in ('validate', 'both'):
            # Secret key is required for validation
            if not config.get('secret_key'):
                errors.append("secret_key is required when mode includes validation")
        
        on_failure = config.get('on_failure', 'block')
        if on_failure == 'redirect' and not config.get('failure_redirect'):
            errors.append("failure_redirect is required when on_failure is 'redirect'")
        
        return errors if errors else None
